"""Bounded local SQLite pools, explicit units of work, and tracked async dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator as LazyIterator
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import itertools
import os
import re
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Iterator, TypeVar

from sqlalchemy import Connection, MetaData, UniqueConstraint, create_engine, event, inspect
from sqlalchemy.engine import CursorResult, URL
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
from sqlalchemy.pool import QueuePool

from kllm.exceptions import PersistenceError
from .operations import CommitOutcomeUnknown, Operation, OperationCancelled
from .options import SQLiteOptions

if os.name == "nt":
    import msvcrt
else:
    import fcntl

T = TypeVar("T")
_generations = itertools.count(1)
_closing: dict[Path, "SQLiteStore"] = {}
_registry_lock = threading.RLock()


@contextmanager
def file_lock(path: Path, timeout: float) -> Iterator[None]:
    """Secure, finite initialization fence; unrelated to network mutation fencing."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    deadline = time.monotonic() + timeout
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        while not locked:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise PersistenceError("storage initialization lock timed out") from None
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        yield
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class SQLiteStore:
    """One engine per owner; no live connection or result escapes a unit of work."""

    def __init__(
        self,
        path: Path,
        metadata: MetaData | None = None,
        *,
        profile: str = "network",
        options: SQLiteOptions | None = None,
        upgrade: Callable[[Connection], None] | None = None,
    ) -> None:
        self.options = options or SQLiteOptions.from_env()
        if profile not in ("usage", "network"):
            raise ValueError("invalid SQLite storage profile")
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise OSError("storage file must not be a symbolic link")
        self.path = self.path.resolve()
        with _registry_lock:
            if self.path in _closing:
                raise PersistenceError("previous storage owner is still closing")
        self.generation = next(_generations)
        self.pid = os.getpid()
        self.state = "INITIALIZING"
        self._condition = threading.Condition(threading.RLock())
        self._local = threading.local()
        self._writer = threading.RLock()
        self._close_lock = threading.Lock()
        self._operations: dict[str, Operation] = {}
        self._futures = set()
        self._waiters = 0
        self._executor: ThreadPoolExecutor | None = None
        self._close_jobs: set[asyncio.Task] = set()
        self._created = self._closed = self._in_use = 0
        self._lease_generation = itertools.count(1)
        self.last_outcome: dict[str, Any] | None = None
        self._initializing = True
        self._timed_out = False
        self._drain_thread: threading.Thread | None = None
        self._profile_ready = False
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.path)),
            poolclass=QueuePool,
            pool_size=self.options.pool_size,
            max_overflow=0,
            pool_timeout=min(0.05, self.options.wait_timeout),
            connect_args={"check_same_thread": False, "timeout": self.options.wait_timeout},
        )

        @event.listens_for(self.engine, "connect")
        def connect(raw, record):
            raw.isolation_level = None
            raw.execute("PRAGMA synchronous=FULL")
            if profile == "network":
                raw.execute("PRAGMA foreign_keys=ON")
            if not self._profile_ready:
                raw.execute("PRAGMA journal_mode=" + ("DELETE" if profile == "usage" else "WAL"))
                if profile == "usage":
                    raw.execute("PRAGMA auto_vacuum=FULL")
                self._profile_ready = True
            with self._condition:
                self._created += 1

        @event.listens_for(self.engine, "close")
        def close(raw, record):
            with self._condition:
                self._closed += 1

        @event.listens_for(self.engine, "begin")
        def begin(db):
            db.exec_driver_sql("BEGIN IMMEDIATE" if db.info.get("write") else "BEGIN")

        try:
            with file_lock(
                self.path.with_suffix(self.path.suffix + ".init.lock"), self.options.wait_timeout
            ):
                fd = os.open(
                    self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
                )
                os.close(fd)
                if os.name != "nt":
                    os.chmod(self.path, 0o600)
                if metadata is not None:
                    with self.connection(write=True) as db:
                        existing = set(inspect(db).get_table_names())
                        if upgrade is not None:
                            upgrade(db)
                        if existing:
                            self._validate_schema(db, metadata, existing)
                        metadata.create_all(db)
            self._initializing = False
            self.state = "OPEN"
        except BaseException:
            self._initializing = False
            self.state = "CLOSING"
            self.engine.dispose()
            self.state = "CLOSED"
            raise

    @staticmethod
    def _validate_schema(db: Connection, metadata: MetaData, existing: set[str]) -> None:
        inspector = inspect(db)
        if not set(metadata.tables) <= existing:
            raise sqlite3.DatabaseError("storage schema is incomplete or unsupported")
        for table in metadata.tables.values():
            columns = {c["name"]: c for c in inspector.get_columns(table.name)}
            for column in table.columns:
                actual = columns.get(column.name)
                if (
                    actual is None
                    or actual["type"]._type_affinity is not column.type._type_affinity
                ):
                    raise sqlite3.DatabaseError("storage schema is incompatible")
                if actual["nullable"] != column.nullable:
                    raise sqlite3.DatabaseError("storage column nullability is incompatible")
            if inspector.get_pk_constraint(table.name)["constrained_columns"] != [
                c.name for c in table.primary_key
            ]:
                raise sqlite3.DatabaseError("storage primary key is incompatible")
            uniques = {
                tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name)
            }
            for constraint in table.constraints:
                if (
                    isinstance(constraint, UniqueConstraint)
                    and tuple(c.name for c in constraint.columns) not in uniques
                ):
                    raise sqlite3.DatabaseError("storage uniqueness constraint is incompatible")
            foreign = {
                (tuple(c["constrained_columns"]), c["referred_table"], tuple(c["referred_columns"]))
                for c in inspector.get_foreign_keys(table.name)
            }
            for constraint in table.foreign_key_constraints:
                expected = (
                    tuple(c.name for c in constraint.columns),
                    constraint.referred_table.name,
                    tuple(element.column.name for element in constraint.elements),
                )
                if expected not in foreign:
                    raise sqlite3.DatabaseError("storage reference constraint is incompatible")
            indexes = {index["name"]: index for index in inspector.get_indexes(table.name)}
            for index in table.indexes:
                actual = indexes.get(index.name)
                if (
                    actual is None
                    or actual["column_names"] != [c.name for c in index.columns]
                    or bool(actual["unique"]) != index.unique
                ):
                    raise sqlite3.DatabaseError("storage index is incompatible")
                expected_where = index.dialect_options["sqlite"].get("where")
                actual_where = actual.get("dialect_options", {}).get("sqlite_where")

                def predicate(value):
                    if value is None:
                        return ""
                    if hasattr(value, "compile"):
                        value = value.compile(
                            dialect=db.dialect, compile_kwargs={"literal_binds": True}
                        )
                    return re.sub(r'[\s()"]', "", str(value).replace(table.name + ".", ""))

                if predicate(expected_where) != predicate(actual_where):
                    raise sqlite3.DatabaseError("storage partial index is incompatible")

    def metrics(self) -> dict[str, int]:
        with self._condition:
            return {
                "created": self._created,
                "closed": self._closed,
                "connections": self._created - self._closed,
                "in_use": self._in_use,
                "admitted": len(self._operations),
                "waiters": self._waiters,
                "jobs": len(self._futures),
                "close_jobs": len(self._close_jobs),
                "workers": sum(t.is_alive() for t in self._executor._threads)
                if self._executor
                else 0,
            }

    def _check_open(self) -> None:
        if self.pid != os.getpid():
            raise PersistenceError("storage owner belongs to another process")
        if self.state != "OPEN" and not self._initializing:
            raise PersistenceError("storage owner is unavailable or closed")

    def _admit(self, op: Operation) -> None:
        deadline = time.monotonic() + self.options.wait_timeout
        with self._condition:
            self._check_open()
            if self._waiters >= 32:
                raise PersistenceError("storage admission capacity exhausted")
            self._waiters += 1
            try:
                while len(self._operations) >= 32:
                    self._check_open()
                    op.check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PersistenceError("storage admission timed out")
                    self._condition.wait(min(remaining, 0.05))
                self._check_open()
                self._operations[op.id] = op
            finally:
                self._waiters -= 1

    def _finish(self, op: Operation) -> None:
        with self._condition:
            if op.id not in self._operations:
                return
            self.last_outcome = {
                "operation_id": op.id,
                "state": op.state,
                "confirmed_units": op.commits,
            }
            self._operations.pop(op.id)
            op.done.set()
            self._condition.notify_all()
            self._reclaim_after_timeout()

    def _reclaim_after_timeout(self) -> None:
        """A failed attempt stays failed; reclaim its tracked work once settled."""
        if (
            self._timed_out
            and not self._operations
            and not self._waiters
            and self.state == "CLOSING"
        ):
            if self._drain_thread is None:
                self._drain_thread = threading.Thread(
                    target=self.close, name="kllm-sqlite-drain", daemon=True
                )
                self._drain_thread.start()

    @contextmanager
    def operation(self, op: Operation | None = None) -> Iterator[Operation]:
        current = getattr(self._local, "operation", None)
        if current is not None:
            yield current
            return
        owned = op is None
        op = op or Operation()
        if owned:
            self._admit(op)
        self._local.operation = op
        op.state = "RUNNING"
        try:
            op.check()
            yield op
            if op.state == "RUNNING":
                op.state = "COMPLETED"
        except OperationCancelled:
            op.state = "CANCELLED"
            raise
        except BaseException:
            if op.state != "OUTCOME_UNKNOWN":
                op.state = "FAILED"
            raise
        finally:
            self._local.operation = None
            self._finish(op)

    @contextmanager
    def protected(self) -> Iterator[None]:
        with self.operation() as op:
            with op.lock:
                op.check()
                op.protected += 1
            try:
                yield
            finally:
                with op.lock:
                    op.protected -= 1

    @contextmanager
    def writer(self) -> Iterator[None]:
        with self.operation() as op:
            deadline = time.monotonic() + self.options.wait_timeout
            while not self._writer.acquire(timeout=min(0.05, max(0, deadline - time.monotonic()))):
                op.check()
                if time.monotonic() >= deadline:
                    raise PersistenceError("storage writer timed out")
            try:
                yield
            finally:
                self._writer.release()

    @property
    def current_connection(self) -> Connection:
        db = getattr(self._local, "connection", None)
        if db is None:
            raise PersistenceError("a scoped storage lease is required")
        return db

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[Connection]:
        current = getattr(self._local, "connection", None)
        if current is not None:
            if write:
                raise PersistenceError("nested public write transaction is unsupported")
            yield current
            return
        with self.operation() as op:
            if write:
                with self.writer():
                    with self._lease(op, write) as db:
                        yield db
            else:
                with self._lease(op, write) as db:
                    yield db

    @contextmanager
    def _lease(self, op: Operation, write: bool) -> Iterator[Connection]:
        deadline = time.monotonic() + self.options.wait_timeout
        db = None
        while db is None:
            op.check()
            try:
                db = self.engine.connect()
            except PoolTimeout:
                if time.monotonic() >= deadline:
                    raise PersistenceError("storage checkout timed out") from None
            except DBAPIError as exc:
                raise exc.orig from None
        raw = db.connection.driver_connection
        self._local.connection = db
        with self._condition:
            self._in_use += 1
        with op.lock:
            op.raw = raw
            op.lease_generation = next(self._lease_generation)
            op.state = "RUNNING"
        raw.set_progress_handler(
            lambda: int(op.cancellable and not op.protected and op.cancelled.is_set()), 1000
        )
        db.info["write"] = write
        transaction = None
        try:
            transaction = db.begin()
            op.check()
            yield db
            if write:
                op.begin_commit()
                try:
                    transaction.commit()
                except BaseException:
                    op.state = "OUTCOME_UNKNOWN"
                    with op.lock:
                        op.raw = None
                    db.invalidate()
                    raise CommitOutcomeUnknown(
                        "storage commit outcome unknown; inspect durable records before retry"
                    ) from None
                op.committed()
            else:
                transaction.rollback()
        except DBAPIError as exc:
            if op.cancelled.is_set() and op.cancellable and not op.protected:
                raise OperationCancelled("storage operation cancelled") from None
            raise exc.orig from None
        finally:
            with op.lock:
                op.raw = None
                if not db.invalidated:
                    raw.set_progress_handler(None, 0)
            try:
                if transaction is not None and transaction.is_active:
                    try:
                        transaction.rollback()
                    except BaseException:
                        db.invalidate()
                db.close()
            finally:
                self._local.connection = None
                with self._condition:
                    self._in_use -= 1
                    self._condition.notify_all()

    @staticmethod
    def _materialized(result: T) -> T:
        seen: set[int] = set()

        def check(value):
            if isinstance(value, (CursorResult, Connection, LazyIterator)):
                raise PersistenceError("storage callbacks must consume results before returning")
            if isinstance(value, (list, tuple, dict)) and id(value) not in seen:
                seen.add(id(value))
                for item in value.values() if isinstance(value, dict) else value:
                    check(item)

        check(result)
        return result

    def read(self, callback: Callable[[Connection], T]) -> T:
        with self.connection() as db:
            return self._materialized(callback(db))

    def write(self, callback: Callable[[Connection], T]) -> T:
        with self.connection(write=True) as db:
            return self._materialized(callback(db))

    def run(self, callback: Callable[..., T], *args, **kwargs) -> T:
        with self.operation():
            return self._materialized(callback(*args, **kwargs))

    async def arun(
        self, callback: Callable[..., T], *args, cancellable: bool = True, **kwargs
    ) -> T:
        op = Operation(cancellable=cancellable)
        deadline = time.monotonic() + self.options.wait_timeout
        with self._condition:
            self._check_open()
            if self._waiters >= 32:
                raise PersistenceError("storage admission capacity exhausted")
            self._waiters += 1
        try:
            while True:
                with self._condition:
                    self._check_open()
                    if len(self._operations) < 32:
                        self._operations[op.id] = op
                        if self._executor is None:
                            self._executor = ThreadPoolExecutor(
                                max_workers=max(2, self.options.pool_size),
                                thread_name_prefix="kllm-sqlite",
                            )

                        def work():
                            with self.operation(op):
                                return self._materialized(callback(*args, **kwargs))

                        future = self._executor.submit(work)
                        self._futures.add(future)

                        def completed(job):
                            with self._condition:
                                self._futures.discard(job)
                                self._condition.notify_all()
                            if not job.cancelled():
                                job.exception()

                        future.add_done_callback(completed)
                        break
                if time.monotonic() >= deadline:
                    raise PersistenceError("storage admission timed out")
                await asyncio.sleep(0.005)
        finally:
            with self._condition:
                self._waiters -= 1
                self._condition.notify_all()
        wrapped = asyncio.wrap_future(future)
        # Always observe the detached result even when its caller disappears.
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            op.cancel()
            if future.cancel():
                op.state = "CANCELLED"
                self._finish(op)
            raise

    async def aread(self, callback: Callable[[Connection], T]) -> T:
        return await self.arun(self.read, callback)

    async def awrite(self, callback: Callable[[Connection], T]) -> T:
        return await self.arun(self.write, callback)

    def reject_new_work(self) -> None:
        with self._condition:
            if self.state == "CLOSED":
                return
            self.state = "CLOSING"
            with _registry_lock:
                _closing[self.path] = self
            for op in list(self._operations.values()):
                op.cancel()
            self._condition.notify_all()

    def close(self, *, deadline: float | None = None) -> None:
        deadline = (
            deadline if deadline is not None else time.monotonic() + self.options.shutdown_timeout
        )
        if self.pid != os.getpid():
            raise PersistenceError("storage owner belongs to another process")
        if self.state == "CLOSED":
            return
        self.reject_new_work()
        if not self._close_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise PersistenceError("storage shutdown deadline expired")
        try:
            if self.state == "CLOSED":
                return
            with self._condition:
                while self._operations or self._waiters or self._futures:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PersistenceError(
                            "storage shutdown deadline expired; outstanding work remains tracked"
                        )
                    self._condition.wait(remaining)
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=True)
            self.engine.dispose()
            self.state = "CLOSED"
            with _registry_lock:
                if _closing.get(self.path) is self:
                    _closing.pop(self.path)
        except PersistenceError:
            with self._condition:
                self._timed_out = True
                self._reclaim_after_timeout()
            raise
        finally:
            self._close_lock.release()

    async def aclose(self, *, deadline: float | None = None) -> None:
        # Cleanup cannot share the executor being joined, nor block the loop.
        task = asyncio.create_task(asyncio.to_thread(self.close, deadline=deadline))
        with self._condition:
            self._close_jobs.add(task)

        def completed(job):
            with self._condition:
                self._close_jobs.discard(job)
                self._condition.notify_all()
            if not job.cancelled():
                job.exception()

        task.add_done_callback(completed)
        await asyncio.shield(task)

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
