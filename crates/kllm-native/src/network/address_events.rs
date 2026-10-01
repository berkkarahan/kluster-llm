//! Bounded native event observation. Loss is terminal for this ownership epoch.
#[cfg(feature = "python-bindings")]
use pyo3::prelude::*;
use serde::Serialize;
use std::collections::VecDeque;
use std::io;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
#[cfg(unix)]
use std::thread::{self, JoinHandle};
#[cfg(unix)]
use std::time::{Duration, Instant};

#[derive(Clone, Serialize)]
pub struct AddressEvent {
    pub sequence: u64,
    pub kind: String,
    pub interface_index: u32,
    pub address: Option<String>,
    pub prefix_length: Option<u8>,
    pub dad_state: Option<i32>,
}
#[derive(Serialize)]
struct Batch {
    epoch: String,
    high_water: u64,
    health: String,
    events: Vec<AddressEvent>,
}
#[cfg_attr(windows, allow(dead_code))]
struct Shared {
    epoch: String,
    index: u32,
    queue: Mutex<VecDeque<AddressEvent>>,
    sequence: AtomicU64,
    lost: AtomicBool,
    closed: AtomicBool,
    barrier_request: AtomicU64,
    barrier_ack: AtomicU64,
}
impl Shared {
    fn emit(&self, mut event: AddressEvent) {
        // Callback contention can hide ordering; conservatively invalidate it.
        match self.queue.try_lock() {
            Ok(mut queue) => {
                event.sequence = self.sequence.fetch_add(1, Ordering::SeqCst) + 1;
                if queue.len() >= 1024 {
                    self.lost.store(true, Ordering::SeqCst);
                } else {
                    queue.push_back(event);
                }
            }
            Err(_) => {
                self.lost.store(true, Ordering::SeqCst);
            }
        }
    }
}
fn failure(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::Other, message)
}

#[cfg_attr(feature = "python-bindings", pyclass)]
pub struct AddressObserver {
    shared: Arc<Shared>,
    #[cfg(unix)]
    thread: Mutex<Option<JoinHandle<()>>>,
    #[cfg(windows)]
    handles: Mutex<Option<(isize, isize)>>,
}
impl AddressObserver {
    pub fn close_native(&self) {
        self.shared.closed.store(true, Ordering::SeqCst);
        #[cfg(unix)]
        if let Some(handle) = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = handle.join();
        }
        #[cfg(windows)]
        if let Some((address, route)) = self
            .handles
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        {
            unsafe {
                use windows::Win32::{
                    Foundation::HANDLE, NetworkManagement::IpHelper::CancelMibChangeNotify2,
                };
                let _ = CancelMibChangeNotify2(HANDLE(address));
                let _ = CancelMibChangeNotify2(HANDLE(route));
            }
        }
    }
    pub fn events_native(&self) -> io::Result<String> {
        // Request a complete drain through WouldBlock, rather than assuming the
        // background reader has already seen the kernel's pending notifications.
        #[cfg(unix)]
        if !self.shared.closed.load(Ordering::SeqCst) {
            let request = self.shared.barrier_request.fetch_add(1, Ordering::SeqCst) + 1;
            let deadline = Instant::now() + Duration::from_secs(2);
            while self.shared.barrier_ack.load(Ordering::SeqCst) < request {
                if Instant::now() >= deadline || self.shared.closed.load(Ordering::SeqCst) {
                    self.shared.lost.store(true, Ordering::SeqCst);
                    break;
                }
                thread::sleep(Duration::from_millis(2));
            }
        }
        let mut queue = self
            .shared
            .queue
            .lock()
            .map_err(|_| failure("observer queue failed"))?;
        let batch = Batch {
            epoch: self.shared.epoch.clone(),
            high_water: self.shared.sequence.load(Ordering::SeqCst),
            health: if self.shared.lost.load(Ordering::SeqCst) {
                "gap"
            } else if self.shared.closed.load(Ordering::SeqCst) {
                "closed"
            } else {
                "continuous"
            }
            .into(),
            events: queue.drain(..).collect(),
        };
        serde_json::to_string(&batch).map_err(|_| failure("observer result encoding failed"))
    }
    pub fn invalidate_native(&self) {
        self.shared.lost.store(true, Ordering::SeqCst);
    }
}
impl Drop for AddressObserver {
    fn drop(&mut self) {
        self.close_native();
    }
}

#[cfg(feature = "python-bindings")]
#[pymethods]
impl AddressObserver {
    fn events(&self, py: Python<'_>) -> PyResult<String> {
        py.allow_threads(|| self.events_native())
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }
    fn close(&self, py: Python<'_>) {
        py.allow_threads(|| self.close_native());
    }
    fn invalidate(&self) {
        self.invalidate_native();
    }
}

#[cfg(unix)]
fn open_socket() -> io::Result<std::os::fd::OwnedFd> {
    use std::os::fd::FromRawFd;
    #[cfg(target_os = "linux")]
    let fd = unsafe {
        libc::socket(
            libc::AF_NETLINK,
            libc::SOCK_RAW | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            libc::NETLINK_ROUTE,
        )
    };
    #[cfg(target_os = "macos")]
    let fd = unsafe { libc::socket(libc::PF_ROUTE, libc::SOCK_RAW, libc::AF_UNSPEC) };
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    return Err(failure("native address observation unsupported on this OS"));
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let owned = unsafe { std::os::fd::OwnedFd::from_raw_fd(fd) };
        #[cfg(target_os = "linux")]
        {
            let mut address: libc::sockaddr_nl = unsafe { std::mem::zeroed() };
            address.nl_family = libc::AF_NETLINK as u16;
            // Link, IPv4 address and all IPv4 route notifications.
            address.nl_groups = 1 | 0x10 | 0x40;
            if unsafe {
                libc::bind(
                    fd,
                    &address as *const _ as *const libc::sockaddr,
                    std::mem::size_of_val(&address) as _,
                )
            } < 0
            {
                return Err(io::Error::last_os_error());
            }
        }
        #[cfg(target_os = "macos")]
        if unsafe { libc::fcntl(fd, libc::F_SETFL, libc::O_NONBLOCK) } < 0
            || unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(owned)
    }
}

#[cfg(unix)]
fn read_events(fd: i32, shared: &Shared) -> io::Result<()> {
    let mut bytes = [0u8; 65536];
    for _ in 0..1024 {
        let mut iov = libc::iovec {
            iov_base: bytes.as_mut_ptr().cast(),
            iov_len: bytes.len(),
        };
        let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
        message.msg_iov = &mut iov;
        message.msg_iovlen = 1;
        let size = unsafe { libc::recvmsg(fd, &mut message, libc::MSG_DONTWAIT) };
        if size < 0 {
            let err = io::Error::last_os_error();
            if err.kind() == io::ErrorKind::WouldBlock {
                return Ok(());
            }
            if err.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(err);
        }
        if size == 0 || message.msg_flags & (libc::MSG_TRUNC | libc::MSG_CTRUNC) != 0 {
            return Err(failure("address observer lost messages"));
        }
        parse(&bytes[..size as usize], shared)?;
    }
    Err(failure("address observation exceeded its drain bound"))
}

#[cfg(target_os = "linux")]
fn parse(bytes: &[u8], shared: &Shared) -> io::Result<()> {
    let mut offset = 0;
    while offset < bytes.len() {
        if bytes.len() - offset < 16 {
            return Err(failure("truncated netlink header"));
        }
        let data = &bytes[offset..];
        let length = u32::from_ne_bytes(data[0..4].try_into().unwrap()) as usize;
        let kind = u16::from_ne_bytes(data[4..6].try_into().unwrap());
        if length < 16 || length > data.len() {
            return Err(failure("invalid netlink length"));
        }
        let data = &data[..length];
        if kind == 2 || kind == 4 {
            return Err(failure("netlink error/overrun"));
        }
        if kind == 20 || kind == 21 {
            if length < 24 {
                return Err(failure("truncated address notification"));
            }
            if data[16] == libc::AF_INET as u8 {
                let index = u32::from_ne_bytes(data[20..24].try_into().unwrap());
                if index == shared.index {
                    let mut address = None;
                    let mut pos = 24;
                    while pos + 4 <= length {
                        let size =
                            u16::from_ne_bytes(data[pos..pos + 2].try_into().unwrap()) as usize;
                        let attr = u16::from_ne_bytes(data[pos + 2..pos + 4].try_into().unwrap());
                        if size < 4 || pos + size > length {
                            return Err(failure("invalid address attribute"));
                        }
                        if (attr == 1 || attr == 2) && size == 8 {
                            address = Some(
                                std::net::Ipv4Addr::new(
                                    data[pos + 4],
                                    data[pos + 5],
                                    data[pos + 6],
                                    data[pos + 7],
                                )
                                .to_string(),
                            );
                        }
                        pos += (size + 3) & !3;
                    }
                    if address.is_none() {
                        return Err(failure("address event lacks IPv4 identity"));
                    }
                    shared.emit(AddressEvent {
                        sequence: 0,
                        kind: if kind == 20 {
                            "address_add"
                        } else {
                            "address_delete"
                        }
                        .into(),
                        interface_index: index,
                        address,
                        prefix_length: Some(data[17]),
                        dad_state: None,
                    });
                }
            }
        } else if kind == 24 || kind == 25 || kind == 16 || kind == 17 {
            // Route/link changes demand policy reinspection; do not infer ownership
            // of a resource from these notifications alone.
            shared.emit(AddressEvent {
                sequence: 0,
                kind: if kind == 24 || kind == 25 {
                    "route_change"
                } else {
                    "link_change"
                }
                .into(),
                interface_index: 0,
                address: None,
                prefix_length: None,
                dad_state: None,
            });
        }
        offset += (length + 3) & !3;
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn parse(bytes: &[u8], shared: &Shared) -> io::Result<()> {
    let mut offset = 0;
    while offset < bytes.len() {
        let data = &bytes[offset..];
        if data.len() < 4 {
            return Err(failure("truncated routing notification"));
        }
        let length = u16::from_ne_bytes(data[0..2].try_into().unwrap()) as usize;
        if length < 4 || length > data.len() || data[2] != 5 {
            return Err(failure("invalid routing notification"));
        }
        let data = &data[..length];
        let kind = data[3];
        if kind == 12 || kind == 13 {
            if length < 20 {
                return Err(failure("truncated address notification"));
            }
            let index = u16::from_ne_bytes(data[12..14].try_into().unwrap()) as u32;
            if index == shared.index {
                let fields = u32::from_ne_bytes(data[4..8].try_into().unwrap());
                let mut pos = 20;
                let mut address = None;
                let mut mask = None;
                for field in 0..8 {
                    if fields & (1 << field) == 0 {
                        continue;
                    }
                    if pos + 2 > length {
                        return Err(failure("routing address truncated"));
                    }
                    let size = data[pos] as usize;
                    let aligned = if size == 0 { 4 } else { (size + 3) & !3 };
                    if pos + aligned > length {
                        return Err(failure("invalid routing address length"));
                    }
                    if field == 5 && data[pos + 1] == libc::AF_INET as u8 && size >= 8 {
                        address = Some(
                            std::net::Ipv4Addr::new(
                                data[pos + 4],
                                data[pos + 5],
                                data[pos + 6],
                                data[pos + 7],
                            )
                            .to_string(),
                        );
                    }
                    if field == 2 {
                        let mut raw = [0u8; 4];
                        for (i, value) in raw.iter_mut().enumerate() {
                            if 4 + i < size {
                                *value = data[pos + 4 + i];
                            }
                        }
                        let bits = u32::from_be_bytes(raw);
                        let prefix = bits.leading_ones() as u8;
                        if bits != (u32::MAX.checked_shl(32 - prefix as u32).unwrap_or(0)) {
                            return Err(failure("noncontiguous address mask"));
                        }
                        mask = Some(prefix);
                    }
                    pos += aligned;
                }
                if address.is_some() {
                    if mask.is_none() {
                        return Err(failure("IPv4 address event lacks prefix"));
                    }
                    shared.emit(AddressEvent {
                        sequence: 0,
                        kind: if kind == 12 {
                            "address_add"
                        } else {
                            "address_delete"
                        }
                        .into(),
                        interface_index: index,
                        address,
                        prefix_length: mask,
                        dad_state: None,
                    });
                }
            }
        } else if [1, 2, 3, 6].contains(&kind) {
            if length < 16 {
                return Err(failure("routing message truncated"));
            }
            let flags = u32::from_ne_bytes(data[8..12].try_into().unwrap());
            // Ignore neighbor cache events; they are not address/connected-route deltas.
            if flags & 0x400 == 0 {
                shared.emit(AddressEvent {
                    sequence: 0,
                    kind: "route_change".into(),
                    interface_index: u16::from_ne_bytes(data[4..6].try_into().unwrap()) as u32,
                    address: None,
                    prefix_length: None,
                    dad_state: None,
                });
            }
        } else if kind == 14 || kind == 18 {
            if length < 14 {
                return Err(failure("link message truncated"));
            }
            let index = u16::from_ne_bytes(data[12..14].try_into().unwrap()) as u32;
            if index == shared.index {
                shared.emit(AddressEvent {
                    sequence: 0,
                    kind: "link_change".into(),
                    interface_index: index,
                    address: None,
                    prefix_length: None,
                    dad_state: None,
                });
            }
        }
        offset += length;
    }
    Ok(())
}

#[cfg(windows)]
mod win {
    use super::*;
    use windows::Win32::{
        Foundation::{BOOLEAN, HANDLE},
        NetworkManagement::IpHelper::*,
        Networking::WinSock::AF_INET,
    };
    unsafe extern "system" fn address(
        context: *const core::ffi::c_void,
        row: *const MIB_UNICASTIPADDRESS_ROW,
        kind: MIB_NOTIFICATION_TYPE,
    ) {
        let shared = &*(context as *const Shared);
        if row.is_null() {
            shared.lost.store(true, Ordering::SeqCst);
            return;
        }
        let row = *row;
        if row.InterfaceIndex != shared.index {
            return;
        }
        let bytes = row.Address.Ipv4.sin_addr.S_un.S_addr.to_ne_bytes();
        shared.emit(AddressEvent {
            sequence: 0,
            kind: if kind == MibDeleteInstance {
                "address_delete"
            } else if kind == MibAddInstance {
                "address_add"
            } else {
                "address_change"
            }
            .into(),
            interface_index: row.InterfaceIndex,
            address: Some(std::net::Ipv4Addr::from(bytes).to_string()),
            prefix_length: Some(row.OnLinkPrefixLength),
            dad_state: Some(row.DadState.0),
        });
    }
    unsafe extern "system" fn route(
        context: *const core::ffi::c_void,
        row: *const MIB_IPFORWARD_ROW2,
        _: MIB_NOTIFICATION_TYPE,
    ) {
        let shared = &*(context as *const Shared);
        if row.is_null() {
            shared.lost.store(true, Ordering::SeqCst);
            return;
        }
        shared.emit(AddressEvent {
            sequence: 0,
            kind: "route_change".into(),
            interface_index: (*row).InterfaceIndex,
            address: None,
            prefix_length: None,
            dad_state: None,
        });
    }
    pub fn register(shared: &Arc<Shared>) -> io::Result<(isize, isize)> {
        unsafe {
            let mut addr = HANDLE::default();
            let mut routes = HANDLE::default();
            let context = Arc::as_ptr(shared).cast();
            let result = NotifyUnicastIpAddressChange(
                AF_INET,
                Some(address),
                Some(context),
                BOOLEAN(0),
                &mut addr,
            );
            if result.0 != 0 {
                return Err(io::Error::from_raw_os_error(result.0 as i32));
            }
            let result = NotifyRouteChange2(AF_INET, Some(route), context, BOOLEAN(0), &mut routes);
            if result.0 != 0 {
                let _ = CancelMibChangeNotify2(addr);
                return Err(io::Error::from_raw_os_error(result.0 as i32));
            }
            Ok((addr.0 as isize, routes.0 as isize))
        }
    }
}

pub fn start(interface_index: u32, epoch: String) -> io::Result<AddressObserver> {
    if interface_index == 0 || epoch.len() != 36 {
        return Err(failure("invalid observation identity"));
    }
    let shared = Arc::new(Shared {
        epoch,
        index: interface_index,
        queue: Mutex::new(VecDeque::new()),
        sequence: AtomicU64::new(0),
        lost: AtomicBool::new(false),
        closed: AtomicBool::new(false),
        barrier_request: AtomicU64::new(0),
        barrier_ack: AtomicU64::new(0),
    });
    #[cfg(unix)]
    {
        use std::os::fd::AsRawFd;
        let socket = open_socket()?;
        let worker = shared.clone();
        let handle = thread::spawn(move || {
            while !worker.closed.load(Ordering::SeqCst) {
                let requested = worker.barrier_request.load(Ordering::SeqCst);
                if read_events(socket.as_raw_fd(), &worker).is_err() {
                    worker.lost.store(true, Ordering::SeqCst);
                    worker.closed.store(true, Ordering::SeqCst);
                    break;
                }
                worker.barrier_ack.store(requested, Ordering::SeqCst);
                thread::sleep(Duration::from_millis(5));
            }
        });
        Ok(AddressObserver {
            shared,
            thread: Mutex::new(Some(handle)),
        })
    }
    #[cfg(windows)]
    {
        let handles = win::register(&shared)?;
        Ok(AddressObserver {
            shared,
            handles: Mutex::new(Some(handles)),
        })
    }
}

#[cfg(feature = "python-bindings")]
#[pyfunction]
pub fn start_address_observer(
    py: Python<'_>,
    interface_index: u32,
    epoch: String,
) -> PyResult<AddressObserver> {
    py.allow_threads(|| start(interface_index, epoch))
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}
