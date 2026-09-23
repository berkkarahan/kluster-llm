//! Collective communication and reduction operations. Mirrors
//! `mpi::collective` in rsmpi: the [`CommunicatorCollectives`] trait (rootless
//! collectives, blanket-implemented for every [`Communicator`]), the [`Root`]
//! trait (rooted collectives, implemented for [`Process`]), the [`Operation`]
//! trait, and [`SystemOperation`] / [`UserOperation`].
//!
//! All algorithms are implemented in pure Rust on top of the point-to-point
//! transport. They run on a communicator's dedicated *collective context*, so
//! they never interfere with user point-to-point messages, and each collective
//! uses its own message tag(s) so successive collectives cannot alias.

use std::io;
use std::ptr;

use crate::datatype::{
    ids, Buffer, BufferMut, Equivalence, PartitionedBuffer, PartitionedBufferMut,
};
use crate::point_to_point::{vec_from_bytes, Process};
use crate::request::{Request, Scope, StaticScope};
use crate::topology::{CommData, Communicator};
use crate::transport;
use crate::{Rank, Tag};

// Distinct tags per collective phase (on the collective context).
const T_BCAST: Tag = 3;
const T_GATHER: Tag = 4;
const T_SCATTER: Tag = 5;
const T_ALLGATHER_UP: Tag = 6;
const T_ALLTOALL: Tag = 8;
const T_REDUCE: Tag = 9;
const T_SCAN: Tag = 11;
const T_EXSCAN: Tag = 12;
const T_ALLGATHERV_UP: Tag = 13;
const T_ALLGATHERV_DOWN: Tag = 14;
const T_GATHERV: Tag = 15;
/// Base tag for dissemination-barrier rounds (`+ round`).
const T_BARRIER_ROUND: Tag = 200;

/// Real rank of relative rank `rel` in a `root`-rooted, `n`-process tree.
#[inline]
fn real_rank(rel: Rank, root: Rank, n: Rank) -> Rank {
    (rel + root) % n
}
const T_SCATTERV: Tag = 16;
const T_ALLTOALLV: Tag = 17;

const DT_BYTES: u32 = ids::U8;

// =====================================================================
// Reduction operations
// =====================================================================

/// The kind of built-in reduction.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
enum OpKind {
    Sum,
    Product,
    Max,
    Min,
    LogicalAnd,
    LogicalOr,
    LogicalXor,
    BitwiseAnd,
    BitwiseOr,
    BitwiseXor,
}

/// A reduction operation usable with `reduce`, `all_reduce`, `scan`, etc.
/// (`MPI_Op`). Implemented by [`SystemOperation`] and [`UserOperation`].
pub trait Operation {
    /// Combine `val` into `acc` element-wise, interpreting the bytes as the
    /// datatype identified by `dt_id`.
    #[doc(hidden)]
    fn reduce_bytes(&self, dt_id: u32, acc: &mut [u8], val: &[u8]);

    /// Whether the operation is commutative (`MPI_Op_commutative`).
    fn is_commutative(&self) -> bool {
        true
    }
}

/// A built-in reduction operation (`MPI_SUM`, `MPI_MAX`, …). Mirrors rsmpi's
/// `SystemOperation`.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct SystemOperation {
    kind: OpKind,
}

impl SystemOperation {
    /// `MPI_SUM`.
    pub fn sum() -> SystemOperation {
        SystemOperation { kind: OpKind::Sum }
    }
    /// `MPI_PROD`.
    pub fn product() -> SystemOperation {
        SystemOperation {
            kind: OpKind::Product,
        }
    }
    /// `MPI_MAX`.
    pub fn max() -> SystemOperation {
        SystemOperation { kind: OpKind::Max }
    }
    /// `MPI_MIN`.
    pub fn min() -> SystemOperation {
        SystemOperation { kind: OpKind::Min }
    }
    /// `MPI_LAND`.
    pub fn logical_and() -> SystemOperation {
        SystemOperation {
            kind: OpKind::LogicalAnd,
        }
    }
    /// `MPI_LOR`.
    pub fn logical_or() -> SystemOperation {
        SystemOperation {
            kind: OpKind::LogicalOr,
        }
    }
    /// `MPI_LXOR`.
    pub fn logical_xor() -> SystemOperation {
        SystemOperation {
            kind: OpKind::LogicalXor,
        }
    }
    /// `MPI_BAND`.
    pub fn bitwise_and() -> SystemOperation {
        SystemOperation {
            kind: OpKind::BitwiseAnd,
        }
    }
    /// `MPI_BOR`.
    pub fn bitwise_or() -> SystemOperation {
        SystemOperation {
            kind: OpKind::BitwiseOr,
        }
    }
    /// `MPI_BXOR`.
    pub fn bitwise_xor() -> SystemOperation {
        SystemOperation {
            kind: OpKind::BitwiseXor,
        }
    }

    /// Serialize the operation kind to a byte (for RMA accumulate transport).
    pub(crate) fn to_code(self) -> u8 {
        match self.kind {
            OpKind::Sum => 0,
            OpKind::Product => 1,
            OpKind::Max => 2,
            OpKind::Min => 3,
            OpKind::LogicalAnd => 4,
            OpKind::LogicalOr => 5,
            OpKind::LogicalXor => 6,
            OpKind::BitwiseAnd => 7,
            OpKind::BitwiseOr => 8,
            OpKind::BitwiseXor => 9,
        }
    }

    /// Reconstruct an operation from a byte produced by [`to_code`].
    ///
    /// [`to_code`]: SystemOperation::to_code
    pub(crate) fn from_code(code: u8) -> SystemOperation {
        let kind = match code {
            0 => OpKind::Sum,
            1 => OpKind::Product,
            2 => OpKind::Max,
            3 => OpKind::Min,
            4 => OpKind::LogicalAnd,
            5 => OpKind::LogicalOr,
            6 => OpKind::LogicalXor,
            7 => OpKind::BitwiseAnd,
            8 => OpKind::BitwiseOr,
            _ => OpKind::BitwiseXor,
        };
        SystemOperation { kind }
    }
}

/// Combine two same-length byte runs element-wise as type `T` using `f`, using
/// unaligned accesses (buffers may originate from unaligned byte vectors).
fn combine<T: Copy>(acc: &mut [u8], val: &[u8], f: impl Fn(T, T) -> T) {
    let size = std::mem::size_of::<T>();
    if size == 0 {
        return;
    }
    let n = acc.len() / size;
    for i in 0..n {
        // SAFETY: both slices hold at least `n * size` bytes; T is POD.
        unsafe {
            let ap = acc.as_mut_ptr().add(i * size) as *mut T;
            let bp = val.as_ptr().add(i * size) as *const T;
            let a = ptr::read_unaligned(ap);
            let b = ptr::read_unaligned(bp);
            ptr::write_unaligned(ap, f(a, b));
        }
    }
}

macro_rules! int_reduce {
    ($kind:expr, $acc:expr, $val:expr, $t:ty) => {{
        match $kind {
            OpKind::Sum => combine::<$t>($acc, $val, |a, b| a.wrapping_add(b)),
            OpKind::Product => combine::<$t>($acc, $val, |a, b| a.wrapping_mul(b)),
            OpKind::Max => combine::<$t>($acc, $val, |a, b| if a >= b { a } else { b }),
            OpKind::Min => combine::<$t>($acc, $val, |a, b| if a <= b { a } else { b }),
            OpKind::LogicalAnd => combine::<$t>($acc, $val, |a, b| ((a != 0) && (b != 0)) as $t),
            OpKind::LogicalOr => combine::<$t>($acc, $val, |a, b| ((a != 0) || (b != 0)) as $t),
            OpKind::LogicalXor => combine::<$t>($acc, $val, |a, b| ((a != 0) ^ (b != 0)) as $t),
            OpKind::BitwiseAnd => combine::<$t>($acc, $val, |a, b| a & b),
            OpKind::BitwiseOr => combine::<$t>($acc, $val, |a, b| a | b),
            OpKind::BitwiseXor => combine::<$t>($acc, $val, |a, b| a ^ b),
        }
    }};
}

macro_rules! float_reduce {
    ($kind:expr, $acc:expr, $val:expr, $t:ty) => {{
        match $kind {
            OpKind::Sum => combine::<$t>($acc, $val, |a, b| a + b),
            OpKind::Product => combine::<$t>($acc, $val, |a, b| a * b),
            OpKind::Max => combine::<$t>($acc, $val, |a, b| a.max(b)),
            OpKind::Min => combine::<$t>($acc, $val, |a, b| a.min(b)),
            other => panic!(
                "operation {:?} is not defined for floating-point data",
                other
            ),
        }
    }};
}

impl Operation for SystemOperation {
    fn reduce_bytes(&self, dt_id: u32, acc: &mut [u8], val: &[u8]) {
        debug_assert_eq!(acc.len(), val.len());
        match dt_id {
            ids::I8 => int_reduce!(self.kind, acc, val, i8),
            ids::U8 => int_reduce!(self.kind, acc, val, u8),
            ids::I16 => int_reduce!(self.kind, acc, val, i16),
            ids::U16 => int_reduce!(self.kind, acc, val, u16),
            ids::I32 => int_reduce!(self.kind, acc, val, i32),
            ids::U32 => int_reduce!(self.kind, acc, val, u32),
            ids::I64 => int_reduce!(self.kind, acc, val, i64),
            ids::U64 => int_reduce!(self.kind, acc, val, u64),
            ids::ISIZE => int_reduce!(self.kind, acc, val, isize),
            ids::USIZE => int_reduce!(self.kind, acc, val, usize),
            ids::I128 => int_reduce!(self.kind, acc, val, i128),
            ids::U128 => int_reduce!(self.kind, acc, val, u128),
            ids::F32 => float_reduce!(self.kind, acc, val, f32),
            ids::F64 => float_reduce!(self.kind, acc, val, f64),
            ids::BOOL => match self.kind {
                OpKind::LogicalAnd | OpKind::BitwiseAnd | OpKind::Min => {
                    combine::<u8>(acc, val, |a, b| ((a != 0) && (b != 0)) as u8)
                }
                OpKind::LogicalOr | OpKind::BitwiseOr | OpKind::Max => {
                    combine::<u8>(acc, val, |a, b| ((a != 0) || (b != 0)) as u8)
                }
                OpKind::LogicalXor | OpKind::BitwiseXor => {
                    combine::<u8>(acc, val, |a, b| ((a != 0) ^ (b != 0)) as u8)
                }
                other => panic!("operation {:?} is not defined for bool", other),
            },
            other => panic!("reduction not supported for datatype id {other}"),
        }
    }
}

/// A user-defined reduction operation wrapping a Rust closure. Mirrors rsmpi's
/// `UserOperation`. The closure receives the incoming values and the
/// accumulator (`inout`), matching MPI's `(invec, inoutvec)` convention.
pub struct UserOperation<T: Equivalence> {
    #[allow(clippy::type_complexity)]
    f: Box<dyn Fn(&[T], &mut [T]) + Send + Sync>,
    commutative: bool,
}

impl<T: Equivalence> UserOperation<T> {
    /// A commutative user operation.
    pub fn commutative<F>(f: F) -> UserOperation<T>
    where
        F: Fn(&[T], &mut [T]) + Send + Sync + 'static,
    {
        UserOperation {
            f: Box::new(f),
            commutative: true,
        }
    }

    /// A non-commutative user operation.
    pub fn non_commutative<F>(f: F) -> UserOperation<T>
    where
        F: Fn(&[T], &mut [T]) + Send + Sync + 'static,
    {
        UserOperation {
            f: Box::new(f),
            commutative: false,
        }
    }
}

impl<T: Equivalence> Operation for UserOperation<T> {
    fn reduce_bytes(&self, _dt_id: u32, acc: &mut [u8], val: &[u8]) {
        let mut a: Vec<T> = vec_from_bytes::<T>(acc);
        let b: Vec<T> = vec_from_bytes::<T>(val);
        (self.f)(&b, &mut a);
        // Write the accumulator back into `acc`.
        let size = std::mem::size_of::<T>();
        // SAFETY: `a` has the same element count as `acc`; T is POD.
        let a_bytes =
            unsafe { std::slice::from_raw_parts(a.as_ptr() as *const u8, a.len() * size) };
        acc[..a_bytes.len()].copy_from_slice(a_bytes);
    }

    fn is_commutative(&self) -> bool {
        self.commutative
    }
}

/// Perform a purely local reduction, combining `inbuf` into `inoutbuf`
/// (`MPI_Reduce_local`).
pub fn reduce_local_into<S, R, O>(inbuf: &S, inoutbuf: &mut R, op: O)
where
    S: Buffer + ?Sized,
    R: BufferMut + ?Sized,
    O: Operation,
{
    let dt = inbuf.as_datatype().id;
    let inbytes = inbuf.as_bytes().to_vec();
    op.reduce_bytes(dt, inoutbuf.as_bytes_mut(), &inbytes);
}

// =====================================================================
// Byte-level collective algorithms (operate on the collective context)
// =====================================================================

fn rt() -> &'static transport::Runtime {
    transport::runtime()
}

/// Dissemination barrier: `O(log n)` rounds, each rank exchanging with the peer
/// `2^k` away.
fn barrier_bytes(comm: &CommData) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    let me = comm.rank;
    if n <= 1 {
        return Ok(());
    }
    let mut dist = 1;
    let mut round = 0;
    while dist < n {
        let dst = comm.world_rank((me + dist) % n);
        let src = (me - dist + n) % n;
        let tag = T_BARRIER_ROUND + round;
        // Send first (eager, buffered on the receiver), then await our own.
        rt().send(ctx, me, dst, tag, 1, DT_BYTES, &[0u8])?;
        let _ = rt().recv(ctx, src, tag);
        dist <<= 1;
        round += 1;
    }
    Ok(())
}

/// Binomial-tree broadcast: `O(log n)` steps.
pub(crate) fn bcast_bytes(comm: &CommData, root: Rank, buf: &mut [u8]) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    if n <= 1 {
        return Ok(());
    }
    let me = comm.rank;
    let rel = (me - root + n) % n;

    // Receive phase: get the data from our parent (unless we are the root).
    let mut mask = 1;
    while mask < n {
        if rel & mask != 0 {
            let src = real_rank(rel - mask, root, n);
            let (_s, _t, _c, _d, payload) = rt().recv(ctx, src, T_BCAST);
            let k = buf.len().min(payload.len());
            buf[..k].copy_from_slice(&payload[..k]);
            break;
        }
        mask <<= 1;
    }

    // Send phase: forward to the subtree below us.
    mask >>= 1;
    while mask > 0 {
        let child_rel = rel + mask;
        if child_rel < n {
            let dst = comm.world_rank(real_rank(child_rel, root, n));
            rt().send(ctx, me, dst, T_BCAST, buf.len() as u64, DT_BYTES, buf)?;
        }
        mask >>= 1;
    }
    Ok(())
}

fn gather_bytes(
    comm: &CommData,
    root: Rank,
    send: &[u8],
    recv: Option<&mut [u8]>,
) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    let blk = send.len();
    if comm.rank == root {
        let recv = recv.expect("root must provide a receive buffer");
        recv[(root as usize) * blk..][..blk].copy_from_slice(send);
        for peer in 0..n {
            if peer != root {
                let (_s, _t, _c, _d, payload) = rt().recv(ctx, peer, T_GATHER);
                recv[(peer as usize) * blk..][..blk].copy_from_slice(&payload);
            }
        }
    } else {
        rt().send(
            ctx,
            comm.rank,
            comm.world_rank(root),
            T_GATHER,
            blk as u64,
            DT_BYTES,
            send,
        )?;
    }
    Ok(())
}

fn scatter_bytes(
    comm: &CommData,
    root: Rank,
    send: Option<&[u8]>,
    recv: &mut [u8],
) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    let blk = recv.len();
    if comm.rank == root {
        let send = send.expect("root must provide a send buffer");
        recv.copy_from_slice(&send[(root as usize) * blk..][..blk]);
        for peer in 0..n {
            if peer != root {
                rt().send(
                    ctx,
                    comm.rank,
                    comm.world_rank(peer),
                    T_SCATTER,
                    blk as u64,
                    DT_BYTES,
                    &send[(peer as usize) * blk..][..blk],
                )?;
            }
        }
    } else {
        let (_s, _t, _c, _d, payload) = rt().recv(ctx, root, T_SCATTER);
        let k = recv.len().min(payload.len());
        recv[..k].copy_from_slice(&payload[..k]);
    }
    Ok(())
}

/// Ring allgather: `n-1` steps, each forwarding one block to the right
/// neighbour and receiving one from the left (bandwidth-optimal).
fn allgather_bytes(comm: &CommData, send: &[u8], recv: &mut [u8]) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    let me = comm.rank;
    let blk = send.len();
    // Seed our own block.
    recv[(me as usize) * blk..][..blk].copy_from_slice(send);
    if n <= 1 {
        return Ok(());
    }
    let right = comm.world_rank((me + 1) % n);
    let left = (me - 1 + n) % n;
    for step in 0..n - 1 {
        let send_idx = ((me - step + n) % n) as usize;
        let recv_idx = ((me - step - 1 + n) % n) as usize;
        rt().send(
            ctx,
            me,
            right,
            T_ALLGATHER_UP,
            blk as u64,
            DT_BYTES,
            &recv[send_idx * blk..send_idx * blk + blk],
        )?;
        let (_s, _t, _c, _d, payload) = rt().recv(ctx, left, T_ALLGATHER_UP);
        recv[recv_idx * blk..recv_idx * blk + blk].copy_from_slice(&payload);
    }
    Ok(())
}

fn all_to_all_bytes(comm: &CommData, send: &[u8], recv: &mut [u8]) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size as usize;
    let blk = send.len().checked_div(n).unwrap_or(0);
    let me = comm.rank as usize;
    for peer in 0..n {
        let block = &send[peer * blk..][..blk];
        if peer == me {
            recv[me * blk..][..blk].copy_from_slice(block);
        } else {
            rt().send(
                ctx,
                comm.rank,
                comm.world_rank(peer as Rank),
                T_ALLTOALL,
                blk as u64,
                DT_BYTES,
                block,
            )?;
        }
    }
    for peer in 0..n {
        if peer != me {
            let (_s, _t, _c, _d, payload) = rt().recv(ctx, peer as Rank, T_ALLTOALL);
            recv[peer * blk..][..blk].copy_from_slice(&payload);
        }
    }
    Ok(())
}

/// Ordered linear reduce to `root`, folding contributions in rank order — used
/// for non-commutative operations where combine order is significant.
fn reduce_ordered<O: Operation>(
    comm: &CommData,
    root: Rank,
    send: &[u8],
    recv: Option<&mut [u8]>,
    op: &O,
    dt: u32,
) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    if comm.rank == root {
        let mut contribs: Vec<Vec<u8>> = vec![Vec::new(); n as usize];
        contribs[root as usize] = send.to_vec();
        for peer in 0..n {
            if peer != root {
                let (_s, _t, _c, _d, payload) = rt().recv(ctx, peer, T_REDUCE);
                contribs[peer as usize] = payload;
            }
        }
        let mut acc = contribs[0].clone();
        for c in contribs.iter().skip(1) {
            op.reduce_bytes(dt, &mut acc, c);
        }
        recv.expect("root must provide a receive buffer")
            .copy_from_slice(&acc);
    } else {
        rt().send(
            ctx,
            comm.rank,
            comm.world_rank(root),
            T_REDUCE,
            send.len() as u64,
            dt,
            send,
        )?;
    }
    Ok(())
}

/// Reduce `send` from every rank into `recv` at `root`. Uses a binomial tree
/// (`O(log n)`) for commutative operations and an ordered linear reduction for
/// non-commutative ones (so combine order is well-defined).
fn reduce_bytes_op<O: Operation>(
    comm: &CommData,
    root: Rank,
    send: &[u8],
    recv: Option<&mut [u8]>,
    op: &O,
    dt: u32,
) -> io::Result<()> {
    let n = comm.size;
    if n <= 1 {
        if let Some(r) = recv {
            r.copy_from_slice(send);
        }
        return Ok(());
    }
    if !op.is_commutative() {
        return reduce_ordered(comm, root, send, recv, op, dt);
    }

    let ctx = comm.coll_context();
    let me = comm.rank;
    let rel = (me - root + n) % n;
    let mut acc = send.to_vec();
    let mut mask = 1;
    while mask < n {
        if rel & mask != 0 {
            // We are a leaf of this level: send our accumulator up and stop.
            let parent = comm.world_rank(real_rank(rel - mask, root, n));
            rt().send(ctx, me, parent, T_REDUCE, acc.len() as u64, dt, &acc)?;
            break;
        }
        // Combine each child's contribution (commutative: order-independent).
        let child_rel = rel + mask;
        if child_rel < n {
            let child = real_rank(child_rel, root, n);
            let (_s, _t, _c, _d, payload) = rt().recv(ctx, child, T_REDUCE);
            op.reduce_bytes(dt, &mut acc, &payload);
        }
        mask <<= 1;
    }
    if rel == 0 {
        recv.expect("root must provide a receive buffer")
            .copy_from_slice(&acc);
    }
    Ok(())
}

/// All-reduce = tree reduce to rank 0 followed by a binomial broadcast, both
/// `O(log n)`.
fn all_reduce_bytes<O: Operation>(
    comm: &CommData,
    send: &[u8],
    recv: &mut [u8],
    op: &O,
    dt: u32,
) -> io::Result<()> {
    let n = comm.size;
    if n <= 1 {
        recv.copy_from_slice(send);
        return Ok(());
    }
    let is_root = comm.rank == 0;
    {
        let recv_opt: Option<&mut [u8]> = if is_root { Some(&mut *recv) } else { None };
        reduce_bytes_op(comm, 0, send, recv_opt, op, dt)?;
    }
    // The result lives in rank 0's `recv`; broadcast it to everyone.
    bcast_bytes(comm, 0, recv)
}

fn scan_bytes<O: Operation>(
    comm: &CommData,
    send: &[u8],
    recv: &mut [u8],
    op: &O,
    dt: u32,
) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    let me = comm.rank;
    recv.copy_from_slice(send);
    if me > 0 {
        let (_s, _t, _c, _d, prefix) = rt().recv(ctx, me - 1, T_SCAN);
        let mut acc = prefix;
        op.reduce_bytes(dt, &mut acc, send);
        recv.copy_from_slice(&acc);
    }
    if me < n - 1 {
        rt().send(
            ctx,
            me,
            comm.world_rank(me + 1),
            T_SCAN,
            recv.len() as u64,
            dt,
            recv,
        )?;
    }
    Ok(())
}

fn exclusive_scan_bytes<O: Operation>(
    comm: &CommData,
    send: &[u8],
    recv: &mut [u8],
    op: &O,
    dt: u32,
) -> io::Result<()> {
    let ctx = comm.coll_context();
    let n = comm.size;
    let me = comm.rank;
    if me > 0 {
        let (_s, _t, _c, _d, prefix) = rt().recv(ctx, me - 1, T_EXSCAN);
        recv.copy_from_slice(&prefix);
    }
    if me < n - 1 {
        // Value forwarded to the next rank is the reduction over 0..=me.
        let acc = if me == 0 {
            send.to_vec()
        } else {
            let mut a = recv.to_vec();
            op.reduce_bytes(dt, &mut a, send);
            a
        };
        rt().send(
            ctx,
            me,
            comm.world_rank(me + 1),
            T_EXSCAN,
            acc.len() as u64,
            dt,
            &acc,
        )?;
    }
    Ok(())
}

// =====================================================================
// Public traits
// =====================================================================

/// Map a transport error to the crate error type.
fn cerr(e: io::Error) -> crate::MpiError {
    crate::MpiError::Other(format!("MPI collective failed: {e}"))
}

/// A raw mutable byte pointer that is `Send`, so a background collective thread
/// can write the receive buffer (kept alive for the operation by the returned
/// [`Request`]'s borrow).
struct SendMutPtr(*mut u8);
// SAFETY: the pointed-to buffer is borrowed by the Request for `'a`, which
// outlives the joined thread; there is a single writer.
unsafe impl Send for SendMutPtr {}

/// Collective operations that do not designate a root, available on every
/// communicator. Mirrors rsmpi's `CommunicatorCollectives`.
///
/// Each blocking collective has a fallible `try_*` counterpart that returns a
/// [`crate::MpiError`] instead of panicking on a transport failure.
pub trait CommunicatorCollectives: Communicator {
    /// Barrier synchronization (`MPI_Barrier`).
    fn barrier(&self) {
        self.try_barrier().expect("MPI_Barrier failed");
    }

    /// Fallible [`barrier`](CommunicatorCollectives::barrier).
    fn try_barrier(&self) -> Result<(), crate::MpiError> {
        barrier_bytes(self.comm_data()).map_err(cerr)
    }

    /// Gather equal-sized contributions from all ranks to all ranks
    /// (`MPI_Allgather`).
    fn all_gather_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        self.try_all_gather_into(sendbuf, recvbuf)
            .expect("MPI_Allgather failed");
    }

    /// Fallible [`all_gather_into`](CommunicatorCollectives::all_gather_into).
    fn try_all_gather_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R) -> Result<(), crate::MpiError>
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        let send = sendbuf.as_bytes().to_vec();
        allgather_bytes(self.comm_data(), &send, recvbuf.as_bytes_mut()).map_err(cerr)
    }

    /// All-to-all scatter/gather with equal block sizes (`MPI_Alltoall`).
    fn all_to_all_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        self.try_all_to_all_into(sendbuf, recvbuf)
            .expect("MPI_Alltoall failed");
    }

    /// Fallible [`all_to_all_into`](CommunicatorCollectives::all_to_all_into).
    fn try_all_to_all_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R) -> Result<(), crate::MpiError>
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        let send = sendbuf.as_bytes().to_vec();
        all_to_all_bytes(self.comm_data(), &send, recvbuf.as_bytes_mut()).map_err(cerr)
    }

    /// Reduce contributions from all ranks and make the result available on all
    /// ranks (`MPI_Allreduce`).
    fn all_reduce_into<S, R, O>(&self, sendbuf: &S, recvbuf: &mut R, op: O)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
        O: Operation,
    {
        self.try_all_reduce_into(sendbuf, recvbuf, op)
            .expect("MPI_Allreduce failed");
    }

    /// Fallible [`all_reduce_into`](CommunicatorCollectives::all_reduce_into).
    fn try_all_reduce_into<S, R, O>(
        &self,
        sendbuf: &S,
        recvbuf: &mut R,
        op: O,
    ) -> Result<(), crate::MpiError>
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
        O: Operation,
    {
        let dt = sendbuf.as_datatype().id;
        let send = sendbuf.as_bytes().to_vec();
        all_reduce_bytes(self.comm_data(), &send, recvbuf.as_bytes_mut(), &op, dt).map_err(cerr)
    }

    /// In-place all-reduce: `buf` is both the input and the output
    /// (`MPI_Allreduce` with `MPI_IN_PLACE`).
    fn all_reduce_into_in_place<R, O>(&self, buf: &mut R, op: O)
    where
        R: BufferMut + ?Sized,
        O: Operation,
    {
        let dt = buf.as_datatype().id;
        let send = buf.as_bytes_mut().to_vec();
        all_reduce_bytes(self.comm_data(), &send, buf.as_bytes_mut(), &op, dt)
            .expect("MPI_Allreduce (in place) failed");
    }

    /// In-place all-gather: `buf` holds this rank's block at slot `rank` on
    /// entry and every rank's block on return (`MPI_Allgather` with
    /// `MPI_IN_PLACE`).
    fn all_gather_into_in_place<R>(&self, buf: &mut R)
    where
        R: BufferMut + ?Sized,
    {
        let comm = self.comm_data();
        let n = comm.size as usize;
        let total = buf.as_bytes_mut().len();
        let blk = total.checked_div(n).unwrap_or(0);
        let me = comm.rank as usize;
        let myblock = buf.as_bytes_mut()[me * blk..me * blk + blk].to_vec();
        allgather_bytes(comm, &myblock, buf.as_bytes_mut())
            .expect("MPI_Allgather (in place) failed");
    }

    /// Reduce, then scatter equal-sized blocks of the result
    /// (`MPI_Reduce_scatter_block`).
    fn reduce_scatter_block_into<S, R, O>(&self, sendbuf: &S, recvbuf: &mut R, op: O)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
        O: Operation,
    {
        let comm = self.comm_data();
        let dt = sendbuf.as_datatype().id;
        let tmp = sendbuf.as_bytes().to_vec();
        let mut reduced = tmp.clone();
        all_reduce_bytes(comm, &tmp, &mut reduced, &op, dt)
            .expect("MPI_Reduce_scatter_block failed");
        let blk = recvbuf.as_bytes_mut().len();
        let start = (comm.rank as usize) * blk;
        recvbuf
            .as_bytes_mut()
            .copy_from_slice(&reduced[start..][..blk]);
    }

    /// Inclusive prefix reduction (`MPI_Scan`).
    fn scan_into<S, R, O>(&self, sendbuf: &S, recvbuf: &mut R, op: O)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
        O: Operation,
    {
        self.try_scan_into(sendbuf, recvbuf, op)
            .expect("MPI_Scan failed");
    }

    /// Fallible [`scan_into`](CommunicatorCollectives::scan_into).
    fn try_scan_into<S, R, O>(
        &self,
        sendbuf: &S,
        recvbuf: &mut R,
        op: O,
    ) -> Result<(), crate::MpiError>
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
        O: Operation,
    {
        let dt = sendbuf.as_datatype().id;
        let send = sendbuf.as_bytes().to_vec();
        scan_bytes(self.comm_data(), &send, recvbuf.as_bytes_mut(), &op, dt).map_err(cerr)
    }

    /// Exclusive prefix reduction (`MPI_Exscan`).
    fn exclusive_scan_into<S, R, O>(&self, sendbuf: &S, recvbuf: &mut R, op: O)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
        O: Operation,
    {
        let dt = sendbuf.as_datatype().id;
        let send = sendbuf.as_bytes().to_vec();
        exclusive_scan_bytes(self.comm_data(), &send, recvbuf.as_bytes_mut(), &op, dt)
            .expect("MPI_Exscan failed");
    }

    /// Varying-count all-gather (`MPI_Allgatherv`).
    fn all_gather_varcount_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: PartitionedBufferMut + ?Sized,
    {
        let comm = self.comm_data();
        let send = sendbuf.as_bytes().to_vec();
        // Gather at rank 0 then broadcast the assembled buffer.
        varcount_allgather(comm, &send, recvbuf);
    }

    /// Varying-count all-to-all (`MPI_Alltoallv`): rank *i* sends its *j*-th
    /// block to rank *j*, with per-peer counts and displacements on both sides.
    fn all_to_all_varcount_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: PartitionedBuffer + ?Sized,
        R: PartitionedBufferMut + ?Sized,
    {
        let comm = self.comm_data();
        let ctx = comm.coll_context();
        let n = comm.size;
        let me = comm.rank;
        let selem = sendbuf.as_datatype().size;
        let relem = recvbuf.as_datatype().size;
        let sc = sendbuf.counts().to_vec();
        let sd = sendbuf.displs().to_vec();
        let rd = recvbuf.displs().to_vec();
        let sbytes = sendbuf.as_bytes().to_vec();
        for peer in 0..n {
            let off = sd[peer as usize] as usize * selem;
            let len = sc[peer as usize] as usize * selem;
            let block = &sbytes[off..off + len];
            if peer == me {
                let ro = rd[me as usize] as usize * relem;
                recvbuf.as_bytes_mut()[ro..ro + len].copy_from_slice(block);
            } else {
                rt().send(
                    ctx,
                    me,
                    comm.world_rank(peer),
                    T_ALLTOALLV,
                    len as u64,
                    DT_BYTES,
                    block,
                )
                .expect("all_to_all_varcount");
            }
        }
        for peer in 0..n {
            if peer != me {
                let (_s, _t, _c, _d, payload) = rt().recv(ctx, peer, T_ALLTOALLV);
                let ro = rd[peer as usize] as usize * relem;
                recvbuf.as_bytes_mut()[ro..ro + payload.len()].copy_from_slice(&payload);
            }
        }
    }

    /// Non-blocking barrier (`MPI_Ibarrier`). Progresses on a background thread
    /// using a fresh context, so it genuinely overlaps subsequent work; `wait`
    /// joins it.
    fn immediate_barrier(&self) -> Request<'static, ()> {
        let data = self.comm_data();
        let comm = data.async_clone(data.derive_context(0x1BA5_1E11));
        let handle = std::thread::spawn(move || {
            let _ = barrier_bytes(&comm);
        });
        Request::from_join(StaticScope, handle)
    }

    /// Non-blocking all-gather (`MPI_Iallgather`).
    fn immediate_all_gather_into<'a, S, R, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        self.all_gather_into(sendbuf, recvbuf);
        Request::completed(scope)
    }

    /// Non-blocking all-to-all (`MPI_Ialltoall`).
    fn immediate_all_to_all_into<'a, S, R, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        self.all_to_all_into(sendbuf, recvbuf);
        Request::completed(scope)
    }

    /// Non-blocking all-reduce (`MPI_Iallreduce`). Progresses on a background
    /// thread using a fresh context so it overlaps computation; `wait` joins it.
    fn immediate_all_reduce_into<'a, S, R, O, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
        op: O,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        O: Operation + Send + 'static,
        Sc: Scope<'a>,
    {
        let data = self.comm_data();
        let comm = data.async_clone(data.derive_context(0x1A11_5EED));
        let dt = sendbuf.as_datatype().id;
        let send = sendbuf.as_bytes().to_vec();
        let rbytes = recvbuf.as_bytes_mut();
        let rptr = SendMutPtr(rbytes.as_mut_ptr());
        let rlen = rbytes.len();
        let handle = std::thread::spawn(move || {
            let rptr = rptr; // capture the whole (Send) wrapper, not `rptr.0`
                             // SAFETY: the receive buffer is borrowed by the returned Request for
                             // `'a`, which outlives this thread (joined on wait/drop).
            let recv = unsafe { std::slice::from_raw_parts_mut(rptr.0, rlen) };
            let _ = all_reduce_bytes(&comm, &send, recv, &op, dt);
        });
        Request::from_join(scope, handle)
    }

    /// Non-blocking inclusive scan (`MPI_Iscan`).
    fn immediate_scan_into<'a, S, R, O, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
        op: O,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        O: Operation,
        Sc: Scope<'a>,
    {
        self.scan_into(sendbuf, recvbuf, op);
        Request::completed(scope)
    }

    /// Non-blocking exclusive scan (`MPI_Iexscan`).
    fn immediate_exclusive_scan_into<'a, S, R, O, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
        op: O,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        O: Operation,
        Sc: Scope<'a>,
    {
        self.exclusive_scan_into(sendbuf, recvbuf, op);
        Request::completed(scope)
    }

    /// Non-blocking reduce-scatter-block (`MPI_Ireduce_scatter_block`).
    fn immediate_reduce_scatter_block_into<'a, S, R, O, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
        op: O,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        O: Operation,
        Sc: Scope<'a>,
    {
        self.reduce_scatter_block_into(sendbuf, recvbuf, op);
        Request::completed(scope)
    }
}

// Every communicator gets the rootless collectives.
impl<C: Communicator + ?Sized> CommunicatorCollectives for C {}

fn varcount_allgather<R>(comm: &CommData, send: &[u8], recvbuf: &mut R)
where
    R: PartitionedBufferMut + ?Sized,
{
    let ctx = comm.coll_context();
    let n = comm.size;
    let root = 0;
    let elem = recvbuf.as_datatype().size;
    let counts: Vec<i32> = recvbuf.counts().to_vec();
    let displs: Vec<i32> = recvbuf.displs().to_vec();
    if comm.rank == root {
        let out = recvbuf.as_bytes_mut();
        // place own
        let off = displs[root as usize] as usize * elem;
        out[off..off + send.len()].copy_from_slice(send);
        for peer in 0..n {
            if peer != root {
                let (_s, _t, _c, _d, payload) = rt().recv(ctx, peer, T_ALLGATHERV_UP);
                let off = displs[peer as usize] as usize * elem;
                out[off..off + payload.len()].copy_from_slice(&payload);
            }
        }
        let full = out.to_vec();
        for peer in 0..n {
            if peer != root {
                rt().send(
                    ctx,
                    comm.rank,
                    comm.world_rank(peer),
                    T_ALLGATHERV_DOWN,
                    full.len() as u64,
                    DT_BYTES,
                    &full,
                )
                .expect("allgatherv");
            }
        }
    } else {
        let _ = &counts;
        rt().send(
            ctx,
            comm.rank,
            comm.world_rank(root),
            T_ALLGATHERV_UP,
            send.len() as u64,
            DT_BYTES,
            send,
        )
        .expect("allgatherv");
        let (_s, _t, _c, _d, payload) = rt().recv(ctx, root, T_ALLGATHERV_DOWN);
        let out = recvbuf.as_bytes_mut();
        let k = out.len().min(payload.len());
        out[..k].copy_from_slice(&payload[..k]);
    }
}

/// Rooted collective operations, called on the [`Process`] that is the root
/// (and, by non-root ranks, on the same root process handle). Mirrors rsmpi's
/// `Root`.
pub trait Root {
    /// The rank of the root process.
    fn root_rank(&self) -> Rank;

    #[doc(hidden)]
    fn root_comm(&self) -> &CommData;

    /// Broadcast the root's buffer to every rank (`MPI_Bcast`).
    fn broadcast_into<Buf: BufferMut + ?Sized>(&self, buffer: &mut Buf) {
        bcast_bytes(self.root_comm(), self.root_rank(), buffer.as_bytes_mut())
            .expect("MPI_Bcast failed");
    }

    /// (non-root) Contribute to a gather (`MPI_Gather`).
    fn gather_into<S: Buffer + ?Sized>(&self, sendbuf: &S) {
        let send = sendbuf.as_bytes().to_vec();
        gather_bytes(self.root_comm(), self.root_rank(), &send, None).expect("MPI_Gather failed");
    }

    /// (root) Collect contributions from all ranks (`MPI_Gather`).
    fn gather_into_root<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        let send = sendbuf.as_bytes().to_vec();
        gather_bytes(
            self.root_comm(),
            self.root_rank(),
            &send,
            Some(recvbuf.as_bytes_mut()),
        )
        .expect("MPI_Gather failed");
    }

    /// (non-root) Receive this rank's slice of a scatter (`MPI_Scatter`).
    fn scatter_into<R: BufferMut + ?Sized>(&self, recvbuf: &mut R) {
        scatter_bytes(
            self.root_comm(),
            self.root_rank(),
            None,
            recvbuf.as_bytes_mut(),
        )
        .expect("MPI_Scatter failed");
    }

    /// (root) Distribute slices of the send buffer to all ranks
    /// (`MPI_Scatter`).
    fn scatter_into_root<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        let send = sendbuf.as_bytes().to_vec();
        scatter_bytes(
            self.root_comm(),
            self.root_rank(),
            Some(&send),
            recvbuf.as_bytes_mut(),
        )
        .expect("MPI_Scatter failed");
    }

    /// (non-root) Contribute to a reduction whose result lands on the root
    /// (`MPI_Reduce`).
    fn reduce_into<S, O>(&self, sendbuf: &S, op: O)
    where
        S: Buffer + ?Sized,
        O: Operation,
    {
        let dt = sendbuf.as_datatype().id;
        let send = sendbuf.as_bytes().to_vec();
        reduce_bytes_op(self.root_comm(), self.root_rank(), &send, None, &op, dt)
            .expect("MPI_Reduce failed");
    }

    /// (root) Reduce contributions from all ranks into `recvbuf`
    /// (`MPI_Reduce`).
    fn reduce_into_root<S, R, O>(&self, sendbuf: &S, recvbuf: &mut R, op: O)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
        O: Operation,
    {
        let dt = sendbuf.as_datatype().id;
        let send = sendbuf.as_bytes().to_vec();
        reduce_bytes_op(
            self.root_comm(),
            self.root_rank(),
            &send,
            Some(recvbuf.as_bytes_mut()),
            &op,
            dt,
        )
        .expect("MPI_Reduce failed");
    }

    /// (root) In-place reduce: the root's `buf` supplies its contribution and
    /// receives the result (`MPI_Reduce` with `MPI_IN_PLACE`). Non-root ranks
    /// call [`Root::reduce_into`] as usual.
    fn reduce_into_root_in_place<R, O>(&self, buf: &mut R, op: O)
    where
        R: BufferMut + ?Sized,
        O: Operation,
    {
        let dt = buf.as_datatype().id;
        let send = buf.as_bytes_mut().to_vec();
        reduce_bytes_op(
            self.root_comm(),
            self.root_rank(),
            &send,
            Some(buf.as_bytes_mut()),
            &op,
            dt,
        )
        .expect("MPI_Reduce (in place) failed");
    }

    /// (root) In-place gather: the root's `buf` holds its own block at slot
    /// `root` on entry and every rank's block on return (`MPI_Gather` with
    /// `MPI_IN_PLACE`). Non-root ranks call [`Root::gather_into`].
    fn gather_into_root_in_place<R>(&self, buf: &mut R)
    where
        R: BufferMut + ?Sized,
    {
        let comm = self.root_comm();
        let root = self.root_rank();
        let n = comm.size as usize;
        let total = buf.as_bytes_mut().len();
        let blk = total.checked_div(n).unwrap_or(0);
        let myblock = buf.as_bytes_mut()[root as usize * blk..root as usize * blk + blk].to_vec();
        gather_bytes(comm, root, &myblock, Some(buf.as_bytes_mut()))
            .expect("MPI_Gather (in place) failed");
    }

    // ---- varying-count rooted collectives ----

    /// (non-root) Contribute to a varying-count gather (`MPI_Gatherv`).
    fn gather_varcount_into<S: Buffer + ?Sized>(&self, sendbuf: &S) {
        let comm = self.root_comm();
        let ctx = comm.coll_context();
        let send = sendbuf.as_bytes();
        rt().send(
            ctx,
            comm.rank,
            comm.world_rank(self.root_rank()),
            T_GATHERV,
            send.len() as u64,
            DT_BYTES,
            send,
        )
        .expect("gatherv");
    }

    /// (root) Collect varying-count contributions (`MPI_Gatherv`).
    fn gather_varcount_into_root<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: PartitionedBufferMut + ?Sized,
    {
        let comm = self.root_comm();
        let root = self.root_rank();
        let ctx = comm.coll_context();
        let n = comm.size;
        let elem = recvbuf.as_datatype().size;
        let displs = recvbuf.displs().to_vec();
        let send = sendbuf.as_bytes().to_vec();
        let off = displs[root as usize] as usize * elem;
        recvbuf.as_bytes_mut()[off..off + send.len()].copy_from_slice(&send);
        for peer in 0..n {
            if peer != root {
                let (_s, _t, _c, _d, payload) = rt().recv(ctx, peer, T_GATHERV);
                let off = displs[peer as usize] as usize * elem;
                recvbuf.as_bytes_mut()[off..off + payload.len()].copy_from_slice(&payload);
            }
        }
    }

    /// (non-root) Receive this rank's slice of a varying-count scatter
    /// (`MPI_Scatterv`).
    fn scatter_varcount_into<R: BufferMut + ?Sized>(&self, recvbuf: &mut R) {
        let comm = self.root_comm();
        let ctx = comm.coll_context();
        let (_s, _t, _c, _d, payload) = rt().recv(ctx, self.root_rank(), T_SCATTERV);
        let out = recvbuf.as_bytes_mut();
        let k = out.len().min(payload.len());
        out[..k].copy_from_slice(&payload[..k]);
    }

    /// (root) Distribute varying-count slices to all ranks (`MPI_Scatterv`).
    fn scatter_varcount_into_root<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: PartitionedBuffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        let comm = self.root_comm();
        let root = self.root_rank();
        let ctx = comm.coll_context();
        let n = comm.size;
        let elem = sendbuf.as_datatype().size;
        let counts = sendbuf.counts().to_vec();
        let displs = sendbuf.displs().to_vec();
        let sbytes = sendbuf.as_bytes().to_vec();
        for peer in 0..n {
            let off = displs[peer as usize] as usize * elem;
            let len = counts[peer as usize] as usize * elem;
            let block = &sbytes[off..off + len];
            if peer == root {
                let out = recvbuf.as_bytes_mut();
                let k = out.len().min(block.len());
                out[..k].copy_from_slice(&block[..k]);
            } else {
                rt().send(
                    ctx,
                    comm.rank,
                    comm.world_rank(peer),
                    T_SCATTERV,
                    len as u64,
                    DT_BYTES,
                    block,
                )
                .expect("scatterv");
            }
        }
    }

    // ---- non-blocking rooted collectives (complete synchronously here) ----

    /// Non-blocking broadcast (`MPI_Ibcast`).
    fn immediate_broadcast_into<'a, Buf, Sc>(
        &self,
        scope: Sc,
        buffer: &'a mut Buf,
    ) -> Request<'a, Buf, Sc>
    where
        Buf: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        self.broadcast_into(buffer);
        Request::completed(scope)
    }

    /// (non-root) Non-blocking gather (`MPI_Igather`).
    fn immediate_gather_into<'a, S, Sc>(&self, scope: Sc, sendbuf: &'a S) -> Request<'a, S, Sc>
    where
        S: 'a + Buffer + ?Sized,
        Sc: Scope<'a>,
    {
        self.gather_into(sendbuf);
        Request::completed(scope)
    }

    /// (root) Non-blocking gather (`MPI_Igather`).
    fn immediate_gather_into_root<'a, S, R, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        self.gather_into_root(sendbuf, recvbuf);
        Request::completed(scope)
    }

    /// (non-root) Non-blocking scatter (`MPI_Iscatter`).
    fn immediate_scatter_into<'a, R, Sc>(&self, scope: Sc, recvbuf: &'a mut R) -> Request<'a, R, Sc>
    where
        R: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        self.scatter_into(recvbuf);
        Request::completed(scope)
    }

    /// (root) Non-blocking scatter (`MPI_Iscatter`).
    fn immediate_scatter_into_root<'a, S, R, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        self.scatter_into_root(sendbuf, recvbuf);
        Request::completed(scope)
    }

    /// (non-root) Non-blocking reduce (`MPI_Ireduce`).
    fn immediate_reduce_into<'a, S, O, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        op: O,
    ) -> Request<'a, S, Sc>
    where
        S: 'a + Buffer + ?Sized,
        O: Operation,
        Sc: Scope<'a>,
    {
        self.reduce_into(sendbuf, op);
        Request::completed(scope)
    }

    /// (root) Non-blocking reduce (`MPI_Ireduce`).
    fn immediate_reduce_into_root<'a, S, R, O, Sc>(
        &self,
        scope: Sc,
        sendbuf: &'a S,
        recvbuf: &'a mut R,
        op: O,
    ) -> Request<'a, R, Sc>
    where
        S: 'a + Buffer + ?Sized,
        R: 'a + BufferMut + ?Sized,
        O: Operation,
        Sc: Scope<'a>,
    {
        self.reduce_into_root(sendbuf, recvbuf, op);
        Request::completed(scope)
    }

    /// Spawn `maxprocs` copies of `program` (with `args`) as a new MPI world and
    /// return an inter-communicator to them (`MPI_Comm_spawn`). Collective over
    /// the root's communicator; the spawned children obtain the matching parent
    /// inter-communicator via [`crate::topology::Communicator::parent`].
    fn spawn(
        &self,
        program: &str,
        args: &[String],
        maxprocs: Rank,
    ) -> Result<crate::topology::InterCommunicator, crate::MpiError> {
        crate::dpm::spawn_impl(self.root_comm(), self.root_rank(), program, args, maxprocs)
    }
}

impl Root for Process<'_> {
    fn root_rank(&self) -> Rank {
        self.rank
    }
    fn root_comm(&self) -> &CommData {
        self.comm
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bytes_of<T: Copy>(v: &[T]) -> Vec<u8> {
        let mut out = vec![0u8; std::mem::size_of_val(v)];
        // SAFETY: T is Copy POD in these tests.
        unsafe {
            std::ptr::copy_nonoverlapping(v.as_ptr() as *const u8, out.as_mut_ptr(), out.len());
        }
        out
    }

    #[test]
    fn sum_i32() {
        let mut acc = bytes_of(&[1i32, 2, 3]);
        let val = bytes_of(&[10i32, 20, 30]);
        SystemOperation::sum().reduce_bytes(ids::I32, &mut acc, &val);
        let result: Vec<i32> = vec_from_bytes(&acc);
        assert_eq!(result, vec![11, 22, 33]);
    }

    #[test]
    fn max_f64() {
        let mut acc = bytes_of(&[1.0f64, 5.0]);
        let val = bytes_of(&[3.0f64, 2.0]);
        SystemOperation::max().reduce_bytes(ids::F64, &mut acc, &val);
        let result: Vec<f64> = vec_from_bytes(&acc);
        assert_eq!(result, vec![3.0, 5.0]);
    }

    #[test]
    fn bitwise_and_u8() {
        let mut acc = bytes_of(&[0b1100u8, 0xFF]);
        let val = bytes_of(&[0b1010u8, 0x0F]);
        SystemOperation::bitwise_and().reduce_bytes(ids::U8, &mut acc, &val);
        assert_eq!(acc, vec![0b1000, 0x0F]);
    }

    #[test]
    fn product_and_min() {
        let mut acc = bytes_of(&[2i64, 9]);
        SystemOperation::product().reduce_bytes(ids::I64, &mut acc, &bytes_of(&[3i64, 3]));
        assert_eq!(vec_from_bytes::<i64>(&acc), vec![6, 27]);

        let mut acc = bytes_of(&[2i64, 9]);
        SystemOperation::min().reduce_bytes(ids::I64, &mut acc, &bytes_of(&[3i64, 3]));
        assert_eq!(vec_from_bytes::<i64>(&acc), vec![2, 3]);
    }

    /// Tiny deterministic xorshift PRNG (no external deps, Miri-friendly).
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            let mut x = self.0;
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            self.0 = x;
            x
        }
        fn i32(&mut self) -> i32 {
            (self.next() >> 32) as i32 / 4
        }
    }

    /// Property test: the byte-level reductions must match a plain-Rust
    /// reference over many random inputs and every commutative i32 op.
    type RefOp = fn(i32, i32) -> i32;

    #[test]
    fn reduce_matches_reference_i32() {
        let mut rng = Rng(0x1234_5678_9abc_def0);
        let ops: &[(SystemOperation, RefOp)] = &[
            (SystemOperation::sum(), |a, b| a.wrapping_add(b)),
            (SystemOperation::product(), |a, b| a.wrapping_mul(b)),
            (SystemOperation::max(), |a, b| a.max(b)),
            (SystemOperation::min(), |a, b| a.min(b)),
            (SystemOperation::bitwise_and(), |a, b| a & b),
            (SystemOperation::bitwise_or(), |a, b| a | b),
            (SystemOperation::bitwise_xor(), |a, b| a ^ b),
        ];
        for _ in 0..500 {
            let len = (rng.next() % 8) as usize + 1;
            let a: Vec<i32> = (0..len).map(|_| rng.i32()).collect();
            let b: Vec<i32> = (0..len).map(|_| rng.i32()).collect();
            for (op, refn) in ops {
                let expected: Vec<i32> = a.iter().zip(&b).map(|(&x, &y)| refn(x, y)).collect();
                let mut acc = bytes_of(&a);
                op.reduce_bytes(ids::I32, &mut acc, &bytes_of(&b));
                assert_eq!(vec_from_bytes::<i32>(&acc), expected, "op mismatch");
            }
        }
    }
}
