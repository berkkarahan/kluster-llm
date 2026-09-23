//! Stress test for `MPI_THREAD_MULTIPLE`: every rank runs several threads that
//! concurrently perform tagged point-to-point exchanges. Verifies the transport
//! (connection writes, inbox matching) is safe under concurrent MPI calls.

use mpi::traits::*;
use std::thread;

const THREADS: i32 = 8;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    let next = (rank + 1) % size;
    let prev = (rank - 1 + size) % size;

    let handles: Vec<_> = (0..THREADS)
        .map(|t| {
            let w = world.clone(); // SimpleCommunicator is Send + Sync (Arc)
            thread::spawn(move || {
                let tag = 1000 + t;
                let msg = [rank * 100 + t, rank, t];
                // Concurrent send + receive on this thread's own tag.
                w.process_at_rank(next).send_with_tag(&msg[..], tag);
                let (got, status) = w.process_at_rank(prev).receive_vec_with_tag::<i32>(tag);
                assert_eq!(status.tag(), tag, "tag mismatch");
                assert_eq!(
                    got,
                    vec![prev * 100 + t, prev, t],
                    "thread {t} payload mismatch"
                );
            })
        })
        .collect();

    for h in handles {
        h.join().expect("thread panicked");
    }

    world.barrier();
    if rank == 0 {
        println!(
            "THREAD PASS: {THREADS} threads/rank did concurrent tagged exchanges on {size} ranks."
        );
    }
}
