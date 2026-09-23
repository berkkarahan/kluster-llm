//! Reserved rank-process entrypoint for structured KLLM control-plane launch.
//!
//! It intentionally rejects direct invocation until R1 supplies the authenticated
//! `MpiJobSpec` handoff. It never executes user-provided commands or accepts a
//! host, environment, port, or raw launcher argument.

use std::process::ExitCode;

fn main() -> ExitCode {
    eprintln!(
        "kllm-mpi-rank: direct invocation is disabled; use an authenticated KLLM control-plane launch"
    );
    ExitCode::from(2)
}
