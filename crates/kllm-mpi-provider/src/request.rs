//! Non-blocking request handling. Mirrors `mpi::request` in rsmpi: [`Request`]
//! borrows the buffer involved in an in-flight operation for the lifetime of
//! the request, [`Scope`] bounds that borrow, and [`WaitGuard`] / [`CancelGuard`]
//! complete a request when they go out of scope.
//!
//! As in rsmpi, **dropping an in-flight [`Request`] panics** — a request must be
//! consumed by `wait`, `test` or `cancel`, or handed to a guard.

use std::marker::PhantomData;

use crate::point_to_point::Status;
use crate::transport;
use crate::{Count, Rank, Tag};

/// A scope that bounds the lifetime of the buffers borrowed by requests. Safe
/// scopes are [`StaticScope`] (`'static`) and the [`LocalScope`] handed to the
/// closure passed to [`scope`].
///
/// # Safety
///
/// Implementors guarantee that any buffer associated with a request created in
/// this scope outlives the scope.
pub unsafe trait Scope<'a> {}

/// The scope of the entire program (`'static`).
#[derive(Clone, Copy, Debug)]
pub struct StaticScope;

// SAFETY: `'static` data outlives everything.
unsafe impl Scope<'static> for StaticScope {}

/// A dynamically-bounded scope created by [`scope`].
pub struct LocalScope<'a> {
    _invariant: PhantomData<std::cell::Cell<&'a ()>>,
}

// SAFETY: requests created with `&LocalScope<'a>` cannot outlive `'a`, and the
// buffers they borrow are constrained to outlive `'a` by the borrow checker.
unsafe impl<'a> Scope<'a> for &LocalScope<'a> {}

/// Open a request scope. Requests created inside `f` may borrow buffers that
/// outlive the scope; the borrow checker forbids letting a request escape.
pub fn scope<'a, F, R>(f: F) -> R
where
    F: FnOnce(&LocalScope<'a>) -> R,
{
    let scope = LocalScope {
        _invariant: PhantomData,
    };
    f(&scope)
}

/// Complete a pending receive: block for the matching message and copy up to
/// `len` bytes into `ptr`. Free function so it can be used from `Drop` impls
/// (which cannot carry the `Scope` bound).
fn complete_recv(ctx: u32, source: Rank, tag: Tag, ptr: *mut u8, len: usize) -> Status {
    let (src, t, count, _dt, payload) = transport::runtime().recv(ctx, source, tag);
    let n = len.min(payload.len());
    // SAFETY: `ptr` points at a live buffer of at least `len` bytes that is
    // exclusively borrowed for `'a` (which outlives this request).
    unsafe {
        std::ptr::copy_nonoverlapping(payload.as_ptr(), ptr, n);
    }
    Status::new(src, t, count as Count, payload.len())
}

enum State {
    /// The operation has already completed (e.g. an eager send).
    Completed { status: Status },
    /// A receive that has not yet been matched; completed on `wait`/`test`.
    PendingRecv {
        ctx: u32,
        source: Rank,
        tag: Tag,
        ptr: *mut u8,
        len: usize,
    },
    /// An operation progressing on a background thread (a truly-async
    /// collective); completed by joining the thread.
    PendingJoin {
        handle: Option<std::thread::JoinHandle<()>>,
    },
    /// The request has been consumed by `wait`/`test`/`cancel`.
    Consumed,
}

/// A handle to a non-blocking operation, borrowing its buffer for `'a`.
///
/// Type parameters mirror rsmpi: `D` is the buffer type and `S` the [`Scope`].
pub struct Request<'a, D: ?Sized = [u8], S = StaticScope> {
    state: State,
    // Ties the request to the lifetime of the borrowed buffer.
    _life: PhantomData<&'a mut ()>,
    _data: PhantomData<*mut D>,
    _scope: PhantomData<S>,
}

impl<'a, D: ?Sized, S: Scope<'a>> Request<'a, D, S> {
    /// A request for an operation that has already completed.
    pub(crate) fn completed(_scope: S) -> Request<'a, D, S> {
        Request {
            state: State::Completed {
                status: Status::new(0, 0, 0, 0),
            },
            _life: PhantomData,
            _data: PhantomData,
            _scope: PhantomData,
        }
    }

    /// A request for a receive that will be matched when completed.
    pub(crate) fn pending_recv(
        _scope: S,
        ptr: *mut u8,
        len: usize,
        ctx: u32,
        source: Rank,
        tag: Tag,
    ) -> Request<'a, D, S> {
        Request {
            state: State::PendingRecv {
                ctx,
                source,
                tag,
                ptr,
                len,
            },
            _life: PhantomData,
            _data: PhantomData,
            _scope: PhantomData,
        }
    }

    /// A request for a collective progressing on a background thread; completed
    /// by joining that thread (a truly-async non-blocking collective).
    pub(crate) fn from_join(_scope: S, handle: std::thread::JoinHandle<()>) -> Request<'a, D, S> {
        Request {
            state: State::PendingJoin {
                handle: Some(handle),
            },
            _life: PhantomData,
            _data: PhantomData,
            _scope: PhantomData,
        }
    }

    /// Whether the request can complete without blocking.
    fn ready(&self) -> bool {
        match &self.state {
            State::Completed { .. } => true,
            State::Consumed => true,
            State::PendingRecv {
                ctx, source, tag, ..
            } => transport::runtime().probe(*ctx, *source, *tag).is_some(),
            State::PendingJoin { handle } => {
                handle.as_ref().map(|h| h.is_finished()).unwrap_or(true)
            }
        }
    }

    /// Wait for the operation to complete (`MPI_Wait`), returning its status.
    pub fn wait(mut self) -> Status {
        let state = std::mem::replace(&mut self.state, State::Consumed);
        match state {
            State::Completed { status } => status,
            State::PendingRecv {
                ctx,
                source,
                tag,
                ptr,
                len,
            } => complete_recv(ctx, source, tag, ptr, len),
            State::PendingJoin { mut handle } => {
                if let Some(h) = handle.take() {
                    let _ = h.join();
                }
                Status::new(0, 0, 0, 0)
            }
            State::Consumed => unreachable!("request already consumed"),
        }
    }

    /// Wait for completion, discarding the status.
    pub fn wait_without_status(self) {
        let _ = self.wait();
    }

    /// Test for completion without blocking (`MPI_Test`). Returns `Ok(status)`
    /// if complete, otherwise `Err(self)` so the request can be retried.
    pub fn test(mut self) -> Result<Status, Request<'a, D, S>> {
        let is_ready = self.ready();
        if !is_ready {
            return Err(self);
        }
        let state = std::mem::replace(&mut self.state, State::Consumed);
        let status = match state {
            State::Completed { status } => status,
            State::PendingRecv {
                ctx,
                source,
                tag,
                ptr,
                len,
            } => complete_recv(ctx, source, tag, ptr, len),
            State::PendingJoin { mut handle } => {
                if let Some(h) = handle.take() {
                    let _ = h.join();
                }
                Status::new(0, 0, 0, 0)
            }
            State::Consumed => unreachable!(),
        };
        Ok(status)
    }

    /// Cancel the operation (`MPI_Cancel`). Because incoming messages are
    /// already buffered by the transport, this simply consumes the request.
    pub fn cancel(mut self) {
        self.state = State::Consumed;
    }
}

impl<D: ?Sized, S> Drop for Request<'_, D, S> {
    fn drop(&mut self) {
        match &mut self.state {
            // A background collective must be joined before its borrowed buffers
            // go out of scope, so completing it on drop is required for soundness
            // (not a misuse to warn about).
            State::PendingJoin { handle } => {
                if let Some(h) = handle.take() {
                    let _ = h.join();
                }
            }
            State::Completed { .. } | State::PendingRecv { .. } => {
                if !std::thread::panicking() {
                    panic!(
                        "an in-flight mpi::request::Request was dropped; complete it with \
                         wait()/test()/cancel() or hold it in a WaitGuard"
                    );
                }
            }
            State::Consumed => {}
        }
    }
}

/// Waits on the contained [`Request`] when dropped (`RAII` completion of a
/// send). Mirrors rsmpi's `WaitGuard`.
pub struct WaitGuard<'a, D: ?Sized = [u8], S = StaticScope>(Option<Request<'a, D, S>>);

impl<'a, D: ?Sized, S: Scope<'a>> From<Request<'a, D, S>> for WaitGuard<'a, D, S> {
    fn from(r: Request<'a, D, S>) -> Self {
        WaitGuard(Some(r))
    }
}

impl<'a, D: ?Sized, S: Scope<'a>> WaitGuard<'a, D, S> {
    /// Explicitly wait, returning the status.
    pub fn wait(mut self) -> Status {
        self.0.take().unwrap().wait()
    }
}

impl<D: ?Sized, S> Drop for WaitGuard<'_, D, S> {
    fn drop(&mut self) {
        if let Some(r) = self.0.take() {
            // Reconstruct the wait path without the Request's own Drop guard.
            let mut r = std::mem::ManuallyDrop::new(r);
            let state = std::mem::replace(&mut r.state, State::Consumed);
            match state {
                State::PendingRecv {
                    ctx,
                    source,
                    tag,
                    ptr,
                    len,
                } => {
                    let _ = complete_recv(ctx, source, tag, ptr, len);
                }
                State::PendingJoin { mut handle } => {
                    if let Some(h) = handle.take() {
                        let _ = h.join();
                    }
                }
                State::Completed { .. } | State::Consumed => {}
            }
        }
    }
}

/// Cancels (then completes) the contained request when dropped. Mirrors
/// rsmpi's `CancelGuard`.
pub struct CancelGuard<'a, D: ?Sized = [u8], S = StaticScope>(Option<Request<'a, D, S>>);

impl<'a, D: ?Sized, S: Scope<'a>> From<Request<'a, D, S>> for CancelGuard<'a, D, S> {
    fn from(r: Request<'a, D, S>) -> Self {
        CancelGuard(Some(r))
    }
}

impl<D: ?Sized, S> Drop for CancelGuard<'_, D, S> {
    fn drop(&mut self) {
        if let Some(r) = self.0.take() {
            let mut r = std::mem::ManuallyDrop::new(r);
            // A background collective can't be safely cancelled mid-flight (its
            // peers are participating), so it must still be joined.
            if let State::PendingJoin { handle } = &mut r.state {
                if let Some(h) = handle.take() {
                    let _ = h.join();
                }
            }
            r.state = State::Consumed;
        }
    }
}

/// Wait for any one of the requests to complete, returning its index and
/// status and removing it from the vector (`MPI_Waitany`). Returns `None` if
/// the vector is empty.
pub fn wait_any<'a, D: ?Sized, S: Scope<'a>>(
    requests: &mut Vec<Request<'a, D, S>>,
) -> Option<(usize, Status)> {
    if requests.is_empty() {
        return None;
    }
    loop {
        for i in 0..requests.len() {
            if requests[i].ready() {
                let r = requests.remove(i);
                return Some((i, r.wait()));
            }
        }
        std::thread::yield_now();
    }
}

/// Wait for all requests to complete (`MPI_Waitall`), returning their statuses.
pub fn wait_all<'a, D: ?Sized, S: Scope<'a>>(requests: Vec<Request<'a, D, S>>) -> Vec<Status> {
    requests.into_iter().map(|r| r.wait()).collect()
}

// ---- Generalized requests (MPI_Grequest_*) ----

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

struct GReqState {
    done: AtomicBool,
    status: std::sync::Mutex<Option<Status>>,
}

/// A user-defined ("generalized") request, completed by external code rather
/// than by the MPI runtime (`MPI_Grequest_start`). Pair it with a
/// [`GeneralizedRequestCompleter`]: whichever code performs the underlying work
/// calls [`GeneralizedRequestCompleter::complete`], after which `wait`/`test`
/// on the request return.
pub struct GeneralizedRequest {
    state: Arc<GReqState>,
}

/// The completion handle for a [`GeneralizedRequest`]
/// (`MPI_Grequest_complete`).
pub struct GeneralizedRequestCompleter {
    state: Arc<GReqState>,
}

impl GeneralizedRequest {
    /// Start a generalized request, returning it together with the completer
    /// used to mark it done (`MPI_Grequest_start`).
    pub fn start() -> (GeneralizedRequest, GeneralizedRequestCompleter) {
        let state = Arc::new(GReqState {
            done: AtomicBool::new(false),
            status: std::sync::Mutex::new(None),
        });
        (
            GeneralizedRequest {
                state: Arc::clone(&state),
            },
            GeneralizedRequestCompleter { state },
        )
    }

    /// Whether the request has been completed.
    pub fn is_complete(&self) -> bool {
        self.state.done.load(Ordering::Acquire)
    }

    /// Block until the request is completed, returning its status.
    pub fn wait(self) -> Status {
        while !self.state.done.load(Ordering::Acquire) {
            std::thread::yield_now();
        }
        self.state
            .status
            .lock()
            .unwrap()
            .unwrap_or(Status::new(0, 0, 0, 0))
    }

    /// Return `Ok(status)` if complete, otherwise `Err(self)`.
    pub fn test(self) -> Result<Status, GeneralizedRequest> {
        if self.state.done.load(Ordering::Acquire) {
            Ok(self
                .state
                .status
                .lock()
                .unwrap()
                .unwrap_or(Status::new(0, 0, 0, 0)))
        } else {
            Err(self)
        }
    }
}

impl GeneralizedRequestCompleter {
    /// Mark the associated request complete (`MPI_Grequest_complete`).
    pub fn complete(self) {
        *self.state.status.lock().unwrap() = Some(Status::new(0, 0, 0, 0));
        self.state.done.store(true, Ordering::Release);
    }
}

// ---- Persistent requests (MPI_Send_init / MPI_Recv_init) ----

enum PersistentKind {
    Send {
        src: Rank,
        dest_world: i32,
        dt: u32,
        count: u64,
    },
    Recv {
        source: Rank,
    },
}

/// A persistent (re-usable) communication request. Created with
/// [`crate::point_to_point::Destination::send_init`] /
/// [`crate::point_to_point::Source::receive_init`], then repeatedly `start`ed
/// and `wait`ed. The associated buffer is borrowed for the request's lifetime.
pub struct PersistentRequest<'a> {
    ctx: u32,
    tag: Tag,
    kind: PersistentKind,
    ptr: *mut u8,
    len: usize,
    last: Option<Status>,
    _life: PhantomData<&'a mut ()>,
}

impl<'a> PersistentRequest<'a> {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new_send(
        ctx: u32,
        src: Rank,
        dest_world: i32,
        tag: Tag,
        dt: u32,
        count: u64,
        ptr: *const u8,
        len: usize,
    ) -> PersistentRequest<'a> {
        PersistentRequest {
            ctx,
            tag,
            kind: PersistentKind::Send {
                src,
                dest_world,
                dt,
                count,
            },
            ptr: ptr as *mut u8,
            len,
            last: None,
            _life: PhantomData,
        }
    }

    pub(crate) fn new_recv(
        ctx: u32,
        source: Rank,
        tag: Tag,
        ptr: *mut u8,
        len: usize,
    ) -> PersistentRequest<'a> {
        PersistentRequest {
            ctx,
            tag,
            kind: PersistentKind::Recv { source },
            ptr,
            len,
            last: None,
            _life: PhantomData,
        }
    }

    /// Activate the operation (`MPI_Start`).
    pub fn start(&mut self) {
        match self.kind {
            PersistentKind::Send {
                src,
                dest_world,
                dt,
                count,
            } => {
                // SAFETY: `ptr`/`len` describe the borrowed buffer, valid for 'a.
                let bytes = unsafe { std::slice::from_raw_parts(self.ptr, self.len) };
                transport::runtime()
                    .send(self.ctx, src, dest_world, self.tag, count, dt, bytes)
                    .expect("persistent send failed");
                self.last = Some(Status::new(dest_world, self.tag, count as Count, self.len));
            }
            PersistentKind::Recv { .. } => {}
        }
    }

    /// Complete the current activation (`MPI_Wait`), returning its status.
    pub fn wait(&mut self) -> Status {
        match self.kind {
            PersistentKind::Send { .. } => self.last.take().unwrap_or(Status::new(0, 0, 0, 0)),
            PersistentKind::Recv { source } => {
                let (s, t, count, _dt, payload) =
                    transport::runtime().recv(self.ctx, source, self.tag);
                let n = self.len.min(payload.len());
                // SAFETY: `ptr`/`len` describe the borrowed receive buffer.
                unsafe {
                    std::ptr::copy_nonoverlapping(payload.as_ptr(), self.ptr, n);
                }
                Status::new(s, t, count as Count, payload.len())
            }
        }
    }
}
