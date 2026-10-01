use super::{address_events, relay::{BridgeConfig, NetworkBridge}, rpc_probe};
use std::{io::{Read, Write}, net::{Ipv4Addr, Shutdown, SocketAddrV4, TcpListener, TcpStream, UdpSocket}, thread, time::{Duration, Instant}};

fn local_private_address() -> Ipv4Addr {
    let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0)).unwrap();
    socket.connect((Ipv4Addr::new(192,0,2,1),9)).unwrap();
    match socket.local_addr().unwrap().ip() {
        std::net::IpAddr::V4(address) if address.is_private() => address,
        _ => panic!("native source-bound bridge check needs a local RFC1918 interface"),
    }
}

#[test]
fn source_bound_forwarding_half_close_and_refusal() {
    let source = local_private_address();
    let upstream = TcpListener::bind((source,0)).unwrap();
    let target_port = upstream.local_addr().unwrap().port();
    let upstream_thread = thread::spawn(move || {
        let (mut stream, peer) = upstream.accept().unwrap();
        assert_eq!(peer.ip(), std::net::IpAddr::V4(source));
        stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let mut body = Vec::new(); stream.read_to_end(&mut body).unwrap();
        assert_eq!(body, vec![17; 200_000]);
        stream.write_all(b"complete").unwrap();
        stream.shutdown(Shutdown::Write).unwrap();
    });
    let config = BridgeConfig { role: "master".into(), instance_id: "00000000-0000-4000-8000-000000000001".into(), network_id: "00000000-0000-4000-8000-000000000002".into(), assignment_revision: 1, bind_host: Ipv4Addr::LOCALHOST, bind_port: 0, target_host: source, target_port, source_host: source, allowed_peer: Ipv4Addr::LOCALHOST, child_pid: None, child_start_token: None };
    let mut invalid = config.clone(); invalid.bind_host = Ipv4Addr::UNSPECIFIED;
    assert!(NetworkBridge::start(invalid).is_err());
    let mut invalid = config.clone(); invalid.allowed_peer = source;
    assert!(NetworkBridge::start(invalid).is_err());
    let bridge = NetworkBridge::start(config).unwrap();
    let status: serde_json::Value = serde_json::from_str(&bridge.status_native().unwrap()).unwrap();
    let listener = status["listener"].as_str().unwrap();
    // An unadmitted connection closes without ever opening the upstream.
    let mut rejected = TcpStream::connect(listener).unwrap();
    rejected.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
    assert_eq!(rejected.read(&mut [0;1]).unwrap(),0);
    bridge.grant_native(1,15000,false).unwrap();
    let mut client = TcpStream::connect(listener).unwrap();
    client.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
    client.write_all(&vec![17;200_000]).unwrap();
    client.shutdown(Shutdown::Write).unwrap();
    let mut reply = Vec::new(); client.read_to_end(&mut reply).unwrap(); assert_eq!(reply,b"complete");
    upstream_thread.join().unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    while !bridge.release_native(1).unwrap() { assert!(Instant::now() < deadline); thread::sleep(Duration::from_millis(5)); }
    assert!(bridge.grant_native(1,15000,false).is_err());
    #[cfg(unix)]
    {
        #[cfg(target_os="macos")] let name = std::ffi::CString::new("lo0").unwrap();
        #[cfg(target_os="linux")] let name = std::ffi::CString::new("lo").unwrap();
        let index = unsafe { libc::if_nametoindex(name.as_ptr()) };
        let observer = address_events::start(index, "00000000-0000-4000-8000-000000000003".into()).unwrap();
        observer.invalidate_native();
        let events: serde_json::Value = serde_json::from_str(&observer.events_native().unwrap()).unwrap();
        assert_eq!(events["health"],"gap");
    }
}

#[test]
fn valid_rpc_probe_releases_serial_server_for_next_connection() {
    let server = TcpListener::bind((Ipv4Addr::LOCALHOST,0)).unwrap();
    let endpoint = SocketAddrV4::new(Ipv4Addr::LOCALHOST,server.local_addr().unwrap().port());
    let server_thread = thread::spawn(move || {
        for _ in 0..2 {
            let (mut stream,_) = server.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
            let mut hello = [0;33]; stream.read_exact(&mut hello).unwrap();
            assert_eq!(hello[0],14); assert_eq!(u64::from_le_bytes(hello[1..9].try_into().unwrap()),24); assert_eq!(&hello[9..],&[0;24]);
            stream.write_all(&28u64.to_le_bytes()).unwrap();
            let mut response = [0;28]; response[0] = 7; stream.write_all(&response).unwrap();
            let mut count = [0;9]; stream.read_exact(&mut count).unwrap(); assert_eq!(count[0],15); assert_eq!(&count[1..],&[0;8]);
            stream.write_all(&4u64.to_le_bytes()).unwrap(); stream.write_all(&1u32.to_le_bytes()).unwrap();
            // This serial server cannot accept again until the probe releases it.
            assert_eq!(stream.read(&mut [0;1]).unwrap(),0);
        }
    });
    for _ in 0..2 { let result = rpc_probe::probe(endpoint,Ipv4Addr::LOCALHOST,2000).unwrap(); assert_eq!(result.device_count,1); assert!(result.connection_released); }
    server_thread.join().unwrap();
}
