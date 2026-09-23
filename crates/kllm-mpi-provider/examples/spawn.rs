//! Dynamic process spawning (`MPI_Comm_spawn`). Run the parent as a normal MPI
//! job (or singleton); it spawns child processes of the same binary. A child
//! detects it was spawned via `world.parent()`.
//!
//! ```text
//! ./target/debug/examples/spawn          # singleton parent spawns 3 children
//! mpiexec -n 2 ./target/debug/examples/spawn   # 2 parents collectively spawn
//! ```

use mpi::traits::*;

const N_CHILDREN: i32 = 3;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();

    if let Some(parent) = world.parent() {
        // ---- child side ----
        let rank = world.rank();
        // Receive a value from parent rank 0, send it back incremented.
        let (val, status) = parent.process_at_rank(0).receive::<i32>();
        assert_eq!(status.source_rank(), 0);
        parent.process_at_rank(0).send(&(val + 1));
        println!(
            "child {rank}/{} (parent group size {}) got {val}",
            world.size(),
            parent.remote_size()
        );
    } else {
        // ---- parent side ----
        let exe = std::env::current_exe().unwrap();
        let inter = world
            .process_at_rank(0)
            .spawn(exe.to_str().unwrap(), &[], N_CHILDREN)
            .expect("spawn failed");

        assert_eq!(inter.remote_size(), N_CHILDREN, "wrong child count");

        if world.rank() == 0 {
            for c in 0..inter.remote_size() {
                inter.process_at_rank(c).send(&(100 + c));
                let (reply, _) = inter.process_at_rank(c).receive::<i32>();
                assert_eq!(reply, 100 + c + 1, "child reply mismatch");
            }
            println!(
                "SPAWN PASS: parent (size {}) round-tripped with {} spawned children.",
                world.size(),
                inter.remote_size()
            );
        }
        world.barrier();
    }
}
