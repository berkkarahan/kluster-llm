//! One admitted stream, exact binds, bounded buffers and independent socket ownership.
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use socket2::{Domain, Protocol, Socket, Type};
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, Shutdown, SocketAddr, SocketAddrV4, TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BridgeConfig {
    pub role: String,
    pub instance_id: String,
    pub network_id: String,
    pub assignment_revision: u64,
    pub bind_host: Ipv4Addr,
    pub bind_port: u16,
    pub target_host: Ipv4Addr,
    pub target_port: u16,
    pub source_host: Ipv4Addr,
    pub allowed_peer: Ipv4Addr,
    pub child_pid: Option<u32>,
    pub child_start_token: Option<String>,
}

pub fn private(ip: Ipv4Addr) -> bool {
    ip.is_private() && !ip.is_unspecified() && !ip.is_multicast() && !ip.is_broadcast()
}

fn invalid(message: &str) -> io::Error { io::Error::new(io::ErrorKind::InvalidInput, message) }

fn uuid(value: &str) -> bool {
    value.len() == 36 && value.bytes().enumerate().all(|(i, c)| {
        if [8, 13, 18, 23].contains(&i) { c == b'-' } else { c.is_ascii_hexdigit() && !c.is_ascii_uppercase() }
    })
}

impl BridgeConfig {
    fn validate(&self) -> io::Result<()> {
        if !uuid(&self.instance_id) || !uuid(&self.network_id) || self.assignment_revision == 0 || self.assignment_revision > (1u64 << 53)-1 || self.target_port == 0 {
            return Err(invalid("invalid bridge identity/revision/port"));
        }
        match self.role.as_str() {
            "master" if self.bind_host == Ipv4Addr::LOCALHOST && self.allowed_peer == Ipv4Addr::LOCALHOST && private(self.source_host) && private(self.target_host) && self.child_pid.is_none() && self.child_start_token.is_none() => (),
            "worker" if private(self.bind_host) && private(self.allowed_peer) && self.bind_port != 0 && (self.target_host.is_loopback() || private(self.target_host)) && self.source_host == self.target_host && self.child_pid.is_some() && self.child_start_token.is_some() => {
                // Bind without connecting: prove the worker target is an address on this host.
                let local = Socket::new(Domain::IPV4, Type::STREAM, Some(Protocol::TCP))?;
                local.bind(&SocketAddrV4::new(self.target_host, 0).into())?;
                self.check_child()?;
            },
            _ => return Err(invalid("bridge requires exact private/loopback source, target and child ownership")),
        }
        Ok(())
    }
    fn check_child(&self) -> io::Result<()> {
        if let Some(pid) = self.child_pid {
            let identity = crate::usage::process_counters(pid).map_err(|_| invalid("RPC child identity unavailable"))?;
            if Some(&identity.token) != self.child_start_token.as_ref() {
                return Err(invalid("RPC child identity changed"));
            }
        }
        Ok(())
    }
}

pub fn source_connect(source: Ipv4Addr, target: SocketAddrV4, timeout: Duration) -> io::Result<TcpStream> {
    let socket = Socket::new(Domain::IPV4, Type::STREAM, Some(Protocol::TCP))?;
    socket.bind(&SocketAddrV4::new(source, 0).into())?;
    socket.connect_timeout(&target.into(), timeout)?;
    socket.set_nonblocking(false)?;
    socket.set_nodelay(true)?;
    Ok(socket.into())
}

#[derive(Serialize)]
struct Status {
    instance_id: String,
    listener: String,
    source_host: String,
    target: String,
    generation: u64,
    admission_live: bool,
    admission_consumed: bool,
    active_streams: u32,
    pending_connects: u32,
    bytes_to_target: u64,
    bytes_to_client: u64,
    last_error: Option<String>,
    closed: bool,
}
struct State {
    closed: bool,
    generation: u64,
    deadline: Option<Instant>,
    active: bool,
    connecting: bool,
    consumed: bool,
    error: Option<String>,
    to_target: u64,
    to_client: u64,
    sockets: Vec<TcpStream>,
}

struct Inner {
    config: BridgeConfig,
    listener: SocketAddr,
    state: Mutex<State>,
    listener_thread: Mutex<Option<JoinHandle<()>>>,
}
impl Inner {
    fn stop(&self, terminate: bool) -> io::Result<bool> {
        {
            let mut state = self.state.lock().map_err(|_| invalid("bridge lock failed"))?;
            state.closed = true;
            state.deadline = None;
            if terminate {
                for socket in &state.sockets { let _ = socket.shutdown(Shutdown::Both); }
            }
        }
        if let Some(handle) = self.listener_thread.lock().map_err(|_| invalid("bridge lock failed"))?.take() {
            let _ = handle.join();
        }
        let state = self.state.lock().map_err(|_| invalid("bridge lock failed"))?;
        Ok(!state.active && !state.connecting)
    }
}

#[pyclass]
pub struct NetworkBridge { inner: Arc<Inner> }
impl Drop for NetworkBridge {
    fn drop(&mut self) { let _ = self.inner.stop(true); }
}

fn copy_direction(mut from: TcpStream, mut to: TcpStream, inner: Arc<Inner>, outbound: bool) -> io::Result<()> {
    let mut buffer = [0u8; 64 * 1024];
    let outcome = (|| {
        loop {
            let count = match from.read(&mut buffer) {
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                result => result?,
            };
            if count == 0 { break; }
            to.write_all(&buffer[..count])?;
            let mut state = inner.state.lock().map_err(|_| invalid("bridge lock failed"))?;
            if outbound { state.to_target += count as u64; } else { state.to_client += count as u64; }
        }
        Ok(())
    })();
    let _ = to.shutdown(Shutdown::Write);
    if outcome.is_err() {
        // A failed direction cannot leave its sibling blocked forever.
        let _ = from.shutdown(Shutdown::Both);
        let _ = to.shutdown(Shutdown::Both);
    }
    outcome
}

fn serve(inner: Arc<Inner>, client: TcpStream) {
    let result: io::Result<()> = (|| {
        inner.config.check_child()?;
        let target = source_connect(inner.config.source_host, SocketAddrV4::new(inner.config.target_host, inner.config.target_port), Duration::from_secs(2))?;
        {
            let mut state = inner.state.lock().map_err(|_| invalid("bridge lock failed"))?;
            state.connecting = false;
            if state.closed { return Err(invalid("bridge closed during connect")); }
            state.active = true;
            state.sockets = vec![client.try_clone()?, target.try_clone()?];
        }
        let client_read = client.try_clone()?;
        let target_write = target.try_clone()?;
        let other_inner = inner.clone();
        let outbound = thread::spawn(move || copy_direction(client_read, target_write, other_inner, true));
        let inbound = copy_direction(target, client, inner.clone(), false);
        let outbound_result = outbound.join().map_err(|_| invalid("bridge forwarding failed"))?;
        inbound?;
        outbound_result
    })();
    let mut state = inner.state.lock().unwrap_or_else(|e| e.into_inner());
    state.active = false;
    state.connecting = false;
    state.sockets.clear();
    if let Err(e) = result { state.error = Some(e.kind().to_string()); }
}

impl NetworkBridge {
    pub fn start(config: BridgeConfig) -> io::Result<Self> {
        config.validate()?;
        let socket = Socket::new(Domain::IPV4, Type::STREAM, Some(Protocol::TCP))?;
        // No SO_REUSEPORT: another process must not share this owned listener.
        socket.bind(&SocketAddrV4::new(config.bind_host, config.bind_port).into())?;
        socket.listen(8)?;
        socket.set_nonblocking(true)?;
        let listener: TcpListener = socket.into();
        let inner = Arc::new(Inner { listener: listener.local_addr()?, config,
            state: Mutex::new(State { closed: false, generation: 0, deadline: None, active: false, connecting: false, consumed: false, error: None, to_target: 0, to_client: 0, sockets: Vec::new() }), listener_thread: Mutex::new(None) });
        let worker = inner.clone();
        let handle = thread::spawn(move || {
            loop {
                if worker.state.lock().unwrap_or_else(|e| e.into_inner()).closed { break; }
                match listener.accept() {
                    Ok((client, peer)) => {
                        // BSD accept inherits the nonblocking listener flag.
                        // Forwarding owns blocking directional threads; a quiet
                        // stream must wait rather than fail with WouldBlock.
                        if let Err(error) = client.set_nonblocking(false) {
                            let mut state = worker.state.lock().unwrap_or_else(|e| e.into_inner());
                            state.error = Some(error.kind().to_string());
                            drop(client);
                            continue;
                        }
                        let mut state = worker.state.lock().unwrap_or_else(|e| e.into_inner());
                        if peer.ip() != std::net::IpAddr::V4(worker.config.allowed_peer) || state.closed || state.active || state.connecting || state.consumed || !state.deadline.is_some_and(|d| d > Instant::now()) {
                            drop(client);
                            continue;
                        }
                        state.connecting = true;
                        state.consumed = true;
                        // Keep cancellation ownership even while outbound connect is pending.
                        match client.try_clone() {
                            Ok(socket) => state.sockets = vec![socket],
                            Err(_) => { state.connecting = false; drop(client); continue; }
                        }
                        drop(state);
                        let stream_inner = worker.clone();
                        thread::spawn(move || serve(stream_inner, client));
                    },
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => thread::sleep(Duration::from_millis(10)),
                    Err(e) if e.kind() == io::ErrorKind::Interrupted => (),
                    Err(e) => {
                        let mut state = worker.state.lock().unwrap_or_else(|e| e.into_inner());
                        state.error = Some(e.kind().to_string()); state.closed = true; break;
                    },
                }
            }
        });
        *inner.listener_thread.lock().unwrap_or_else(|e| e.into_inner()) = Some(handle);
        Ok(Self { inner })
    }

    pub fn grant_native(&self, generation: u64, ttl_ms: u64, renewal: bool) -> io::Result<()> {
        let mut state = self.inner.state.lock().map_err(|_| invalid("bridge lock failed"))?;
        if state.closed || (!renewal && generation <= state.generation) || (renewal && generation != state.generation) || generation > (1u64 << 53)-1 || ttl_ms == 0 || ttl_ms > 15000 {
            return Err(invalid("stale/invalid admission generation"));
        }
        if !renewal && (state.active || state.connecting || state.deadline.is_some()) { return Err(invalid("RPC_BUSY")); }
        if renewal && state.deadline.is_none() { return Err(invalid("cannot revive released admission")); }
        state.generation = generation;
        state.deadline = Some(Instant::now() + Duration::from_millis(ttl_ms));
        if !renewal { state.consumed = false; }
        Ok(())
    }
    pub fn release_native(&self, generation: u64) -> io::Result<bool> {
        let mut state = self.inner.state.lock().map_err(|_| invalid("bridge lock failed"))?;
        if generation != state.generation { return Err(invalid("stale admission generation")); }
        state.deadline = None;
        Ok(!state.active && !state.connecting)
    }
    pub fn status_native(&self) -> io::Result<String> {
        let state = self.inner.state.lock().map_err(|_| invalid("bridge lock failed"))?;
        serde_json::to_string(&Status { instance_id: self.inner.config.instance_id.clone(), listener: self.inner.listener.to_string(), source_host: self.inner.config.source_host.to_string(), target: SocketAddrV4::new(self.inner.config.target_host, self.inner.config.target_port).to_string(), generation: state.generation, admission_live: !state.closed && state.deadline.is_some_and(|d| d > Instant::now()), admission_consumed: state.consumed, active_streams: state.active as u32, pending_connects: state.connecting as u32, bytes_to_target: state.to_target, bytes_to_client: state.to_client, last_error: state.error.clone(), closed: state.closed }).map_err(|_| invalid("bridge status encoding failed"))
    }
}

#[pymethods]
impl NetworkBridge {
    fn status(&self) -> PyResult<String> { self.status_native().map_err(pyo3::exceptions::PyRuntimeError::new_err) }
    fn grant(&self, generation: u64, ttl_ms: u64, renewal: bool) -> PyResult<()> { self.grant_native(generation, ttl_ms, renewal).map_err(pyo3::exceptions::PyRuntimeError::new_err) }
    fn release(&self, generation: u64) -> PyResult<bool> { self.release_native(generation).map_err(pyo3::exceptions::PyRuntimeError::new_err) }
    fn stop(&self, py: Python<'_>, instance_id: String, terminate: bool) -> PyResult<bool> {
        if instance_id != self.inner.config.instance_id { return Err(pyo3::exceptions::PyValueError::new_err("stale bridge instance")); }
        py.allow_threads(|| self.inner.stop(terminate)).map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }
}

#[pyfunction]
pub fn start_network_bridge(py: Python<'_>, config: String) -> PyResult<NetworkBridge> {
    if config.len() > 8192 { return Err(pyo3::exceptions::PyValueError::new_err("bridge configuration too large")); }
    let parsed: BridgeConfig = serde_json::from_str(&config).map_err(|_| pyo3::exceptions::PyValueError::new_err("invalid bridge configuration"))?;
    py.allow_threads(|| NetworkBridge::start(parsed)).map_err(pyo3::exceptions::PyRuntimeError::new_err)
}
