//! Exercises the large-message rendezvous path: multi-megabyte point-to-point
//! transfers (both directions) and a large all-reduce.

use mpi::collective::SystemOperation;
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    // ---- point-to-point: 4 MB round trip (well above the 64 KiB threshold) ----
    let n: i32 = 1_000_000;
    if size >= 2 {
        if rank == 0 {
            let data: Vec<i32> = (0..n).collect();
            world.process_at_rank(1).send(&data[..]);
            let (back, _) = world.process_at_rank(1).receive_vec::<i32>();
            assert_eq!(back.len(), n as usize, "echo length");
            assert_eq!(back[n as usize - 1], n - 1, "echo tail");
        } else if rank == 1 {
            let (data, _) = world.process_at_rank(0).receive_vec::<i32>();
            assert_eq!(data.len(), n as usize, "bigmsg length");
            assert_eq!(data[0], 0);
            assert_eq!(data[n as usize - 1], n - 1, "bigmsg content");
            world.process_at_rank(0).send(&data[..]);
        }
    }

    // ---- large all-reduce (400 KB per rank -> rendezvous inside the tree) ----
    {
        let local = vec![rank; 100_000];
        let mut sum = vec![0i32; 100_000];
        world.all_reduce_into(&local[..], &mut sum[..], SystemOperation::sum());
        let expected: i32 = (0..size).sum();
        assert!(
            sum.iter().all(|&x| x == expected),
            "big all_reduce mismatch"
        );
    }

    world.barrier();
    if rank == 0 {
        println!(
            "BIGMSG PASS: {} MB round-trip + large all-reduce via rendezvous on {size} ranks.",
            n * 4 / 1_000_000
        );
    }
}
