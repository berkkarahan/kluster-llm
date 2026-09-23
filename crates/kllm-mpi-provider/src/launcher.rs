//! The pure-Rust MPI process launcher, shared by the `mpiexec` and `mpirun`
//! binaries.
//!
//! [`run`] spawns N copies of a program, assigns each a rank, and runs a tiny
//! PMI-style rendezvous so every process learns the network address of every
//! peer before `mpi::initialize()` returns.
//!
//! Usage:
//!
//! ```text
//! mpiexec -n <N> <program> [args...]
//! mpiexec --np <N> <program> [args...]
//!
//! # KLLM provider fixture boundary
//! mpiexec -n <N> [--host localhost[,127.0.0.1...]] <program> [args...]
//! ```
//!
//! This launcher is loopback-only. Remote SSH and multi-host launch are
//! deliberately disabled rather than exposing an unauthenticated PMI listener.
//! It is an upstream compatibility fixture, not a KLLM production launch path.

use std::io::{self, ErrorKind, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const CLEANUP_TIMEOUT: Duration = Duration::from_secs(2);
const BOOTSTRAP_TIMEOUT: Duration = Duration::from_secs(15);
const CONTROL_READ_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_CONTROL_LINE_BYTES: usize = 1024;
const MAX_LOCAL_RANKS: usize = 1024;

struct LocalChild {
    rank: usize,
    child: Child,
    reaped: bool,
    #[cfg(unix)]
    ownership_lost: bool,
}

impl LocalChild {
    fn new(rank: usize, child: Child) -> Self {
        Self {
            rank,
            child,
            reaped: false,
            #[cfg(unix)]
            ownership_lost: false,
        }
    }

    fn poll_exit(&mut self, deadline: Instant) -> io::Result<Option<std::process::ExitStatus>> {
        match retry_interrupted_until(deadline, || self.child.try_wait()) {
            Ok(Some(status)) => {
                self.reaped = true;
                Ok(Some(status))
            }
            Ok(None) => Ok(None),
            Err(error) => {
                #[cfg(unix)]
                {
                    // A failed direct-child observation may mean the PID was
                    // released. Cleanup must never signal it afterward.
                    self.ownership_lost = true;
                }
                Err(error)
            }
        }
    }
}

fn retry_interrupted_until<T>(
    deadline: Instant,
    mut operation: impl FnMut() -> io::Result<T>,
) -> io::Result<T> {
    loop {
        match operation() {
            Err(error) if error.kind() == ErrorKind::Interrupted && Instant::now() >= deadline => {
                return Err(io::Error::new(
                    ErrorKind::TimedOut,
                    "interrupted launcher child operation exceeded its deadline",
                ));
            }
            Err(error) if error.kind() == ErrorKind::Interrupted => continue,
            result => return result,
        }
    }
}

fn usage() -> ! {
    eprintln!("usage: mpiexec -n <N> [--host h1,h2,...] [--remote-bin PATH] <program> [args...]");
    std::process::exit(2);
}

/// Read a single newline-terminated line directly off the stream (no buffering
/// beyond the line, so the stream can be reused for writing afterwards).
fn read_line(stream: &mut TcpStream, deadline: Instant) -> std::io::Result<String> {
    let mut buf = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "PMI bootstrap deadline elapsed while reading a control line",
                )
            })?;
        if remaining.is_zero() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "PMI bootstrap deadline elapsed while reading a control line",
            ));
        }
        stream.set_read_timeout(Some(remaining.min(CONTROL_READ_TIMEOUT)))?;
        let n = stream.read(&mut byte)?;
        if n == 0 || byte[0] == b'\n' {
            break;
        }
        if buf.len() == MAX_CONTROL_LINE_BYTES {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "PMI control line exceeds its byte limit",
            ));
        }
        buf.push(byte[0]);
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

fn is_local_host(host: &str) -> bool {
    matches!(host, "" | "localhost" | "127.0.0.1" | "::1")
}

fn validate_nprocs(nprocs: usize) -> Result<(), String> {
    if nprocs > MAX_LOCAL_RANKS {
        return Err(format!(
            "requested rank count {nprocs} exceeds the loopback fixture limit of {MAX_LOCAL_RANKS}"
        ));
    }
    Ok(())
}

#[cfg(unix)]
#[derive(Clone, Copy)]
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
            "launcher requires default SIGCHLD, no SA_NOCLDWAIT, and exclusive direct-child reaping",
        ));
    }
    Ok(())
}

#[cfg(unix)]
fn require_exclusive_sigchld_reaping() -> io::Result<()> {
    // SAFETY: `sigaction` writes the initialized output structure synchronously.
    let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
    // SAFETY: a null new-action queries SIGCHLD without modifying it.
    if unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), &mut action) } != 0 {
        return Err(io::Error::last_os_error());
    }
    validate_sigchld_reaping_policy(action.sa_sigaction, action.sa_flags)
}

#[cfg(not(unix))]
fn require_exclusive_sigchld_reaping() -> io::Result<()> {
    Ok(())
}

#[cfg(unix)]
fn observe_owned_child(child: &mut Child, deadline: Instant) -> io::Result<OwnedChildExit> {
    retry_interrupted_until(deadline, || {
        // SAFETY: `siginfo_t` is valid writable storage for `waitid`.
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        // SAFETY: `child.id()` is the direct child PID while it remains owned.
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
        // SAFETY: successful `waitid` initialized `info`.
        if unsafe { info.si_pid() } == 0 {
            Ok(OwnedChildExit::Running)
        } else {
            Ok(OwnedChildExit::Exited)
        }
    })
}

fn wait_for_child_exit(
    child: &mut LocalChild,
    deadline: Instant,
) -> io::Result<Option<std::process::ExitStatus>> {
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

/// Terminate and reap only direct launcher children. The absolute deadline is
/// shared by the whole set; Unix signals require a non-reaping ownership probe.
#[cfg(unix)]
fn terminate_and_reap_children(children: &mut [LocalChild]) -> Result<(), String> {
    let deadline = Instant::now() + CLEANUP_TIMEOUT;
    let mut errors = Vec::new();

    for child in children.iter_mut() {
        if child.reaped {
            continue;
        }
        if child.ownership_lost {
            errors.push(format!("rank {} ownership was already lost", child.rank));
            continue;
        }
        match observe_owned_child(&mut child.child, deadline) {
            Ok(OwnedChildExit::Running) => {
                if let Err(error) = child.child.kill() {
                    errors.push(format!("terminate rank {}: {error}", child.rank));
                }
            }
            Ok(OwnedChildExit::Exited) => {}
            Err(error) => {
                child.ownership_lost = true;
                errors.push(format!(
                    "inspect rank {} before cleanup: {error}",
                    child.rank
                ));
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
fn terminate_and_reap_children(children: &mut [LocalChild]) -> Result<(), String> {
    let deadline = Instant::now() + CLEANUP_TIMEOUT;
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

fn fail_after_cleanup(children: &mut [LocalChild], message: impl AsRef<str>) -> ! {
    match terminate_and_reap_children(children) {
        Ok(()) => eprintln!("mpiexec: {}", message.as_ref()),
        Err(cleanup) => eprintln!(
            "mpiexec: {}; local rank cleanup failed: {cleanup}",
            message.as_ref()
        ),
    }
    std::process::exit(1);
}

/// Entry point for the launcher binaries. Parses arguments, spawns the job
/// (locally and/or via ssh), runs the PMI rendezvous, and exits with the job's
/// aggregate exit code.
pub fn run() -> ! {
    let mut args = std::env::args().skip(1).peekable();

    let mut nprocs: Option<usize> = None;
    let mut hosts: Vec<String> = Vec::new();
    let mut remote_bin: Option<String> = None;

    while let Some(arg) = args.peek() {
        match arg.as_str() {
            "-n" | "-np" | "--n" | "--np" => {
                args.next();
                let v = args.next().unwrap_or_else(|| usage());
                nprocs = Some(v.parse().unwrap_or_else(|_| usage()));
            }
            "--host" | "--hosts" | "-host" | "-hosts" => {
                args.next();
                let v = args.next().unwrap_or_else(|| usage());
                hosts.extend(v.split(',').map(|s| s.trim().to_string()));
            }
            "--remote-bin" | "-remote-bin" => {
                args.next();
                remote_bin = Some(args.next().unwrap_or_else(|| usage()));
            }
            "-h" | "--help" => usage(),
            _ => break,
        }
    }

    let nprocs = nprocs.unwrap_or_else(|| usage());
    if nprocs == 0 {
        usage();
    }
    if let Err(error) = validate_nprocs(nprocs) {
        eprintln!("mpiexec: {error}");
        std::process::exit(2);
    }
    let program = args.next().unwrap_or_else(|| usage());
    let prog_args: Vec<String> = args.collect();

    if remote_bin.is_some() || hosts.iter().any(|host| !is_local_host(host)) {
        eprintln!(
            "mpiexec: remote SSH and multi-host launch are disabled in the KLLM provider fixture; use the authenticated KLLM control plane"
        );
        std::process::exit(2);
    }
    if let Err(error) = require_exclusive_sigchld_reaping() {
        eprintln!("mpiexec: require exclusive direct-child reaping: {error}");
        std::process::exit(1);
    }

    // This compatibility fixture accepts PMI registrations only from loopback.
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap_or_else(|error| {
        eprintln!("mpiexec: failed to bind loopback rendezvous socket: {error}");
        std::process::exit(1);
    });
    listener.set_nonblocking(true).unwrap_or_else(|error| {
        eprintln!("mpiexec: failed to configure rendezvous socket: {error}");
        std::process::exit(1);
    });
    let root_addr = listener.local_addr().unwrap_or_else(|error| {
        eprintln!("mpiexec: failed to read rendezvous socket address: {error}");
        std::process::exit(1);
    });
    let bootstrap_deadline = Instant::now() + BOOTSTRAP_TIMEOUT;

    // Spawn only local direct children. Every subsequent failure path goes
    // through `fail_after_cleanup` while those handles are still owned here.
    let mut children = Vec::with_capacity(nprocs);
    for rank in 0..nprocs {
        if Instant::now() >= bootstrap_deadline {
            fail_after_cleanup(
                &mut children,
                format!("bootstrap deadline elapsed before launching rank {rank}"),
            );
        }
        let mut command = Command::new(&program);
        for key in [
            "MPI_PMI_ROOT",
            "MPI_PMI_RANK",
            "MPI_PMI_SIZE",
            "MPI_JOBID",
            "MPI_MULTIHOST",
            "MPI_HOST_IP",
            "MPI_SPAWN",
        ] {
            command.env_remove(key);
        }
        let spawn = command
            .args(&prog_args)
            .env("MPI_PMI_ROOT", root_addr.to_string())
            .env("MPI_PMI_RANK", rank.to_string())
            .env("MPI_PMI_SIZE", nprocs.to_string())
            .env("MPI_JOBID", std::process::id().to_string())
            .stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn();
        match spawn {
            Ok(child) => children.push(LocalChild::new(rank, child)),
            Err(error) => fail_after_cleanup(
                &mut children,
                format!("failed to launch local rank {rank}: {error}"),
            ),
        }
    }

    // Rendezvous: accept a connection from every rank, learn its data-plane
    // address, then broadcast the full address table back.
    let mut conns: Vec<Option<TcpStream>> = (0..nprocs).map(|_| None).collect();
    let mut table: Vec<Option<SocketAddr>> = vec![None; nprocs];
    let mut endians: Vec<String> = vec!["le".to_string(); nprocs];

    for _ in 0..nprocs {
        let (mut stream, _peer) = loop {
            match listener.accept() {
                Ok(connection) => break connection,
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    let bootstrap_error = children.iter_mut().find_map(|child| {
                        match child.poll_exit(bootstrap_deadline) {
                            Ok(Some(status)) => Some(format!(
                                "rank {} exited during PMI bootstrap with {status}",
                                child.rank
                            )),
                            Ok(None) => None,
                            Err(error) => Some(format!(
                                "inspect rank {} during PMI bootstrap: {error}",
                                child.rank
                            )),
                        }
                    });
                    if let Some(bootstrap_error) = bootstrap_error {
                        fail_after_cleanup(&mut children, bootstrap_error);
                    }
                    if Instant::now() >= bootstrap_deadline {
                        fail_after_cleanup(
                            &mut children,
                            "timed out waiting for local PMI registrations",
                        );
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => {
                    fail_after_cleanup(&mut children, format!("accept PMI registration: {error}"));
                }
            }
        };
        let line = match read_line(&mut stream, bootstrap_deadline) {
            Ok(line) => line,
            Err(error) => fail_after_cleanup(&mut children, format!("PMI read failed: {error}")),
        };
        let mut parts = line.split_whitespace();
        let rank: usize = match parts.next().and_then(|s| s.parse().ok()) {
            Some(rank) => rank,
            None => fail_after_cleanup(&mut children, format!("malformed PMI line: {line:?}")),
        };
        if rank >= nprocs {
            fail_after_cleanup(&mut children, format!("out-of-range PMI rank {rank}"));
        }
        let addr: SocketAddr = match parts.next().and_then(|s| s.parse().ok()) {
            Some(addr) => addr,
            None => fail_after_cleanup(&mut children, format!("malformed PMI address: {line:?}")),
        };
        if !addr.ip().is_loopback() {
            fail_after_cleanup(
                &mut children,
                format!("rank {rank} reported a non-loopback PMI address"),
            );
        }
        if conns[rank].is_some() {
            fail_after_cleanup(
                &mut children,
                format!("duplicate PMI registration for rank {rank}"),
            );
        }
        let endian = parts.next().unwrap_or("le");
        if !matches!(endian, "le" | "be") || parts.next().is_some() {
            fail_after_cleanup(&mut children, "invalid PMI registration fields");
        }
        endians[rank] = endian.to_string();
        table[rank] = Some(addr);
        conns[rank] = Some(stream);
    }

    // Serialize and broadcast the address table (rank addr endian).
    let mut blob = String::new();
    for (rank, address) in table.iter().enumerate() {
        let address = match address {
            Some(address) => address,
            None => fail_after_cleanup(
                &mut children,
                format!("missing PMI registration for rank {rank}"),
            ),
        };
        blob.push_str(&format!("{rank} {address} {}\n", endians[rank]));
    }
    for stream in conns.iter_mut().flatten() {
        let remaining = match bootstrap_deadline.checked_duration_since(Instant::now()) {
            Some(remaining) if !remaining.is_zero() => remaining,
            _ => fail_after_cleanup(
                &mut children,
                "bootstrap deadline elapsed while publishing the PMI address table",
            ),
        };
        if let Err(error) = stream.set_write_timeout(Some(remaining.min(CONTROL_READ_TIMEOUT))) {
            fail_after_cleanup(
                &mut children,
                format!("configure PMI table write timeout: {error}"),
            );
        }
        if let Err(error) = stream.write_all(blob.as_bytes()) {
            fail_after_cleanup(&mut children, format!("write PMI address table: {error}"));
        }
        if let Err(error) = stream.flush() {
            fail_after_cleanup(&mut children, format!("flush PMI address table: {error}"));
        }
    }
    drop(conns);
    drop(listener);

    // Poll every direct child so a failing rank cancels the remainder rather
    // than being hidden behind a blocked earlier `wait()`.
    let mut completed = vec![false; children.len()];
    loop {
        let mut pending = 0;
        let mut failure = None;
        for child in children.iter_mut() {
            let rank = child.rank;
            if completed[rank] {
                continue;
            }
            match child.poll_exit(Instant::now() + CLEANUP_TIMEOUT) {
                Ok(Some(status)) if status.success() => completed[rank] = true,
                Ok(Some(status)) => {
                    failure = Some((
                        format!("rank {rank} exited unsuccessfully with {status}"),
                        status.code().unwrap_or(1),
                    ));
                    break;
                }
                Ok(None) => pending += 1,
                Err(error) => {
                    failure = Some((format!("inspect rank {rank}: {error}"), 1));
                    break;
                }
            }
        }
        if let Some((message, exit_code)) = failure {
            match terminate_and_reap_children(&mut children) {
                Ok(()) => eprintln!("mpiexec: {message}"),
                Err(cleanup) => {
                    eprintln!("mpiexec: {message}; local rank cleanup failed: {cleanup}")
                }
            }
            std::process::exit(exit_code);
        }
        if pending == 0 {
            std::process::exit(0);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn fixture_rejects_nondefault_sigchld_reaping_policy() {
        let error = validate_sigchld_reaping_policy(libc::SIG_IGN, 0)
            .expect_err("SIG_IGN makes direct-child ownership unsafe");
        assert!(error.to_string().contains("default SIGCHLD"));
    }

    #[test]
    fn fixture_rejects_an_excessive_rank_count_before_spawning() {
        let error = validate_nprocs(MAX_LOCAL_RANKS + 1)
            .expect_err("loopback fixture must cap its direct child count");
        assert!(error.contains("exceeds the loopback fixture limit"));
    }
}
