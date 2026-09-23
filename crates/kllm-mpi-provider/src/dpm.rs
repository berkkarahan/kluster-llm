//! Dynamic process management: `MPI_Comm_spawn`.
//!
//! The root of the spawning communicator launches `maxprocs` child processes,
//! serves their world bootstrap (a PMI rendezvous, like `mpiexec`), and hands
//! each child a "parent block" describing the spawning group. Both sides then
//! build an [`InterCommunicator`] whose remote group lives in the other world;
//! cross-world traffic is routed through per-context peer tables registered on
//! the transport.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::process::{Command, Stdio};

use crate::collective::bcast_bytes;
use crate::topology::{CommData, InterCommunicator};
use crate::transport;
use crate::{MpiError, Rank};

fn read_line(stream: &mut TcpStream) -> std::io::Result<String> {
    let mut buf = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        let n = stream.read(&mut byte)?;
        if n == 0 || byte[0] == b'\n' {
            break;
        }
        buf.push(byte[0]);
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

/// Broadcast a variable-length byte vector from `root` over `comm`
/// (length-prefixed, two `bcast` phases).
fn broadcast_vec(comm: &CommData, root: Rank, data: Vec<u8>) -> Vec<u8> {
    let mut lenb = (data.len() as u64).to_le_bytes();
    let _ = bcast_bytes(comm, root, &mut lenb);
    let len = u64::from_le_bytes(lenb) as usize;
    let mut blob = if comm.rank == root {
        data
    } else {
        vec![0u8; len]
    };
    let _ = bcast_bytes(comm, root, &mut blob);
    blob
}

/// Collective implementation of `MPI_Comm_spawn`.
pub(crate) fn spawn_impl(
    comm: &CommData,
    root: Rank,
    program: &str,
    args: &[String],
    maxprocs: Rank,
) -> Result<InterCommunicator, MpiError> {
    let rt = transport::runtime();
    // A fresh context, agreed by all members of the spawning communicator.
    let ictx = comm.derive_context(0x5350_4157); // "SPAW"

    let children_blob = if comm.rank == root {
        let addrs = root_launch(rt, comm, program, args, maxprocs, ictx)?;
        let mut blob = String::new();
        for a in &addrs {
            blob.push_str(&format!("{a}\n"));
        }
        blob.into_bytes()
    } else {
        Vec::new()
    };

    // Distribute the children's addresses to every member of the spawning comm.
    let blob = broadcast_vec(comm, root, children_blob);
    let children: Vec<SocketAddr> = String::from_utf8_lossy(&blob)
        .lines()
        .filter_map(|l| l.trim().parse().ok())
        .collect();

    rt.register_context_peers(ictx, children.clone());
    Ok(InterCommunicator::new_spawned(
        ictx,
        comm.rank,
        comm_world_ranks(comm),
        children.len(),
    ))
}

fn comm_world_ranks(comm: &CommData) -> Vec<i32> {
    (0..comm.size).map(|r| comm.world_rank(r)).collect()
}

/// Root side: launch the children, serve their world PMI, and send each the
/// parent block. Returns the children's data-plane addresses.
fn root_launch(
    rt: &transport::Runtime,
    comm: &CommData,
    program: &str,
    args: &[String],
    maxprocs: Rank,
    ictx: u32,
) -> Result<Vec<SocketAddr>, MpiError> {
    let bootstrap = |m: String| MpiError::Bootstrap(m);

    let my_addr = rt.my_addr();
    let multihost = my_addr.map(|a| !a.ip().is_loopback()).unwrap_or(false);
    let my_ip = my_addr
        .map(|a| a.ip().to_string())
        .unwrap_or_else(|| "127.0.0.1".to_string());
    let bind_ip = if multihost { "0.0.0.0" } else { "127.0.0.1" };

    let listener =
        TcpListener::bind((bind_ip, 0)).map_err(|e| bootstrap(format!("spawn bind: {e}")))?;
    let sport = listener
        .local_addr()
        .map_err(|e| bootstrap(format!("spawn local_addr: {e}")))?
        .port();
    let saddr = format!("{my_ip}:{sport}");
    // A job id shared by all spawned children (distinct from other jobs) so
    // their data-plane connection namespaces remain distinct.
    let child_jobid = (std::process::id() as u64).wrapping_mul(1_000_003) ^ (ictx as u64);

    for i in 0..maxprocs {
        let mut cmd = Command::new(program);
        cmd.args(args)
            .env("MPI_PMI_ROOT", &saddr)
            .env("MPI_PMI_RANK", i.to_string())
            .env("MPI_PMI_SIZE", maxprocs.to_string())
            .env("MPI_SPAWN", "1")
            .env("MPI_JOBID", child_jobid.to_string())
            .stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit());
        if multihost {
            cmd.env("MPI_MULTIHOST", "1");
        }
        cmd.spawn()
            .map_err(|e| bootstrap(format!("spawn '{program}': {e}")))?;
    }

    // Rendezvous: collect the children's addresses.
    let mut conns: Vec<Option<TcpStream>> = (0..maxprocs).map(|_| None).collect();
    let mut table: Vec<Option<SocketAddr>> = vec![None; maxprocs as usize];
    for _ in 0..maxprocs {
        let (mut stream, _) = listener
            .accept()
            .map_err(|e| bootstrap(format!("spawn accept: {e}")))?;
        let line = read_line(&mut stream).map_err(|e| bootstrap(format!("spawn read: {e}")))?;
        let mut parts = line.split_whitespace();
        let r: usize = parts
            .next()
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| bootstrap("bad spawn PMI line".into()))?;
        let a: SocketAddr = parts
            .next()
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| bootstrap("bad spawn PMI addr".into()))?;
        table[r] = Some(a);
        conns[r] = Some(stream);
    }
    let children: Vec<SocketAddr> = table
        .into_iter()
        .map(|a| a.ok_or_else(|| bootstrap("missing spawn child addr".into())))
        .collect::<Result<_, _>>()?;

    // Parent-group addresses (this spawning communicator's members).
    let parent_addrs: Vec<SocketAddr> = (0..comm.size)
        .filter_map(|r| rt.peer_addr(comm.world_rank(r)))
        .collect();

    // Send each child the world table and the parent block.
    let mut world_blob = String::new();
    for (r, a) in children.iter().enumerate() {
        world_blob.push_str(&format!("{r} {a}\n"));
    }
    let mut parent_blob = format!("PARENT {ictx} {}\n", parent_addrs.len());
    for a in &parent_addrs {
        parent_blob.push_str(&format!("{a}\n"));
    }
    for conn in conns.iter_mut().flatten() {
        conn.write_all(world_blob.as_bytes())
            .map_err(|e| bootstrap(format!("spawn write: {e}")))?;
        conn.write_all(parent_blob.as_bytes())
            .map_err(|e| bootstrap(format!("spawn write parent: {e}")))?;
        conn.flush().ok();
    }

    Ok(children)
}
