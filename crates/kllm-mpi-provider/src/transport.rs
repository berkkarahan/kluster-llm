//! Internal networking runtime for pure-Rust MPI.
//!
//! This module is the equivalent of the "byte transfer layer" in a C MPI
//! implementation such as Open MPI or MPICH. It is responsible for:
//!
//! * bootstrapping the job (discovering our rank, the world size and the
//!   network address of every peer) via a tiny PMI-style protocol spoken to
//!   the `mpiexec` launcher, and
//! * moving tagged, typed byte buffers between processes over TCP with the
//!   ordering guarantees MPI requires (non-overtaking messages between a given
//!   sender/receiver pair on a communicator).
//!
//! Everything above this module (datatypes, point-to-point, collectives, …) is
//! implemented in pure Rust on top of the primitives exposed here.

use std::collections::{HashMap, VecDeque};
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::thread;
use std::time::Duration;

/// Wildcard matching any source rank (`MPI_ANY_SOURCE`).
pub const ANY_SOURCE: i32 = -1;
/// Wildcard matching any tag (`MPI_ANY_TAG`).
pub const ANY_TAG: i32 = -1;

const MAGIC: u32 = 0x4D50_4921; // "MPI!"
const HEADER_LEN: usize = 4 + 4 + 4 + 4 + 4 + 8 + 4 + 8; // 40 bytes

/// This host's byte order, reported during bootstrap. The wire format is fixed
/// little-endian for headers/control data; typed payloads are transcoded where
/// supported. Mixed-endian jobs warn because RMA and byte-transparent derived
/// structs require same-endian ranks.
#[cfg(target_endian = "big")]
const MY_ENDIAN: &str = "be";
#[cfg(target_endian = "little")]
const MY_ENDIAN: &str = "le";

/// Parent inter-communicator info handed to a spawned child:
/// `(inter-comm context, parent-group addresses)`.
pub type ParentBlock = (u32, Vec<SocketAddr>);

/// Reserved context for abort notifications. A message on this context makes
/// the receiving process exit, so one rank's failure tears the whole job down
/// instead of leaving peers blocked forever.
const ABORT_CONTEXT: u32 = 0x7AB0_0117;

/// Reserved context on which a receiver requests a large message (clear-to-send).
const RNDV_CTS_CTEXT: u32 = 0x7AB0_0C75;
/// Reserved context on which the actual large-message data is delivered.
const RNDV_DATA_CTEXT: u32 = 0x7AB0_0DA7;
/// Datatype-field bit marking a rendezvous ready-to-send announcement.
const RTS_BIT: u32 = 0x8000_0000;
const RNDV_RTS_LEN: usize = 20;
const RNDV_CTS_LEN: usize = 12;
const RNDV_ID_RANK_SHIFT: u32 = 40;
/// Messages larger than this use the rendezvous protocol (the receiver pulls
/// the data when ready), bounding memory instead of eagerly buffering.
const RNDV_THRESHOLD: usize = 64 * 1024;
/// Hard upper bound for a single framed payload accepted from a peer. Large
/// MPI payloads may use rendezvous, but no peer may force an unbounded allocation.
const MAX_FRAME_LEN: usize = 64 * 1024 * 1024;

/// Set once an abort/panic is in progress, to prevent re-entrant propagation.
static ABORTING: AtomicBool = AtomicBool::new(false);

/// Pending outgoing large messages, keyed by transfer id, awaiting a
/// clear-to-send from the receiver.
static RNDV_OUT: Mutex<Option<HashMap<u64, Vec<u8>>>> = Mutex::new(None);
/// Monotonic transfer-id counter (mixed with rank for global uniqueness).
static RNDV_SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// A message that has arrived and is waiting to be matched by a receive.
#[derive(Debug)]
struct Incoming {
    comm: u32,
    source: i32,
    tag: i32,
    count: u64,
    datatype: u32,
    payload: Vec<u8>,
}

/// The header of the wire envelope for a single message.
#[derive(Debug, Clone, Copy)]
struct Header {
    comm: u32,
    source: i32,
    dest: i32,
    tag: i32,
    count: u64,
    datatype: u32,
    len: u64,
}

impl Header {
    fn to_bytes(self) -> [u8; HEADER_LEN] {
        let mut b = [0u8; HEADER_LEN];
        b[0..4].copy_from_slice(&MAGIC.to_le_bytes());
        b[4..8].copy_from_slice(&self.comm.to_le_bytes());
        b[8..12].copy_from_slice(&self.source.to_le_bytes());
        b[12..16].copy_from_slice(&self.dest.to_le_bytes());
        b[16..20].copy_from_slice(&self.tag.to_le_bytes());
        b[20..28].copy_from_slice(&self.count.to_le_bytes());
        b[28..32].copy_from_slice(&self.datatype.to_le_bytes());
        b[32..40].copy_from_slice(&self.len.to_le_bytes());
        b
    }

    fn from_bytes(b: &[u8; HEADER_LEN]) -> io::Result<Header> {
        let magic = u32::from_le_bytes(b[0..4].try_into().unwrap());
        if magic != MAGIC {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "bad MPI wire magic",
            ));
        }
        Ok(Header {
            comm: u32::from_le_bytes(b[4..8].try_into().unwrap()),
            source: i32::from_le_bytes(b[8..12].try_into().unwrap()),
            dest: i32::from_le_bytes(b[12..16].try_into().unwrap()),
            tag: i32::from_le_bytes(b[16..20].try_into().unwrap()),
            count: u64::from_le_bytes(b[20..28].try_into().unwrap()),
            datatype: u32::from_le_bytes(b[28..32].try_into().unwrap()),
            len: u64::from_le_bytes(b[32..40].try_into().unwrap()),
        })
    }
}

fn checked_frame_len(len: u64) -> io::Result<usize> {
    let len = usize::try_from(len).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "MPI wire payload length does not fit this target",
        )
    })?;
    if len > MAX_FRAME_LEN {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "MPI wire payload exceeds the configured frame limit",
        ));
    }
    Ok(len)
}

fn invalid_control_record(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn parse_rendezvous_announcement(payload: &[u8]) -> io::Result<(u64, i32, usize)> {
    if payload.len() != RNDV_RTS_LEN {
        return Err(invalid_control_record(
            "MPI rendezvous announcement has an invalid length",
        ));
    }
    let id = u64::from_le_bytes(payload[0..8].try_into().expect("RTS id width"));
    let sender_world = i32::from_le_bytes(
        payload[8..12]
            .try_into()
            .expect("RTS sender world-rank width"),
    );
    if sender_world < 0 || (id >> RNDV_ID_RANK_SHIFT) != sender_world as u64 {
        return Err(invalid_control_record(
            "MPI rendezvous announcement has an inconsistent sender identity",
        ));
    }
    let payload_len = u64::from_le_bytes(
        payload[12..20]
            .try_into()
            .expect("RTS payload length width"),
    );
    let payload_len = checked_frame_len(payload_len)?;
    if payload_len <= RNDV_THRESHOLD {
        return Err(invalid_control_record(
            "MPI rendezvous announcement is below the eager threshold",
        ));
    }
    Ok((id, sender_world, payload_len))
}

fn validate_wire_message(header: &Header, payload: &[u8]) -> io::Result<()> {
    if header.datatype & RTS_BIT != 0 {
        if matches!(
            header.comm,
            ABORT_CONTEXT | RNDV_CTS_CTEXT | RNDV_DATA_CTEXT
        ) {
            return Err(invalid_control_record(
                "MPI rendezvous announcement uses a reserved control context",
            ));
        }
        parse_rendezvous_announcement(payload)?;
        return Ok(());
    }

    match header.comm {
        ABORT_CONTEXT
            if header.datatype != crate::datatype::ids::I32
                || header.count != 1
                || payload.len() != std::mem::size_of::<i32>() =>
        {
            Err(invalid_control_record(
                "MPI abort control record is invalid",
            ))
        }
        RNDV_CTS_CTEXT
            if header.datatype != crate::datatype::ids::U8
                || header.count != 1
                || header.tag != 0
                || payload.len() != RNDV_CTS_LEN =>
        {
            Err(invalid_control_record(
                "MPI rendezvous clear-to-send control record is invalid",
            ))
        }
        RNDV_CTS_CTEXT => {
            let requester = i32::from_le_bytes(
                payload[8..12]
                    .try_into()
                    .expect("CTS requester world-rank width"),
            );
            if requester < 0 || requester != header.source {
                return Err(invalid_control_record(
                    "MPI rendezvous clear-to-send has an inconsistent requester identity",
                ));
            }
            Ok(())
        }
        RNDV_DATA_CTEXT
            if header.datatype != crate::datatype::ids::U8
                || header.count != payload.len() as u64 =>
        {
            Err(invalid_control_record(
                "MPI rendezvous data control record is invalid",
            ))
        }
        _ => Ok(()),
    }
}

/// An active-message handler: invoked for messages arriving on a registered
/// context (used by RMA windows to service `put`/`get`/`accumulate`). Called
/// with `(source, tag, count, datatype, payload)`.
pub type Handler = Arc<dyn Fn(i32, i32, u64, u32, Vec<u8>) + Send + Sync>;

/// Shared mailbox of received-but-not-yet-matched messages, plus a table of
/// active-message handlers for contexts that should be serviced immediately
/// rather than queued.
struct Inbox {
    queue: Mutex<VecDeque<Incoming>>,
    cvar: Condvar,
    handlers: Mutex<HashMap<u32, Handler>>,
}

impl Inbox {
    fn new() -> Inbox {
        Inbox {
            queue: Mutex::new(VecDeque::new()),
            cvar: Condvar::new(),
            handlers: Mutex::new(HashMap::new()),
        }
    }

    fn push(&self, msg: Incoming) {
        // Contexts with a registered handler are serviced immediately (e.g. RMA
        // requests) instead of being queued for a matching receive.
        let handler = self.handlers.lock().unwrap().get(&msg.comm).cloned();
        if let Some(h) = handler {
            h(msg.source, msg.tag, msg.count, msg.datatype, msg.payload);
            return;
        }
        let mut q = self.queue.lock().unwrap();
        q.push_back(msg);
        self.cvar.notify_all();
    }

    /// Test whether a message matches a receive request.
    fn matches(msg: &Incoming, comm: u32, source: i32, tag: i32) -> bool {
        msg.comm == comm
            && (source == ANY_SOURCE || msg.source == source)
            && (tag == ANY_TAG || msg.tag == tag)
    }

    /// Block until a matching message is available, then remove and return it.
    ///
    /// If the wait lasts unusually long, emit a one-time diagnostic naming the
    /// receive that is stuck — a likely deadlock — instead of hanging silently.
    fn take_matching(&self, comm: u32, source: i32, tag: i32) -> Incoming {
        let mut q = self.queue.lock().unwrap();
        let mut waited = Duration::ZERO;
        let mut warned = false;
        loop {
            if let Some(pos) = q.iter().position(|m| Inbox::matches(m, comm, source, tag)) {
                return q.remove(pos).unwrap();
            }
            let (guard, res) = self.cvar.wait_timeout(q, Duration::from_secs(5)).unwrap();
            q = guard;
            if res.timed_out() {
                waited += Duration::from_secs(5);
                if waited >= Duration::from_secs(30) && !warned {
                    warned = true;
                    let rank = RUNTIME.get().map(|r| r.rank).unwrap_or(-1);
                    eprintln!(
                        "[mpi] rank {rank} has been blocked for {}s in receive \
                         (comm ctx {comm}, source {source}, tag {tag}) — possible deadlock",
                        waited.as_secs()
                    );
                }
            }
        }
    }

    /// Non-blocking check for a matching message; returns a lightweight
    /// description of the earliest match without consuming it.
    fn peek_matching(
        &self,
        comm: u32,
        source: i32,
        tag: i32,
    ) -> Option<(i32, i32, u64, u32, usize)> {
        let q = self.queue.lock().unwrap();
        q.iter()
            .find(|m| Inbox::matches(m, comm, source, tag))
            .map(|m| (m.source, m.tag, m.count, m.datatype, m.payload.len()))
    }
}

/// The process-global MPI runtime state.
pub struct Runtime {
    pub rank: i32,
    pub size: i32,
    pub threading: crate::environment::Threading,
    /// This process's own advertised address (`None` for a singleton job).
    my_addr: Option<SocketAddr>,
    /// World address table, indexed by world rank.
    addresses: Vec<SocketAddr>,
    inbox: Arc<Inbox>,
    /// Outbound connection cache, keyed by peer address (so the same rank id on
    /// different communication contexts — e.g. inter-communicators reaching a
    /// separately-bootstrapped world — maps to distinct connections).
    outgoing: Mutex<HashMap<SocketAddr, Arc<Mutex<TcpStream>>>>,
    /// Per-context peer address overrides. A context present here routes by
    /// looking the destination rank up in this table instead of the world
    /// address table — used by spawned inter-communicators to reach processes
    /// in another world.
    context_peers: Mutex<HashMap<u32, Vec<SocketAddr>>>,
    /// Bytes attached by the user for buffered sends (`MPI_Buffer_attach`).
    buffer_size: Mutex<usize>,
}

static RUNTIME: OnceLock<Runtime> = OnceLock::new();

/// Parent inter-communicator info captured during a spawned child's bootstrap:
/// `(inter-comm context, parent-group addresses)`. Consumed by
/// `Communicator::parent()`.
static SPAWN_PARENT: Mutex<Option<ParentBlock>> = Mutex::new(None);

/// The parent info for a spawned process, if any (see [`SPAWN_PARENT`]).
pub fn spawn_parent() -> Option<ParentBlock> {
    SPAWN_PARENT.lock().unwrap().clone()
}

/// Best-effort notify every peer that the job is aborting, then exit this
/// process with `code`. Peers receive the notice on [`ABORT_CONTEXT`] and exit
/// too, so no rank is left blocked in a receive.
pub fn abort_job(code: i32) -> ! {
    // Only the first aborter broadcasts; others just exit.
    if !ABORTING.swap(true, Ordering::SeqCst) {
        if let Some(rt) = RUNTIME.get() {
            if std::env::var("MPI_DEBUG").is_ok() {
                eprintln!(
                    "[abort] rank {} broadcasting abort {code} to {} peers",
                    rt.rank,
                    rt.size - 1
                );
            }
            let header = Header {
                comm: ABORT_CONTEXT,
                source: rt.rank,
                dest: 0,
                tag: 0,
                count: 1,
                datatype: crate::datatype::ids::I32,
                len: 4,
            }
            .to_bytes();
            let code_bytes = code.to_le_bytes();
            for w in 0..rt.size {
                if w == rt.rank {
                    continue;
                }
                if let Some(addr) = rt.peer_addr(w) {
                    // Short timeout so a dead/blocked peer doesn't stall abort.
                    match TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
                        Ok(mut s) => {
                            let _ = s.write_all(&header);
                            let _ = s.write_all(&code_bytes);
                            let _ = s.flush();
                            if std::env::var("MPI_DEBUG").is_ok() {
                                eprintln!("[abort] sent to rank {w} at {addr}");
                            }
                        }
                        Err(e) => {
                            if std::env::var("MPI_DEBUG").is_ok() {
                                eprintln!("[abort] connect to rank {w} at {addr} failed: {e}");
                            }
                        }
                    }
                }
            }
        }
    }
    std::process::exit(code);
}

/// Install the abort handler and a panic hook so a panic in one rank propagates
/// to the rest of the job instead of hanging them.
fn install_fault_handling() {
    // A message on ABORT_CONTEXT makes us exit with the sender's code.
    runtime().register_handler(
        ABORT_CONTEXT,
        Arc::new(|_src, _tag, _count, _dt, payload: Vec<u8>| {
            let code = if payload.len() >= 4 {
                i32::from_le_bytes(payload[..4].try_into().unwrap())
            } else {
                1
            };
            if !ABORTING.swap(true, Ordering::SeqCst) {
                eprintln!("MPI job aborting (peer signalled exit {code})");
            }
            std::process::exit(code);
        }),
    );

    // Rendezvous clear-to-send: a receiver has asked for a large message; send
    // it the data now.
    runtime().register_handler(
        RNDV_CTS_CTEXT,
        Arc::new(|_src, _tag, _count, _dt, payload: Vec<u8>| {
            let id = u64::from_le_bytes(payload[0..8].try_into().expect("validated CTS id"));
            let requester = i32::from_le_bytes(
                payload[8..12]
                    .try_into()
                    .expect("validated CTS requester world-rank"),
            );
            let data = RNDV_OUT
                .lock()
                .unwrap()
                .as_mut()
                .and_then(|pending| pending.remove(&id));
            let Some(data) = data else {
                eprintln!("MPI rendezvous clear-to-send referenced an unknown transfer");
                abort_job(1);
            };
            let rt = runtime();
            let id_tag = (id & 0x7FFF_FFFF) as i32;
            if let Err(error) = rt.send_eager(
                RNDV_DATA_CTEXT,
                rt.rank,
                requester,
                id_tag,
                data.len() as u64,
                crate::datatype::ids::U8,
                &data,
            ) {
                eprintln!("MPI rendezvous data send failed after clear-to-send: {error}");
                abort_job(1);
            }
        }),
    );

    // On panic, print as usual then tear the job down.
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        default_hook(info);
        abort_job(101);
    }));
}

/// Whether [`init`] has been called.
#[allow(dead_code)]
pub fn is_initialized() -> bool {
    RUNTIME.get().is_some()
}

/// Access the initialized runtime, panicking with an MPI-style message if the
/// library has not been initialized.
pub fn runtime() -> &'static Runtime {
    RUNTIME
        .get()
        .expect("MPI used before mpi::initialize() / after finalize")
}

/// Reverse the bytes of each `elem`-sized element in `buf` (endianness swap).
/// Compiled to a no-op on little-endian hosts, so the LE build and all its
/// behaviour are unchanged; big-endian hosts use it to transcode typed payloads
/// to/from the canonical little-endian wire format.
#[cfg(target_endian = "big")]
fn swap_elems(buf: &mut [u8], elem: usize) {
    if elem > 1 {
        for chunk in buf.chunks_exact_mut(elem) {
            chunk.reverse();
        }
    }
}

/// Whether a handler is registered for `comm` in this process. Handler contexts
/// (RMA windows, rendezvous control, abort) carry raw/native bytes and are not
/// endian-swapped.
#[cfg(target_endian = "big")]
impl Runtime {
    fn handler_registered(&self, comm: u32) -> bool {
        self.inbox.handlers.lock().unwrap().contains_key(&comm)
    }
}

/// Best-effort detection of the local source IP the OS would use to reach
/// `peer` (an `ip:port` string). Connecting a UDP socket sends no packets; it
/// just selects the source address.
fn detect_local_ip_toward(peer: &str) -> Option<String> {
    let sock = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    sock.connect(peer).ok()?;
    Some(sock.local_addr().ok()?.ip().to_string())
}

/// Bootstrap the runtime. Returns an error if called twice.
pub fn init(threading: crate::environment::Threading) -> Result<(), crate::MpiError> {
    if RUNTIME.get().is_some() {
        return Err(crate::MpiError::AlreadyInitialized);
    }

    let inbox = Arc::new(Inbox::new());

    // Are we launched under `mpiexec`? If not, run as a singleton (rank 0 of 1).
    let pmi = std::env::var("MPI_PMI_ROOT").ok();
    let (rank, size, addresses, my_addr) = match pmi {
        Some(root_addr) => {
            let rank: i32 = std::env::var("MPI_PMI_RANK")
                .map_err(|_| crate::MpiError::Bootstrap("MPI_PMI_RANK missing".into()))?
                .parse()
                .map_err(|_| crate::MpiError::Bootstrap("MPI_PMI_RANK invalid".into()))?;
            let size: i32 = std::env::var("MPI_PMI_SIZE")
                .map_err(|_| crate::MpiError::Bootstrap("MPI_PMI_SIZE missing".into()))?
                .parse()
                .map_err(|_| crate::MpiError::Bootstrap("MPI_PMI_SIZE invalid".into()))?;

            // Bind our own data listener and start accepting peer connections.
            // For multi-host jobs the launcher sets `MPI_MULTIHOST=1`; we then
            // advertise this node's routable IP (explicit `MPI_HOST_IP`, or
            // auto-detected as the source address toward the launcher) and bind
            // all interfaces so peers on other machines can reach us. Without it
            // we stay on loopback (single-host, unchanged behaviour).
            let advertise_ip = std::env::var("MPI_HOST_IP").ok().or_else(|| {
                if std::env::var("MPI_MULTIHOST").is_ok() {
                    detect_local_ip_toward(&root_addr)
                } else {
                    None
                }
            });
            let bind_host: &str = if advertise_ip.is_some() {
                "0.0.0.0"
            } else {
                "127.0.0.1"
            };
            let listener = TcpListener::bind((bind_host, 0))
                .map_err(|e| crate::MpiError::Bootstrap(format!("bind failed: {e}")))?;
            let port = listener
                .local_addr()
                .map_err(|e| crate::MpiError::Bootstrap(format!("local_addr failed: {e}")))?
                .port();
            let ip = advertise_ip.unwrap_or_else(|| "127.0.0.1".to_string());
            let my_addr: SocketAddr = format!("{ip}:{port}")
                .parse()
                .map_err(|e| crate::MpiError::Bootstrap(format!("bad advertise addr: {e}")))?;

            if std::env::var("MPI_DEBUG").is_ok() {
                eprintln!(
                    "[mpi rank {rank}] multihost={} advertise={my_addr} root={root_addr}",
                    std::env::var("MPI_MULTIHOST").is_ok()
                );
            }
            let (addresses, parent) = pmi_exchange(&root_addr, rank, size, my_addr)?;
            if let Some(p) = parent {
                *SPAWN_PARENT.lock().unwrap() = Some(p);
            }

            spawn_acceptor(listener, Arc::clone(&inbox));

            (rank, size, addresses, Some(my_addr))
        }
        None => {
            // Singleton job (rank 0 of 1). Still bring up a loopback listener so
            // the process has an address and can act as a spawn parent.
            let listener = TcpListener::bind(("127.0.0.1", 0))
                .map_err(|e| crate::MpiError::Bootstrap(format!("bind failed: {e}")))?;
            let port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
            let my_addr: SocketAddr = format!("127.0.0.1:{port}")
                .parse()
                .map_err(|e| crate::MpiError::Bootstrap(format!("bad addr: {e}")))?;
            spawn_acceptor(listener, Arc::clone(&inbox));
            (0, 1, vec![my_addr], Some(my_addr))
        }
    };

    let rt = Runtime {
        rank,
        size,
        threading,
        my_addr,
        addresses,
        inbox,
        outgoing: Mutex::new(HashMap::new()),
        context_peers: Mutex::new(HashMap::new()),
        buffer_size: Mutex::new(0),
    };

    RUNTIME
        .set(rt)
        .map_err(|_| crate::MpiError::AlreadyInitialized)?;
    install_fault_handling();
    Ok(())
}

/// Speak the PMI rendezvous protocol with the launcher: report our data-plane
/// address and receive the full address table for the job.
fn pmi_exchange(
    root_addr: &str,
    rank: i32,
    size: i32,
    my_addr: SocketAddr,
) -> Result<(Vec<SocketAddr>, Option<ParentBlock>), crate::MpiError> {
    let mut stream = TcpStream::connect(root_addr)
        .map_err(|e| crate::MpiError::Bootstrap(format!("connect to launcher failed: {e}")))?;
    let line = format!("{} {} {}\n", rank, my_addr, MY_ENDIAN);
    stream
        .write_all(line.as_bytes())
        .map_err(|e| crate::MpiError::Bootstrap(format!("PMI write failed: {e}")))?;

    // Read the address table: `size` newline-terminated "rank addr [endian]" lines.
    let mut buf = String::new();
    let mut reader = io::BufReader::new(stream);
    use std::io::BufRead;
    let mut table: Vec<Option<SocketAddr>> = vec![None; size as usize];
    let mut endians: Vec<String> = vec![MY_ENDIAN.to_string(); size as usize];
    for _ in 0..size {
        buf.clear();
        let n = reader
            .read_line(&mut buf)
            .map_err(|e| crate::MpiError::Bootstrap(format!("PMI read failed: {e}")))?;
        if n == 0 {
            return Err(crate::MpiError::Bootstrap(
                "launcher closed connection early".into(),
            ));
        }
        let mut parts = buf.split_whitespace();
        let r: usize = parts
            .next()
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| crate::MpiError::Bootstrap("bad PMI table entry".into()))?;
        let a: SocketAddr = parts
            .next()
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| crate::MpiError::Bootstrap("bad PMI table addr".into()))?;
        table[r] = Some(a);
        if let Some(e) = parts.next() {
            endians[r] = e.to_string();
        }
    }

    // The wire format is canonical little-endian and typed payloads are
    // transcoded per host, so point-to-point, collectives and rendezvous work
    // across mixed-endian hosts. RMA windows and derived (`#[derive(Equivalence)]`)
    // structs are byte-transparent, so warn once if the job is mixed-endian.
    if rank == 0 {
        if let Some(bad) = endians.iter().position(|e| e != MY_ENDIAN) {
            eprintln!(
                "[mpi] warning: mixed-endian job (rank 0 is {MY_ENDIAN}, rank {bad} is {}); \
                 point-to-point/collectives are transcoded, but RMA windows and \
                 #[derive(Equivalence)] structs require same-endian ranks",
                endians[bad]
            );
        }
    }

    let mut addresses = Vec::with_capacity(size as usize);
    for (r, a) in table.into_iter().enumerate() {
        addresses.push(
            a.ok_or_else(|| crate::MpiError::Bootstrap(format!("missing address for rank {r}")))?,
        );
    }

    // Spawned children receive an extra parent block: "PARENT <ictx> <count>"
    // followed by `count` parent-address lines. Absent for normal jobs.
    let mut parent = None;
    if std::env::var("MPI_SPAWN").is_ok() {
        buf.clear();
        if reader.read_line(&mut buf).unwrap_or(0) > 0 {
            let mut it = buf.split_whitespace();
            if it.next() == Some("PARENT") {
                let ictx: u32 = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
                let count: usize = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
                let mut paddrs = Vec::with_capacity(count);
                for _ in 0..count {
                    buf.clear();
                    reader.read_line(&mut buf).ok();
                    if let Ok(a) = buf.trim().parse::<SocketAddr>() {
                        paddrs.push(a);
                    }
                }
                parent = Some((ictx, paddrs));
            }
        }
    }

    Ok((addresses, parent))
}

/// Accept incoming peer connections; each gets a dedicated reader thread.
fn spawn_acceptor(listener: TcpListener, inbox: Arc<Inbox>) {
    thread::spawn(move || {
        for stream in listener.incoming() {
            match stream {
                Ok(s) => {
                    let inbox = Arc::clone(&inbox);
                    thread::spawn(move || reader_loop(s, inbox));
                }
                Err(_) => break,
            }
        }
    });
}

/// Read one frame header. A clean EOF between frames is normal; any partial
/// header is a malformed frame and must fail the whole job.
fn read_next_header(stream: &mut TcpStream) -> io::Result<Option<[u8; HEADER_LEN]>> {
    let mut header = [0_u8; HEADER_LEN];
    if stream.read(&mut header[..1])? == 0 {
        return Ok(None);
    }
    stream.read_exact(&mut header[1..])?;
    Ok(Some(header))
}

/// Read framed messages off a single peer connection until it closes.
fn reader_loop(mut stream: TcpStream, inbox: Arc<Inbox>) {
    let _ = stream.set_nodelay(true);
    loop {
        let hdr = match read_next_header(&mut stream) {
            Ok(Some(header)) => header,
            Ok(None) => return,
            Err(error) => {
                eprintln!("MPI transport rejected an incomplete wire header: {error}");
                abort_job(1);
            }
        };
        let header = match Header::from_bytes(&hdr) {
            Ok(header) => header,
            Err(error) => {
                eprintln!("MPI transport rejected a malformed wire header: {error}");
                abort_job(1);
            }
        };
        let payload_len = match checked_frame_len(header.len) {
            Ok(len) => len,
            Err(error) => {
                eprintln!("MPI transport rejected an invalid wire payload length: {error}");
                abort_job(1);
            }
        };
        let mut payload = vec![0_u8; payload_len];
        if let Err(error) = stream.read_exact(&mut payload) {
            eprintln!("MPI transport rejected an incomplete wire payload: {error}");
            abort_job(1);
        }
        if let Err(error) = validate_wire_message(&header, &payload) {
            eprintln!("MPI transport rejected an invalid wire control record: {error}");
            abort_job(1);
        }
        inbox.push(Incoming {
            comm: header.comm,
            source: header.source,
            tag: header.tag,
            count: header.count,
            datatype: header.datatype,
            payload,
        });
    }
}

impl Runtime {
    /// Resolve `(context, dest)` to a peer address: a context registered in
    /// `context_peers` routes by that override table (cross-world inter-comms);
    /// otherwise the world address table is used.
    fn resolve(&self, comm: u32, dest: i32) -> Option<SocketAddr> {
        if let Some(peers) = self.context_peers.lock().unwrap().get(&comm) {
            return peers.get(dest as usize).copied();
        }
        self.addresses.get(dest as usize).copied()
    }

    /// Whether `comm` has a context-peer override (i.e. routes off-world).
    fn has_override(&self, comm: u32) -> bool {
        self.context_peers.lock().unwrap().contains_key(&comm)
    }

    /// Register per-context peer addresses (used by spawned inter-comms).
    pub fn register_context_peers(&self, ctx: u32, peers: Vec<SocketAddr>) {
        self.context_peers.lock().unwrap().insert(ctx, peers);
    }

    /// Remove a context-peer override.
    #[allow(dead_code)]
    pub fn unregister_context_peers(&self, ctx: u32) {
        self.context_peers.lock().unwrap().remove(&ctx);
    }

    /// The advertised address of a world rank.
    pub fn peer_addr(&self, world_rank: i32) -> Option<SocketAddr> {
        self.addresses.get(world_rank as usize).copied()
    }

    /// This process's own advertised address.
    pub fn my_addr(&self) -> Option<SocketAddr> {
        self.my_addr
    }

    /// Obtain (creating if necessary) the outbound connection to `addr`.
    fn connection(&self, addr: SocketAddr) -> io::Result<Arc<Mutex<TcpStream>>> {
        {
            let map = self.outgoing.lock().unwrap();
            if let Some(c) = map.get(&addr) {
                return Ok(Arc::clone(c));
            }
        }
        // Retry briefly: a peer launched on another host (e.g. a second ssh
        // process) may not have its listener ready the instant we first try.
        let stream = {
            let mut attempt = 0u32;
            loop {
                match TcpStream::connect(addr) {
                    Ok(s) => break s,
                    Err(_) if attempt < 250 => {
                        attempt += 1;
                        std::thread::sleep(std::time::Duration::from_millis(20));
                    }
                    Err(e) => return Err(e),
                }
            }
        };
        stream.set_nodelay(true).ok();
        let conn = Arc::new(Mutex::new(stream));
        let mut map = self.outgoing.lock().unwrap();
        // Another thread may have raced us; keep the first.
        let entry = map.entry(addr).or_insert_with(|| Arc::clone(&conn));
        Ok(Arc::clone(entry))
    }

    /// Send a typed byte buffer, choosing eager or rendezvous delivery.
    ///
    /// * `comm` is the communicator context (for isolation / matching).
    /// * `src` is the value stamped as the message source — the sender's rank
    ///   *within `comm`*, so receivers see communicator-local ranks.
    /// * `dest_world` is the destination's **world** rank, used for routing.
    ///
    /// Messages above [`RNDV_THRESHOLD`] on a normal same-world context use the
    /// rendezvous protocol so the receiver pulls the data when ready (bounding
    /// buffered memory). Everything else is delivered eagerly.
    #[allow(clippy::too_many_arguments)]
    pub fn send(
        &self,
        comm: u32,
        src: i32,
        dest_world: i32,
        tag: i32,
        count: u64,
        datatype: u32,
        payload: &[u8],
    ) -> io::Result<()> {
        if payload.len() > MAX_FRAME_LEN {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "MPI send payload exceeds the configured frame limit",
            ));
        }
        let normal_ctx = comm != ABORT_CONTEXT && comm != RNDV_CTS_CTEXT && comm != RNDV_DATA_CTEXT;
        if payload.len() > RNDV_THRESHOLD
            && normal_ctx
            && !self.has_override(comm)
            && dest_world != self.rank
        {
            return self.send_rendezvous(comm, src, dest_world, tag, count, datatype, payload);
        }
        self.send_eager(comm, src, dest_world, tag, count, datatype, payload)
    }

    /// Announce a large message and stash it until the receiver asks for it.
    #[allow(clippy::too_many_arguments)]
    fn send_rendezvous(
        &self,
        comm: u32,
        src: i32,
        dest_world: i32,
        tag: i32,
        count: u64,
        datatype: u32,
        payload: &[u8],
    ) -> io::Result<()> {
        let id = ((self.rank as u64) << RNDV_ID_RANK_SHIFT)
            | (RNDV_SEQ.fetch_add(1, Ordering::Relaxed) & 0xFF_FFFF_FFFF);
        // Store the payload transcoded to the wire byte order (no-op on LE).
        let stored = payload.to_vec();
        #[cfg(target_endian = "big")]
        let stored = {
            let mut v = stored;
            swap_elems(&mut v, crate::datatype::wire_elem_size(datatype));
            v
        };
        RNDV_OUT
            .lock()
            .unwrap()
            .get_or_insert_with(HashMap::new)
            .insert(id, stored);
        // RTS record: [id][sender_world][len]; datatype carries the RTS bit.
        let mut rts = Vec::with_capacity(RNDV_RTS_LEN);
        rts.extend_from_slice(&id.to_le_bytes());
        rts.extend_from_slice(&self.rank.to_le_bytes());
        rts.extend_from_slice(&(payload.len() as u64).to_le_bytes());
        self.send_eager(comm, src, dest_world, tag, count, datatype | RTS_BIT, &rts)
    }

    #[allow(clippy::too_many_arguments)]
    fn send_eager(
        &self,
        comm: u32,
        src: i32,
        dest_world: i32,
        tag: i32,
        count: u64,
        datatype: u32,
        payload: &[u8],
    ) -> io::Result<()> {
        // Same-world send to self short-circuits into the local inbox. Contexts
        // with a peer override (cross-world inter-comms) never target self.
        if !self.has_override(comm) && dest_world == self.rank {
            self.inbox.push(Incoming {
                comm,
                source: src,
                tag,
                count,
                datatype,
                payload: payload.to_vec(),
            });
            return Ok(());
        }
        let addr = self.resolve(comm, dest_world).ok_or_else(|| {
            io::Error::new(io::ErrorKind::NotFound, "no address for destination rank")
        })?;
        // A cross-world send whose destination is our own address is delivered
        // locally (a process spawning ranks on itself, etc.).
        if Some(addr) == self.my_addr {
            self.inbox.push(Incoming {
                comm,
                source: src,
                tag,
                count,
                datatype,
                payload: payload.to_vec(),
            });
            return Ok(());
        }
        // Big-endian hosts transcode typed payloads to the canonical
        // little-endian wire (no-op / compiled out on little-endian). Skipped
        // for rendezvous announcements and handler contexts (raw/native bytes).
        #[cfg(target_endian = "big")]
        let swapped: Option<Vec<u8>> = {
            let esz = crate::datatype::wire_elem_size(datatype);
            if datatype & RTS_BIT == 0 && esz > 1 && !self.handler_registered(comm) {
                let mut v = payload.to_vec();
                swap_elems(&mut v, esz);
                Some(v)
            } else {
                None
            }
        };
        #[cfg(target_endian = "big")]
        let payload: &[u8] = swapped.as_deref().unwrap_or(payload);

        let header = Header {
            comm,
            source: src,
            dest: dest_world,
            tag,
            count,
            datatype,
            len: payload.len() as u64,
        };
        let conn = self.connection(addr)?;
        let mut s = conn.lock().unwrap();
        s.write_all(&header.to_bytes())?;
        s.write_all(payload)?;
        s.flush()?;
        Ok(())
    }

    /// Block until a message matching `(comm, source, tag)` arrives; return
    /// `(actual_source, actual_tag, count, datatype, payload)`. Transparently
    /// completes the rendezvous handshake for large messages.
    pub fn recv(&self, comm: u32, source: i32, tag: i32) -> (i32, i32, u64, u32, Vec<u8>) {
        let m = self.inbox.take_matching(comm, source, tag);
        if m.datatype & RTS_BIT != 0 {
            // Rendezvous announcement: ask the sender for the data, then pull it.
            let (id, sender_world, expected_payload_len) =
                parse_rendezvous_announcement(&m.payload).unwrap_or_else(|error| {
                    eprintln!(
                        "MPI transport rejected an invalid queued rendezvous announcement: {error}"
                    );
                    abort_job(1)
                });
            let id_tag = (id & 0x7FFF_FFFF) as i32;
            let mut cts = Vec::with_capacity(RNDV_CTS_LEN);
            cts.extend_from_slice(&id.to_le_bytes());
            cts.extend_from_slice(&self.rank.to_le_bytes());
            if let Err(error) = self.send_eager(
                RNDV_CTS_CTEXT,
                self.rank,
                sender_world,
                0,
                1,
                crate::datatype::ids::U8,
                &cts,
            ) {
                eprintln!("MPI rendezvous clear-to-send failed: {error}");
                abort_job(1);
            }
            let data = self
                .inbox
                .take_matching(RNDV_DATA_CTEXT, sender_world, id_tag);
            if data.payload.len() != expected_payload_len {
                eprintln!(
                    "MPI rendezvous data length disagreed with its announcement: expected {expected_payload_len}, got {}",
                    data.payload.len()
                );
                abort_job(1);
            }
            let real_dt = m.datatype & !RTS_BIT;
            let payload = data.payload;
            // Transcode the pulled bulk data from wire order (no-op on LE).
            #[cfg(target_endian = "big")]
            let payload = {
                let mut v = payload;
                swap_elems(&mut v, crate::datatype::wire_elem_size(real_dt));
                v
            };
            return (m.source, m.tag, m.count, real_dt, payload);
        }
        // Transcode typed eager payloads from wire order (no-op on LE).
        let payload = m.payload;
        #[cfg(target_endian = "big")]
        let payload = {
            let mut v = payload;
            swap_elems(&mut v, crate::datatype::wire_elem_size(m.datatype));
            v
        };
        (m.source, m.tag, m.count, m.datatype, payload)
    }

    /// Non-blocking probe: describe the earliest matching message, if any.
    pub fn probe(&self, comm: u32, source: i32, tag: i32) -> Option<(i32, i32, u64, u32, usize)> {
        self.inbox.peek_matching(comm, source, tag)
    }

    /// Blocking probe: wait until a matching message exists, then describe it
    /// without consuming it.
    pub fn probe_blocking(&self, comm: u32, source: i32, tag: i32) -> (i32, i32, u64, u32, usize) {
        loop {
            if let Some(info) = self.probe(comm, source, tag) {
                return info;
            }
            // Wait for any new arrival, then re-check.
            let q = self.inbox.queue.lock().unwrap();
            let _unused = self.inbox.cvar.wait(q).unwrap();
        }
    }

    /// Total buffer size attached for buffered sends.
    pub fn buffer_attach(&self, size: usize) {
        *self.buffer_size.lock().unwrap() += size;
    }

    /// Detach and return the current attached buffer size.
    pub fn buffer_detach(&self) -> usize {
        let mut b = self.buffer_size.lock().unwrap();
        std::mem::replace(&mut *b, 0)
    }

    /// Register an active-message handler for a context (used by RMA windows).
    pub fn register_handler(&self, ctx: u32, handler: Handler) {
        self.inbox.handlers.lock().unwrap().insert(ctx, handler);
    }

    /// Remove the active-message handler for a context.
    pub fn unregister_handler(&self, ctx: u32) {
        self.inbox.handlers.lock().unwrap().remove(&ctx);
    }

    /// The currently attached buffered-send buffer size.
    pub fn buffer_size(&self) -> usize {
        *self.buffer_size.lock().unwrap()
    }

    /// Replace the attached buffered-send buffer size.
    pub fn set_buffer_size(&self, size: usize) {
        *self.buffer_size.lock().unwrap() = size;
    }
}

#[cfg(test)]
mod tests {
    use super::{
        checked_frame_len, parse_rendezvous_announcement, validate_wire_message, Header,
        MAX_FRAME_LEN, RNDV_THRESHOLD, RTS_BIT,
    };

    #[test]
    fn frame_length_must_fit_and_respect_the_allocation_bound() {
        assert_eq!(checked_frame_len(0).unwrap(), 0);
        assert_eq!(
            checked_frame_len(MAX_FRAME_LEN as u64).unwrap(),
            MAX_FRAME_LEN
        );
        assert!(checked_frame_len(MAX_FRAME_LEN as u64 + 1).is_err());
        assert!(checked_frame_len(u64::MAX).is_err());
    }

    #[test]
    fn rendezvous_announcement_requires_exact_bounded_control_grammar() {
        let header = Header {
            comm: 0,
            source: 2,
            dest: 1,
            tag: 0,
            count: 1,
            datatype: crate::datatype::ids::U8 | RTS_BIT,
            len: 12,
        };
        let mut truncated = Vec::with_capacity(12);
        truncated.extend_from_slice(&(2_u64 << 40).to_le_bytes());
        truncated.extend_from_slice(&2_i32.to_le_bytes());
        assert!(validate_wire_message(&header, &truncated).is_err());

        let mut valid = truncated;
        valid.extend_from_slice(&((RNDV_THRESHOLD + 1) as u64).to_le_bytes());
        assert_eq!(
            parse_rendezvous_announcement(&valid).unwrap(),
            (2_u64 << 40, 2, RNDV_THRESHOLD + 1)
        );

        valid[12..20].copy_from_slice(&(MAX_FRAME_LEN as u64 + 1).to_le_bytes());
        assert!(parse_rendezvous_announcement(&valid).is_err());
    }
}
