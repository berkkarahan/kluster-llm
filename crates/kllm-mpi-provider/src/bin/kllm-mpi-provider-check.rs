//! Print an auditable, provider-only capability stamp.
//!
//! This binary deliberately does not accept worker, host, environment, port,
//! executable, or launcher arguments. Structured KLLM job launching is added by
//! the control-plane work in R1; this R0 target only reports its own identity.

use sha2::{Digest, Sha256};
use std::env;
use std::fs::File;
use std::io::{self, ErrorKind, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::process::{Child, Command, ExitCode, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

fn binary_sha256() -> io::Result<String> {
    let mut file = File::open(env::current_exe()?)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 8192];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("sha256:{:x}", hasher.finalize()))
}

fn enabled_features() -> &'static str {
    if cfg!(feature = "derive") {
        "[\"derive\"]"
    } else {
        "[]"
    }
}

fn capability_json(
    self_test: &str,
    bad_header_agreement: &str,
    truncated_frame_agreement: &str,
    malformed_rendezvous_agreement: &str,
    oversized_frame_agreement: &str,
    world_size: Option<usize>,
) -> Result<String, String> {
    let binary_hash =
        binary_sha256().map_err(|error| format!("hash current executable: {error}"))?;
    let world_size = world_size
        .map(|size| format!(",\"world_size\":{size}"))
        .unwrap_or_default();
    Ok(format!(
        concat!(
            "{{\"kind\":\"kllm-mpi-provider-capabilities\",",
            "\"provider\":{{\"name\":\"mpi-rs\",",
            "\"upstream_revision\":\"{}\",",
            "\"local_patch_digest\":\"{}\",",
            "\"protocol_version\":{},",
            "\"target_triple\":\"{}\",",
            "\"features\":{},",
            "\"binary_sha256\":\"{}\"}},",
            "\"readiness\":{{\"provider_self_test\":\"{}\",",
            "\"bad_header_agreement\":\"{}\",",
            "\"truncated_frame_agreement\":\"{}\",",
            "\"malformed_rendezvous_agreement\":\"{}\",",
            "\"oversized_frame_agreement\":\"{}\",",
            "\"kllm_engine\":\"unavailable\",\"gpu\":\"unavailable\"}}{}",
            "}}"
        ),
        env!("KLLM_MPI_UPSTREAM_REVISION"),
        env!("KLLM_MPI_PATCH_DIGEST"),
        env!("KLLM_MPI_PROTOCOL_VERSION"),
        env!("KLLM_MPI_TARGET"),
        enabled_features(),
        binary_hash,
        self_test,
        bad_header_agreement,
        truncated_frame_agreement,
        malformed_rendezvous_agreement,
        oversized_frame_agreement,
        world_size,
    ))
}

fn print_capabilities(
    self_test: &str,
    bad_header_agreement: &str,
    truncated_frame_agreement: &str,
    malformed_rendezvous_agreement: &str,
    oversized_frame_agreement: &str,
    world_size: Option<usize>,
) -> Result<(), String> {
    println!(
        "{}",
        capability_json(
            self_test,
            bad_header_agreement,
            truncated_frame_agreement,
            malformed_rendezvous_agreement,
            oversized_frame_agreement,
            world_size,
        )?
    );
    Ok(())
}

const SELF_TEST_RANKS: usize = 3;
const SELF_TEST_TIMEOUT: Duration = Duration::from_secs(15);
const SELF_TEST_CLEANUP_TIMEOUT: Duration = Duration::from_secs(2);
const LOCAL_CONTROL_READ_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_LOCAL_CONTROL_LINE_BYTES: usize = 1024;

struct SelfTestChild {
    rank: usize,
    child: Child,
    reaped: bool,
    #[cfg(unix)]
    ownership_lost: bool,
}

impl SelfTestChild {
    fn poll_exit(&mut self, deadline: Instant) -> io::Result<Option<ExitStatus>> {
        match retry_interrupted_until(deadline, || self.child.try_wait()) {
            Ok(Some(status)) => {
                self.reaped = true;
                Ok(Some(status))
            }
            Ok(None) => Ok(None),
            Err(error) => {
                #[cfg(unix)]
                {
                    // Do not later signal this numeric PID: a failed direct-child
                    // observation can mean the PID has already been released.
                    self.ownership_lost = true;
                }
                Err(error)
            }
        }
    }
}

#[derive(Clone, Copy)]
enum LocalTest {
    Collectives,
    BadHeaderAgreement,
    TruncatedFrameAgreement,
    MalformedRendezvousAgreement,
    OversizedFrameAgreement,
}

impl LocalTest {
    fn rank_flag(self) -> &'static str {
        match self {
            Self::Collectives => "--__provider-self-test-rank",
            Self::BadHeaderAgreement => "--__provider-bad-header-test-rank",
            Self::TruncatedFrameAgreement => "--__provider-truncated-frame-test-rank",
            Self::MalformedRendezvousAgreement => "--__provider-malformed-rendezvous-test-rank",
            Self::OversizedFrameAgreement => "--__provider-oversized-frame-test-rank",
        }
    }

    fn requires_ready_signal(self) -> bool {
        !matches!(self, Self::Collectives)
    }
}

/// Retry an interrupted child-state operation through its shared lifecycle
/// deadline. A final ordinary probe at the deadline lets callers choose the
/// correct cleanup path instead of treating a still-running child as unknown.
fn retry_interrupted_until<T>(
    deadline: Instant,
    mut operation: impl FnMut() -> io::Result<T>,
) -> io::Result<T> {
    loop {
        match operation() {
            Err(error) if error.kind() == ErrorKind::Interrupted && Instant::now() >= deadline => {
                return Err(io::Error::new(
                    ErrorKind::TimedOut,
                    "interrupted local-rank operation exceeded its deadline",
                ));
            }
            Err(error) if error.kind() == ErrorKind::Interrupted => continue,
            result => return result,
        }
    }
}

#[cfg(unix)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OwnedChildExit {
    Running,
    Exited,
}

#[cfg(unix)]
fn validate_sigchld_reaping_policy(
    handler: libc::sighandler_t,
    flags: libc::c_int,
) -> io::Result<()> {
    if handler != libc::SIG_DFL || flags & libc::SA_NOCLDWAIT != 0 {
        return Err(io::Error::new(
            ErrorKind::InvalidInput,
            "local provider tests require the default SIGCHLD disposition, no SA_NOCLDWAIT, and exclusive child reaping",
        ));
    }
    Ok(())
}

#[cfg(unix)]
fn require_exclusive_sigchld_reaping() -> io::Result<()> {
    // SAFETY: `sigaction` is a C output struct and an all-zero value is valid
    // storage for a query-only `sigaction` call.
    let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
    // SAFETY: a null second argument queries the current SIGCHLD disposition;
    // `action` is valid writable storage for this synchronous libc call.
    let result = unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), &mut action) };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    validate_sigchld_reaping_policy(action.sa_sigaction, action.sa_flags)
}

#[cfg(unix)]
fn observe_owned_child(child: &mut Child, deadline: Instant) -> io::Result<OwnedChildExit> {
    retry_interrupted_until(deadline, || {
        // SAFETY: `siginfo_t` is a C output struct and an all-zero value is a
        // valid initialization before `waitid` writes it.
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        // SAFETY: `child.id()` identifies the direct child; `info` is valid
        // writable storage for the duration of this synchronous libc call.
        let result = unsafe {
            libc::waitid(
                libc::P_PID,
                child.id() as libc::id_t,
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: successful `waitid` initialized `info`; zero means WNOHANG
        // observed no exited child.
        if unsafe { info.si_pid() } == 0 {
            Ok(OwnedChildExit::Running)
        } else {
            Ok(OwnedChildExit::Exited)
        }
    })
}

#[cfg(unix)]
fn signal_owned_child(
    ownership: io::Result<OwnedChildExit>,
    signal: impl FnOnce() -> io::Result<()>,
) -> io::Result<OwnedChildExit> {
    match ownership {
        Ok(OwnedChildExit::Running) => {
            signal()?;
            Ok(OwnedChildExit::Running)
        }
        Ok(OwnedChildExit::Exited) => Ok(OwnedChildExit::Exited),
        Err(error) => Err(error),
    }
}

fn read_line(stream: &mut TcpStream, deadline: Instant) -> io::Result<String> {
    let mut bytes = Vec::new();
    let mut byte = [0_u8; 1];
    loop {
        let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
            return Err(io::Error::new(
                ErrorKind::TimedOut,
                "local control line exceeded its deadline",
            ));
        };
        if remaining.is_zero() {
            return Err(io::Error::new(
                ErrorKind::TimedOut,
                "local control line exceeded its deadline",
            ));
        }
        stream.set_read_timeout(Some(remaining.min(LOCAL_CONTROL_READ_TIMEOUT)))?;
        let count = stream.read(&mut byte)?;
        if count == 0 || byte[0] == b'\n' {
            break;
        }
        if bytes.len() == MAX_LOCAL_CONTROL_LINE_BYTES {
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                "local control line exceeds its byte limit",
            ));
        }
        bytes.push(byte[0]);
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

fn wait_for_child_exit(
    child: &mut SelfTestChild,
    deadline: Instant,
) -> io::Result<Option<ExitStatus>> {
    loop {
        if let Some(status) = child.poll_exit(deadline)? {
            return Ok(Some(status));
        }
        if Instant::now() >= deadline {
            return Ok(None);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(unix)]
fn terminate_children(children: &mut [SelfTestChild]) -> Result<(), String> {
    let deadline = Instant::now() + SELF_TEST_CLEANUP_TIMEOUT;
    let mut errors = Vec::new();

    // Signal every rank we can still prove is our direct child before waiting
    // for any one of them, so the cleanup deadline is shared by the whole set.
    for child in children.iter_mut() {
        if child.reaped {
            continue;
        }
        if child.ownership_lost {
            errors.push(format!("rank {} ownership was already lost", child.rank));
            continue;
        }
        match signal_owned_child(observe_owned_child(&mut child.child, deadline), || {
            child.child.kill()
        }) {
            Ok(_) => {}
            Err(error) => {
                child.ownership_lost = true;
                errors.push(format!("inspect or terminate rank {}: {error}", child.rank));
            }
        }
    }

    for child in children.iter_mut() {
        if child.reaped || child.ownership_lost {
            continue;
        }
        match wait_for_child_exit(child, deadline) {
            Ok(Some(_)) => {}
            Ok(None) => errors.push(format!(
                "rank {} did not exit before cleanup deadline",
                child.rank
            )),
            Err(error) => errors.push(format!("reap rank {}: {error}", child.rank)),
        }
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

#[cfg(not(unix))]
fn terminate_children(children: &mut [SelfTestChild]) -> Result<(), String> {
    let deadline = Instant::now() + SELF_TEST_CLEANUP_TIMEOUT;
    let mut errors = Vec::new();
    for child in children.iter_mut() {
        if child.reaped {
            continue;
        }
        if let Err(error) = child.child.kill() {
            errors.push(format!("terminate rank {}: {error}", child.rank));
        }
    }
    for child in children.iter_mut() {
        if child.reaped {
            continue;
        }
        match wait_for_child_exit(child, deadline) {
            Ok(Some(_)) => {}
            Ok(None) => errors.push(format!(
                "rank {} did not exit before cleanup deadline",
                child.rank
            )),
            Err(error) => errors.push(format!("reap rank {}: {error}", child.rank)),
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

fn any_child_exited(children: &mut [SelfTestChild], deadline: Instant) -> Result<(), String> {
    for child in children.iter_mut() {
        if let Some(status) = child
            .poll_exit(deadline)
            .map_err(|error| format!("inspect rank {}: {error}", child.rank))?
        {
            return Err(format!(
                "rank {} exited before local bootstrap completed with {status}",
                child.rank
            ));
        }
    }
    Ok(())
}

fn parse_registration(line: &str, ranks: usize) -> Result<(usize, SocketAddr), String> {
    let mut parts = line.split_whitespace();
    let rank = parts
        .next()
        .ok_or_else(|| "missing rank in local PMI registration".to_string())?
        .parse::<usize>()
        .map_err(|_| "invalid rank in local PMI registration".to_string())?;
    if rank >= ranks {
        return Err(format!(
            "out-of-range rank {rank} in local PMI registration"
        ));
    }
    let address = parts
        .next()
        .ok_or_else(|| "missing address in local PMI registration".to_string())?
        .parse::<SocketAddr>()
        .map_err(|_| "invalid address in local PMI registration".to_string())?;
    if !address.ip().is_loopback() {
        return Err(format!("rank {rank} reported a non-loopback address"));
    }
    if parts.next().is_none() || parts.next().is_some() {
        return Err("invalid local PMI registration fields".to_string());
    }
    Ok((rank, address))
}

fn collect_registrations(
    listener: &TcpListener,
    children: &mut [SelfTestChild],
    ranks: usize,
) -> Result<(Vec<SocketAddr>, Vec<Option<TcpStream>>), String> {
    let deadline = Instant::now() + SELF_TEST_TIMEOUT;
    let mut addresses = vec![None; ranks];
    let mut connections = (0..ranks).map(|_| None).collect::<Vec<_>>();
    let mut connected = 0;

    while connected < ranks {
        any_child_exited(children, deadline)?;
        match listener.accept() {
            Ok((mut stream, _)) => {
                let line = read_line(&mut stream, deadline)
                    .map_err(|error| format!("read local PMI registration: {error}"))?;
                let (rank, address) = parse_registration(&line, ranks)?;
                if addresses[rank].is_some() {
                    return Err(format!("duplicate local PMI registration for rank {rank}"));
                }
                addresses[rank] = Some(address);
                connections[rank] = Some(stream);
                connected += 1;
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                if Instant::now() >= deadline {
                    return Err(
                        "timed out waiting for local self-test ranks to bootstrap".to_string()
                    );
                }
                thread::sleep(Duration::from_millis(10));
            }
            Err(error) => return Err(format!("accept local PMI registration: {error}")),
        }
    }

    let addresses = addresses
        .into_iter()
        .enumerate()
        .map(|(rank, address)| {
            address.ok_or_else(|| format!("missing local PMI registration for rank {rank}"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok((addresses, connections))
}

fn publish_address_table(
    addresses: &[SocketAddr],
    connections: &mut [Option<TcpStream>],
) -> Result<(), String> {
    let table = addresses
        .iter()
        .enumerate()
        .map(|(rank, address)| format!("{rank} {address} le\n"))
        .collect::<String>();
    for stream in connections.iter_mut().flatten() {
        stream
            .write_all(table.as_bytes())
            .map_err(|error| format!("write local PMI address table: {error}"))?;
        stream
            .flush()
            .map_err(|error| format!("flush local PMI address table: {error}"))?;
    }
    Ok(())
}

fn wait_for_successful_children(children: &mut [SelfTestChild]) -> Result<(), String> {
    let deadline = Instant::now() + SELF_TEST_TIMEOUT;
    let mut finished = vec![false; children.len()];
    loop {
        let mut pending = 0;
        for child in children.iter_mut() {
            if finished[child.rank] {
                continue;
            }
            match child
                .poll_exit(deadline)
                .map_err(|error| format!("inspect rank {}: {error}", child.rank))?
            {
                Some(status) if status.success() => finished[child.rank] = true,
                Some(status) => {
                    return Err(format!(
                        "rank {} failed local self-test with {status}",
                        child.rank
                    ));
                }
                None => pending += 1,
            }
        }
        if pending == 0 {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("local provider self-test exceeded its watchdog bound".to_string());
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn wait_for_malformed_frame_abort(children: &mut [SelfTestChild]) -> Result<(), String> {
    let deadline = Instant::now() + SELF_TEST_TIMEOUT;
    let mut finished = vec![false; children.len()];
    loop {
        let mut pending = 0;
        for child in children.iter_mut() {
            if finished[child.rank] {
                continue;
            }
            match child
                .poll_exit(deadline)
                .map_err(|error| format!("inspect rank {}: {error}", child.rank))?
            {
                Some(status) if status.code() == Some(1) => finished[child.rank] = true,
                Some(status) => {
                    return Err(format!(
                        "rank {} did not agree on the malformed-frame abort: {status}",
                        child.rank
                    ));
                }
                None => pending += 1,
            }
        }
        if pending == 0 {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("malformed-frame ranks exceeded their watchdog bound".to_string());
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn wait_for_ready_ranks(
    listener: &TcpListener,
    children: &mut [SelfTestChild],
    ranks: usize,
) -> Result<(), String> {
    let deadline = Instant::now() + SELF_TEST_TIMEOUT;
    let mut ready = vec![false; ranks];
    let mut count = 0;
    while count < ranks {
        any_child_exited(children, deadline)?;
        match listener.accept() {
            Ok((mut stream, _)) => {
                let line = read_line(&mut stream, deadline)
                    .map_err(|error| format!("read self-test ready signal: {error}"))?;
                let rank = line
                    .trim()
                    .parse::<usize>()
                    .map_err(|_| "invalid self-test ready rank".to_string())?;
                if rank >= ranks || ready[rank] {
                    return Err("duplicate or out-of-range self-test ready rank".to_string());
                }
                ready[rank] = true;
                count += 1;
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                if Instant::now() >= deadline {
                    return Err("timed out waiting for self-test ranks to become ready".to_string());
                }
                thread::sleep(Duration::from_millis(10));
            }
            Err(error) => return Err(format!("accept self-test ready signal: {error}")),
        }
    }
    Ok(())
}

fn inject_bad_header(address: SocketAddr) -> Result<(), String> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))
        .map_err(|error| format!("connect malformed-header probe: {error}"))?;
    // The provider's fixed frame header is 40 bytes; an all-zero magic is invalid.
    stream
        .write_all(&[0_u8; 40])
        .map_err(|error| format!("write malformed-header probe: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("flush malformed-header probe: {error}"))?;
    Ok(())
}

fn inject_truncated_valid_frame(address: SocketAddr) -> Result<(), String> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))
        .map_err(|error| format!("connect truncated-frame probe: {error}"))?;
    // This has the provider's valid `MPI!` magic and declares one payload byte,
    // then the connection is closed without that byte.
    let mut header = [0_u8; 40];
    header[..4].copy_from_slice(&0x4D50_4921_u32.to_le_bytes());
    header[32..].copy_from_slice(&1_u64.to_le_bytes());
    stream
        .write_all(&header)
        .map_err(|error| format!("write truncated-frame probe: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("flush truncated-frame probe: {error}"))?;
    Ok(())
}

fn inject_malformed_rendezvous_announcement(address: SocketAddr) -> Result<(), String> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))
        .map_err(|error| format!("connect malformed-rendezvous probe: {error}"))?;
    // The 12-byte body contains only `[id][sender_world]`; a real RTS record is
    // `[id][sender_world][payload_len]` and must be exactly 20 bytes.
    let sender_world = 2_i32;
    let id = (sender_world as u64) << 40;
    let mut header = [0_u8; 40];
    header[..4].copy_from_slice(&0x4D50_4921_u32.to_le_bytes());
    header[8..12].copy_from_slice(&sender_world.to_le_bytes());
    header[12..16].copy_from_slice(&1_i32.to_le_bytes());
    header[20..28].copy_from_slice(&1_u64.to_le_bytes());
    header[28..32].copy_from_slice(&(0x8000_0000_u32 | 3).to_le_bytes());
    header[32..].copy_from_slice(&12_u64.to_le_bytes());
    let mut body = Vec::with_capacity(12);
    body.extend_from_slice(&id.to_le_bytes());
    body.extend_from_slice(&sender_world.to_le_bytes());
    stream
        .write_all(&header)
        .and_then(|()| stream.write_all(&body))
        .map_err(|error| format!("write malformed-rendezvous probe: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("flush malformed-rendezvous probe: {error}"))?;
    Ok(())
}

fn inject_oversized_frame(address: SocketAddr) -> Result<(), String> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))
        .map_err(|error| format!("connect oversized-frame probe: {error}"))?;
    let mut header = [0_u8; 40];
    header[..4].copy_from_slice(&0x4D50_4921_u32.to_le_bytes());
    header[32..].copy_from_slice(&(64_u64 * 1024 * 1024 + 1).to_le_bytes());
    stream
        .write_all(&header)
        .map_err(|error| format!("write oversized-frame probe: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("flush oversized-frame probe: {error}"))?;
    Ok(())
}

fn run_local_test(ranks: usize, test: LocalTest) -> Result<(), String> {
    #[cfg(unix)]
    require_exclusive_sigchld_reaping()
        .map_err(|error| format!("local self-test requires exclusive SIGCHLD reaping: {error}"))?;

    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("bind local provider self-test: {error}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|error| format!("configure local provider self-test listener: {error}"))?;
    let root = listener
        .local_addr()
        .map_err(|error| format!("read local provider self-test listener: {error}"))?;
    let ready_listener = if test.requires_ready_signal() {
        let listener = TcpListener::bind(("127.0.0.1", 0))
            .map_err(|error| format!("bind self-test ready listener: {error}"))?;
        listener
            .set_nonblocking(true)
            .map_err(|error| format!("configure self-test ready listener: {error}"))?;
        Some(listener)
    } else {
        None
    };
    let ready_address = ready_listener
        .as_ref()
        .map(|listener| listener.local_addr())
        .transpose()
        .map_err(|error| format!("read self-test ready listener: {error}"))?;
    let executable =
        env::current_exe().map_err(|error| format!("locate provider self-test binary: {error}"))?;
    let job_id = format!("provider-self-test-{}", std::process::id());
    let mut children = Vec::with_capacity(ranks);

    let result = (|| {
        for rank in 0..ranks {
            let mut command = Command::new(&executable);
            for key in [
                "MPI_PMI_ROOT",
                "MPI_PMI_RANK",
                "MPI_PMI_SIZE",
                "MPI_JOBID",
                "MPI_SPAWN",
                "MPI_MULTIHOST",
                "MPI_HOST_IP",
                "KLLM_MPI_PROVIDER_TEST_READY",
            ] {
                command.env_remove(key);
            }
            command
                .arg(test.rank_flag())
                .env("MPI_PMI_ROOT", root.to_string())
                .env("MPI_PMI_RANK", rank.to_string())
                .env("MPI_PMI_SIZE", ranks.to_string())
                .env("MPI_JOBID", &job_id)
                .stdin(Stdio::null())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit());
            if let Some(ready_address) = ready_address {
                command.env("KLLM_MPI_PROVIDER_TEST_READY", ready_address.to_string());
            }
            let child = command
                .spawn()
                .map_err(|error| format!("start local self-test rank {rank}: {error}"))?;
            children.push(SelfTestChild {
                rank,
                child,
                reaped: false,
                #[cfg(unix)]
                ownership_lost: false,
            });
        }
        let (addresses, mut connections) = collect_registrations(&listener, &mut children, ranks)?;
        publish_address_table(&addresses, &mut connections)?;
        drop(connections);
        match test {
            LocalTest::Collectives => wait_for_successful_children(&mut children),
            LocalTest::BadHeaderAgreement
            | LocalTest::TruncatedFrameAgreement
            | LocalTest::MalformedRendezvousAgreement
            | LocalTest::OversizedFrameAgreement => {
                let ready_listener = ready_listener
                    .as_ref()
                    .expect("invalid-frame test has a ready listener");
                wait_for_ready_ranks(ready_listener, &mut children, ranks)?;
                match test {
                    LocalTest::BadHeaderAgreement => inject_bad_header(addresses[1])?,
                    LocalTest::TruncatedFrameAgreement => {
                        inject_truncated_valid_frame(addresses[1])?
                    }
                    LocalTest::MalformedRendezvousAgreement => {
                        inject_malformed_rendezvous_announcement(addresses[1])?
                    }
                    LocalTest::OversizedFrameAgreement => inject_oversized_frame(addresses[1])?,
                    LocalTest::Collectives => unreachable!(),
                }
                wait_for_malformed_frame_abort(&mut children)
            }
        }
    })();

    match result {
        Ok(()) => Ok(()),
        Err(primary) => match terminate_children(&mut children) {
            Ok(()) => Err(primary),
            Err(cleanup) => Err(format!(
                "{primary}; local self-test cleanup failed: {cleanup}"
            )),
        },
    }
}

fn run_local_self_test(ranks: usize) -> Result<(), String> {
    run_local_test(ranks, LocalTest::Collectives)
}

fn run_bad_header_agreement_test(ranks: usize) -> Result<(), String> {
    run_local_test(ranks, LocalTest::BadHeaderAgreement)
}

fn run_truncated_frame_agreement_test(ranks: usize) -> Result<(), String> {
    run_local_test(ranks, LocalTest::TruncatedFrameAgreement)
}

fn run_malformed_rendezvous_agreement_test(ranks: usize) -> Result<(), String> {
    run_local_test(ranks, LocalTest::MalformedRendezvousAgreement)
}

fn run_oversized_frame_agreement_test(ranks: usize) -> Result<(), String> {
    run_local_test(ranks, LocalTest::OversizedFrameAgreement)
}

fn run_rank_self_test() -> Result<(), String> {
    use mpi::collective::SystemOperation;
    use mpi::datatype::PartitionMut;
    use mpi::traits::*;

    let universe = mpi::initialize().ok_or_else(|| "MPI initialized twice".to_string())?;
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();
    if size != SELF_TEST_RANKS as i32 {
        return Err(format!("expected {SELF_TEST_RANKS} ranks, got {size}"));
    }

    let root = world.process_at_rank(0);
    let mut broadcast = if rank == 0 { vec![7_i32] } else { vec![0_i32] };
    root.broadcast_into(&mut broadcast[..]);
    if broadcast != [7] {
        return Err(format!("rank {rank} observed an invalid broadcast"));
    }

    let mut total = 0_i32;
    world.all_reduce_into(&(rank + 1), &mut total, SystemOperation::sum());
    if total != 6 {
        return Err(format!("rank {rank} observed all-reduce total {total}"));
    }

    let counts = vec![1_i32, 2, 3];
    let displacements = vec![0_i32, 1, 3];
    let send = vec![rank; (rank + 1) as usize];
    let mut gathered = vec![-1_i32; 6];
    {
        let mut partition = PartitionMut::new(&mut gathered[..], counts, displacements);
        world.all_gather_varcount_into(&send[..], &mut partition);
    }
    if gathered != [0, 1, 1, 2, 2, 2] {
        return Err(format!(
            "rank {rank} observed invalid all-gatherv data {gathered:?}"
        ));
    }

    world.barrier();
    Ok(())
}

fn run_rank_malformed_frame_test() -> Result<(), String> {
    use mpi::traits::*;

    let universe = mpi::initialize().ok_or_else(|| "MPI initialized twice".to_string())?;
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();
    if size != SELF_TEST_RANKS as i32 {
        return Err(format!("expected {SELF_TEST_RANKS} ranks, got {size}"));
    }

    let ready_address = env::var("KLLM_MPI_PROVIDER_TEST_READY")
        .map_err(|_| "missing local malformed-frame ready address".to_string())?;
    let mut ready = TcpStream::connect(&ready_address)
        .map_err(|error| format!("connect local bad-header ready listener: {error}"))?;
    ready
        .write_all(format!("{rank}\n").as_bytes())
        .map_err(|error| format!("write local bad-header ready signal: {error}"))?;
    ready
        .flush()
        .map_err(|error| format!("flush local bad-header ready signal: {error}"))?;

    // No rank sends this message. The malformed header injected by the parent
    // must cause the reader loop to abort every member of this blocked world.
    let source = (rank + 1) % size;
    let _ = world.process_at_rank(source).receive::<u8>();
    Err("bad-header test reached an unexpected receive".to_string())
}

fn usage() -> &'static str {
    "usage: kllm-mpi-provider-check [--capabilities | --self-test --ranks 3 | --bad-header-agreement-test --ranks 3 | --truncated-frame-agreement-test --ranks 3 | --malformed-rendezvous-agreement-test --ranks 3 | --oversized-frame-agreement-test --ranks 3]"
}

fn parse_local_rank_count(count: &str) -> Result<usize, String> {
    let ranks = count
        .parse::<usize>()
        .map_err(|_| "--ranks must be an integer".to_string())?;
    if ranks != SELF_TEST_RANKS {
        return Err(format!(
            "R0 local provider tests require exactly {SELF_TEST_RANKS} ranks"
        ));
    }
    Ok(ranks)
}

fn run() -> Result<(), String> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    match args.as_slice() {
        [] => print_capabilities("not_run", "not_run", "not_run", "not_run", "not_run", None),
        [flag] if flag == "--capabilities" => {
            print_capabilities("not_run", "not_run", "not_run", "not_run", "not_run", None)
        }
        [flag] if flag == "--__provider-self-test-rank" => run_rank_self_test(),
        [flag] if flag == "--__provider-bad-header-test-rank" => run_rank_malformed_frame_test(),
        [flag] if flag == "--__provider-truncated-frame-test-rank" => {
            run_rank_malformed_frame_test()
        }
        [flag] if flag == "--__provider-malformed-rendezvous-test-rank" => {
            run_rank_malformed_frame_test()
        }
        [flag] if flag == "--__provider-oversized-frame-test-rank" => {
            run_rank_malformed_frame_test()
        }
        [self_test, ranks, count] if self_test == "--self-test" && ranks == "--ranks" => {
            let ranks = parse_local_rank_count(count)?;
            run_local_self_test(ranks)?;
            print_capabilities(
                "passed",
                "not_run",
                "not_run",
                "not_run",
                "not_run",
                Some(ranks),
            )
        }
        [bad_header_test, ranks, count]
            if bad_header_test == "--bad-header-agreement-test" && ranks == "--ranks" =>
        {
            let ranks = parse_local_rank_count(count)?;
            run_bad_header_agreement_test(ranks)?;
            print_capabilities(
                "not_run",
                "passed",
                "not_run",
                "not_run",
                "not_run",
                Some(ranks),
            )
        }
        [truncated_frame_test, ranks, count]
            if truncated_frame_test == "--truncated-frame-agreement-test" && ranks == "--ranks" =>
        {
            let ranks = parse_local_rank_count(count)?;
            run_truncated_frame_agreement_test(ranks)?;
            print_capabilities(
                "not_run",
                "not_run",
                "passed",
                "not_run",
                "not_run",
                Some(ranks),
            )
        }
        [malformed_rendezvous_test, ranks, count]
            if malformed_rendezvous_test == "--malformed-rendezvous-agreement-test"
                && ranks == "--ranks" =>
        {
            let ranks = parse_local_rank_count(count)?;
            run_malformed_rendezvous_agreement_test(ranks)?;
            print_capabilities(
                "not_run",
                "not_run",
                "not_run",
                "passed",
                "not_run",
                Some(ranks),
            )
        }
        [oversized_frame_test, ranks, count]
            if oversized_frame_test == "--oversized-frame-agreement-test" && ranks == "--ranks" =>
        {
            let ranks = parse_local_rank_count(count)?;
            run_oversized_frame_agreement_test(ranks)?;
            print_capabilities(
                "not_run",
                "not_run",
                "not_run",
                "not_run",
                "passed",
                Some(ranks),
            )
        }
        _ => Err(usage().to_string()),
    }
}

#[cfg(test)]
mod control_line_tests {
    use super::*;

    #[test]
    fn local_control_line_rejects_an_oversized_registration() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind test listener");
        let address = listener.local_addr().expect("read test listener address");
        let sender = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).expect("connect test sender");
            stream
                .write_all(&vec![b'x'; MAX_LOCAL_CONTROL_LINE_BYTES + 1])
                .expect("write oversized registration");
        });
        let (mut stream, _) = listener.accept().expect("accept test sender");

        let error = read_line(&mut stream, Instant::now() + Duration::from_secs(1))
            .expect_err("oversized local control lines must fail closed");
        assert_eq!(error.kind(), ErrorKind::InvalidData);
        sender.join().expect("join test sender");
    }

    #[test]
    fn local_control_line_respects_its_total_deadline() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind test listener");
        let address = listener.local_addr().expect("read test listener address");
        let sender = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).expect("connect test sender");
            stream.write_all(b"x").expect("write first byte");
            stream.flush().expect("flush first byte");
            thread::sleep(Duration::from_millis(100));
        });
        let (mut stream, _) = listener.accept().expect("accept test sender");

        let error = read_line(&mut stream, Instant::now() + Duration::from_millis(20))
            .expect_err("a dripped local control line must not bypass its deadline");
        assert_eq!(error.kind(), ErrorKind::TimedOut);
        sender.join().expect("join test sender");
    }
}

#[cfg(all(test, windows))]
mod windows_cleanup_tests {
    use super::*;

    #[test]
    fn local_child_wait_respects_an_expired_cleanup_deadline() {
        let child = Command::new(
            env::var_os("ComSpec").expect("Windows must provide the ComSpec command interpreter"),
        )
        .args(["/C", "ping -n 5 127.0.0.1 > NUL"])
        .spawn()
        .expect("start a long-running child");
        let mut child = SelfTestChild {
            rank: 0,
            child,
            reaped: false,
        };

        assert!(
            wait_for_child_exit(&mut child, Instant::now())
                .expect("inspect running child")
                .is_none(),
            "an expired cleanup deadline must not block waiting for a child"
        );
        child.child.kill().expect("terminate test child");
        assert!(
            wait_for_child_exit(&mut child, Instant::now() + Duration::from_secs(2))
                .expect("reap test child after termination")
                .is_some()
        );
    }
}

#[cfg(all(test, unix))]
mod unix_tests {
    use super::*;

    #[test]
    fn sigchld_preflight_requires_exclusive_reaping() {
        assert!(validate_sigchld_reaping_policy(libc::SIG_DFL, 0).is_ok());
        assert!(validate_sigchld_reaping_policy(libc::SIG_IGN, 0).is_err());
        assert!(validate_sigchld_reaping_policy(libc::SIG_DFL, libc::SA_NOCLDWAIT).is_err());
    }

    #[test]
    fn cleanup_does_not_signal_after_a_nonreaping_exit_observation() {
        let mut signalled = false;
        let exit = signal_owned_child(Ok(OwnedChildExit::Exited), || {
            signalled = true;
            Ok(())
        })
        .expect("an observed exited child must not be signalled");

        assert_eq!(exit, OwnedChildExit::Exited);
        assert!(!signalled, "an exited child must be reaped, not signalled");
    }

    #[test]
    fn cleanup_does_not_signal_after_lost_child_ownership() {
        let mut signalled = false;
        let error = signal_owned_child(Err(io::Error::from_raw_os_error(libc::ECHILD)), || {
            signalled = true;
            Ok(())
        })
        .expect_err("ECHILD must fail closed");

        assert_eq!(error.raw_os_error(), Some(libc::ECHILD));
        assert!(
            !signalled,
            "lost ownership must not permit a numeric PID kill"
        );
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("kllm-mpi-provider-check: {error}");
            ExitCode::from(2)
        }
    }
}
