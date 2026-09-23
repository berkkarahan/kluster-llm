//! Point-to-point communication. Mirrors `mpi::point_to_point` in rsmpi: the
//! [`Source`] (receive) and [`Destination`] (send) traits, [`Status`],
//! [`Message`] (matched probe) and the `send_receive` free functions.

use std::marker::PhantomData;

use crate::datatype::{Buffer, BufferMut, DatatypeRef, Equivalence};
use crate::request::{Request, Scope};
use crate::topology::CommData;
use crate::transport::{self, ANY_SOURCE, ANY_TAG};
use crate::{Count, Rank, Tag};

/// The default tag used by tagless send/receive calls.
pub const DEFAULT_TAG: Tag = 0;

// ---- Byte <-> typed helpers (POD reinterpretation for `Equivalence`). ----

pub(crate) fn elem_from_bytes<T: Equivalence>(bytes: &[u8]) -> T {
    assert!(
        bytes.len() >= std::mem::size_of::<T>(),
        "received message smaller than expected scalar"
    );
    // SAFETY: `T: Equivalence` is POD; read unaligned since `bytes` may not be
    // aligned to `T`.
    unsafe { std::ptr::read_unaligned(bytes.as_ptr() as *const T) }
}

pub(crate) fn vec_from_bytes<T: Equivalence>(bytes: &[u8]) -> Vec<T> {
    let size = std::mem::size_of::<T>();
    let count = bytes.len().checked_div(size).unwrap_or(0);
    let mut v: Vec<T> = Vec::with_capacity(count);
    // SAFETY: capacity is `count`, and we copy exactly `count * size` bytes into
    // properly-aligned storage; `T` is POD so any bit pattern is valid.
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), v.as_mut_ptr() as *mut u8, count * size);
        v.set_len(count);
    }
    v
}

pub(crate) fn copy_into_buffer<Buf: BufferMut + ?Sized>(buf: &mut Buf, bytes: &[u8]) {
    // Routes through `scatter_from` so derived-datatype receive buffers place
    // the bytes according to their type map; the default is a contiguous copy.
    buf.scatter_from(bytes);
}

/// The outcome of a receive or probe (`MPI_Status`).
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct Status {
    source: Rank,
    tag: Tag,
    count: Count,
    byte_len: usize,
}

impl Status {
    pub(crate) fn new(source: Rank, tag: Tag, count: Count, byte_len: usize) -> Status {
        Status {
            source,
            tag,
            count,
            byte_len,
        }
    }

    /// The rank (within the communicator) that sent the message.
    pub fn source_rank(&self) -> Rank {
        self.source
    }

    /// The tag the message was sent with.
    pub fn tag(&self) -> Tag {
        self.tag
    }

    /// Number of elements of datatype `dt` in the message.
    pub fn count(&self, dt: DatatypeRef) -> Count {
        self.byte_len.checked_div(dt.size).unwrap_or(0) as Count
    }

    /// Whether the matching operation was cancelled. Always `false` in this
    /// implementation (cancellation of already-buffered messages is a no-op).
    pub fn is_cancelled(&self) -> bool {
        false
    }

    /// American-spelling alias of [`Status::is_cancelled`].
    pub fn is_canceled(&self) -> bool {
        false
    }
}

/// A message that has been matched by a probe but not yet received
/// (`MPI_Message`). In this implementation the payload has already been pulled
/// off the wire and is stored inline; receiving it is a local copy.
pub struct Message {
    source: Rank,
    tag: Tag,
    count: Count,
    payload: Vec<u8>,
}

impl Message {
    /// Receive a previously-probed message into a freshly allocated `Vec`
    /// (`MPI_Mrecv`).
    pub fn matched_receive_vec<Msg: Equivalence>(self) -> (Vec<Msg>, Status) {
        let status = Status::new(self.source, self.tag, self.count, self.payload.len());
        (vec_from_bytes::<Msg>(&self.payload), status)
    }

    /// Receive a previously-probed message into an existing buffer.
    pub fn matched_receive_into<Buf: BufferMut + ?Sized>(self, buf: &mut Buf) -> Status {
        let status = Status::new(self.source, self.tag, self.count, self.payload.len());
        copy_into_buffer(buf, &self.payload);
        status
    }
}

/// The receive side of point-to-point communication (`MPI_ANY_SOURCE` or a
/// specific rank).
pub trait Source {
    /// Internal: the communicator this source belongs to.
    #[doc(hidden)]
    fn src_comm(&self) -> &CommData;

    /// The rank being received from (or `MPI_ANY_SOURCE`).
    fn source_rank(&self) -> Rank;

    /// Receive a single value (`MPI_Recv`).
    fn receive<Msg: Equivalence>(&self) -> (Msg, Status) {
        self.receive_with_tag(ANY_TAG)
    }

    /// Receive a single value with a specific tag.
    fn receive_with_tag<Msg: Equivalence>(&self, tag: Tag) -> (Msg, Status) {
        let comm = self.src_comm();
        let (src, t, count, _dt, payload) =
            transport::runtime().recv(comm.context, self.source_rank(), tag);
        let val = elem_from_bytes::<Msg>(&payload);
        (val, Status::new(src, t, count as Count, payload.len()))
    }

    /// Receive a variable number of values into a new `Vec`.
    fn receive_vec<Msg: Equivalence>(&self) -> (Vec<Msg>, Status) {
        self.receive_vec_with_tag(ANY_TAG)
    }

    /// Receive a variable number of values into a new `Vec`, with a tag.
    fn receive_vec_with_tag<Msg: Equivalence>(&self, tag: Tag) -> (Vec<Msg>, Status) {
        let comm = self.src_comm();
        let (src, t, count, _dt, payload) =
            transport::runtime().recv(comm.context, self.source_rank(), tag);
        let v = vec_from_bytes::<Msg>(&payload);
        (v, Status::new(src, t, count as Count, payload.len()))
    }

    /// Receive into an existing buffer (`MPI_Recv`).
    fn receive_into<Buf: BufferMut + ?Sized>(&self, buf: &mut Buf) -> Status {
        self.receive_into_with_tag(buf, ANY_TAG)
    }

    /// Receive into an existing buffer, with a tag.
    fn receive_into_with_tag<Buf: BufferMut + ?Sized>(&self, buf: &mut Buf, tag: Tag) -> Status {
        let comm = self.src_comm();
        let (src, t, count, _dt, payload) =
            transport::runtime().recv(comm.context, self.source_rank(), tag);
        copy_into_buffer(buf, &payload);
        Status::new(src, t, count as Count, payload.len())
    }

    /// Blocking probe (`MPI_Probe`): wait for a matching message, describe it
    /// without receiving.
    fn probe(&self) -> Status {
        self.probe_with_tag(ANY_TAG)
    }

    /// Blocking probe with a tag.
    fn probe_with_tag(&self, tag: Tag) -> Status {
        let comm = self.src_comm();
        let (src, t, count, _dt, len) =
            transport::runtime().probe_blocking(comm.context, self.source_rank(), tag);
        Status::new(src, t, count as Count, len)
    }

    /// Non-blocking probe (`MPI_Iprobe`).
    fn immediate_probe(&self) -> Option<Status> {
        self.immediate_probe_with_tag(ANY_TAG)
    }

    /// Non-blocking probe with a tag.
    fn immediate_probe_with_tag(&self, tag: Tag) -> Option<Status> {
        let comm = self.src_comm();
        transport::runtime()
            .probe(comm.context, self.source_rank(), tag)
            .map(|(src, t, count, _dt, len)| Status::new(src, t, count as Count, len))
    }

    /// Matched probe (`MPI_Mprobe`): remove a matching message from the queue
    /// and return a [`Message`] to receive it.
    fn matched_probe(&self) -> (Message, Status) {
        self.matched_probe_with_tag(ANY_TAG)
    }

    /// Matched probe with a tag.
    fn matched_probe_with_tag(&self, tag: Tag) -> (Message, Status) {
        let comm = self.src_comm();
        let (src, t, count, _dt, payload) =
            transport::runtime().recv(comm.context, self.source_rank(), tag);
        let status = Status::new(src, t, count as Count, payload.len());
        (
            Message {
                source: src,
                tag: t,
                count: count as Count,
                payload,
            },
            status,
        )
    }

    /// Non-blocking matched probe (`MPI_Improbe`).
    fn immediate_matched_probe(&self) -> Option<(Message, Status)> {
        self.immediate_matched_probe_with_tag(ANY_TAG)
    }

    /// Non-blocking matched probe with a tag.
    fn immediate_matched_probe_with_tag(&self, tag: Tag) -> Option<(Message, Status)> {
        if self.immediate_probe_with_tag(tag).is_some() {
            Some(self.matched_probe_with_tag(tag))
        } else {
            None
        }
    }

    /// Non-blocking receive of a single value, returning a [`ReceiveFuture`].
    fn immediate_receive<Msg: Equivalence>(&self) -> ReceiveFuture<Msg> {
        self.immediate_receive_with_tag(ANY_TAG)
    }

    /// Non-blocking receive of a single value with a tag.
    fn immediate_receive_with_tag<Msg: Equivalence>(&self, tag: Tag) -> ReceiveFuture<Msg> {
        let comm = self.src_comm();
        ReceiveFuture {
            ctx: comm.context,
            source: self.source_rank(),
            tag,
            _p: PhantomData,
        }
    }

    /// Non-blocking receive into an existing buffer (`MPI_Irecv`), returning a
    /// [`Request`] that must be completed via `wait`/`test`.
    fn immediate_receive_into<'a, Sc, Buf>(
        &self,
        scope: Sc,
        buf: &'a mut Buf,
    ) -> Request<'a, Buf, Sc>
    where
        Buf: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        self.immediate_receive_into_with_tag(scope, buf, ANY_TAG)
    }

    /// Non-blocking receive into a buffer, with a tag.
    fn immediate_receive_into_with_tag<'a, Sc, Buf>(
        &self,
        scope: Sc,
        buf: &'a mut Buf,
        tag: Tag,
    ) -> Request<'a, Buf, Sc>
    where
        Buf: 'a + BufferMut + ?Sized,
        Sc: Scope<'a>,
    {
        let ctx = self.src_comm().context;
        let source = self.source_rank();
        let bytes = buf.as_bytes_mut();
        let ptr = bytes.as_mut_ptr();
        let len = bytes.len();
        Request::pending_recv(scope, ptr, len, ctx, source, tag)
    }

    /// Create a persistent receive request bound to `buf` (`MPI_Recv_init`).
    /// `start` it (and `wait`) repeatedly to re-post the receive.
    fn receive_init<'a, Buf: BufferMut + ?Sized>(
        &self,
        buf: &'a mut Buf,
    ) -> crate::request::PersistentRequest<'a> {
        let ctx = self.src_comm().context;
        let source = self.source_rank();
        let bytes = buf.as_bytes_mut();
        let ptr = bytes.as_mut_ptr();
        let len = bytes.len();
        crate::request::PersistentRequest::new_recv(ctx, source, DEFAULT_TAG, ptr, len)
    }
}

/// The send side of point-to-point communication.
pub trait Destination {
    /// Internal: the communicator this destination belongs to.
    #[doc(hidden)]
    fn dst_comm(&self) -> &CommData;

    /// The rank being sent to.
    fn destination_rank(&self) -> Rank;

    #[doc(hidden)]
    fn do_try_send<Buf: Buffer + ?Sized>(
        &self,
        buf: &Buf,
        tag: Tag,
    ) -> Result<(), crate::MpiError> {
        let comm = self.dst_comm();
        let dt = buf.as_datatype();
        transport::runtime()
            .send(
                comm.context,
                comm.rank,
                comm.world_rank(self.destination_rank()),
                tag,
                buf.count() as u64,
                dt.id,
                buf.as_bytes(),
            )
            .map_err(|e| crate::MpiError::Other(format!("send failed: {e}")))
    }

    #[doc(hidden)]
    fn do_send<Buf: Buffer + ?Sized>(&self, buf: &Buf, tag: Tag) {
        self.do_try_send(buf, tag).expect("MPI send failed");
    }

    /// Standard-mode blocking send (`MPI_Send`).
    fn send<Buf: Buffer + ?Sized>(&self, buf: &Buf) {
        self.do_send(buf, DEFAULT_TAG);
    }

    /// Standard-mode blocking send with a tag.
    fn send_with_tag<Buf: Buffer + ?Sized>(&self, buf: &Buf, tag: Tag) {
        self.do_send(buf, tag);
    }

    /// Fallible send: returns an error instead of panicking if the transport
    /// fails (e.g. the destination is unreachable). Honors the spirit of
    /// [`crate::error::ErrorHandler::Return`] without changing the panic-based
    /// default API.
    fn try_send<Buf: Buffer + ?Sized>(&self, buf: &Buf) -> Result<(), crate::MpiError> {
        self.do_try_send(buf, DEFAULT_TAG)
    }

    /// Fallible send with a tag.
    fn try_send_with_tag<Buf: Buffer + ?Sized>(
        &self,
        buf: &Buf,
        tag: Tag,
    ) -> Result<(), crate::MpiError> {
        self.do_try_send(buf, tag)
    }

    /// Buffered-mode send (`MPI_Bsend`). Behaves as a standard send here since
    /// the transport already buffers on the receiver side.
    fn buffered_send<Buf: Buffer + ?Sized>(&self, buf: &Buf) {
        self.do_send(buf, DEFAULT_TAG);
    }

    /// Buffered-mode send with a tag.
    fn buffered_send_with_tag<Buf: Buffer + ?Sized>(&self, buf: &Buf, tag: Tag) {
        self.do_send(buf, tag);
    }

    /// Synchronous-mode send (`MPI_Ssend`).
    fn synchronous_send<Buf: Buffer + ?Sized>(&self, buf: &Buf) {
        self.do_send(buf, DEFAULT_TAG);
    }

    /// Synchronous-mode send with a tag.
    fn synchronous_send_with_tag<Buf: Buffer + ?Sized>(&self, buf: &Buf, tag: Tag) {
        self.do_send(buf, tag);
    }

    /// Ready-mode send (`MPI_Rsend`).
    ///
    /// # Safety
    ///
    /// The matching receive must already be posted. This implementation does
    /// not rely on that, so it is effectively safe, but the `unsafe` marker is
    /// retained for signature parity.
    unsafe fn ready_send<Buf: Buffer + ?Sized>(&self, buf: &Buf) {
        self.do_send(buf, DEFAULT_TAG);
    }

    /// Ready-mode send with a tag.
    ///
    /// # Safety
    ///
    /// See [`Destination::ready_send`].
    unsafe fn ready_send_with_tag<Buf: Buffer + ?Sized>(&self, buf: &Buf, tag: Tag) {
        self.do_send(buf, tag);
    }

    /// Non-blocking standard send (`MPI_Isend`), returning a [`Request`].
    fn immediate_send<'a, Sc, Buf>(&self, scope: Sc, buf: &'a Buf) -> Request<'a, Buf, Sc>
    where
        Buf: 'a + Buffer + ?Sized,
        Sc: Scope<'a>,
    {
        self.immediate_send_with_tag(scope, buf, DEFAULT_TAG)
    }

    /// Non-blocking standard send with a tag.
    fn immediate_send_with_tag<'a, Sc, Buf>(
        &self,
        scope: Sc,
        buf: &'a Buf,
        tag: Tag,
    ) -> Request<'a, Buf, Sc>
    where
        Buf: 'a + Buffer + ?Sized,
        Sc: Scope<'a>,
    {
        // Returns promptly: small messages are a single buffered socket write;
        // large messages use the rendezvous protocol, which only sends a tiny
        // ready-to-send announcement here and transfers the bulk data later on
        // a background thread when the receiver is ready. Receives likewise
        // overlap, since per-connection reader threads keep draining the wire.
        self.do_send(buf, tag);
        Request::completed(scope)
    }

    /// Non-blocking buffered send.
    fn immediate_buffered_send<'a, Sc, Buf>(&self, scope: Sc, buf: &'a Buf) -> Request<'a, Buf, Sc>
    where
        Buf: 'a + Buffer + ?Sized,
        Sc: Scope<'a>,
    {
        self.immediate_send_with_tag(scope, buf, DEFAULT_TAG)
    }

    /// Non-blocking synchronous send.
    fn immediate_synchronous_send<'a, Sc, Buf>(
        &self,
        scope: Sc,
        buf: &'a Buf,
    ) -> Request<'a, Buf, Sc>
    where
        Buf: 'a + Buffer + ?Sized,
        Sc: Scope<'a>,
    {
        self.immediate_send_with_tag(scope, buf, DEFAULT_TAG)
    }

    /// Create a persistent send request bound to `buf` (`MPI_Send_init`).
    /// `start` it (and `wait`) repeatedly to re-send the current buffer
    /// contents.
    fn send_init<'a, Buf: Buffer + ?Sized>(
        &self,
        buf: &'a Buf,
    ) -> crate::request::PersistentRequest<'a> {
        let comm = self.dst_comm();
        let bytes = buf.as_bytes();
        crate::request::PersistentRequest::new_send(
            comm.context,
            comm.rank,
            comm.world_rank(self.destination_rank()),
            DEFAULT_TAG,
            buf.as_datatype().id,
            buf.count() as u64,
            bytes.as_ptr(),
            bytes.len(),
        )
    }
}

/// A pending single-value receive (`MPI_Irecv` for a scalar), completed by
/// [`ReceiveFuture::get`]. Mirrors rsmpi's `ReceiveFuture`.
pub struct ReceiveFuture<T> {
    ctx: u32,
    source: Rank,
    tag: Tag,
    _p: PhantomData<T>,
}

impl<T: Equivalence> ReceiveFuture<T> {
    /// Block until the message arrives and return it.
    pub fn get(self) -> (T, Status) {
        let (src, t, count, _dt, payload) =
            transport::runtime().recv(self.ctx, self.source, self.tag);
        (
            elem_from_bytes::<T>(&payload),
            Status::new(src, t, count as Count, payload.len()),
        )
    }
}

// ---- send_receive family (MPI_Sendrecv). ----

/// Send `msg` to `destination` and receive a value from `source`
/// (`MPI_Sendrecv`).
pub fn send_receive<R, M, D, S>(msg: &M, destination: &D, source: &S) -> (R, Status)
where
    M: Equivalence,
    R: Equivalence,
    D: Destination,
    S: Source,
{
    destination.send(msg);
    source.receive::<R>()
}

/// Send `msg` and receive into `buf`, using explicit tags
/// (`MPI_Sendrecv`).
pub fn send_receive_into_with_tags<M, D, B, S>(
    msg: &M,
    destination: &D,
    sendtag: Tag,
    buf: &mut B,
    source: &S,
    receivetag: Tag,
) -> Status
where
    M: Buffer + ?Sized,
    D: Destination,
    B: BufferMut + ?Sized,
    S: Source,
{
    destination.send_with_tag(msg, sendtag);
    source.receive_into_with_tag(buf, receivetag)
}

/// Send `msg` and receive into `buf` with default tags.
pub fn send_receive_into<M, D, B, S>(msg: &M, destination: &D, buf: &mut B, source: &S) -> Status
where
    M: Buffer + ?Sized,
    D: Destination,
    B: BufferMut + ?Sized,
    S: Source,
{
    send_receive_into_with_tags(msg, destination, DEFAULT_TAG, buf, source, ANY_TAG)
}

/// Send the contents of `buf`, then receive into the same `buf`
/// (`MPI_Sendrecv_replace`), using explicit tags.
pub fn send_receive_replace_into_with_tags<B, D, S>(
    buf: &mut B,
    destination: &D,
    sendtag: Tag,
    source: &S,
    receivetag: Tag,
) -> Status
where
    B: Buffer + BufferMut + ?Sized,
    D: Destination,
    S: Source,
{
    let saved = buf.as_bytes().to_vec();
    destination.send_with_tag(&saved[..], sendtag);
    source.receive_into_with_tag(buf, receivetag)
}

/// `MPI_Sendrecv_replace` with default tags.
pub fn send_receive_replace_into<B, D, S>(buf: &mut B, destination: &D, source: &S) -> Status
where
    B: Buffer + BufferMut + ?Sized,
    D: Destination,
    S: Source,
{
    send_receive_replace_into_with_tags(buf, destination, DEFAULT_TAG, source, ANY_TAG)
}

// ---- Process / AnyProcess handles. ----

/// A handle to a specific process in a communicator. Implements both
/// [`Source`] and [`Destination`] (and, via the `collective` module,
/// [`crate::collective::Root`]).
#[derive(Copy, Clone)]
pub struct Process<'a> {
    pub(crate) comm: &'a CommData,
    pub(crate) rank: Rank,
}

impl<'a> Process<'a> {
    pub(crate) fn new(comm: &'a CommData, rank: Rank) -> Process<'a> {
        Process { comm, rank }
    }

    /// The rank of this process within its communicator.
    pub fn rank(&self) -> Rank {
        self.rank
    }

    /// Whether this handle refers to the calling process.
    pub fn is_self(&self) -> bool {
        self.rank == self.comm.rank
    }
}

impl Source for Process<'_> {
    fn src_comm(&self) -> &CommData {
        self.comm
    }
    fn source_rank(&self) -> Rank {
        self.rank
    }
}

impl Destination for Process<'_> {
    fn dst_comm(&self) -> &CommData {
        self.comm
    }
    fn destination_rank(&self) -> Rank {
        self.rank
    }
}

/// A handle matching any source (`MPI_ANY_SOURCE`); implements [`Source`] only.
#[derive(Copy, Clone)]
pub struct AnyProcess<'a> {
    comm: &'a CommData,
}

impl<'a> AnyProcess<'a> {
    pub(crate) fn new(comm: &'a CommData) -> AnyProcess<'a> {
        AnyProcess { comm }
    }
}

impl Source for AnyProcess<'_> {
    fn src_comm(&self) -> &CommData {
        self.comm
    }
    fn source_rank(&self) -> Rank {
        ANY_SOURCE
    }
}
