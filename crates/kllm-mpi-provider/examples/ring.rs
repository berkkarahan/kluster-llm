//! Non-blocking ring exchange, adapted from the rsmpi README: each rank sends a
//! message to its successor while receiving from its predecessor.

use mpi::request::WaitGuard;
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let size = world.size();
    let rank = world.rank();

    let next_rank = (rank + 1) % size;
    let previous_rank = (rank - 1 + size) % size;

    let msg = [rank, 2 * rank, 4 * rank];
    mpi::request::scope(|scope| {
        let _sreq = WaitGuard::from(
            world
                .process_at_rank(next_rank)
                .immediate_send(scope, &msg[..]),
        );

        let (msg, status) = world.any_process().receive_vec::<i32>();

        println!(
            "Rank {rank} received {msg:?} from rank {} (tag {}).",
            status.source_rank(),
            status.tag()
        );
        assert_eq!(status.source_rank(), previous_rank);
        let x = previous_rank;
        assert_eq!(vec![x, 2 * x, 4 * x], msg);
    });

    world.barrier();
    if rank == 0 {
        println!("Ring exchange completed successfully.");
    }
}
