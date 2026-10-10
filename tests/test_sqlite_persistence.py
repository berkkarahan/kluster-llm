"""Observable contracts for bounded file-backed storage ownership."""

import asyncio
import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, event
from sqlalchemy.exc import OperationalError

from kllm.shared.exceptions import PersistenceError
from kllm.shared.persistence import SQLiteOptions, SQLiteStore
from kllm.shared.persistence.operations import drain_initializations, initialize, pending_initializations


def storage(tmp_path, **options):
    metadata = MetaData()
    items = Table("items", metadata, Column("id", Integer, primary_key=True))
    store = SQLiteStore(tmp_path / "test.sqlite", metadata, options=SQLiteOptions(**options))
    return store, items


@pytest.mark.parametrize(
    "settings",
    [
        {"pool_size": 0},
        {"pool_size": 5},
        {"pool_size": True},
        {"wait_timeout": 0},
        {"wait_timeout": float("inf")},
        {"shutdown_timeout": float("nan")},
    ],
)
def test_invalid_options(settings):
    with pytest.raises(ValueError):
        SQLiteOptions(**settings)


def test_environment_options(monkeypatch):
    monkeypatch.setenv("KLLM_SQLITE_POOL_SIZE", "4")
    assert SQLiteOptions.from_env().pool_size == 4
    monkeypatch.setenv("KLLM_SQLITE_WAIT_TIMEOUT", "not-a-number")
    with pytest.raises(ValueError, match="KLLM_SQLITE_WAIT_TIMEOUT"):
        SQLiteOptions.from_env()


def test_atomic_failure_reuse_and_same_snapshot(tmp_path):
    store, items = storage(tmp_path)
    try:

        def fail(db):
            db.execute(insert(items).values(id=1))
            raise ValueError("failed second step")

        with pytest.raises(ValueError):
            store.write(fail)
        assert store.read(lambda db: db.execute(select(items)).all()) == []
        store.write(lambda db: db.execute(insert(items).values(id=2)).rowcount)
        with store.connection() as db:
            assert store.read(lambda inner: inner is db)
            assert db.execute(select(items.c.id)).scalar_one() == 2
        assert store.metrics()["created"] == 1
    finally:
        store.close()
    assert store.metrics()["connections"] == 0
    with pytest.raises(PersistenceError):
        store.read(lambda db: None)


def test_pool_timeout_and_no_uncommitted_visibility(tmp_path):
    store, items = storage(tmp_path, pool_size=2, wait_timeout=0.05)
    held = threading.Event()
    release = threading.Event()

    def writer():
        with store.connection(write=True) as db:
            db.execute(insert(items).values(id=1))
            held.set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(writer)
        assert held.wait(3)
        assert store.read(lambda db: db.execute(select(items.c.id)).all()) == []
        release.set()
        job.result(3)
    store.close()


def test_checkout_timeout(tmp_path):
    store, _ = storage(tmp_path, wait_timeout=0.05)
    try:
        with store.connection():
            with ThreadPoolExecutor(max_workers=1) as executor:
                job = executor.submit(store.read, lambda db: None)
                with pytest.raises(PersistenceError, match="checkout"):
                    job.result(3)
        assert store.metrics()["in_use"] == 0
    finally:
        store.close()


def test_nested_write_rejected(tmp_path):
    store, _ = storage(tmp_path)
    try:
        with store.connection(write=True):
            with pytest.raises(PersistenceError, match="nested"):
                store.write(lambda db: None)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_precommit_cancel_rolls_back_and_retains_lease(tmp_path):
    store, items = storage(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def write(db):
        db.execute(insert(items).values(id=1))
        entered.set()
        assert release.wait(3)

    job = asyncio.create_task(store.awrite(write))
    assert await asyncio.to_thread(entered.wait, 3)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert store.metrics()["in_use"] == 1
    release.set()
    await store.aclose()
    reopened = SQLiteStore(store.path, items.metadata)
    assert reopened.read(lambda db: db.execute(select(items)).all()) == []
    reopened.close()


def test_close_timeout_quarantines_until_actual_cleanup(tmp_path):
    store, _ = storage(tmp_path, shutdown_timeout=0.05)
    entered, release = threading.Event(), threading.Event()

    def work():
        with store.connection():
            entered.set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(work)
        assert entered.wait(3)
        with pytest.raises(PersistenceError, match="shutdown"):
            store.close()
        assert store.state == "CLOSING"
        with pytest.raises(PersistenceError):
            SQLiteStore(store.path)
        release.set()
        job.result(3)
    store.close()
    assert store.state == "CLOSED"
    assert store.metrics()["connections"] == 0


@pytest.mark.asyncio
async def test_commit_wins_cancel_reports_confirmed_durable_unit(tmp_path, monkeypatch):
    from kllm.shared.persistence.operations import Operation

    store, items = storage(tmp_path)
    committing, release = threading.Event(), threading.Event()
    original = Operation.begin_commit

    def barrier(op):
        original(op)
        committing.set()
        assert release.wait(3)

    monkeypatch.setattr(Operation, "begin_commit", barrier)
    job = asyncio.create_task(
        store.awrite(lambda db: db.execute(insert(items).values(id=7)).rowcount)
    )
    assert await asyncio.to_thread(committing.wait, 3)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    release.set()
    await store.aclose()
    assert store.last_outcome["confirmed_units"] == 1
    with SQLiteStore(store.path, items.metadata) as reopened:
        assert reopened.read(lambda db: db.execute(select(items.c.id)).scalar_one()) == 7


def test_unknown_commit_has_no_parameter_content_and_retires_connection(tmp_path):
    from kllm.shared.persistence.operations import CommitOutcomeUnknown

    store, items = storage(tmp_path)

    def fail_commit(db):
        raise OperationalError(
            "private statement",
            {"token": "secret-value"},
            sqlite3.OperationalError("commit failed"),
        )

    event.listen(store.engine, "commit", fail_commit)
    with pytest.raises(CommitOutcomeUnknown) as error:
        store.write(lambda db: db.execute(insert(items).values(id=9)).rowcount)
    assert "secret-value" not in str(error.value)
    assert store.last_outcome["state"] == "OUTCOME_UNKNOWN"
    event.remove(store.engine, "commit", fail_commit)
    assert store.read(lambda db: db.execute(select(items)).all()) == []
    assert store.metrics()["created"] == 2
    store.close()


def test_schema_failure_closes_partial_owner_and_does_not_reset_file(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE incompatible (id TEXT)")
        db.execute("INSERT INTO incompatible VALUES ('keep')")
    metadata = MetaData()
    Table("needed", metadata, Column("id", Integer, primary_key=True))
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteStore(path, metadata)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM incompatible").fetchall() == [("keep",)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='needed'").fetchone() is None


def test_reset_failure_invalidates_before_reuse(tmp_path):
    store, items = storage(tmp_path)

    def fail_rollback(db):
        raise RuntimeError("reset failed")

    event.listen(store.engine, "rollback", fail_rollback)
    with pytest.raises(ValueError):
        store.write(lambda db: (_ for _ in ()).throw(ValueError("unit failed")))
    event.remove(store.engine, "rollback", fail_rollback)
    assert store.read(lambda db: db.execute(select(items)).all()) == []
    assert store.metrics()["created"] == 2
    store.close()


@pytest.mark.parametrize("nested", [False, True])
def test_lazy_result_cannot_escape_lease(tmp_path, nested):
    store, items = storage(tmp_path)
    try:

        def result(db):
            cursor = db.execute(select(items))
            return {"cursor": cursor} if nested else cursor

        with pytest.raises(PersistenceError, match="consume"):
            store.read(result)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_dispatch_admission_is_bounded_and_waiter_cancelled(tmp_path):
    store, _ = storage(tmp_path, wait_timeout=0.1)
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(3)

    jobs = [asyncio.create_task(store.arun(hold)) for _ in range(32)]
    assert await asyncio.to_thread(entered.wait, 3)
    while store.metrics()["admitted"] < 32:
        await asyncio.sleep(0)
    waiting = asyncio.create_task(store.arun(lambda: None))
    while not store.metrics()["waiters"]:
        await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert store.metrics()["admitted"] == 32
    with pytest.raises(PersistenceError, match="admission"):
        await store.arun(lambda: None)
    for job in jobs:
        job.cancel()
    release.set()
    await asyncio.gather(*jobs, return_exceptions=True)
    await store.aclose()
    assert store.metrics()["admitted"] == store.metrics()["waiters"] == 0


@pytest.mark.asyncio
async def test_inline_composition_uses_one_admission_slot(tmp_path):
    store, items = storage(tmp_path)

    def composition():
        store.write(lambda db: db.execute(insert(items).values(id=3)).rowcount)
        assert store.metrics()["admitted"] == 1
        return store.read(lambda db: db.execute(select(items.c.id)).scalar_one())

    assert await store.arun(composition) == 3
    await store.aclose()


@pytest.mark.asyncio
async def test_long_sql_is_interrupted_and_connection_is_reused_safely(tmp_path):
    store, _ = storage(tmp_path)
    entered = threading.Event()

    def long_query(db):
        entered.set()
        return db.exec_driver_sql(
            "WITH RECURSIVE numbers(x) AS (VALUES(0) UNION ALL "
            "SELECT x + 1 FROM numbers WHERE x < 100000000) SELECT sum(x) FROM numbers"
        ).scalar_one()

    job = asyncio.create_task(store.aread(long_query))
    assert await asyncio.to_thread(entered.wait, 3)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    deadline = asyncio.get_running_loop().time() + 3
    while store.metrics()["in_use"]:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.005)
    assert store.read(lambda db: db.exec_driver_sql("SELECT 42").scalar_one()) == 42
    assert store.metrics()["connections"] == 1
    await store.aclose()


@pytest.mark.asyncio
async def test_cancelled_initialization_reclaims_unpublished_owner():
    entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()

    class Owner:
        pass

    def factory():
        entered.set()
        assert release.wait(3)
        return Owner()

    task = asyncio.create_task(initialize(factory, cleanup=lambda _owner: cleaned.set()))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pending_initializations() == 1
    release.set()
    await drain_initializations(asyncio.get_running_loop().time() + 3)
    assert cleaned.is_set()
    assert pending_initializations() == 0
