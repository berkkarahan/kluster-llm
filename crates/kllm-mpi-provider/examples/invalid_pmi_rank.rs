//! Test-only process that registers an invalid PMI rank and then blocks.
//!
//! The integration harness uses its PID marker to prove that launcher-side
//! bootstrap rejection reaps the direct child rather than leaving it alive.

use std::env;
use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::thread;
use std::time::Duration;

fn main() {
    let base = env::var("KLLM_MPI_INVALID_PMI_PROBE_BASE")
        .expect("KLLM_MPI_INVALID_PMI_PROBE_BASE is required by the integration test");
    let rank = env::var("MPI_PMI_RANK").unwrap_or_else(|_| "0".to_string());
    fs::write(format!("{base}.{rank}"), std::process::id().to_string())
        .expect("write invalid-PMI probe PID");

    let mode = env::var("KLLM_MPI_INVALID_PMI_PROBE_MODE").unwrap_or_default();
    if mode == "silent" {
        thread::sleep(Duration::from_secs(60));
        return;
    }
    if mode == "exit" {
        return;
    }

    let root = env::var("MPI_PMI_ROOT").expect("MPI_PMI_ROOT is required by mpiexec");
    let mut stream = TcpStream::connect(root).expect("connect to launcher PMI listener");
    if mode == "oversized" {
        stream
            .write_all(&vec![b'x'; 1025])
            .expect("send oversized PMI control line");
        stream
            .write_all(b"\n")
            .expect("terminate oversized PMI control line");
    } else if mode == "stalled" {
        stream
            .write_all(b"x")
            .expect("start stalled PMI control line");
    } else if mode == "duplicate" {
        stream
            .write_all(b"0 127.0.0.1:1 le\n")
            .expect("send duplicate PMI rank");
    } else if mode == "extra-fields" {
        stream
            .write_all(b"0 127.0.0.1:1 le unexpected\n")
            .expect("send PMI registration with extra fields");
    } else if mode == "valid-then-fail" {
        stream
            .write_all(format!("{rank} 127.0.0.1:1 le\n").as_bytes())
            .expect("send valid PMI registration");
    } else if mode == "non-loopback" {
        stream
            .write_all(b"0 192.0.2.1:1 le\n")
            .expect("send non-loopback PMI address");
    } else {
        stream
            .write_all(b"999 127.0.0.1:1 le\n")
            .expect("send invalid PMI rank");
    }
    stream.flush().expect("flush PMI probe registration");
    if mode == "valid-then-fail" {
        let expected_lines = env::var("MPI_PMI_SIZE")
            .expect("MPI_PMI_SIZE is required by mpiexec")
            .parse::<usize>()
            .expect("MPI_PMI_SIZE must be numeric");
        let mut lines = 0;
        let mut byte = [0_u8; 1];
        while lines < expected_lines {
            let read = stream.read(&mut byte).expect("read PMI address table");
            assert_eq!(read, 1, "launcher closed the PMI stream before its table");
            if byte[0] == b'\n' {
                lines += 1;
            }
        }
        if rank == "0" {
            std::process::exit(7);
        }
    }

    thread::sleep(Duration::from_secs(60));
}
