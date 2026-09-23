//! Parallel I/O self test: each rank writes its own block at a distinct offset;
//! rank 0 reads the whole file back and verifies. Exits non-zero on mismatch.

use mpi::io::{File, MODE_CREATE, MODE_DELETE_ON_CLOSE, MODE_RDWR};
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    // Path depends on world size so concurrent jobs of different sizes don't
    // collide; identical across the ranks of a single job.
    let path = std::env::temp_dir().join(format!("mpi_no_c_io_{size}.bin"));
    let mut f = File::open(
        &world,
        &path,
        MODE_CREATE | MODE_RDWR | MODE_DELETE_ON_CLOSE,
    )
    .expect("File::open failed");

    let block = [rank * 100, rank * 100 + 1, rank * 100 + 2, rank * 100 + 3];
    f.write_at_all(rank as u64 * 16, &block)
        .expect("write failed");

    if rank == 0 {
        for r in 0..size {
            let mut buf = [0i32; 4];
            f.read_at(r as u64 * 16, &mut buf).expect("read failed");
            assert_eq!(
                buf,
                [r * 100, r * 100 + 1, r * 100 + 2, r * 100 + 3],
                "I/O block mismatch"
            );
        }
        println!("IO PASS: parallel write/read verified on {size} ranks.");
    }

    world.barrier();
    // File is deleted on close (drop).
}
