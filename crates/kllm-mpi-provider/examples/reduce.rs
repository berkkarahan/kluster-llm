//! Reduction examples: reduce-to-root and all-reduce with built-in operations.

use mpi::collective::SystemOperation;
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();
    let root_rank = 0;
    let root = world.process_at_rank(root_rank);

    // Sum of all ranks lands on the root.
    if rank == root_rank {
        let mut sum = 0i32;
        root.reduce_into_root(&rank, &mut sum, SystemOperation::sum());
        let expected = (0..size).sum::<i32>();
        println!("Sum of ranks = {sum} (expected {expected})");
        assert_eq!(sum, expected);
    } else {
        root.reduce_into(&rank, SystemOperation::sum());
    }

    // Every rank learns the maximum rank via all-reduce.
    let mut max = 0i32;
    world.all_reduce_into(&rank, &mut max, SystemOperation::max());
    assert_eq!(max, size - 1);

    // Element-wise all-reduce over a vector.
    let local = [rank as f64, (rank * rank) as f64];
    let mut total = vec![0.0f64; 2];
    world.all_reduce_into(&local[..], &mut total[..], SystemOperation::sum());
    let expected0: f64 = (0..size).map(|r| r as f64).sum();
    let expected1: f64 = (0..size).map(|r| (r * r) as f64).sum();
    assert!((total[0] - expected0).abs() < 1e-9);
    assert!((total[1] - expected1).abs() < 1e-9);

    world.barrier();
    if rank == root_rank {
        println!("Reductions verified. vector all-reduce = {total:?}");
    }
}
