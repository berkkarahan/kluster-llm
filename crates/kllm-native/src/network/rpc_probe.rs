//! Independent valid RPC enumeration; the socket is always closed before return.
use super::relay::source_connect;
use pyo3::prelude::*;
use serde::Serialize;
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, Shutdown, SocketAddrV4, TcpStream};
use std::time::{Duration, Instant};

#[derive(Serialize)]
pub struct ProbeResult { pub major: u8, pub minor: u8, pub patch: u8, pub device_count: u32, pub source: String, pub peer: String, pub duration_ms: u128, pub connection_released: bool }
fn error(message: &str) -> io::Error { io::Error::new(io::ErrorKind::InvalidData, message) }
fn bounded_read(stream: &mut TcpStream, data: &mut [u8], deadline: Instant) -> io::Result<()> {
    let mut offset = 0;
    while offset < data.len() {
        let remaining = deadline.checked_duration_since(Instant::now()).ok_or_else(|| error("RPC probe deadline expired"))?;
        stream.set_read_timeout(Some(remaining))?;
        let count = stream.read(&mut data[offset..])?;
        if count == 0 { return Err(error("RPC response truncated")); }
        offset += count;
    }
    Ok(())
}
fn exchange(stream: &mut TcpStream, command: u8, body: &[u8], expected: usize, deadline: Instant) -> io::Result<Vec<u8>> {
    fn remaining(deadline: Instant) -> io::Result<Duration> { deadline.checked_duration_since(Instant::now()).ok_or_else(|| error("RPC probe deadline expired")) }
    stream.set_write_timeout(Some(remaining(deadline)?))?;
    let mut request = vec![command];
    request.extend_from_slice(&(body.len() as u64).to_le_bytes());
    request.extend_from_slice(body);
    stream.write_all(&request)?;
    let mut size = [0; 8];
    bounded_read(stream, &mut size, deadline)?;
    if u64::from_le_bytes(size) != expected as u64 { return Err(error("RPC response size mismatch")); }
    let mut result = vec![0; expected];
    bounded_read(stream, &mut result, deadline)?;
    Ok(result)
}

pub fn probe(endpoint: SocketAddrV4, source: Ipv4Addr, timeout_ms: u64) -> io::Result<ProbeResult> {
    if timeout_ms == 0 || timeout_ms > 5000 || endpoint.port() == 0 { return Err(error("invalid RPC probe deadline/port")); }
    let started = Instant::now();
    let deadline = started + Duration::from_millis(timeout_ms);
    let mut stream = source_connect(source, endpoint, Duration::from_millis(timeout_ms))?;
    let result = (|| {
        let hello = exchange(&mut stream, 14, &[0; 24], 28, deadline)?;
        if hello[0] != 7 || hello[1] != 0 || hello[4..].iter().any(|b| *b != 0) { return Err(error("RPC protocol/transport unsupported")); }
        let count = exchange(&mut stream, 15, &[], 4, deadline)?;
        let device_count = u32::from_le_bytes(count.try_into().map_err(|_| error("RPC count invalid"))?);
        if device_count == 0 || device_count > 256 { return Err(error("RPC device count unsupported")); }
        Ok(ProbeResult { major: hello[0], minor: hello[1], patch: hello[2], device_count, source: stream.local_addr()?.to_string(), peer: stream.peer_addr()?.to_string(), duration_ms: started.elapsed().as_millis(), connection_released: true })
    })();
    // Worker relay release/drain acknowledgement remains required independently.
    let _ = stream.shutdown(Shutdown::Both);
    drop(stream);
    result
}

#[pyfunction]
pub fn probe_network_rpc(py: Python<'_>, endpoint: String, source: String, installed_tag: String, timeout_ms: u64) -> PyResult<String> {
    if installed_tag != "v0.5.0" || cfg!(target_endian = "big") { return Err(pyo3::exceptions::PyValueError::new_err("unvalidated RPC pin/architecture")); }
    let target: SocketAddrV4 = endpoint.parse().map_err(|_| pyo3::exceptions::PyValueError::new_err("literal IPv4 endpoint required"))?;
    let source: Ipv4Addr = source.parse().map_err(|_| pyo3::exceptions::PyValueError::new_err("literal IPv4 source required"))?;
    if !(super::relay::private(*target.ip()) || target.ip().is_loopback()) || !(super::relay::private(source) || source.is_loopback()) { return Err(pyo3::exceptions::PyValueError::new_err("private RPC source/endpoint required")); }
    let result = py.allow_threads(|| probe(target, source, timeout_ms)).map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    serde_json::to_string(&result).map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("probe result encoding failed"))
}
