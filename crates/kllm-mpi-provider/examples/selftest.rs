//! End-to-end self test: exercises point-to-point, probes, every collective,
//! reductions (built-in and user-defined), communicator split/dup and groups,
//! asserting correctness. Exits non-zero (via panic/abort) on any failure.
//!
//! Run with e.g. `mpiexec -n 4 ./target/debug/examples/selftest`.

use mpi::collective::{SystemOperation, UserOperation};
use mpi::datatype::{Equivalence, Partition, PartitionMut};
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    // ---- point-to-point: typed scalar ----
    if size >= 2 {
        if rank == 0 {
            world.process_at_rank(1).send(&42u64);
        } else if rank == 1 {
            let (v, status) = world.process_at_rank(0).receive::<u64>();
            assert_eq!(v, 42);
            assert_eq!(status.source_rank(), 0);
        }
    }

    // ---- point-to-point: vector + probe ----
    if size >= 2 {
        if rank == 0 {
            let data: Vec<i32> = vec![10, 20, 30, 40, 50];
            world.process_at_rank(1).send(&data[..]);
        } else if rank == 1 {
            let status = world.process_at_rank(0).probe();
            let count = status.count(i32::equivalent_datatype());
            assert_eq!(count, 5, "probe reported wrong count");
            let (data, _s) = world.process_at_rank(0).receive_vec::<i32>();
            assert_eq!(data, vec![10, 20, 30, 40, 50]);
        }
    }

    // ---- send_receive ring ----
    {
        let next = (rank + 1) % size;
        let prev = (rank - 1 + size) % size;
        let (got, _status): (i32, _) = mpi::point_to_point::send_receive(
            &rank,
            &world.process_at_rank(next),
            &world.process_at_rank(prev),
        );
        assert_eq!(got, prev, "send_receive ring mismatch");
    }

    // ---- broadcast ----
    {
        let root = world.process_at_rank(0);
        let mut buf = if rank == 0 {
            vec![2, 4, 8, 16]
        } else {
            vec![0; 4]
        };
        root.broadcast_into(&mut buf[..]);
        assert_eq!(buf, vec![2, 4, 8, 16], "broadcast mismatch");
    }

    // ---- scatter ----
    {
        let root = world.process_at_rank(0);
        let mut mine = 0i32;
        if rank == 0 {
            let send: Vec<i32> = (0..size).collect();
            root.scatter_into_root(&send[..], &mut mine);
        } else {
            root.scatter_into(&mut mine);
        }
        assert_eq!(mine, rank, "scatter mismatch");
    }

    // ---- gather ----
    {
        let root = world.process_at_rank(0);
        if rank == 0 {
            let mut buf = vec![-1i32; size as usize];
            root.gather_into_root(&rank, &mut buf[..]);
            let expected: Vec<i32> = (0..size).collect();
            assert_eq!(buf, expected, "gather mismatch");
        } else {
            root.gather_into(&rank);
        }
    }

    // ---- all_gather ----
    {
        let mut buf = vec![-1i32; size as usize];
        world.all_gather_into(&rank, &mut buf[..]);
        let expected: Vec<i32> = (0..size).collect();
        assert_eq!(buf, expected, "all_gather mismatch");
    }

    // ---- all_to_all ----
    {
        let send: Vec<i32> = (0..size).map(|j| rank * 100 + j).collect();
        let mut recv = vec![-1i32; size as usize];
        world.all_to_all_into(&send[..], &mut recv[..]);
        for i in 0..size {
            assert_eq!(recv[i as usize], i * 100 + rank, "all_to_all mismatch");
        }
    }

    // ---- reduce to root (sum) ----
    {
        let root = world.process_at_rank(0);
        if rank == 0 {
            let mut sum = 0i32;
            root.reduce_into_root(&rank, &mut sum, SystemOperation::sum());
            assert_eq!(sum, (0..size).sum::<i32>(), "reduce sum mismatch");
        } else {
            root.reduce_into(&rank, SystemOperation::sum());
        }
    }

    // ---- all_reduce (max) ----
    {
        let mut m = 0i32;
        world.all_reduce_into(&rank, &mut m, SystemOperation::max());
        assert_eq!(m, size - 1, "all_reduce max mismatch");
    }

    // ---- inclusive scan ----
    {
        let mut acc = 0i32;
        world.scan_into(&rank, &mut acc, SystemOperation::sum());
        let expected: i32 = (0..=rank).sum();
        assert_eq!(acc, expected, "scan mismatch");
    }

    // ---- exclusive scan ----
    {
        let mut acc = -1i32;
        world.exclusive_scan_into(&rank, &mut acc, SystemOperation::sum());
        if rank > 0 {
            let expected: i32 = (0..rank).sum();
            assert_eq!(acc, expected, "exclusive_scan mismatch");
        }
    }

    // ---- reduce_scatter_block ----
    {
        let send: Vec<i32> = vec![rank + 1; size as usize];
        let mut recv = [0i32; 1];
        world.reduce_scatter_block_into(&send[..], &mut recv[..], SystemOperation::sum());
        let expected: i32 = (0..size).map(|k| k + 1).sum();
        assert_eq!(recv[0], expected, "reduce_scatter_block mismatch");
    }

    // ---- user-defined operation (sum) matches built-in ----
    {
        let op = UserOperation::commutative(|inv: &[i32], inout: &mut [i32]| {
            for (i, o) in inv.iter().zip(inout.iter_mut()) {
                *o += *i;
            }
        });
        let mut u = 0i32;
        world.all_reduce_into(&rank, &mut u, op);
        assert_eq!(u, (0..size).sum::<i32>(), "user op mismatch");
    }

    // ---- communicator split by parity ----
    {
        let color = mpi::topology::Color::with_value(rank % 2);
        let sub = world.split_by_color(color).expect("split produced None");
        let expected_size = (0..size).filter(|r| r % 2 == rank % 2).count() as i32;
        assert_eq!(sub.size(), expected_size, "split size mismatch");
        // Ranks in the sub-communicator sum to a known value.
        let mut s = 0i32;
        sub.all_reduce_into(&sub.rank(), &mut s, SystemOperation::sum());
        let expected_sum: i32 = (0..sub.size()).sum();
        assert_eq!(s, expected_sum, "split all_reduce mismatch");
    }

    // ---- communicator duplicate ----
    {
        let dup = world.duplicate();
        assert_eq!(dup.size(), size);
        assert_eq!(dup.rank(), rank);
        dup.barrier();
    }

    // ---- group ----
    {
        let g = world.group();
        assert_eq!(g.size(), size, "group size mismatch");
        assert_eq!(g.rank(), Some(rank), "group rank mismatch");
    }

    // ---- group set algebra ----
    {
        let g = world.group();
        let evens: Vec<i32> = (0..size).filter(|r| r % 2 == 0).collect();
        let odds: Vec<i32> = (0..size).filter(|r| r % 2 == 1).collect();
        let ge = g.include(&evens);
        let go = g.include(&odds);
        assert_eq!(ge.size() + go.size(), size, "group split size mismatch");
        assert_eq!(ge.union(&go).size(), size, "group union mismatch");
        assert_eq!(
            ge.intersection(&go).size(),
            0,
            "group intersection mismatch"
        );
    }

    // ---- varying-count all-gather (allgatherv) ----
    {
        let counts: Vec<i32> = (0..size).map(|i| i + 1).collect();
        let mut displs = vec![0i32; size as usize];
        for i in 1..size as usize {
            displs[i] = displs[i - 1] + counts[i - 1];
        }
        let total: i32 = counts.iter().sum();
        let send = vec![rank; (rank + 1) as usize];
        let mut recvbuf = vec![-1i32; total as usize];
        {
            let mut part = PartitionMut::new(&mut recvbuf[..], counts.clone(), displs.clone());
            world.all_gather_varcount_into(&send[..], &mut part);
        }
        for i in 0..size as usize {
            let start = displs[i] as usize;
            for k in 0..counts[i] as usize {
                assert_eq!(recvbuf[start + k], i as i32, "allgatherv mismatch");
            }
        }
    }

    // ---- varying-count all-to-all (alltoallv) ----
    {
        let counts: Vec<i32> = vec![1; size as usize];
        let displs: Vec<i32> = (0..size).collect();
        let send: Vec<i32> = (0..size).map(|j| rank * 10 + j).collect();
        let mut recv = vec![-1i32; size as usize];
        {
            let sp = Partition::new(&send[..], counts.clone(), displs.clone());
            let mut rp = PartitionMut::new(&mut recv[..], counts.clone(), displs.clone());
            world.all_to_all_varcount_into(&sp, &mut rp);
        }
        for i in 0..size {
            assert_eq!(recv[i as usize], i * 10 + rank, "alltoallv mismatch");
        }
    }

    // ---- Cartesian topology (1-D periodic ring) ----
    {
        let cart = world
            .create_cartesian_communicator(&[size], &[true], false)
            .expect("cartesian create returned None");
        assert_eq!(cart.my_coordinates(), vec![rank], "cart coords mismatch");
        let (src, dst) = cart.shift(0, 1);
        assert_eq!(dst, Some((rank + 1) % size), "cart shift dest mismatch");
        assert_eq!(
            src,
            Some((rank - 1 + size) % size),
            "cart shift source mismatch"
        );
        assert_eq!(cart.rank_from_coordinates(&[0]), Some(0));
        cart.barrier();
    }

    // ---- error handlers ----
    {
        use mpi::error::{class, error_string, ErrorHandler};
        world.set_error_handler(ErrorHandler::Return);
        assert_eq!(
            world.error_handler(),
            ErrorHandler::Return,
            "errhandler mismatch"
        );
        world.set_error_handler(ErrorHandler::Fatal);
        assert_eq!(world.error_handler(), ErrorHandler::Fatal);
        assert!(!error_string(class::TRUNCATE).is_empty());
    }

    // ---- generalized request (completed from another thread) ----
    {
        use mpi::request::GeneralizedRequest;
        let (req, completer) = GeneralizedRequest::start();
        let handle = std::thread::spawn(move || completer.complete());
        let _status = req.wait();
        handle.join().unwrap();
    }

    // ---- pack / unpack ----
    {
        let val = vec![rank, rank * 2, rank * 3];
        let packed = world.pack(&val[..]);
        let mut out = vec![0i32; 3];
        let pos = unsafe { world.unpack_into(&packed, &mut out[..], 0) };
        assert_eq!(out, val, "pack/unpack mismatch");
        assert_eq!(pos as usize, packed.len(), "unpack position mismatch");
    }

    // ---- communicator naming ----
    {
        assert_eq!(world.get_name(), "MPI_COMM_WORLD", "comm name mismatch");
        let d = world.duplicate();
        d.set_name("dup");
        assert_eq!(d.get_name(), "dup", "set/get name mismatch");
    }

    // ---- non-blocking all-reduce + barrier ----
    {
        let send = rank;
        let mut recv = 0i32;
        mpi::request::scope(|s| {
            world
                .immediate_all_reduce_into(s, &send, &mut recv, SystemOperation::sum())
                .wait();
        });
        assert_eq!(
            recv,
            (0..size).sum::<i32>(),
            "immediate all_reduce mismatch"
        );
        world.immediate_barrier().wait();
    }

    // ---- non-blocking broadcast (Root) ----
    {
        let root = world.process_at_rank(0);
        let mut buf = if rank == 0 {
            vec![7, 7, 7]
        } else {
            vec![0, 0, 0]
        };
        mpi::request::scope(|s| {
            root.immediate_broadcast_into(s, &mut buf[..]).wait();
        });
        assert_eq!(buf, vec![7, 7, 7], "immediate broadcast mismatch");
    }

    // ---- in-place all-reduce (MPI_IN_PLACE) ----
    {
        let mut b = vec![rank, rank + 1];
        world.all_reduce_into_in_place(&mut b[..], SystemOperation::sum());
        let s: i32 = (0..size).sum();
        assert_eq!(b, vec![s, s + size], "in-place all_reduce mismatch");
    }

    // ---- persistent send/receive, reused across iterations ----
    {
        let next = (rank + 1) % size;
        let prev = (rank - 1 + size) % size;
        let sendbuf = [rank, rank * 2];
        let mut recvbuf = vec![-1i32; 2];
        {
            let mut sreq = world.process_at_rank(next).send_init(&sendbuf[..]);
            let mut rreq = world.process_at_rank(prev).receive_init(&mut recvbuf[..]);
            for _ in 0..3 {
                rreq.start();
                sreq.start();
                sreq.wait();
                rreq.wait();
            }
        } // requests dropped -> buffer borrows released
        assert_eq!(recvbuf, vec![prev, prev * 2], "persistent request mismatch");
    }

    world.barrier();
    if rank == 0 {
        println!("SELFTEST PASS: all checks passed on {size} ranks.");
    }
}
