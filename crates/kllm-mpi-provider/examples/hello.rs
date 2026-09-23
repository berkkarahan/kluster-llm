//! The classic MPI hello-world: every process prints its rank and the world
//! size. Run with e.g. `mpiexec -n 4 ./target/debug/examples/hello`.

use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let size = world.size();
    let rank = world.rank();
    let processor = mpi::processor_name().unwrap_or_else(|_| "unknown".into());

    println!("Hello from rank {rank} of {size} on {processor}");

    // Make sure the output is interleaved cleanly before exit.
    world.barrier();
    if rank == 0 {
        println!("All {size} processes checked in.");
    }
}
