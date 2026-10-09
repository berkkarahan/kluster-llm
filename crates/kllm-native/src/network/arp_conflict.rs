//! Interface-scoped RFC5227 probes. Incomplete capture never means no conflict.
#[cfg(feature = "python-bindings")]
use pyo3::prelude::*;
#[cfg(unix)]
use std::{
    ffi::CString,
    fs::File,
    io::{self, Read},
    net::Ipv4Addr,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    time::{Duration, Instant},
};

#[cfg(unix)]
fn failure(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::Other, message)
}

#[cfg(unix)]
fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

#[cfg(all(unix, feature = "python-bindings"))]
fn python_io(error: io::Error) -> PyErr {
    if error.kind() == io::ErrorKind::InvalidInput {
        pyo3::exceptions::PyValueError::new_err(error.to_string())
    } else {
        pyo3::exceptions::PyRuntimeError::new_err(error.to_string())
    }
}

#[cfg(unix)]
fn parse_probe(mac: &str, address: &str) -> io::Result<([u8; 6], Ipv4Addr)> {
    let values = mac
        .split(':')
        .map(|b| u8::from_str_radix(b, 16))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| invalid("invalid Ethernet MAC"))?;
    let mac: [u8; 6] = values
        .try_into()
        .map_err(|_| invalid("invalid Ethernet MAC"))?;
    if mac == [0; 6] || mac[0] & 1 != 0 {
        return Err(invalid("unicast Ethernet MAC required"));
    }
    let address: Ipv4Addr = address
        .parse()
        .map_err(|_| invalid("literal IPv4 required"))?;
    if !super::relay::private(address) {
        return Err(invalid("private IPv4 required"));
    }
    Ok((mac, address))
}

#[cfg(unix)]
fn frame(mac: [u8; 6], address: Ipv4Addr) -> [u8; 60] {
    let mut frame = [0u8; 60];
    frame[..6].fill(255);
    frame[6..12].copy_from_slice(&mac);
    frame[12..14].copy_from_slice(&[8, 6]);
    frame[14..22].copy_from_slice(&[0, 1, 8, 0, 6, 4, 0, 1]);
    frame[22..28].copy_from_slice(&mac);
    // Sender IPv4 and target MAC are zero; target IPv4 is the proposed address.
    frame[38..42].copy_from_slice(&address.octets());
    frame
}
#[cfg(unix)]
fn conflict(packet: &[u8], mac: [u8; 6], address: Ipv4Addr) -> bool {
    if packet.len() < 42
        || packet[12..14] != [8, 6]
        || packet[14..20] != [0, 1, 8, 0, 6, 4]
        || packet[22..28] == mac
    {
        return false;
    }
    packet[28..32] == address.octets()
        || (packet[28..32] == [0, 0, 0, 0] && packet[38..42] == address.octets())
}

#[cfg(target_os = "linux")]
fn open(interface: &str) -> io::Result<OwnedFd> {
    let name = CString::new(interface).map_err(|_| failure("invalid interface"))?;
    let index = unsafe { libc::if_nametoindex(name.as_ptr()) };
    if index == 0 {
        return Err(failure("interface disappeared"));
    }
    let fd = unsafe {
        libc::socket(
            libc::AF_PACKET,
            libc::SOCK_RAW | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
            (0x0806u16).to_be() as i32,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let owned = unsafe { OwnedFd::from_raw_fd(fd) };
    let mut addr: libc::sockaddr_ll = unsafe { std::mem::zeroed() };
    addr.sll_family = libc::AF_PACKET as u16;
    addr.sll_protocol = (0x0806u16).to_be();
    addr.sll_ifindex = index as i32;
    if unsafe {
        libc::bind(
            fd,
            &addr as *const _ as *const libc::sockaddr,
            std::mem::size_of_val(&addr) as _,
        )
    } < 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(owned)
}
#[cfg(target_os = "macos")]
fn open(interface: &str) -> io::Result<OwnedFd> {
    let name = CString::new(interface).map_err(|_| failure("invalid interface"))?;
    if interface.len() >= libc::IFNAMSIZ || unsafe { libc::if_nametoindex(name.as_ptr()) } == 0 {
        return Err(failure("invalid/missing interface"));
    }
    let mut opened = None;
    for number in 0..256 {
        let path = CString::new(format!("/dev/bpf{number}")).unwrap();
        let fd = unsafe {
            libc::open(
                path.as_ptr(),
                libc::O_RDWR | libc::O_NONBLOCK | libc::O_CLOEXEC,
            )
        };
        if fd >= 0 {
            opened = Some(unsafe { OwnedFd::from_raw_fd(fd) });
            break;
        }
        let err = io::Error::last_os_error();
        if err.raw_os_error() != Some(libc::EBUSY) {
            return Err(err);
        }
    }
    let owned = opened.ok_or_else(|| failure("no BPF capture handle available"))?;
    let fd = owned.as_raw_fd();
    let mut length = 65536u32;
    let mut immediate = 1u32;
    let mut req: libc::ifreq = unsafe { std::mem::zeroed() };
    for (target, source) in req.ifr_name.iter_mut().zip(interface.bytes()) {
        *target = source as _;
    }
    // Values derived from SDK net/bpf.h _IO{R,W,WR} and the actual ifreq size.
    let setif = 0x80000000u64 | ((std::mem::size_of::<libc::ifreq>() as u64) << 16) | 0x426c;
    if unsafe { libc::ioctl(fd, 0xc0044266, &mut length) } < 0
        || unsafe { libc::ioctl(fd, setif, &mut req) } < 0
        || unsafe { libc::ioctl(fd, 0x80044270, &mut immediate) } < 0
    {
        return Err(io::Error::last_os_error());
    }
    let mut dlt = 0u32;
    if unsafe { libc::ioctl(fd, 0x4004426a, &mut dlt) } < 0 || dlt != 1 {
        return Err(failure("BPF interface does not expose Ethernet frames"));
    }
    if length > 65536 {
        return Err(failure("BPF buffer exceeds capture bound"));
    }
    Ok(owned)
}

#[cfg(target_os = "linux")]
fn check_loss(fd: i32) -> io::Result<()> {
    let mut statistics = [0u32; 3];
    let mut length = std::mem::size_of_val(&statistics) as libc::socklen_t;
    if unsafe {
        libc::getsockopt(
            fd,
            libc::SOL_PACKET,
            libc::PACKET_STATISTICS,
            statistics.as_mut_ptr().cast(),
            &mut length,
        )
    } < 0
    {
        return Err(io::Error::last_os_error());
    }
    if statistics[1] != 0 {
        return Err(failure("ARP capture dropped packets"));
    }
    Ok(())
}
#[cfg(target_os = "macos")]
fn check_loss(fd: i32) -> io::Result<()> {
    let mut statistics = [0u32; 2];
    if unsafe { libc::ioctl(fd, 0x4008426f, &mut statistics) } < 0 {
        return Err(io::Error::last_os_error());
    }
    if statistics[1] != 0 {
        return Err(failure("ARP capture dropped packets"));
    }
    Ok(())
}

#[cfg(unix)]
fn capture(
    fd: i32,
    bytes: &mut [u8; 65536],
    mac: [u8; 6],
    address: Ipv4Addr,
) -> io::Result<Option<bool>> {
    #[cfg(target_os = "linux")]
    let size = unsafe {
        libc::recv(
            fd,
            bytes.as_mut_ptr().cast(),
            bytes.len(),
            libc::MSG_TRUNC | libc::MSG_DONTWAIT,
        )
    };
    #[cfg(target_os = "macos")]
    let size = unsafe { libc::read(fd, bytes.as_mut_ptr().cast(), bytes.len()) };
    if size < 0 {
        let err = io::Error::last_os_error();
        if err.kind() == io::ErrorKind::WouldBlock || err.kind() == io::ErrorKind::Interrupted {
            return Ok(None);
        }
        return Err(err);
    }
    if size == 0 || size as usize > bytes.len() {
        return Err(failure("ARP capture truncated"));
    }
    #[cfg(target_os = "linux")]
    return Ok(Some(conflict(&bytes[..size as usize], mac, address)));
    #[cfg(target_os = "macos")]
    {
        let mut offset = 0;
        while offset < size as usize {
            let data = &bytes[offset..size as usize];
            if data.len() < 18 {
                return Err(failure("BPF capture header truncated"));
            }
            let caplen = u32::from_ne_bytes(data[8..12].try_into().unwrap()) as usize;
            let datalen = u32::from_ne_bytes(data[12..16].try_into().unwrap()) as usize;
            let hdrlen = u16::from_ne_bytes(data[16..18].try_into().unwrap()) as usize;
            if caplen != datalen || hdrlen < 18 || hdrlen + caplen > data.len() {
                return Err(failure("BPF capture packet truncated"));
            }
            if conflict(&data[hdrlen..hdrlen + caplen], mac, address) {
                return Ok(Some(true));
            }
            offset += (hdrlen + caplen + 3) & !3;
        }
        Ok(Some(false))
    }
}

#[cfg(unix)]
fn probe(interface: &str, mac: [u8; 6], address: Ipv4Addr) -> io::Result<bool> {
    let socket = open(interface)?;
    let fd = socket.as_raw_fd();
    let mut random = [0u8; 6];
    File::open("/dev/urandom")?.read_exact(&mut random)?;
    let random_delay = |n: usize| {
        Duration::from_millis(u16::from_ne_bytes([random[n], random[n + 1]]) as u64 % 1001)
    };
    let initial = random_delay(0);
    let gap_one = Duration::from_secs(1) + random_delay(2);
    let gap_two = Duration::from_secs(1) + random_delay(4);
    let started = Instant::now();
    let times = [
        started + initial,
        started + initial + gap_one,
        started + initial + gap_one + gap_two,
    ];
    let end = times[2] + Duration::from_secs(2);
    let deadline = started + Duration::from_secs(10);
    let mut sent = 0;
    let packet = frame(mac, address);
    let mut bytes = [0u8; 65536];
    check_loss(fd)?;
    loop {
        if Instant::now() >= deadline {
            return Err(failure("ARP observation deadline exceeded"));
        }
        if sent < 3 && Instant::now() >= times[sent] {
            if unsafe { libc::write(fd, packet.as_ptr().cast(), packet.len()) }
                != packet.len() as isize
            {
                return Err(failure("ARP probe send incomplete"));
            }
            sent += 1;
        }
        for _ in 0..128 {
            match capture(fd, &mut bytes, mac, address)? {
                Some(true) => return Ok(true),
                Some(false) => (),
                None => break,
            }
            if Instant::now() >= deadline {
                return Err(failure("ARP capture deadline exceeded"));
            }
        }
        check_loss(fd)?;
        if sent == 3 && Instant::now() >= end {
            return Ok(false);
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(feature = "python-bindings")]
#[pyfunction]
pub fn probe_ipv4_conflict(
    py: Python<'_>,
    interface: String,
    mac: String,
    address: String,
) -> PyResult<bool> {
    #[cfg(windows)]
    {
        let _ = (py, interface, mac, address);
        Err(pyo3::exceptions::PyRuntimeError::new_err(
            "Windows requires native post-add DAD observation",
        ))
    }
    #[cfg(unix)]
    {
        py.allow_threads(|| probe_native(&interface, &mac, &address))
            .map_err(python_io)
    }
}

#[cfg(unix)]
struct ConflictState {
    conflict: std::sync::atomic::AtomicBool,
    lost: std::sync::atomic::AtomicBool,
    closed: std::sync::atomic::AtomicBool,
}

#[cfg(unix)]
#[cfg_attr(feature = "python-bindings", pyclass)]
pub struct IPv4ConflictObserver {
    state: std::sync::Arc<ConflictState>,
    thread: std::sync::Mutex<Option<std::thread::JoinHandle<()>>>,
}
#[cfg(unix)]
impl IPv4ConflictObserver {
    pub fn close_native(&self) {
        self.state
            .closed
            .store(true, std::sync::atomic::Ordering::SeqCst);
        if let Some(thread) = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = thread.join();
        }
    }
    pub fn status_native(&self) -> (bool, bool, bool) {
        use std::sync::atomic::Ordering::SeqCst;
        (
            self.state.conflict.load(SeqCst),
            self.state.lost.load(SeqCst),
            self.state.closed.load(SeqCst),
        )
    }
}
#[cfg(unix)]
impl Drop for IPv4ConflictObserver {
    fn drop(&mut self) {
        self.close_native();
    }
}
#[cfg(unix)]
#[cfg(feature = "python-bindings")]
#[pymethods]
impl IPv4ConflictObserver {
    fn status(&self) -> (bool, bool, bool) {
        self.status_native()
    }
    fn close(&self, py: Python<'_>) {
        py.allow_threads(|| self.close_native());
    }
}
#[cfg(unix)]
#[cfg(feature = "python-bindings")]
#[pyfunction]
pub fn start_ipv4_conflict_observer(
    py: Python<'_>,
    interface: String,
    mac: String,
    address: String,
) -> PyResult<IPv4ConflictObserver> {
    py.allow_threads(|| start_native(interface, mac, address)).map_err(python_io)
}

#[cfg(unix)]
pub fn start_native(
    interface: String,
    mac: String,
    address: String,
) -> io::Result<IPv4ConflictObserver> {
    let (mac, address) = parse_probe(&mac, &address)?;
    let socket = open(&interface)?;
    let state = std::sync::Arc::new(ConflictState {
        conflict: std::sync::atomic::AtomicBool::new(false),
        lost: std::sync::atomic::AtomicBool::new(false),
        closed: std::sync::atomic::AtomicBool::new(false),
    });
    let worker = state.clone();
    let thread = std::thread::spawn(move || {
        use std::sync::atomic::Ordering::SeqCst;
        let mut bytes = [0u8; 65536];
        while !worker.closed.load(SeqCst) {
            for _ in 0..128 {
                match capture(socket.as_raw_fd(), &mut bytes, mac, address) {
                    Ok(Some(true)) => {
                        worker.conflict.store(true, SeqCst);
                    }
                    Ok(Some(false)) => (),
                    Ok(None) => break,
                    Err(_) => {
                        worker.lost.store(true, SeqCst);
                        worker.closed.store(true, SeqCst);
                        return;
                    }
                }
            }
            if check_loss(socket.as_raw_fd()).is_err() {
                worker.lost.store(true, SeqCst);
                worker.closed.store(true, SeqCst);
                return;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    });
    Ok(IPv4ConflictObserver {
        state,
        thread: std::sync::Mutex::new(Some(thread)),
    })
}

#[cfg(unix)]
pub fn probe_native(interface: &str, mac: &str, address: &str) -> io::Result<bool> {
    let (mac, address) = parse_probe(mac, address)?;
    probe(interface, mac, address)
}

#[cfg(all(test, unix))]
mod tests {
    use super::probe_native;

    #[test]
    fn probe_native_rejects_invalid_inputs_before_opening_a_socket() {
        let cases = [
            ("zz", "10.0.0.1", "invalid Ethernet MAC"),
            (
                "01:00:00:00:00:00",
                "10.0.0.1",
                "unicast Ethernet MAC required",
            ),
            ("02:00:00:00:00:01", "nope", "literal IPv4 required"),
            ("02:00:00:00:00:01", "8.8.8.8", "private IPv4 required"),
        ];
        for (mac, address, message) in cases {
            let error = probe_native("lo0", mac, address).unwrap_err();
            assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
            assert_eq!(error.to_string(), message);
        }
    }
}
