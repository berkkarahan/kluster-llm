//! OSU-style micro-benchmark: point-to-point latency and bandwidth between
//! ranks 0 and 1, plus an all-reduce timing across all ranks (to show the
//! `O(log n)` collectives). Run with e.g. `mpiexec -n 2 ./pingpong`.

use mpi::collective::SystemOperation;
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    if size >= 2 {
        let latency_iters = 2000;
        let bw_iters = 100;
        let bw_bytes = 1 << 20; // 1 MiB

        if rank == 0 {
            // ---- latency: 8-byte round trip ----
            let small = [0u8; 8];
            // warm up
            for _ in 0..100 {
                world.process_at_rank(1).send(&small[..]);
                let _ = world.process_at_rank(1).receive_vec::<u8>();
            }
            let t0 = mpi::time();
            for _ in 0..latency_iters {
                world.process_at_rank(1).send(&small[..]);
                let _ = world.process_at_rank(1).receive_vec::<u8>();
            }
            let dt = mpi::time() - t0;
            let lat_us = dt / latency_iters as f64 / 2.0 * 1e6;
            println!("latency:   {lat_us:.2} µs (8 B, half round-trip, {latency_iters} iters)");

            // ---- bandwidth: 1 MiB round trip ----
            let big = vec![0u8; bw_bytes];
            let t0 = mpi::time();
            for _ in 0..bw_iters {
                world.process_at_rank(1).send(&big[..]);
                let _ = world.process_at_rank(1).receive_vec::<u8>();
            }
            let dt = mpi::time() - t0;
            let mb = bw_bytes as f64 * bw_iters as f64 * 2.0 / 1e6;
            println!("bandwidth: {:.1} MB/s (1 MiB, {bw_iters} iters)", mb / dt);
        } else if rank == 1 {
            let small = [0u8; 8];
            for _ in 0..latency_iters + 100 {
                let _ = world.process_at_rank(0).receive_vec::<u8>();
                world.process_at_rank(0).send(&small[..]);
            }
            for _ in 0..bw_iters {
                let (v, _) = world.process_at_rank(0).receive_vec::<u8>();
                world.process_at_rank(0).send(&v[..]);
            }
        }
    }

    // ---- collective timing: all-reduce over all ranks ----
    world.barrier();
    let coll_iters = 1000;
    let x = rank;
    let mut acc = 0i32;
    let t0 = mpi::time();
    for _ in 0..coll_iters {
        world.all_reduce_into(&x, &mut acc, SystemOperation::sum());
    }
    let dt = mpi::time() - t0;
    if rank == 0 {
        println!(
            "all_reduce: {:.2} µs/op over {size} ranks ({coll_iters} iters), result {acc}",
            dt / coll_iters as f64 * 1e6
        );
    }
    world.barrier();
}
