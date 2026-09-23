//! One-sided (RMA) self test: `put`, `get`, and `accumulate` over [`Window`]s.
//! Exits non-zero on any mismatch.

use mpi::collective::SystemOperation;
use mpi::traits::*;
use mpi::window::Window;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    // ---- put: rank 0 writes distinct data into every other rank's window ----
    let win = Window::<i32>::allocate(4, &world);
    win.fence();
    if rank == 0 {
        for r in 1..size {
            win.put(r, 0, &[r * 10, r * 10 + 1, r * 10 + 2, r * 10 + 3]);
        }
        win.with_local_mut(|m| {
            for (i, v) in m.iter_mut().enumerate() {
                *v = i as i32;
            }
        });
    }
    win.fence();
    if rank != 0 {
        win.with_local(|m| {
            assert_eq!(
                m,
                [rank * 10, rank * 10 + 1, rank * 10 + 2, rank * 10 + 3],
                "put mismatch"
            );
        });
    }
    win.fence();

    // ---- get: every rank reads rank 0's window (0,1,2,3) ----
    if rank != 0 {
        let mut buf = [0i32; 4];
        win.get(0, 0, &mut buf);
        assert_eq!(buf, [0, 1, 2, 3], "get mismatch");
    }
    win.fence();

    // ---- accumulate: every rank sums [1;4] into rank 0's window ----
    let win2 = Window::<i32>::allocate(4, &world);
    win2.fence();
    win2.accumulate(0, 0, &[1, 1, 1, 1], SystemOperation::sum());
    win2.fence();
    if rank == 0 {
        win2.with_local(|m| assert_eq!(m, [size, size, size, size], "accumulate mismatch"));
    }
    win2.fence();

    if rank == 0 {
        println!("RMA PASS: put/get/accumulate verified on {size} ranks.");
    }
}
