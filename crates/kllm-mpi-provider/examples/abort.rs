//! Demonstrates that `MPI_Abort` (and panics) tear the whole job down instead
//! of leaving peers blocked. Rank 1 waits forever in a receive; rank 0 aborts.
//! Every rank should exit promptly (non-zero), never hang.

use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();

    if world.rank() == 1 {
        // Would block forever — but the abort from rank 0 unblocks us by
        // terminating the process.
        let _ = world.process_at_rank(0).receive::<i32>();
        eprintln!("rank 1 should never reach here");
    } else if world.rank() == 0 {
        eprintln!("rank 0 calling abort");
        world.abort(7);
    }
}
