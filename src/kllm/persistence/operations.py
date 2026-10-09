"""Operation ownership and cancellation arbitration shared by sync/async callers."""

from dataclasses import dataclass, field
import threading
import asyncio
import time
from uuid import uuid4
from typing import Any

from kllm.exceptions import PersistenceError


class OperationCancelled(PersistenceError):
    """A cancelled unit has not entered its commit boundary."""


class CommitOutcomeUnknown(PersistenceError):
    """The caller must inspect existing durable identifiers before retrying."""


@dataclass
class Operation:
    cancellable: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)
    state: str = "QUEUED"
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: Any = field(default_factory=threading.RLock)
    done: threading.Event = field(default_factory=threading.Event)
    raw: Any = None
    lease_generation: int = 0
    protected: int = 0
    commits: int = 0

    def check(self) -> None:
        if self.cancellable and not self.protected and self.cancelled.is_set():
            raise OperationCancelled("storage operation cancelled")

    def cancel(self) -> None:
        with self.lock:
            self.cancelled.set()
            # Ownership and the generation cannot change while interrupt runs.
            if (
                self.raw is not None
                and self.cancellable
                and not self.protected
                and self.state != "COMMITTING"
            ):
                self.raw.interrupt()

    def begin_commit(self) -> None:
        with self.lock:
            self.state = "PRE_COMMIT"
            self.check()
            self.state = "COMMITTING"

    def committed(self) -> None:
        with self.lock:
            self.commits += 1
            self.state = "COMMITTED"


# Initial construction precedes a store owner. Use the loop's shared executor,
# bounded admission, and explicit tracking so cancellation cannot orphan work.
_initialization_gate = threading.BoundedSemaphore(32)
_initialization_lock = threading.Lock()
_initializations: set[threading.Event] = set()


async def initialize(factory, *, cleanup=None):
    """Offload a publishing factory; cleanup unattached results after cancellation."""
    if not _initialization_gate.acquire(blocking=False):
        raise PersistenceError("storage initialization capacity exhausted")
    settled = threading.Event()
    cancelled = threading.Event()
    arbitration = threading.Lock()
    missing = object()
    result = [missing]
    with _initialization_lock:
        _initializations.add(settled)

    def finish():
        with _initialization_lock:
            if settled.is_set():
                return
            settled.set()
            _initializations.discard(settled)
            _initialization_gate.release()

    def reclaim(value):
        try:
            if cleanup is not None:
                cleanup(value)
        finally:
            finish()

    def work():
        try:
            value = factory()
        except BaseException:
            finish()
            raise
        with arbitration:
            dispose = cancelled.is_set()
            if not dispose:
                result[0] = value
        if dispose:
            reclaim(value)
        return value

    task = asyncio.create_task(asyncio.to_thread(work))
    task.add_done_callback(lambda job: job.exception() if not job.cancelled() else None)
    try:
        value = await asyncio.shield(task)
        with arbitration:
            result[0] = missing
        finish()
        return value
    except asyncio.CancelledError:
        with arbitration:
            cancelled.set()
            value, result[0] = result[0], missing
        if value is not missing:
            # Submit immediately: even a closing loop cannot discard queued cleanup.
            future = asyncio.get_running_loop().run_in_executor(None, reclaim, value)
            future.add_done_callback(lambda job: job.exception() if not job.cancelled() else None)
        raise


def pending_initializations() -> int:
    with _initialization_lock:
        return len(_initializations)


async def drain_initializations(deadline: float):
    while pending_initializations():
        if time.monotonic() >= deadline:
            raise PersistenceError("storage initialization shutdown deadline expired")
        await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
