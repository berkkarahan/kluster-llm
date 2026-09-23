//! Integration tests: launch the example programs under the bundled `mpiexec`
//! across several process counts and assert they pass.

use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex, MutexGuard, Once};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::io::AsRawHandle;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
#[cfg(windows)]
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
#[cfg(windows)]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
#[cfg(windows)]
use windows_sys::Win32::System::Threading::{
    OpenThread, ResumeThread, CREATE_SUSPENDED, THREAD_SUSPEND_RESUME,
};

#[path = "../stamp.rs"]
mod provider_stamp;

/// Directory holding the built example binaries (sibling `examples/` dir of the
/// launcher binary; works for both debug and release profiles).
fn examples_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_mpiexec"))
        .parent()
        .unwrap()
        .join("examples")
}

fn example_binary(name: &str) -> PathBuf {
    let path = examples_dir().join(name);
    #[cfg(windows)]
    {
        path.with_extension("exe")
    }
    #[cfg(not(windows))]
    {
        path
    }
}

#[test]
fn example_binary_uses_the_platform_executable_suffix() {
    let path = example_binary("probe");
    #[cfg(windows)]
    assert_eq!(path.extension().and_then(|ext| ext.to_str()), Some("exe"));
    #[cfg(not(windows))]
    assert_eq!(
        path.file_name().and_then(|name| name.to_str()),
        Some("probe")
    );
}

static BUILD: Once = Once::new();
static PROCESS_JOB_SLOT: Mutex<()> = Mutex::new(());

#[test]
fn provider_process_jobs_share_one_slot() {
    let _slot = acquire_job_slot();
    assert!(PROCESS_JOB_SLOT.try_lock().is_err());
}

#[cfg(windows)]
#[test]
fn failed_windows_spawn_cleanup_is_bounded_and_preserves_termination_errors() {
    let mut child = Command::new(
        std::env::var_os("ComSpec").expect("Windows must provide the ComSpec command interpreter"),
    )
    .args(["/C", "ping -n 5 127.0.0.1 > NUL"])
    .spawn()
    .expect("start a long-running child");
    let started = Instant::now();
    let cleanup = cleanup_failed_windows_spawn(&mut child, Instant::now(), |_| {
        Err(std::io::Error::other("injected job-termination failure"))
    });
    let elapsed = started.elapsed();

    let _ = child.kill();
    let _ = wait_for_child_exit(&mut child, Instant::now() + Duration::from_secs(2));

    let error = cleanup.expect_err("failed termination must be reported");
    assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
    assert!(error
        .to_string()
        .contains("injected job-termination failure"));
    assert!(
        elapsed < Duration::from_secs(1),
        "failed-spawn cleanup exceeded its deadline: {elapsed:?}"
    );
}

#[cfg(windows)]
#[test]
fn normal_windows_teardown_falls_back_after_job_termination_failure() {
    let mut child = Command::new(
        std::env::var_os("ComSpec").expect("Windows must provide the ComSpec command interpreter"),
    )
    .args(["/C", "ping -n 5 127.0.0.1 > NUL"])
    .spawn()
    .expect("start a long-running child");
    let mut process_tree = OwnedProcessTree {
        job: std::ptr::null_mut(),
    };

    let teardown = terminate_owned_job(&mut process_tree, &mut child);
    let reaped_before_manual_cleanup =
        wait_for_child_exit(&mut child, Instant::now() + Duration::from_millis(100))
            .expect("inspect child after failed Job Object termination");
    let _ = child.kill();
    let _ = wait_for_child_exit(&mut child, Instant::now() + Duration::from_secs(2));

    let error = teardown.expect_err("a Job Object termination failure must be reported");
    assert!(error.to_string().contains("terminate Windows Job Object"));
    assert!(
        reaped_before_manual_cleanup.is_some(),
        "normal teardown must use the direct child-handle fallback before returning"
    );
}

#[cfg(unix)]
#[test]
fn owned_process_tree_can_be_disarmed_after_a_verified_clean_exit() {
    let mut process_tree = OwnedProcessTree {
        process_group: 999_999,
        armed: true,
    };
    assert!(process_tree.is_armed());
    process_tree.disarm();
    assert!(!process_tree.is_armed());
}

#[test]
fn operation_retries_an_interrupted_wait() {
    let mut attempts = 0;
    let state = retry_interrupted_until(Instant::now() + Duration::from_secs(1), || {
        attempts += 1;
        if attempts == 1 {
            Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "injected waitid interruption",
            ))
        } else {
            Ok(ChildExit::Running)
        }
    })
    .expect("EINTR must be retried before process-tree ownership is abandoned");

    assert_eq!(state, ChildExit::Running);
    assert_eq!(attempts, 2);
}

#[test]
fn interrupted_operation_stops_after_an_attempt_at_its_deadline() {
    let mut attempts = 0;
    let error = retry_interrupted_until(Instant::now(), || {
        attempts += 1;
        Err::<(), _>(std::io::Error::new(
            std::io::ErrorKind::Interrupted,
            "injected persistent interruption",
        ))
    })
    .expect_err("a persistent interruption must not bypass the watchdog deadline");

    assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
    assert_eq!(attempts, 1);
}

#[cfg(unix)]
#[test]
fn unix_sigchld_preflight_requires_exclusive_reaping() {
    assert!(validate_sigchld_reaping_policy(libc::SIG_DFL, 0).is_ok());

    let ignored = validate_sigchld_reaping_policy(libc::SIG_IGN, 0)
        .expect_err("SIG_IGN makes child status unavailable");
    assert_eq!(ignored.kind(), std::io::ErrorKind::InvalidInput);

    let no_cldwait = validate_sigchld_reaping_policy(libc::SIG_DFL, libc::SA_NOCLDWAIT)
        .expect_err("SA_NOCLDWAIT makes child status unavailable");
    assert_eq!(no_cldwait.kind(), std::io::ErrorKind::InvalidInput);
}

#[cfg(unix)]
#[test]
fn unix_ownership_observation_error_disarms_without_signalling_process_group() {
    let mut armed = true;
    let mut signalled = false;
    let error = terminate_after_ownership_check(
        &mut armed,
        Err(std::io::Error::from_raw_os_error(libc::ECHILD)),
        || {
            signalled = true;
            Ok(())
        },
    )
    .expect_err("an ownership-observation error must fail closed");

    assert_eq!(error.raw_os_error(), Some(libc::ECHILD));
    assert!(!armed, "failed ownership must disarm cleanup");
    assert!(
        !signalled,
        "failed ownership must not signal a numeric PGID"
    );
}

#[cfg(unix)]
#[test]
fn unix_exit_observation_preserves_process_group_ownership_until_reap() {
    let _slot = acquire_job_slot();
    let mut command = Command::new("sh");
    command.args(["-c", "exit 7"]);
    let (mut child, mut process_tree) = spawn_owned_job(&mut command).expect("spawn owned child");
    // SAFETY: `child` is alive and owned by this test; `getsid` only queries
    // its session membership.
    assert_eq!(
        unsafe { libc::getsid(child.id() as libc::pid_t) },
        child.id() as libc::pid_t,
        "owned launcher must lead an isolated session"
    );

    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        match observe_child_exit(&mut child, deadline).expect("observe owned child exit") {
            ChildExit::Exited => break,
            ChildExit::Running => {
                assert!(
                    Instant::now() < deadline,
                    "owned child did not exit before the test deadline"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }

    assert!(process_tree.is_armed());
    let leader = child.id() as libc::pid_t;
    // A successful zero-signal probe proves `waitid(...WNOWAIT)` left the
    // exited leader present, so its numeric PID cannot yet be reused as a PGID.
    assert_eq!(unsafe { libc::kill(leader, 0) }, 0);
    let status = terminate_owned_job(&mut process_tree, &mut child)
        .expect("reap an observed owned child without releasing its process group");
    assert_eq!(status.code(), Some(7));
    assert!(!process_tree.is_armed());
}

#[test]
fn source_stamp_is_identical_for_lf_and_crlf_inputs() {
    let lf = vec![
        (
            "Cargo.toml".to_string(),
            b"[package]\nname = \"mpi-rs\"\n".to_vec(),
        ),
        ("src/lib.rs".to_string(), b"pub fn provider() {}\n".to_vec()),
    ];
    let crlf = vec![
        (
            "Cargo.toml".to_string(),
            b"[package]\r\nname = \"mpi-rs\"\r\n".to_vec(),
        ),
        (
            "src/lib.rs".to_string(),
            b"pub fn provider() {}\r\n".to_vec(),
        ),
    ];
    assert_eq!(
        provider_stamp::source_digest(lf),
        provider_stamp::source_digest(crlf)
    );
}

struct TemporaryProviderCopy {
    root: PathBuf,
}

impl TemporaryProviderCopy {
    fn new() -> Self {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock should be after the Unix epoch")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "kllm-mpi-provider-stamp-{}-{unique}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("create temporary provider copy");
        Self { root }
    }
}

impl Drop for TemporaryProviderCopy {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn copy_provider_tree(source: &Path, destination: &Path) {
    for entry in fs::read_dir(source).expect("read provider source directory") {
        let entry = entry.expect("read provider source entry");
        let name = entry.file_name();
        if name == "target" || name == ".git" {
            continue;
        }
        let source_path = entry.path();
        let destination_path = destination.join(&name);
        if source_path.is_dir() {
            fs::create_dir_all(&destination_path).expect("create provider source subdirectory");
            copy_provider_tree(&source_path, &destination_path);
        } else {
            fs::copy(&source_path, &destination_path).expect("copy provider source file");
        }
    }
}

fn provider_copy_capabilities(root: &Path) -> serde_json::Value {
    let output = Command::new(env!("CARGO"))
        .current_dir(root)
        .args([
            "run",
            "--quiet",
            "--locked",
            "--bin",
            "kllm-mpi-provider-check",
            "--",
            "--capabilities",
        ])
        .env("CARGO_TARGET_DIR", root.join("target"))
        .output()
        .expect("run copied provider capability check");
    assert!(
        output.status.success(),
        "copied provider capability check failed: {}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("copied provider capability check must emit JSON")
}

#[test]
fn provider_capability_stamp_tracks_added_and_removed_source_files() {
    let _job_slot = acquire_job_slot();
    let copy = TemporaryProviderCopy::new();
    copy_provider_tree(Path::new(env!("CARGO_MANIFEST_DIR")), &copy.root);

    let before = provider_copy_capabilities(&copy.root)["provider"]["local_patch_digest"]
        .as_str()
        .expect("initial capability digest")
        .to_string();
    let probe = copy.root.join("src").join("__stamp_rerun_probe.rs");
    fs::write(&probe, b"// test-only source stamp probe\n").expect("add source stamp probe");
    let added = provider_copy_capabilities(&copy.root)["provider"]["local_patch_digest"]
        .as_str()
        .expect("capability digest after source addition")
        .to_string();
    fs::remove_file(&probe).expect("remove source stamp probe");
    let removed = provider_copy_capabilities(&copy.root)["provider"]["local_patch_digest"]
        .as_str()
        .expect("capability digest after source removal")
        .to_string();

    assert_ne!(
        before, added,
        "adding a stamped source input must rebuild the stamp"
    );
    assert_eq!(
        before, removed,
        "removing the stamped source input must restore the original stamp"
    );
}

struct InterruptedThenDataReader {
    interrupted: bool,
    inner: std::io::Cursor<Vec<u8>>,
}

impl Read for InterruptedThenDataReader {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        if !self.interrupted {
            self.interrupted = true;
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "injected pipe interruption",
            ));
        }
        self.inner.read(buffer)
    }
}

struct CancelOnInterruptedRead {
    cancellation: Arc<AtomicBool>,
}

impl Read for CancelOnInterruptedRead {
    fn read(&mut self, _buffer: &mut [u8]) -> std::io::Result<usize> {
        self.cancellation.store(true, Ordering::Release);
        Err(std::io::Error::new(
            std::io::ErrorKind::Interrupted,
            "injected cancelled pipe interruption",
        ))
    }
}

#[test]
fn captured_pipe_stops_retrying_when_collection_is_cancelled() {
    let cancellation = Arc::new(AtomicBool::new(false));
    let captured = drain_pipe(
        CancelOnInterruptedRead {
            cancellation: Arc::clone(&cancellation),
        },
        &cancellation,
    );

    assert!(captured.cancelled);
}

#[test]
fn captured_pipe_retries_an_interrupted_read() {
    let cancellation = AtomicBool::new(false);
    let captured = drain_pipe(
        InterruptedThenDataReader {
            interrupted: false,
            inner: std::io::Cursor::new(b"retained diagnostics".to_vec()),
        },
        &cancellation,
    );

    assert_eq!(captured.bytes, b"retained diagnostics");
    assert!(!captured.truncated);
    assert!(!captured.cancelled);
}

#[test]
fn captured_pipe_marks_and_drains_output_over_the_capture_limit() {
    let cancellation = AtomicBool::new(false);
    let captured = drain_pipe(
        std::io::Cursor::new(vec![b'x'; MAX_CAPTURE_BYTES + 1]),
        &cancellation,
    );
    assert_eq!(captured.bytes.len(), MAX_CAPTURE_BYTES);
    assert!(captured.truncated);
    assert!(!captured.cancelled);
}

fn sha256_file(path: &Path) -> String {
    let mut hasher = Sha256::new();
    hasher.update(fs::read(path).expect("read binary to hash"));
    format!("sha256:{:x}", hasher.finalize())
}

#[test]
fn loopback_launcher_rejects_remote_launch_before_starting_a_program() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-remote-launch-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args([
            "-n",
            "1",
            "--host",
            "untrusted.example.invalid",
            "--remote-bin",
        ])
        .arg(&probe)
        .arg(&probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base);

    let result = run_with_timeout(&mut command, Duration::from_secs(5));

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "remote launcher request returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result
        .output
        .contains("remote SSH and multi-host launch are disabled"));
    assert!(
        !base.with_extension("0").exists(),
        "launcher started a program after rejecting remote launch"
    );
}

#[test]
fn loopback_launcher_rejects_an_early_rank_exit_during_bootstrap() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-early-exit-pmi-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "1"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "exit");

    let started = Instant::now();
    let result = run_with_timeout(&mut command, Duration::from_secs(5));
    let elapsed = started.elapsed();
    let pids = wait_for_timeout_probe_pids(&base, 1);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "early rank exit returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result.output.contains("rank 0 exited during PMI bootstrap"));
    assert!(
        elapsed < Duration::from_secs(3),
        "launcher did not detect early rank exit promptly: {elapsed:?}"
    );
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left early-exit rank PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_bounds_missing_pmi_registration_and_reaps_its_rank() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-missing-pmi-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "1"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "silent");

    let started = Instant::now();
    let result = run_with_timeout(&mut command, Duration::from_secs(20));
    let elapsed = started.elapsed();
    let pids = wait_for_timeout_probe_pids(&base, 1);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "missing PMI registration returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result
        .output
        .contains("timed out waiting for local PMI registrations"));
    assert!(
        elapsed < Duration::from_secs(19),
        "missing PMI registration was not bounded: {elapsed:?}"
    );
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left missing-PMI rank PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_bounds_a_stalled_pmi_control_line_and_reaps_its_rank() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-stalled-pmi-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "1"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "stalled");

    let started = Instant::now();
    let result = run_with_timeout(&mut command, Duration::from_secs(20));
    let elapsed = started.elapsed();
    let pids = wait_for_timeout_probe_pids(&base, 1);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "stalled PMI line returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result.output.contains("PMI read failed"));
    assert!(
        elapsed < Duration::from_secs(19),
        "stalled PMI control phase was not bounded: {elapsed:?}"
    );
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left stalled-PMI rank PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_rejects_an_oversized_pmi_control_line_and_reaps_its_rank() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-oversized-pmi-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "1"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "oversized");

    let result = run_with_timeout(&mut command, Duration::from_secs(5));
    let pids = wait_for_timeout_probe_pids(&base, 1);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "oversized PMI line returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result
        .output
        .contains("PMI control line exceeds its byte limit"));
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left oversized-PMI rank PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_rejects_non_loopback_pmi_address_and_reaps_rank() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-nonloopback-pmi-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "1"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "non-loopback");

    let result = run_with_timeout(&mut command, Duration::from_secs(5));
    let pids = wait_for_timeout_probe_pids(&base, 1);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "non-loopback PMI address returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result
        .output
        .contains("reported a non-loopback PMI address"));
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left non-loopback PMI rank PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_cancels_remaining_ranks_after_a_rank_fails() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-rank-failure-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 2,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "2"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "valid-then-fail");

    let started = Instant::now();
    let result = run_with_timeout(&mut command, Duration::from_secs(5));
    let elapsed = started.elapsed();
    let pids = wait_for_timeout_probe_pids(&base, 2);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "rank failure returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result.output.contains("rank 0 exited unsuccessfully"));
    assert!(
        elapsed < Duration::from_secs(3),
        "launcher waited for another rank after rank 0 failed: {elapsed:?}"
    );
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left rank-failure probe PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_rejects_pmi_registration_with_extra_fields_and_reaps_rank() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-extra-pmi-fields-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "1"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "extra-fields");

    let result = run_with_timeout(&mut command, Duration::from_secs(5));
    let pids = wait_for_timeout_probe_pids(&base, 1);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "extra PMI fields returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result.output.contains("invalid PMI registration fields"));
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left extra-fields PMI rank PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_rejects_duplicate_pmi_rank_registration_and_reaps_ranks() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-duplicate-pmi-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 2,
    };
    let probe = example_binary("invalid_pmi_rank");
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "2"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base)
        .env("KLLM_MPI_INVALID_PMI_PROBE_MODE", "duplicate");

    let result = run_with_timeout(&mut command, Duration::from_secs(5));
    let pids = wait_for_timeout_probe_pids(&base, 2);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "duplicate PMI rank returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result
        .output
        .contains("duplicate PMI registration for rank 0"));
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left duplicate-PMI rank PID {pid} alive"
        );
    }
}

#[test]
fn loopback_launcher_rejects_an_out_of_range_registration_and_reaps_its_rank() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-invalid-pmi-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 1,
    };
    let probe = example_binary("invalid_pmi_rank");
    assert!(
        probe.exists(),
        "invalid PMI probe binary not found at {probe:?}"
    );
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .args(["-n", "1"])
        .arg(probe)
        .env("KLLM_MPI_INVALID_PMI_PROBE_BASE", &base);

    let result = run_with_timeout(&mut command, Duration::from_secs(5));
    let pids = wait_for_timeout_probe_pids(&base, 1);

    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "invalid PMI rank returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(result.output.contains("out-of-range PMI rank"));
    for pid in pids {
        assert!(
            wait_for_process_exit(pid),
            "launcher left invalid-PMI rank PID {pid} alive"
        );
    }
}

#[test]
fn provider_check_reports_provider_only_capabilities() {
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_kllm-mpi-provider-check"));
    let output = run_provider_check(&["--capabilities"]);
    assert_eq!(
        output.outcome,
        RunOutcome::Exited,
        "provider check failed: {}",
        output.output
    );

    let capabilities: serde_json::Value =
        serde_json::from_str(&output.stdout).expect("capabilities must be valid JSON");
    assert_eq!(
        capabilities["kind"].as_str(),
        Some("kllm-mpi-provider-capabilities")
    );

    let provider = &capabilities["provider"];
    assert_eq!(provider["name"].as_str(), Some("mpi-rs"));
    assert_eq!(
        provider["upstream_revision"].as_str(),
        Some("99be8ac11c04f5419452428a486ae0eee1f5552e")
    );
    assert_eq!(provider["protocol_version"].as_u64(), Some(1));
    assert!(provider["target_triple"]
        .as_str()
        .is_some_and(|target| !target.is_empty()));
    let expected_features = if cfg!(feature = "derive") {
        serde_json::json!(["derive"])
    } else {
        serde_json::json!([])
    };
    assert_eq!(provider["features"], expected_features);
    let source_stamp = provider_stamp::provider_source_stamp(Path::new(env!("CARGO_MANIFEST_DIR")))
        .expect("recompute provider source stamp");
    assert!(source_stamp.files.iter().any(|path| {
        path.file_name()
            .is_some_and(|name| name == ".gitattributes")
    }));
    assert_eq!(
        provider["local_patch_digest"].as_str(),
        Some(source_stamp.digest.as_str())
    );
    assert_eq!(
        provider["binary_sha256"].as_str(),
        Some(sha256_file(&binary).as_str())
    );

    let readiness = &capabilities["readiness"];
    assert_eq!(readiness["provider_self_test"].as_str(), Some("not_run"));
    assert_eq!(readiness["bad_header_agreement"].as_str(), Some("not_run"));
    assert_eq!(
        readiness["truncated_frame_agreement"].as_str(),
        Some("not_run")
    );
    assert_eq!(
        readiness["malformed_rendezvous_agreement"].as_str(),
        Some("not_run")
    );
    assert_eq!(
        readiness["oversized_frame_agreement"].as_str(),
        Some("not_run")
    );
    assert_eq!(readiness["kllm_engine"].as_str(), Some("unavailable"));
    assert_eq!(readiness["gpu"].as_str(), Some("unavailable"));
}

#[test]
fn provider_check_runner_owns_an_invalid_invocation() {
    let _job_slot = acquire_job_slot();
    let result = run_provider_check(&["--not-a-provider-check-argument"]);

    assert_eq!(result.outcome, RunOutcome::LauncherFailed);
    assert!(result.output.contains("usage: kllm-mpi-provider-check"));
}

#[test]
fn rank_binary_rejects_an_unstructured_direct_launch() {
    let output = Command::new(env!("CARGO_BIN_EXE_kllm-mpi-rank"))
        .arg("--untrusted-argument")
        .output()
        .expect("rank binary should start");
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("KLLM control-plane launch"));
}

#[test]
fn structured_singleton_rank_warms_up_health_checks_and_stops() {
    let _job_slot = acquire_job_slot();
    let address = (20_000..=20_999)
        .find_map(|port| {
            let address = format!("127.0.0.1:{port}");
            std::net::TcpListener::bind(&address).ok().map(|listener| {
                drop(listener);
                address
            })
        })
        .expect("a production data-plane test port should be available");
    let spec = serde_json::json!({
        "job_id": "11".repeat(16),
        "rank": 0,
        "world_size": 1,
        "bind_address": address,
        "peers": [{"rank": 0, "address": address, "nonce": "22".repeat(32)}],
        "job_secret": "33".repeat(32),
        "protocol_version": 1,
        "provider_revision": env!("KLLM_MPI_UPSTREAM_REVISION"),
        "provider_patch_digest": env!("KLLM_MPI_PATCH_DIGEST"),
        "engine_revision": "provider-validation-v1",
        "backend": "vulkan",
        "physical_device_id": "test-gpu",
        "backend_device": "test-device",
        "model_id": "provider-validation",
        "manifest_identity": "sha256:test-manifest",
        "collective_timeout_ms": 2000,
        "max_collective_bytes": 4096,
    });
    let mut child = Command::new(env!("CARGO_BIN_EXE_kllm-mpi-rank"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("structured rank should start");
    {
        let input = child.stdin.as_mut().unwrap();
        writeln!(input, "{spec}").unwrap();
        writeln!(input, "{{\"command\":\"health\"}}").unwrap();
        writeln!(input, "{{\"command\":\"stop\"}}").unwrap();
    }
    let output = child
        .wait_with_output()
        .expect("structured rank should exit");
    assert!(
        output.status.success(),
        "rank failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).unwrap();
    let records: Vec<serde_json::Value> = stdout
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(records.len(), 3);
    assert_eq!(records[0]["kind"], "kllm-mpi-rank-ready");
    assert_eq!(records[0]["warmup"], "passed");
    assert_eq!(records[1]["kind"], "kllm-mpi-rank-health");
    assert_eq!(records[2]["kind"], "kllm-mpi-rank-stopped");
    assert!(!stdout.contains(&"33".repeat(32)));
}

#[test]
fn provider_check_self_test_uses_exactly_three_local_ranks() {
    let _job_slot = acquire_job_slot();
    let output = run_provider_check(&["--self-test", "--ranks", "3"]);
    assert_eq!(
        output.outcome,
        RunOutcome::Exited,
        "provider self-test failed: {}",
        output.output
    );
    let json: serde_json::Value =
        serde_json::from_str(&output.stdout).expect("self-test result must be valid JSON");
    assert_eq!(
        json["readiness"]["provider_self_test"].as_str(),
        Some("passed")
    );
    assert_eq!(json["world_size"].as_u64(), Some(3));
}

#[test]
fn provider_check_agrees_on_a_bad_header_across_three_local_ranks() {
    let _job_slot = acquire_job_slot();
    let output = run_provider_check(&["--bad-header-agreement-test", "--ranks", "3"]);
    assert_eq!(
        output.outcome,
        RunOutcome::Exited,
        "bad-header agreement check failed: {}",
        output.output
    );
    let json: serde_json::Value =
        serde_json::from_str(&output.stdout).expect("bad-header result must be valid JSON");
    assert_eq!(
        json["readiness"]["bad_header_agreement"].as_str(),
        Some("passed")
    );
    assert_eq!(json["world_size"].as_u64(), Some(3));
}

#[test]
fn provider_check_agrees_on_a_truncated_valid_frame_across_three_local_ranks() {
    let _job_slot = acquire_job_slot();
    let output = run_provider_check(&["--truncated-frame-agreement-test", "--ranks", "3"]);
    assert_eq!(
        output.outcome,
        RunOutcome::Exited,
        "truncated-frame agreement check failed: {}",
        output.output
    );
    let json: serde_json::Value =
        serde_json::from_str(&output.stdout).expect("truncated-frame result must be valid JSON");
    assert_eq!(
        json["readiness"]["truncated_frame_agreement"].as_str(),
        Some("passed")
    );
    assert_eq!(json["world_size"].as_u64(), Some(3));
}

#[test]
fn provider_check_agrees_on_a_malformed_rendezvous_control_across_three_local_ranks() {
    let _job_slot = acquire_job_slot();
    let output = run_provider_check(&["--malformed-rendezvous-agreement-test", "--ranks", "3"]);
    assert_eq!(
        output.outcome,
        RunOutcome::Exited,
        "malformed-rendezvous agreement check failed: {}",
        output.output
    );
    let json: serde_json::Value = serde_json::from_str(&output.stdout)
        .expect("malformed-rendezvous result must be valid JSON");
    assert_eq!(
        json["readiness"]["malformed_rendezvous_agreement"].as_str(),
        Some("passed")
    );
    assert_eq!(json["world_size"].as_u64(), Some(3));
}

#[test]
fn provider_check_agrees_on_an_oversized_frame_across_three_local_ranks() {
    let _job_slot = acquire_job_slot();
    let output = run_provider_check(&["--oversized-frame-agreement-test", "--ranks", "3"]);
    assert_eq!(
        output.outcome,
        RunOutcome::Exited,
        "oversized-frame agreement check failed: {}",
        output.output
    );
    let json: serde_json::Value =
        serde_json::from_str(&output.stdout).expect("oversized-frame result must be valid JSON");
    assert_eq!(
        json["readiness"]["oversized_frame_agreement"].as_str(),
        Some("passed")
    );
    assert_eq!(json["world_size"].as_u64(), Some(3));
}

fn acquire_job_slot() -> MutexGuard<'static, ()> {
    // The integration cases launch loopback MPI jobs that share the test
    // process's working directory and inherited resources. Running two at once
    // makes the suite nondeterministic on Windows, so each job owns this slot.
    PROCESS_JOB_SLOT
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn probe_path(base: &Path, rank: usize) -> PathBuf {
    PathBuf::from(format!("{}.{}", base.display(), rank))
}

fn timeout_probe_pids(base: &Path, ranks: usize) -> Option<Vec<u32>> {
    let mut pids = Vec::with_capacity(ranks);
    for rank in 0..ranks {
        let pid = fs::read_to_string(probe_path(base, rank))
            .ok()?
            .trim()
            .parse()
            .ok()?;
        pids.push(pid);
    }
    Some(pids)
}

#[cfg(windows)]
fn process_is_alive(pid: u32) -> bool {
    let filter = format!("PID eq {pid}");
    let output = Command::new("tasklist")
        .args(["/FI", filter.as_str(), "/FO", "CSV", "/NH"])
        .output()
        .expect("query timeout-probe process");
    String::from_utf8_lossy(&output.stdout).contains(&format!("\"{pid}\""))
}

#[cfg(unix)]
fn process_is_alive(pid: u32) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

struct TimeoutProbeFiles {
    base: PathBuf,
    ranks: usize,
}

impl Drop for TimeoutProbeFiles {
    fn drop(&mut self) {
        for rank in 0..self.ranks {
            let _ = fs::remove_file(probe_path(&self.base, rank));
        }
    }
}

fn wait_for_timeout_probe_pids(base: &Path, ranks: usize) -> Vec<u32> {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        if let Some(pids) = timeout_probe_pids(base, ranks) {
            return pids;
        }
        assert!(
            Instant::now() < deadline,
            "timeout probe ranks did not publish their PIDs"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn wait_for_process_exit(pid: u32) -> bool {
    let deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < deadline {
        if !process_is_alive(pid) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    !process_is_alive(pid)
}

struct PidFileCleanup(PathBuf);

impl Drop for PidFileCleanup {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn wait_for_pid_file(path: &Path) -> u32 {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        if let Ok(contents) = fs::read_to_string(path) {
            return contents
                .trim()
                .parse()
                .expect("pipe-holder child PID must be numeric");
        }
        assert!(
            Instant::now() < deadline,
            "pipe-holder descendant did not publish its PID"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn ensure_examples_built() {
    // Build all examples (including the `derive`-gated one) exactly once, even
    // when tests run in parallel.
    BUILD.call_once(|| {
        // Build examples with the derive support used by the integration suite.
        let status = Command::new(env!("CARGO"))
            .args(["build", "--examples", "--features", "derive", "--quiet"])
            .status()
            .expect("failed to invoke cargo build");
        assert!(status.success(), "cargo build --examples failed");
    });
}

const MAX_CAPTURE_BYTES: usize = 1024 * 1024;

const OUTPUT_CLEANUP_TIMEOUT: Duration = Duration::from_secs(2);
const PROVIDER_CHECK_TIMEOUT: Duration = Duration::from_secs(25);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RunOutcome {
    Exited,
    LauncherFailed,
    RuntimeTimeout,
    OutputDrainTimeout,
    HarnessError,
}

struct RunResult {
    outcome: RunOutcome,
    stdout: String,
    output: String,
    output_truncated: bool,
}

struct PipeCapture {
    bytes: Vec<u8>,
    truncated: bool,
    cancelled: bool,
}

enum CapturedPipe {
    Stdout(PipeCapture),
    Stderr(PipeCapture),
}

struct OutputCapture {
    receiver: mpsc::Receiver<CapturedPipe>,
    stdout: Option<PipeCapture>,
    stderr: Option<PipeCapture>,
    cancellation: Arc<AtomicBool>,
}

impl OutputCapture {
    fn new(stdout: impl Read + Send + 'static, stderr: impl Read + Send + 'static) -> Self {
        let (sender, receiver) = mpsc::channel();
        let cancellation = Arc::new(AtomicBool::new(false));
        let stdout_sender = sender.clone();
        let stdout_cancellation = Arc::clone(&cancellation);
        std::thread::spawn(move || {
            let _ = stdout_sender.send(CapturedPipe::Stdout(drain_pipe(
                stdout,
                &stdout_cancellation,
            )));
        });
        let stderr_cancellation = Arc::clone(&cancellation);
        std::thread::spawn(move || {
            let _ = sender.send(CapturedPipe::Stderr(drain_pipe(
                stderr,
                &stderr_cancellation,
            )));
        });
        Self {
            receiver,
            stdout: None,
            stderr: None,
            cancellation,
        }
    }

    fn cancel(&self) {
        self.cancellation.store(true, Ordering::Release);
    }

    fn was_cancelled(&self) -> bool {
        self.cancellation.load(Ordering::Acquire)
            || self
                .stdout
                .as_ref()
                .is_some_and(|capture| capture.cancelled)
            || self
                .stderr
                .as_ref()
                .is_some_and(|capture| capture.cancelled)
    }

    fn collect_until(&mut self, deadline: Instant) -> bool {
        while self.stdout.is_none() || self.stderr.is_none() {
            let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
                self.cancel();
                return false;
            };
            match self.receiver.recv_timeout(remaining) {
                Ok(CapturedPipe::Stdout(output)) => self.stdout = Some(output),
                Ok(CapturedPipe::Stderr(output)) => self.stderr = Some(output),
                Err(mpsc::RecvTimeoutError::Timeout | mpsc::RecvTimeoutError::Disconnected) => {
                    self.cancel();
                    return false;
                }
            }
        }
        !self.was_cancelled()
    }

    fn truncated(&self) -> bool {
        self.stdout
            .as_ref()
            .is_some_and(|capture| capture.truncated)
            || self
                .stderr
                .as_ref()
                .is_some_and(|capture| capture.truncated)
    }

    fn stdout_text(&self) -> String {
        String::from_utf8_lossy(self.stdout.as_ref().map_or(&[], |capture| &capture.bytes))
            .into_owned()
    }

    fn output(&self) -> String {
        let stdout = self.stdout.as_ref();
        let stderr = self.stderr.as_ref();
        let mut output =
            String::from_utf8_lossy(stdout.map_or(&[], |capture| &capture.bytes)).into_owned();
        output.push_str(&String::from_utf8_lossy(
            stderr.map_or(&[], |capture| &capture.bytes),
        ));
        if self.truncated() {
            if !output.ends_with('\n') {
                output.push('\n');
            }
            output.push_str(&format!(
                "[output capture truncated at {MAX_CAPTURE_BYTES} bytes per stream]\n"
            ));
        }
        if self.was_cancelled() {
            if !output.ends_with('\n') {
                output.push('\n');
            }
            output.push_str("[output capture cancelled at its lifecycle deadline]\n");
        }
        output
    }
}

fn captured_result(outcome: RunOutcome, capture: &OutputCapture) -> RunResult {
    RunResult {
        outcome,
        stdout: capture.stdout_text(),
        output: capture.output(),
        output_truncated: capture.truncated(),
    }
}

fn drain_pipe<R: Read + Send + 'static>(mut reader: R, cancellation: &AtomicBool) -> PipeCapture {
    let mut bytes = Vec::new();
    let mut truncated = false;
    let mut cancelled = false;
    let mut buffer = [0_u8; 8192];
    loop {
        let read = match reader.read(&mut buffer) {
            Ok(0) => break,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {
                if cancellation.load(Ordering::Acquire) {
                    cancelled = true;
                    break;
                }
                continue;
            }
            Err(_) => break,
            Ok(read) => read,
        };
        let remaining = MAX_CAPTURE_BYTES.saturating_sub(bytes.len());
        bytes.extend_from_slice(&buffer[..read.min(remaining)]);
        truncated |= read > remaining;
    }
    PipeCapture {
        bytes,
        truncated,
        cancelled,
    }
}

#[cfg(windows)]
struct OwnedProcessTree {
    job: HANDLE,
}

#[cfg(windows)]
impl OwnedProcessTree {
    fn new() -> std::io::Result<Self> {
        let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if job.is_null() {
            return Err(std::io::Error::last_os_error());
        }

        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const std::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            let error = std::io::Error::last_os_error();
            unsafe {
                let _ = CloseHandle(job);
            }
            return Err(error);
        }
        Ok(Self { job })
    }

    fn assign_suspended(&self, child: &Child) -> std::io::Result<()> {
        let assigned =
            unsafe { AssignProcessToJobObject(self.job, child.as_raw_handle() as HANDLE) };
        if assigned == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }

    fn terminate(&mut self, _child: &mut Child, _deadline: Instant) -> std::io::Result<()> {
        let terminated = unsafe { TerminateJobObject(self.job, 1) };
        if terminated == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }

    // The Windows Job Object is a durable ownership handle. Keeping it armed
    // after a successful launcher exit still reaps any escaped descendant when
    // the test harness releases the handle.
    fn disarm(&mut self) {}
}

#[cfg(windows)]
fn resume_suspended_primary_thread(process_id: u32) -> std::io::Result<()> {
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(std::io::Error::last_os_error());
    }

    let result = (|| {
        let mut entry = THREADENTRY32 {
            dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        if unsafe { Thread32First(snapshot, &mut entry) } == 0 {
            return Err(std::io::Error::last_os_error());
        }
        loop {
            if entry.th32OwnerProcessID == process_id {
                let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
                if thread.is_null() {
                    return Err(std::io::Error::last_os_error());
                }
                let resumed = unsafe { ResumeThread(thread) };
                let result = if resumed == u32::MAX {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(())
                };
                unsafe {
                    let _ = CloseHandle(thread);
                }
                return result;
            }
            entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;
            if unsafe { Thread32Next(snapshot, &mut entry) } == 0 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    "suspended launcher primary thread not found",
                ));
            }
        }
    })();

    unsafe {
        let _ = CloseHandle(snapshot);
    }
    result
}

#[cfg(windows)]
impl Drop for OwnedProcessTree {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.job);
        }
    }
}

#[cfg(unix)]
struct OwnedProcessTree {
    process_group: libc::pid_t,
    armed: bool,
}

#[cfg(unix)]
fn terminate_after_ownership_check(
    armed: &mut bool,
    ownership: std::io::Result<ChildExit>,
    signal_process_group: impl FnOnce() -> std::io::Result<()>,
) -> std::io::Result<()> {
    if !*armed {
        return Ok(());
    }
    match ownership {
        Ok(_) => {
            let result = signal_process_group();
            *armed = false;
            result
        }
        Err(error) => {
            // The leader may have been released, so its numeric PGID may now
            // belong to an unrelated job. Never signal without ownership proof.
            *armed = false;
            Err(error)
        }
    }
}

#[cfg(unix)]
impl OwnedProcessTree {
    fn terminate(&mut self, child: &mut Child, deadline: Instant) -> std::io::Result<()> {
        let process_group = self.process_group;
        terminate_after_ownership_check(
            &mut self.armed,
            observe_child_exit(child, deadline),
            || {
                // SAFETY: the group remains owned until the successful WNOWAIT
                // observation is explicitly reaped; negative PID targets only it.
                let signalled = unsafe { libc::kill(-process_group, libc::SIGKILL) };
                if signalled == -1 {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(())
                }
            },
        )
    }

    fn disarm(&mut self) {
        self.armed = false;
    }

    fn is_armed(&self) -> bool {
        self.armed
    }
}

#[cfg(not(any(windows, unix)))]
struct OwnedProcessTree;

#[cfg(not(any(windows, unix)))]
impl OwnedProcessTree {
    fn terminate(&mut self, _child: &mut Child, _deadline: Instant) -> std::io::Result<()> {
        Ok(())
    }

    fn disarm(&mut self) {}
}

#[cfg(unix)]
fn validate_sigchld_reaping_policy(
    handler: libc::sighandler_t,
    flags: libc::c_int,
) -> std::io::Result<()> {
    if handler != libc::SIG_DFL || flags & libc::SA_NOCLDWAIT != 0 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "Unix owned-process tests require the default SIGCHLD disposition, no SA_NOCLDWAIT, and exclusive child reaping",
        ));
    }
    Ok(())
}

#[cfg(unix)]
fn require_exclusive_sigchld_reaping() -> std::io::Result<()> {
    // SAFETY: `sigaction` is a C output struct and an all-zero value is valid
    // storage for a query-only `sigaction` call.
    let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
    // SAFETY: a null second argument queries the current SIGCHLD disposition;
    // `action` is valid writable storage for this synchronous libc call.
    let result = unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), &mut action) };
    if result != 0 {
        return Err(std::io::Error::last_os_error());
    }
    validate_sigchld_reaping_policy(action.sa_sigaction, action.sa_flags)
}

fn spawn_owned_job(cmd: &mut Command) -> std::io::Result<(Child, OwnedProcessTree)> {
    #[cfg(unix)]
    {
        require_exclusive_sigchld_reaping()?;
        use std::os::unix::process::CommandExt;
        // SAFETY: the pre-exec hook performs only `setsid`, an async-signal-safe
        // libc call. It creates a new session and process group before exec.
        unsafe {
            cmd.pre_exec(|| {
                // SAFETY: this executes in the newly forked child before exec.
                // `setsid` has no Rust-managed state and returns -1 on failure.
                if libc::setsid() == -1 {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(())
                }
            });
        }
    }
    #[cfg(windows)]
    let mut process_tree = OwnedProcessTree::new()?;
    #[cfg(windows)]
    cmd.creation_flags(CREATE_SUSPENDED);

    let child = cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;

    #[cfg(windows)]
    {
        let mut child = child;
        if let Err(error) = process_tree.assign_suspended(&child) {
            let cleanup_deadline = Instant::now() + OUTPUT_CLEANUP_TIMEOUT;
            let cleanup =
                cleanup_failed_windows_spawn(&mut child, cleanup_deadline, |child| child.kill());
            return Err(failed_windows_spawn_error(error, cleanup));
        }
        if let Err(error) = resume_suspended_primary_thread(child.id()) {
            let cleanup_deadline = Instant::now() + OUTPUT_CLEANUP_TIMEOUT;
            let cleanup = cleanup_failed_windows_spawn(&mut child, cleanup_deadline, |child| {
                terminate_owned_windows_job_or_fallback(&mut process_tree, child, cleanup_deadline)
            });
            return Err(failed_windows_spawn_error(error, cleanup));
        }
        Ok((child, process_tree))
    }
    #[cfg(unix)]
    {
        let process_group = child.id() as libc::pid_t;
        Ok((
            child,
            OwnedProcessTree {
                process_group,
                armed: true,
            },
        ))
    }
    #[cfg(not(any(windows, unix)))]
    {
        Ok((child, OwnedProcessTree))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ChildExit {
    Running,
    Exited,
}

/// Retry an interrupted process operation until its lifecycle deadline. `EINTR`
/// does not release a child or process group, but a signal storm must not bypass
/// the harness watchdog.
fn retry_interrupted_until<T>(
    deadline: Instant,
    mut operation: impl FnMut() -> std::io::Result<T>,
) -> std::io::Result<T> {
    loop {
        match operation() {
            Err(error)
                if error.kind() == std::io::ErrorKind::Interrupted
                    && Instant::now() >= deadline =>
            {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "interrupted process operation exceeded its deadline",
                ));
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            result => return result,
        }
    }
}

/// Observe whether the launcher has exited without releasing the Unix leader
/// PID. The process group uses that PID as its numeric id, so keeping it
/// unreaped prevents a later group-id reuse from redirecting our owned signal.
#[cfg(unix)]
fn observe_child_exit(child: &mut Child, deadline: Instant) -> std::io::Result<ChildExit> {
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
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: successful `waitid` initialized `info`; zero means WNOHANG
        // observed no exited child.
        if unsafe { info.si_pid() } == 0 {
            Ok(ChildExit::Running)
        } else {
            Ok(ChildExit::Exited)
        }
    })
}

#[cfg(not(unix))]
fn observe_child_exit(child: &mut Child, deadline: Instant) -> std::io::Result<ChildExit> {
    Ok(
        match retry_interrupted_until(deadline, || child.try_wait())? {
            Some(_) => ChildExit::Exited,
            None => ChildExit::Running,
        },
    )
}

fn wait_for_child_exit(
    child: &mut Child,
    deadline: Instant,
) -> std::io::Result<Option<ExitStatus>> {
    loop {
        if let Some(status) = retry_interrupted_until(deadline, || child.try_wait())? {
            return Ok(Some(status));
        }
        if Instant::now() >= deadline {
            return Ok(None);
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[cfg(windows)]
fn cleanup_failed_windows_spawn(
    child: &mut Child,
    deadline: Instant,
    terminate: impl FnOnce(&mut Child) -> std::io::Result<()>,
) -> std::io::Result<()> {
    let termination = terminate(child);
    let reaping = match wait_for_child_exit(child, deadline) {
        Ok(Some(_)) => Ok(()),
        Ok(None) => Err(std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            "failed Windows launcher cleanup exceeded its deadline",
        )),
        Err(error) => Err(error),
    };
    match (termination, reaping) {
        (Ok(()), Ok(())) => Ok(()),
        (Err(error), Ok(())) | (Ok(()), Err(error)) => Err(error),
        (Err(termination), Err(reaping)) => Err(std::io::Error::new(
            reaping.kind(),
            format!("{reaping}; launcher termination also failed: {termination}"),
        )),
    }
}

#[cfg(windows)]
fn terminate_owned_windows_job_or_fallback(
    process_tree: &mut OwnedProcessTree,
    child: &mut Child,
    deadline: Instant,
) -> std::io::Result<()> {
    match process_tree.terminate(child, deadline) {
        Ok(()) => Ok(()),
        Err(job_error) => match child.kill() {
            Ok(()) => Err(std::io::Error::new(
                job_error.kind(),
                format!(
                    "terminate Windows Job Object: {job_error}; sent direct launcher termination fallback"
                ),
            )),
            Err(fallback_error) => Err(std::io::Error::new(
                job_error.kind(),
                format!(
                    "terminate Windows Job Object: {job_error}; direct launcher termination fallback failed: {fallback_error}"
                ),
            )),
        },
    }
}

#[cfg(windows)]
fn failed_windows_spawn_error(
    primary: std::io::Error,
    cleanup: std::io::Result<()>,
) -> std::io::Error {
    match cleanup {
        Ok(()) => primary,
        Err(cleanup) => std::io::Error::new(
            primary.kind(),
            format!("{primary}; failed Windows spawn cleanup: {cleanup}"),
        ),
    }
}

#[cfg(windows)]
fn wait_for_windows_launcher_status(
    child: &mut Child,
    deadline: Instant,
) -> std::io::Result<ExitStatus> {
    wait_for_child_exit(child, deadline)?.ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            "owned launcher did not exit before its cleanup deadline",
        )
    })
}

#[cfg(windows)]
fn kill_windows_launcher_handle_and_reap(
    child: &mut Child,
    deadline: Instant,
) -> std::io::Result<ExitStatus> {
    let termination = child.kill();
    let reaping = wait_for_windows_launcher_status(child, deadline);
    match (termination, reaping) {
        (Ok(()), Ok(status)) => Ok(status),
        (Err(error), Ok(_)) | (Ok(()), Err(error)) => Err(error),
        (Err(termination), Err(reaping)) => Err(std::io::Error::new(
            reaping.kind(),
            format!("{reaping}; direct launcher termination also failed: {termination}"),
        )),
    }
}

#[cfg(windows)]
fn terminate_owned_windows_job(
    process_tree: &mut OwnedProcessTree,
    child: &mut Child,
    cleanup_deadline: Instant,
) -> std::io::Result<ExitStatus> {
    let termination =
        terminate_owned_windows_job_or_fallback(process_tree, child, cleanup_deadline);
    let reaping = wait_for_windows_launcher_status(child, cleanup_deadline);
    match (termination, reaping) {
        (Ok(()), Ok(status)) => Ok(status),
        (Ok(()), Err(_)) => {
            kill_windows_launcher_handle_and_reap(child, Instant::now() + OUTPUT_CLEANUP_TIMEOUT)
        }
        (Err(termination), Ok(_)) => Err(termination),
        (Err(termination), Err(reaping)) => {
            match kill_windows_launcher_handle_and_reap(
                child,
                Instant::now() + OUTPUT_CLEANUP_TIMEOUT,
            ) {
                Ok(_) => Err(std::io::Error::new(
                    termination.kind(),
                    format!(
                        "{termination}; launcher required a second direct-handle cleanup after: {reaping}"
                    ),
                )),
                Err(fallback) => Err(std::io::Error::new(
                    termination.kind(),
                    format!(
                        "{termination}; launcher was not reaped after: {reaping}; direct-handle cleanup failed: {fallback}"
                    ),
                )),
            }
        }
    }
}

fn terminate_owned_job(
    process_tree: &mut OwnedProcessTree,
    child: &mut Child,
) -> std::io::Result<ExitStatus> {
    let cleanup_deadline = Instant::now() + OUTPUT_CLEANUP_TIMEOUT;
    #[cfg(windows)]
    {
        terminate_owned_windows_job(process_tree, child, cleanup_deadline)
    }
    #[cfg(not(windows))]
    {
        process_tree.terminate(child, cleanup_deadline)?;
        if let Some(status) = wait_for_child_exit(child, cleanup_deadline)? {
            return Ok(status);
        }
        #[cfg(unix)]
        {
            // The group signal was the last action permitted by the ownership proof.
            // A later numeric PID kill could target a recycled launcher PID.
            Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "owned launcher did not exit after tree termination",
            ))
        }
        #[cfg(not(unix))]
        {
            let _ = child.kill();
            wait_for_child_exit(child, Instant::now() + OUTPUT_CLEANUP_TIMEOUT)?.ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "owned launcher did not exit after tree termination",
                )
            })
        }
    }
}

/// Run an owned MPI launcher with bounded concurrent output capture. A timeout
/// or retained output handle tears down and reaps the complete owned rank tree.
fn run_with_timeout(cmd: &mut Command, timeout: Duration) -> RunResult {
    let (mut child, mut process_tree) = match spawn_owned_job(cmd) {
        Ok(job) => job,
        Err(error) => {
            return RunResult {
                outcome: RunOutcome::HarnessError,
                stdout: String::new(),
                output: format!("spawn owned launcher: {error}"),
                output_truncated: false,
            };
        }
    };
    let Some(stdout) = child.stdout.take() else {
        let _ = terminate_owned_job(&mut process_tree, &mut child);
        return RunResult {
            outcome: RunOutcome::HarnessError,
            stdout: String::new(),
            output: "owned launcher stdout was not piped".to_string(),
            output_truncated: false,
        };
    };
    let Some(stderr) = child.stderr.take() else {
        let _ = terminate_owned_job(&mut process_tree, &mut child);
        return RunResult {
            outcome: RunOutcome::HarnessError,
            stdout: String::new(),
            output: "owned launcher stderr was not piped".to_string(),
            output_truncated: false,
        };
    };
    let mut output_capture = OutputCapture::new(stdout, stderr);

    let deadline = Instant::now() + timeout;
    loop {
        match observe_child_exit(&mut child, deadline) {
            Ok(ChildExit::Exited) => {
                let output_drained =
                    output_capture.collect_until(Instant::now() + OUTPUT_CLEANUP_TIMEOUT);
                let status = match terminate_owned_job(&mut process_tree, &mut child) {
                    Ok(status) => status,
                    Err(error) => {
                        let _ =
                            output_capture.collect_until(Instant::now() + OUTPUT_CLEANUP_TIMEOUT);
                        let mut result = captured_result(RunOutcome::HarnessError, &output_capture);
                        if result.output.is_empty() {
                            result.output = format!("reap owned launcher after exit: {error}");
                        } else {
                            result.output = format!(
                                "reap owned launcher after exit: {error}\n{}",
                                result.output
                            );
                        }
                        return result;
                    }
                };
                if !output_drained {
                    let _ = output_capture.collect_until(Instant::now() + OUTPUT_CLEANUP_TIMEOUT);
                    return captured_result(RunOutcome::OutputDrainTimeout, &output_capture);
                }
                if status.success() {
                    process_tree.disarm();
                    return captured_result(RunOutcome::Exited, &output_capture);
                }
                let mut result = captured_result(RunOutcome::LauncherFailed, &output_capture);
                if result.output.is_empty() {
                    result.output = format!("launcher exited unsuccessfully: {status}");
                }
                return result;
            }
            Ok(ChildExit::Running) => {}
            Err(error) => {
                // An observation failure means the numeric Unix process group is
                // no longer proven to belong to this harness. Fail closed rather
                // than risking a signal to a recycled group id.
                process_tree.disarm();
                let _ = output_capture.collect_until(Instant::now() + OUTPUT_CLEANUP_TIMEOUT);
                let mut result = captured_result(RunOutcome::HarnessError, &output_capture);
                if result.output.is_empty() {
                    result.output = format!("launcher ownership observation failed: {error}");
                } else {
                    result.output = format!(
                        "launcher ownership observation failed: {error}\n{}",
                        result.output
                    );
                }
                return result;
            }
        }
        if Instant::now() >= deadline {
            if let Err(error) = terminate_owned_job(&mut process_tree, &mut child) {
                let _ = output_capture.collect_until(Instant::now() + OUTPUT_CLEANUP_TIMEOUT);
                let mut result = captured_result(RunOutcome::HarnessError, &output_capture);
                if result.output.is_empty() {
                    result.output = format!("reap owned launcher after timeout: {error}");
                } else {
                    result.output = format!(
                        "reap owned launcher after timeout: {error}\n{}",
                        result.output
                    );
                }
                return result;
            }
            let _ = output_capture.collect_until(Instant::now() + OUTPUT_CLEANUP_TIMEOUT);
            return captured_result(RunOutcome::RuntimeTimeout, &output_capture);
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn run_provider_check(args: &[&str]) -> RunResult {
    let mut command = Command::new(env!("CARGO_BIN_EXE_kllm-mpi-provider-check"));
    command.args(args);
    run_with_timeout(&mut command, PROVIDER_CHECK_TIMEOUT)
}

/// Launch `example` under `mpiexec -n nprocs` and assert it prints `expect`.
fn run_example(example: &str, nprocs: usize, expect: &str) {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let bin = example_binary(example);
    assert!(bin.exists(), "example {example} not found at {bin:?}");

    let mut cmd = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    cmd.arg("-n").arg(nprocs.to_string()).arg(&bin);

    let result = run_with_timeout(&mut cmd, Duration::from_secs(30));
    assert_eq!(
        result.outcome,
        RunOutcome::Exited,
        "{example} n={nprocs} returned {:?}. Output:\n{}",
        result.outcome,
        result.output
    );
    assert!(
        !result.output_truncated,
        "{example} n={nprocs} exceeded the bounded output capture"
    );
    assert!(
        result.output.contains(expect),
        "{example} n={nprocs} missing {expect:?}. Output:\n{}",
        result.output
    );
}

#[test]
fn timeout_reaps_the_owned_mpiexec_rank_tree() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let base = std::env::temp_dir().join(format!(
        "kllm-mpi-timeout-probe-{}-{unique}",
        std::process::id()
    ));
    let _files = TimeoutProbeFiles {
        base: base.clone(),
        ranks: 2,
    };
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .arg("-n")
        .arg("2")
        .arg(example_binary("timeout_probe"))
        .env("KLLM_MPI_TIMEOUT_PROBE_BASE", &base);

    let result = run_with_timeout(&mut command, Duration::from_secs(3));
    assert_eq!(
        result.outcome,
        RunOutcome::RuntimeTimeout,
        "timeout probe returned {:?}: {}",
        result.outcome,
        result.output
    );

    let pids = wait_for_timeout_probe_pids(&base, 2);
    for pid in &pids {
        assert!(
            wait_for_process_exit(*pid),
            "timeout left owned rank PID {pid} alive"
        );
    }
}

#[test]
fn post_exit_output_deadline_reaps_a_retained_pipe_descendant() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after the Unix epoch")
        .as_nanos();
    let pid_file = std::env::temp_dir().join(format!(
        "kllm-mpi-pipe-holder-{}-{unique}.pid",
        std::process::id()
    ));
    let ready_file = std::env::temp_dir().join(format!(
        "kllm-mpi-pipe-holder-{}-{unique}.ready",
        std::process::id()
    ));
    let _pid_cleanup = PidFileCleanup(pid_file.clone());
    let _ready_cleanup = PidFileCleanup(ready_file.clone());
    let mut command = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    command
        .arg("-n")
        .arg("1")
        .arg(example_binary("pipe_holder"))
        .env("KLLM_MPI_PIPE_HOLDER_PID_FILE", &pid_file)
        .env("KLLM_MPI_PIPE_HOLDER_READY_FILE", &ready_file);

    let started = Instant::now();
    let result = run_with_timeout(&mut command, Duration::from_secs(1));
    let descendant_pid = wait_for_pid_file(&pid_file);
    assert_eq!(
        result.outcome,
        RunOutcome::OutputDrainTimeout,
        "retained-pipe job returned {:?}: {}",
        result.outcome,
        result.output
    );
    assert!(
        !result.output_truncated,
        "post-exit output should not exceed the capture limit"
    );
    assert!(
        result.output.starts_with("PIPE_HOLDER_MPI_BOOTSTRAPPED"),
        "post-exit timeout must retain captured rank output: {}",
        result.output
    );
    assert_eq!(
        fs::read_to_string(&ready_file).expect("pipe-holder rank must wait for its descendant"),
        "ready"
    );
    assert!(
        started.elapsed() < Duration::from_secs(4),
        "post-exit output drain bypassed the watchdog"
    );
    assert!(
        wait_for_process_exit(descendant_pid),
        "post-exit timeout left owned descendant PID {descendant_pid} alive"
    );
}

#[test]
fn selftest_1() {
    run_example("selftest", 1, "SELFTEST PASS");
}
#[test]
fn selftest_2() {
    run_example("selftest", 2, "SELFTEST PASS");
}
#[test]
fn selftest_4() {
    run_example("selftest", 4, "SELFTEST PASS");
}
#[test]
fn selftest_7() {
    run_example("selftest", 7, "SELFTEST PASS");
}

#[test]
fn rma_2() {
    run_example("rma", 2, "RMA PASS");
}
#[test]
fn rma_4() {
    run_example("rma", 4, "RMA PASS");
}

#[test]
fn parallel_io_2() {
    run_example("parallel_io", 2, "IO PASS");
}
#[test]
fn parallel_io_5() {
    run_example("parallel_io", 5, "IO PASS");
}

#[test]
fn topo_inter_3() {
    run_example("topo_inter", 3, "TOPO/INTER PASS");
}
#[test]
fn topo_inter_6() {
    run_example("topo_inter", 6, "TOPO/INTER PASS");
}

#[test]
fn derive_2() {
    run_example("derive", 2, "derive(Equivalence) round-trip OK");
}

/// A blocked peer must not hang when another rank aborts: the job should exit
/// (non-zero) well within the timeout rather than being killed by it.
#[test]
fn abort_does_not_hang() {
    ensure_examples_built();
    let _job_slot = acquire_job_slot();
    let bin = example_binary("abort");
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_mpiexec"));
    cmd.arg("-n").arg("2").arg(&bin);
    let result = run_with_timeout(&mut cmd, Duration::from_secs(15));
    assert_eq!(
        result.outcome,
        RunOutcome::LauncherFailed,
        "abort example returned {:?}: {}",
        result.outcome,
        result.output
    );
}

#[test]
fn spawn_singleton() {
    run_example("spawn", 1, "SPAWN PASS");
}
#[test]
fn spawn_2() {
    run_example("spawn", 2, "SPAWN PASS");
}

#[test]
fn thread_multiple_1() {
    run_example("thread_multiple", 1, "THREAD PASS");
}
#[test]
fn thread_multiple_4() {
    run_example("thread_multiple", 4, "THREAD PASS");
}

#[test]
fn bigmsg_2() {
    run_example("bigmsg", 2, "BIGMSG PASS");
}
#[test]
fn bigmsg_4() {
    run_example("bigmsg", 4, "BIGMSG PASS");
}
