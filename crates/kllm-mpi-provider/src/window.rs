//! One-sided (RMA) communication: [`Window`]s of memory that remote ranks can
//! `put` into, `get` from, and `accumulate` into without the target making a
//! matching call. This corresponds to MPI's one-sided chapter (`MPI_Win_*`),
//! which released rsmpi does not currently expose.
//!
//! Each window runs on its own communication context; a lightweight
//! active-message handler on every rank services incoming RMA requests against
//! the local window memory (guarded by a mutex), so operations complete even
//! while the target is busy computing. Operations are synchronous (they wait
//! for the target to acknowledge), which makes [`Window::fence`] a simple
//! epoch barrier.
//!
//! ```no_run
//! use mpi::traits::*;
//! use mpi::window::Window;
//!
//! let universe = mpi::initialize().unwrap();
//! let world = universe.world();
//! let mut win = Window::<i32>::allocate(4, &world);
//! win.fence();
//! if world.rank() == 0 {
//!     win.put(1, 0, &[10, 20, 30, 40]); // write into rank 1's window
//! }
//! win.fence();
//! ```

use std::marker::PhantomData;
use std::sync::{Arc, Mutex};

use crate::collective::{CommunicatorCollectives, Operation, SystemOperation};
use crate::datatype::Equivalence;
use crate::topology::{Communicator, SimpleCommunicator};
use crate::transport;
use crate::Rank;

// Request tags (on the window request context).
const OP_PUT: i32 = 1;
const OP_GET: i32 = 2;
const OP_ACC: i32 = 3;
// Reply tags (on the window reply context).
const REP_ACK: i32 = 1;
const REP_GET: i32 = 2;

#[inline]
fn as_bytes<T>(v: &[T]) -> &[u8] {
    // SAFETY: callers use POD `Equivalence` types.
    unsafe { std::slice::from_raw_parts(v.as_ptr() as *const u8, std::mem::size_of_val(v)) }
}

#[inline]
fn as_bytes_mut<T>(v: &mut [T]) -> &mut [u8] {
    // SAFETY: as above.
    unsafe { std::slice::from_raw_parts_mut(v.as_mut_ptr() as *mut u8, std::mem::size_of_val(v)) }
}

fn u64_le(bytes: &[u8]) -> u64 {
    u64::from_le_bytes(bytes[..8].try_into().unwrap())
}

/// A window of memory exposed for one-sided access (`MPI_Win`).
pub struct Window<T: Equivalence + Send + Sync> {
    comm: SimpleCommunicator,
    req_ctx: u32,
    reply_ctx: u32,
    mem: Arc<Mutex<Vec<T>>>,
    elem: usize,
    _t: PhantomData<T>,
}

impl<T: Equivalence + Send + Sync> Window<T> {
    /// Collectively create a window of `count` elements (zero-initialized) on
    /// every rank of `comm` (`MPI_Win_allocate`).
    pub fn allocate<C: Communicator>(count: usize, comm: &C) -> Window<T>
    where
        T: Default + Clone,
    {
        Window::from_vec(vec![T::default(); count], comm)
    }

    /// Collectively expose an existing buffer as a window (`MPI_Win_create`).
    pub fn from_vec<C: Communicator>(data: Vec<T>, comm: &C) -> Window<T> {
        // A private context for this window's traffic, agreed by all members.
        let dup = comm.duplicate();
        let base = dup.comm_data().derive_context(0x5749_4E00); // "WIN\0"
        let req_ctx = dup.comm_data().derive_context(base ^ 0x11);
        let reply_ctx = dup.comm_data().derive_context(base ^ 0x22);

        let elem = std::mem::size_of::<T>().max(1);
        let mem = Arc::new(Mutex::new(data));

        // Register the active-message handler that services incoming RMA ops.
        let mem_h = Arc::clone(&mem);
        let world_ranks: Vec<i32> = dup.comm_data().world_ranks.clone();
        let my_rank = dup.rank();
        let handler: transport::Handler =
            Arc::new(move |source, tag, _count, datatype, payload| {
                let rt = transport::runtime();
                let reply_to = world_ranks[source as usize];
                match tag {
                    OP_PUT => {
                        let disp = u64_le(&payload) as usize;
                        let data = &payload[8..];
                        {
                            let mut m = mem_h.lock().unwrap();
                            let off = disp * elem;
                            as_bytes_mut(&mut m[..])[off..off + data.len()].copy_from_slice(data);
                        }
                        let _ = rt.send(reply_ctx, my_rank, reply_to, REP_ACK, 0, datatype, &[]);
                    }
                    OP_GET => {
                        let disp = u64_le(&payload) as usize;
                        let cnt = u64_le(&payload[8..]) as usize;
                        let out = {
                            let m = mem_h.lock().unwrap();
                            let off = disp * elem;
                            as_bytes(&m[..])[off..off + cnt * elem].to_vec()
                        };
                        let _ = rt.send(
                            reply_ctx, my_rank, reply_to, REP_GET, cnt as u64, datatype, &out,
                        );
                    }
                    OP_ACC => {
                        let op = SystemOperation::from_code(payload[0]);
                        let disp = u64_le(&payload[1..]) as usize;
                        let data = &payload[9..];
                        {
                            let mut m = mem_h.lock().unwrap();
                            let off = disp * elem;
                            op.reduce_bytes(
                                datatype,
                                &mut as_bytes_mut(&mut m[..])[off..off + data.len()],
                                data,
                            );
                        }
                        let _ = rt.send(reply_ctx, my_rank, reply_to, REP_ACK, 0, datatype, &[]);
                    }
                    _ => {}
                }
            });
        transport::runtime().register_handler(req_ctx, handler);

        Window {
            comm: dup,
            req_ctx,
            reply_ctx,
            mem,
            elem,
            _t: PhantomData,
        }
    }

    /// This rank in the window's communicator.
    pub fn rank(&self) -> Rank {
        self.comm.rank()
    }

    /// Number of ranks sharing the window.
    pub fn size(&self) -> Rank {
        self.comm.size()
    }

    /// Number of elements in the local window memory.
    pub fn local_len(&self) -> usize {
        self.mem.lock().unwrap().len()
    }

    /// Inspect the local window memory.
    pub fn with_local<R>(&self, f: impl FnOnce(&[T]) -> R) -> R {
        f(&self.mem.lock().unwrap())
    }

    /// Mutate the local window memory.
    pub fn with_local_mut<R>(&self, f: impl FnOnce(&mut [T]) -> R) -> R {
        f(&mut self.mem.lock().unwrap())
    }

    /// Write `data` into rank `target`'s window at element offset
    /// `target_disp` (`MPI_Put`). Completes when the target acknowledges.
    pub fn put(&self, target: Rank, target_disp: usize, data: &[T]) {
        let mut payload = Vec::with_capacity(8 + data.len() * self.elem);
        payload.extend_from_slice(&(target_disp as u64).to_le_bytes());
        payload.extend_from_slice(as_bytes(data));
        self.request(
            target,
            OP_PUT,
            T::equivalent_datatype().id,
            data.len() as u64,
            &payload,
        );
        let _ = transport::runtime().recv(self.reply_ctx, target, REP_ACK);
    }

    /// Read `buf.len()` elements from rank `target`'s window at element offset
    /// `target_disp` into `buf` (`MPI_Get`).
    pub fn get(&self, target: Rank, target_disp: usize, buf: &mut [T]) {
        let mut payload = Vec::with_capacity(16);
        payload.extend_from_slice(&(target_disp as u64).to_le_bytes());
        payload.extend_from_slice(&(buf.len() as u64).to_le_bytes());
        self.request(
            target,
            OP_GET,
            T::equivalent_datatype().id,
            buf.len() as u64,
            &payload,
        );
        let (_s, _t, _c, _d, reply) = transport::runtime().recv(self.reply_ctx, target, REP_GET);
        let dst = as_bytes_mut(buf);
        let n = dst.len().min(reply.len());
        dst[..n].copy_from_slice(&reply[..n]);
    }

    /// Combine `data` into rank `target`'s window at element offset
    /// `target_disp` using `op` (`MPI_Accumulate`).
    pub fn accumulate(&self, target: Rank, target_disp: usize, data: &[T], op: SystemOperation) {
        let mut payload = Vec::with_capacity(9 + data.len() * self.elem);
        payload.push(op.to_code());
        payload.extend_from_slice(&(target_disp as u64).to_le_bytes());
        payload.extend_from_slice(as_bytes(data));
        self.request(
            target,
            OP_ACC,
            T::equivalent_datatype().id,
            data.len() as u64,
            &payload,
        );
        let _ = transport::runtime().recv(self.reply_ctx, target, REP_ACK);
    }

    fn request(&self, target: Rank, tag: i32, datatype: u32, count: u64, payload: &[u8]) {
        transport::runtime()
            .send(
                self.req_ctx,
                self.comm.rank(),
                self.comm.comm_data().world_rank(target),
                tag,
                count,
                datatype,
                payload,
            )
            .expect("RMA request send failed");
    }

    /// Collective synchronization delimiting an access epoch (`MPI_Win_fence`).
    /// Because operations are synchronous, this is a barrier over the window's
    /// communicator.
    pub fn fence(&self) {
        self.comm.barrier();
    }

    /// Begin passive-target access to `_target` (`MPI_Win_lock`). Operations in
    /// this implementation are already atomic per element run, so this is a
    /// no-op provided for API completeness.
    pub fn lock(&self, _target: Rank) {}

    /// End passive-target access (`MPI_Win_unlock`).
    pub fn unlock(&self, _target: Rank) {}
}

impl<T: Equivalence + Send + Sync> Drop for Window<T> {
    fn drop(&mut self) {
        if transport::is_initialized() {
            transport::runtime().unregister_handler(self.req_ctx);
        }
    }
}
