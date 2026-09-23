//! Multi-host demo: prints each rank's OS/arch (to show heterogeneity) and
//! exercises real cross-node communication — a send/receive ring, an
//! all-reduce, and a broadcast.
//!
//! ```text
//! mpiexec -n 2 --host localhost,msi --remote-bin /tmp/cluster ./target/debug/examples/cluster
//! ```

use mpi::collective::SystemOperation;
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    println!(
        "rank {rank}/{size}: os={} arch={}",
        std::env::consts::OS,
        std::env::consts::ARCH
    );

    // Ring send/receive across nodes.
    let next = (rank + 1) % size;
    let prev = (rank - 1 + size) % size;
    let msg = rank * 1000;
    let (got, status): (i32, _) = mpi::point_to_point::send_receive(
        &msg,
        &world.process_at_rank(next),
        &world.process_at_rank(prev),
    );
    assert_eq!(got, prev * 1000, "ring mismatch");
    assert_eq!(status.source_rank(), prev);

    // All-reduce sum of ranks.
    let mut sum = 0i32;
    world.all_reduce_into(&rank, &mut sum, SystemOperation::sum());
    assert_eq!(sum, (0..size).sum::<i32>(), "allreduce mismatch");

    // Broadcast from rank 0.
    let mut buf = if rank == 0 {
        vec![42i32; 3]
    } else {
        vec![0; 3]
    };
    world.process_at_rank(0).broadcast_into(&mut buf[..]);
    assert_eq!(buf, vec![42, 42, 42], "broadcast mismatch");

    world.barrier();
    println!("rank {rank}: cross-node comm OK (sum of ranks = {sum})");
    if rank == 0 {
        println!("CLUSTER PASS: {size} ranks communicated across hosts.");
    }
}
