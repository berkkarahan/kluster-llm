//! Demonstrates `#[derive(Equivalence)]` for sending a POD struct directly.
//! Build with the `derive` feature:
//!
//! ```text
//! cargo build --example derive --features derive
//! mpiexec -n 2 ./target/debug/examples/derive
//! ```

use mpi::traits::*;
use mpi::Equivalence;

#[derive(Clone, Copy, Debug, PartialEq, Equivalence)]
#[repr(C)]
struct Particle {
    x: f64,
    y: f64,
    z: f64,
    id: u64,
}

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    if size >= 2 {
        if rank == 0 {
            let p = Particle {
                x: 1.0,
                y: 2.0,
                z: 3.0,
                id: 42,
            };
            world.process_at_rank(1).send(&p);
            println!("rank 0 sent {p:?}");
        } else if rank == 1 {
            let (p, _status) = world.process_at_rank(0).receive::<Particle>();
            println!("rank 1 received {p:?}");
            assert_eq!(
                p,
                Particle {
                    x: 1.0,
                    y: 2.0,
                    z: 3.0,
                    id: 42
                }
            );
        }
    }

    world.barrier();
    if rank == 0 {
        println!("derive(Equivalence) round-trip OK");
    }
}
