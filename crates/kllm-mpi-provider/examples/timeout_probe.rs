//! Test-only rank process used to verify launcher timeout cleanup.
//!
//! Each rank records its own PID at a path supplied by the integration test,
//! then blocks long enough for that test to terminate the owning launcher tree.

use std::env;
use std::fs;
use std::thread;
use std::time::Duration;

fn main() {
    let base = env::var("KLLM_MPI_TIMEOUT_PROBE_BASE")
        .expect("KLLM_MPI_TIMEOUT_PROBE_BASE is required by the integration test");
    let rank = env::var("MPI_PMI_RANK").expect("MPI_PMI_RANK is required by mpiexec");
    fs::write(format!("{base}.{rank}"), std::process::id().to_string())
        .expect("write timeout-probe PID");
    thread::sleep(Duration::from_secs(60));
}
