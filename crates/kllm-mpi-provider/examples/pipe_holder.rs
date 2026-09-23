//! Test-only process that exits a rank while a descendant retains its inherited
//! stdout/stderr handles. It exercises the integration harness's post-exit
//! output-drain watchdog and owned-tree cleanup.

use std::env;
use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const CHILD_ENV: &str = "KLLM_MPI_PIPE_HOLDER_CHILD";
const PID_FILE_ENV: &str = "KLLM_MPI_PIPE_HOLDER_PID_FILE";
const READY_FILE_ENV: &str = "KLLM_MPI_PIPE_HOLDER_READY_FILE";
const READY_TIMEOUT: Duration = Duration::from_secs(2);

fn wait_for_ready_file(path: &Path) {
    let deadline = Instant::now() + READY_TIMEOUT;
    loop {
        if matches!(fs::read_to_string(path).as_deref(), Ok("ready")) {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "pipe-holder descendant did not become ready"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

#[allow(clippy::zombie_processes)]
fn spawn_pipe_holder_descendant() {
    // Intentional: the rank exits immediately so its child retains inherited
    // output handles. The integration harness must terminate/reap this tree.
    Command::new(env::current_exe().expect("locate pipe-holder executable"))
        .env(CHILD_ENV, "1")
        .stdin(Stdio::null())
        .spawn()
        .expect("spawn pipe-holder descendant");
}

fn main() {
    if env::var_os(CHILD_ENV).is_some() {
        let pid_file = env::var_os(PID_FILE_ENV).expect("pipe-holder child PID file must be set");
        let ready_file =
            env::var_os(READY_FILE_ENV).expect("pipe-holder child ready file must be set");
        fs::write(pid_file, std::process::id().to_string()).expect("publish pipe-holder child PID");
        fs::write(ready_file, "ready").expect("publish pipe-holder child readiness");
        thread::sleep(Duration::from_secs(5));
        return;
    }

    let _universe = mpi::initialize().expect("pipe-holder rank must bootstrap MPI");
    let ready_file = env::var_os(READY_FILE_ENV).expect("pipe-holder ready file must be set");
    spawn_pipe_holder_descendant();
    wait_for_ready_file(Path::new(&ready_file));
    println!(
        "PIPE_HOLDER_MPI_BOOTSTRAPPED rank exited while its descendant retains output handles"
    );
}
