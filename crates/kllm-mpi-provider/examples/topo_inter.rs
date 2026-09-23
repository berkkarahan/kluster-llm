//! Self test for graph topologies and inter-communicators.

use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let rank = world.rank();
    let size = world.size();

    // ---- graph topology: a ring, node i adjacent to i-1 and i+1 ----
    if size >= 2 {
        let mut index = Vec::new();
        let mut edges = Vec::new();
        for i in 0..size {
            edges.push((i - 1 + size) % size);
            edges.push((i + 1) % size);
            index.push(2 * (i + 1));
        }
        let g = world
            .create_graph_communicator(&index, &edges)
            .expect("graph create returned None");
        let expected = vec![(rank - 1 + size) % size, (rank + 1) % size];
        assert_eq!(g.my_neighbors(), expected, "graph neighbours mismatch");
        assert_eq!(g.neighbor_count(rank), 2);
        // Neighbourhood all-gather: each neighbour contributes its own rank.
        let mut ng = vec![-1i32; 2];
        g.neighbor_all_gather_into(&rank, &mut ng[..]);
        assert_eq!(ng, expected, "neighbor_all_gather mismatch");
        g.barrier();
    }

    // ---- distributed-graph adjacent: directed ring (recv prev, send next) ----
    if size >= 2 {
        let prev = (rank - 1 + size) % size;
        let next = (rank + 1) % size;
        let dg = world.create_dist_graph_adjacent(&[prev], &[next]);
        assert_eq!(dg.in_degree(), 1, "dist-graph in-degree");
        assert_eq!(dg.out_degree(), 1, "dist-graph out-degree");
        let mut r = vec![-1i32; 1];
        dg.neighbor_all_gather_into(&rank, &mut r[..]);
        assert_eq!(r, vec![prev], "dist-graph neighbor mismatch");
        dg.barrier();
    }

    // ---- inter-communicator: split world into two halves ----
    if size >= 2 {
        let half = size / 2;
        let in_a = rank < half;
        let inter = world.split_intercommunicator(in_a);
        assert_eq!(
            inter.local_size() + inter.remote_size(),
            size,
            "intercomm size mismatch"
        );

        // Leaders of the two groups exchange their world ranks across the
        // inter-communicator.
        if inter.rank() == 0 {
            if in_a {
                inter.process_at_rank(0).send(&rank);
                let (peer, _): (i32, _) = inter.process_at_rank(0).receive();
                assert_eq!(peer, half, "intercomm exchange mismatch (A)");
            } else {
                let (peer, _): (i32, _) = inter.process_at_rank(0).receive();
                assert_eq!(peer, 0, "intercomm exchange mismatch (B)");
                inter.process_at_rank(0).send(&rank);
            }
        }

        // Merge back into one intra-communicator spanning everyone.
        let merged = inter.merge();
        assert_eq!(merged.size(), size, "merge size mismatch");
        merged.barrier();
    }

    world.barrier();
    if rank == 0 {
        println!("TOPO/INTER PASS: graph + inter-communicator verified on {size} ranks.");
    }
}
