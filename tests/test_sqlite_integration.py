"""Real-file, HTTP and process acceptance for adopted persistence domains."""

from datetime import datetime, timezone
from datetime import timedelta
import json
import multiprocessing
import queue
import sqlite3
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, MetaData, Text, delete, func, inspect, select, update
from sqlalchemy.dialects.sqlite import insert

from kllm.shared.persistence import SQLiteOptions, SQLiteStore
from kllm.modules.usage.gateways.usage_store import UsageStore
from kllm.modules.usage.interactors.usage_runtime import UsageRuntime
from kllm.modules.managed_network.gateways.store import NetworkStore
from kllm.modules.managed_network.entities import NodeNetworkIdentity
from sqlite_fixtures import legacy_file, seed_legacy_rows
from test_usage import reading


def _open_network_store_racing(home, barrier, results, index):
    try:
        barrier.wait(timeout=10)
        store = NetworkStore(home, options=SQLiteOptions(pool_size=1))
        store.set_metadata(f"process-{index}", f"value-{index}")
        store.close()
        results.put((index, "ok"))
    except BaseException as error:
        results.put((index, type(error).__name__))


def _mutate_network_under_fence(home, attempting, results):
    store = NetworkStore(home, options=SQLiteOptions(pool_size=1))
    attempting.put("ready")
    with store.execution():
        store.set_metadata("fenced-process", "committed")
    store.close()
    results.put("committed")


def usage_application(tmp_path):
    from fastapi import FastAPI, Request
    from kllm.modules.usage.interactors import usage
    from kllm.modules.usage.router_http import UsageIngestHandlers
    from kllm.shared.state import ClusterState

    point = reading(datetime.now(timezone.utc))
    state = ClusterState(
        node_id="master",
        role="master",
        members={
            "worker": {
                "role": "worker",
                "process_pid": 12,
                "process_role": "worker",
                "process_started_at": point["process_started_at"],
                "host": "worker-host",
            }
        },
    )
    store = UsageStore(tmp_path / "usage.sqlite3", options=SQLiteOptions(wait_timeout=0.1))
    handlers = UsageIngestHandlers(
        lambda: state,
        usage.ResourceState(),
        lambda: store,
        lambda node: None,
        lambda member, model: {},
        lambda: False,
    )
    app = FastAPI()

    async def ingest(request: Request):
        return await handlers.ingest(request)

    app.post("/v2/telemetry/ingest")(ingest)

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app, store, point


@pytest.mark.asyncio
async def test_http_ingest_saturation_is_unavailable_without_loop_stall(tmp_path):
    import httpx

    app, store, point = usage_application(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def hold():
        with store.maintenance():
            entered.set()
            assert release.wait(3)

    job = asyncio.create_task(asyncio.to_thread(hold))
    assert await asyncio.to_thread(entered.wait, 3)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v2/telemetry/ingest", json={"node_id": "worker", "readings": [point]}
            )
            assert response.status_code == 503
            assert (await client.get("/health")).status_code == 200
    finally:
        release.set()
        await job
        await store.aclose()


@pytest.mark.asyncio
async def test_cancelled_http_ingest_retains_precommit_ownership_and_rolls_back(tmp_path):
    import httpx

    app, store, point = usage_application(tmp_path)
    entered, release = threading.Event(), threading.Event()
    write = store._write

    def blocked_write(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return write(*args, **kwargs)

    store._write = blocked_write
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            request = asyncio.create_task(
                client.post("/v2/telemetry/ingest", json={"node_id": "worker", "readings": [point]})
            )
            assert await asyncio.to_thread(entered.wait, 3)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert store._store.metrics()["in_use"] == 1
            release.set()
            deadline = time.monotonic() + 3
            while store._store.metrics()["in_use"]:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.005)
            assert store.count() == 0
            store._write = write
            assert store.record("worker", "worker-host", point) is True
            assert store.count() == 1
    finally:
        release.set()
        store._write = write
        await store.aclose()


@pytest.mark.asyncio
async def test_cancelled_http_ingest_after_commit_boundary_keeps_durable_batch(
    tmp_path, monkeypatch
):
    import httpx
    from kllm.shared.persistence.operations import Operation

    app, store, point = usage_application(tmp_path)
    committing, release = threading.Event(), threading.Event()
    begin_commit = Operation.begin_commit

    def blocked_commit(operation):
        begin_commit(operation)
        committing.set()
        assert release.wait(3)

    monkeypatch.setattr(Operation, "begin_commit", blocked_commit)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            request = asyncio.create_task(
                client.post("/v2/telemetry/ingest", json={"node_id": "worker", "readings": [point]})
            )
            assert await asyncio.to_thread(committing.wait, 3)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert store._store.metrics()["in_use"] == 1
            release.set()
            deadline = time.monotonic() + 3
            while store._store.metrics()["in_use"]:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.005)
            assert store._store.last_outcome["confirmed_units"] >= 1
            assert store.count() == 1
            retry = await client.post(
                "/v2/telemetry/ingest", json={"node_id": "worker", "readings": [point]}
            )
            assert retry.status_code == 200
            assert retry.json()["accepted"] == 0
            assert retry.json()["duplicates"] == 1
    finally:
        release.set()
        await store.aclose()


@pytest.mark.asyncio
async def test_async_network_route_held_lease_preserves_status_progress(tmp_path):
    from fastapi import FastAPI
    import httpx
    from kllm.modules.managed_network.router_http import build_router
    from uuid import uuid4

    store = NetworkStore(tmp_path, options=SQLiteOptions(wait_timeout=0.1))

    def network_view(network_id):
        return {"metadata": store.metadata("probe")}

    service = SimpleNamespace(
        store=store, state=lambda: SimpleNamespace(role="master"), network_view=network_view
    )
    app = FastAPI()
    app.include_router(build_router(lambda: service, lambda token: True))

    @app.get("/health")
    async def health():
        return {"ok": True}

    entered, release = threading.Event(), threading.Event()

    def hold():
        with store.read():
            entered.set()
            assert release.wait(3)

    job = asyncio.create_task(asyncio.to_thread(hold))
    assert await asyncio.to_thread(entered.wait, 3)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get(f"/v2/cluster/networks/{uuid4()}")
            assert response.status_code == 503
            assert (await client.get("/health")).status_code == 200
    finally:
        release.set()
        await job
        await store.aclose()


@pytest.mark.parametrize("capacity", [1, 4])
@pytest.mark.parametrize("missing_context", [False, True])
def test_usage_legacy_adoption_and_cursor_parity(tmp_path, capacity, missing_context):
    path = tmp_path / "usage.sqlite3"
    legacy_file(path, "usage", missing_context=missing_context)
    now = datetime.now(timezone.utc)
    point = reading(now)
    options = SQLiteOptions(pool_size=capacity)
    with UsageStore(path, options=options) as store:
        assert store._meta("cursor_secret") == "ab" * 32
        assert store.record("worker", "O'Reilly", point, now=now)
        assert store.record("worker", "O'Reilly", point, now=now) is False
        assert store.known_nodes()["worker"]["host"] == "O'Reilly"
        page = store.readings("worker", point["lifetime_id"], "15m", now=now)
        assert page["readings"][0]["observed_at"] == point["observed_at"]
        assert store.latest("worker")["pid"] == 12
        assert store.count() == 1
    with UsageStore(path, options=options) as reopened:
        assert reopened._meta("cursor_secret") == "ab" * 32
        assert (
            reopened.readings("worker", point["lifetime_id"], "15m", now=now)["readings"]
            == page["readings"]
        )
        assert reopened._store.metrics()["created"] == 1


@pytest.mark.parametrize("capacity", [1, 4])
def test_network_legacy_crud_and_parameter_like_values(tmp_path, capacity):
    path = tmp_path / "managed-network.sqlite"
    legacy_file(path, "network")
    seed_legacy_rows(path, {"metadata": [{"key": "keep", "value": "old"}]})
    store = NetworkStore(tmp_path, options=SQLiteOptions(pool_size=capacity))
    try:
        assert store.metadata("keep") == "old"
        value = "'; DROP TABLE metadata; --"
        store.set_metadata("quote", value)
        assert store.metadata("quote") == value
        store.set_metadata("quote", "updated")
        assert store.metadata("quote") == "updated"
        item = NodeNetworkIdentity(node_id="n", hostname="node-one", role="worker")
        store.put_directory(item)
        assert store.directory() == [item]
        store.remove_directory("n")
        assert store.directory() == []
    finally:
        store.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='keep'").fetchone()[0] == "old"


def test_unsupported_schema_never_recreated(tmp_path):
    path = tmp_path / "managed-network.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
        db.execute("INSERT INTO metadata VALUES ('keep', 7)")
    with pytest.raises(sqlite3.DatabaseError):
        NetworkStore(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM metadata").fetchall() == [("keep", 7)]


def test_two_process_initialization_preserves_both_writers(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_open_network_store_racing,
            args=(tmp_path, barrier, results, index),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(timeout=15)
            assert not process.is_alive()
            assert process.exitcode == 0
        outcomes = sorted(results.get(timeout=2) for _ in processes)
        assert outcomes == [(0, "ok"), (1, "ok")]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
    store = NetworkStore(tmp_path)
    assert store.metadata("process-0") == "value-0"
    assert store.metadata("process-1") == "value-1"
    store.close()


def test_two_process_network_mutations_share_the_execution_fence(tmp_path):
    context = multiprocessing.get_context("spawn")
    parent = NetworkStore(tmp_path)
    attempting = context.Queue()
    results = context.Queue()
    with parent.execution():
        process = context.Process(
            target=_mutate_network_under_fence,
            args=(tmp_path, attempting, results),
        )
        process.start()
        assert attempting.get(timeout=10) == "ready"
        time.sleep(0.1)
        assert process.is_alive()
        with pytest.raises(queue.Empty):
            results.get(timeout=0.05)
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    assert process.exitcode == 0
    assert results.get(timeout=2) == "committed"
    assert parent.metadata("fenced-process") == "committed"
    parent.close()


@pytest.mark.parametrize("domain", ["usage", "network"])
def test_temporary_field_and_filter_maintenance_workflow(tmp_path, domain):
    """A future-field exercise changes a private test schema, never product fields."""
    from kllm.modules.usage import entities as usage_schema
    from kllm.modules.managed_network.gateways import schema as network_schema

    original = usage_schema.metadata if domain == "usage" else network_schema.schema
    extended = MetaData()
    for table in original.tables.values():
        table.to_metadata(extended)
    table = extended.tables["nodes" if domain == "usage" else "metadata"]
    table.append_column(Column("maintenance_note", Text))
    path = tmp_path / (domain + ".sqlite")
    legacy_file(path, domain)

    def upgrade(db):
        if "maintenance_note" not in {c["name"] for c in inspect(db).get_columns(table.name)}:
            db.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN maintenance_note TEXT')

    options = SQLiteOptions(pool_size=1)
    with SQLiteStore(path, extended, profile=domain, options=options, upgrade=upgrade) as store:
        values = (
            {"node_id": "n", "host": "host", "role": "worker", "context": "{}"}
            if domain == "usage"
            else {"key": "field-test", "value": "keep"}
        )
        values["maintenance_note"] = "quoted ' value"
        store.write(lambda db: db.execute(insert(table).values(values)).rowcount)
        assert (
            store.read(
                lambda db: db.execute(
                    select(table.c.maintenance_note).where(
                        table.c.maintenance_note == "quoted ' value"
                    )
                ).scalar_one()
            )
            == "quoted ' value"
        )
        store.write(
            lambda db: (
                db.execute(
                    update(table)
                    .where(table.c.maintenance_note == "quoted ' value")
                    .values(maintenance_note="updated")
                ).rowcount
            )
        )
        assert (
            store.read(
                lambda db: db.execute(
                    select(table.c.maintenance_note).where(table.c.maintenance_note == "updated")
                ).scalar_one()
            )
            == "updated"
        )
        store.write(
            lambda db: (
                db.execute(delete(table).where(table.c.maintenance_note == "updated")).rowcount
            )
        )
    with SQLiteStore(path, extended, profile=domain, options=options, upgrade=upgrade) as store:
        assert (
            store.read(
                lambda db: db.execute(
                    select(table.c.maintenance_note).where(table.c.maintenance_note == "updated")
                ).all()
            )
            == []
        )


def test_usage_runtime_directory_replacement_closes_before_publish(tmp_path):
    app = SimpleNamespace(state=SimpleNamespace(usage_store=None))
    current_home = [tmp_path / "first"]
    current_home[0].mkdir()
    runtime = UsageRuntime.__new__(UsageRuntime)
    runtime._app_state = lambda: app.state
    runtime._home_dir = lambda: current_home[0]
    runtime._store_lock = threading.Lock()

    original = runtime.store()
    original.record("worker", "host", reading(datetime.now(timezone.utc)))
    current_home[0] = tmp_path / "second"
    current_home[0].mkdir()
    replacement = runtime.store()
    try:
        assert replacement is not original
        assert replacement.path == current_home[0] / "usage.sqlite3"
        assert replacement.count() == 0
        assert original._store.state == "CLOSED"
    finally:
        replacement.close()

    app.state.usage_store = original
    current_home[0] = tmp_path / "third"
    current_home[0].mkdir()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(original, "close", lambda **_kwargs: (_ for _ in ()).throw(OSError("busy")))
    try:
        with pytest.raises(OSError, match="busy"):
            runtime.store()
        assert app.state.usage_store is original
    finally:
        monkeypatch.undo()
        original.close()


@pytest.mark.parametrize("kind", ["usage", "network"])
@pytest.mark.parametrize("capacity", [1, 4])
def test_resource_cycles_reuse_and_release_every_owner(tmp_path, kind, capacity):
    path = tmp_path / ("usage.sqlite3" if kind == "usage" else "managed-network.sqlite")
    options = SQLiteOptions(pool_size=capacity)
    observed = datetime.now(timezone.utc)
    max_connections = 0
    total_operations = 0
    for cycle in range(100):
        store = (
            UsageStore(path, options=options)
            if kind == "usage"
            else NetworkStore(tmp_path, options=options)
        )
        for offset in range(5):
            key = f"cycle:{cycle}:{offset}"
            if kind == "usage":
                point = reading(observed.replace(microsecond=(cycle * 5 + offset) % 1_000_000))
                store.record("worker", "fixture-host", point)
                store.count()
            else:
                store.set_metadata(key, str(offset))
                store.metadata(key)
            total_operations += 2
        metrics = store._store.metrics()
        max_connections = max(max_connections, metrics["connections"])
        assert 0 < metrics["created"] <= capacity
        store.close()
        final = store._store.metrics()
        assert final["connections"] == final["in_use"] == final["admitted"] == final["jobs"] == 0
        assert final["waiters"] == final["workers"] == final["close_jobs"] == 0
    assert total_operations == 1000
    assert max_connections <= capacity


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [1, 4])
async def test_responsiveness_with_retained_history_and_delayed_usage_lease(tmp_path, capacity):
    import httpx
    from fastapi import FastAPI
    from kllm.modules.usage import entities as usage_schema
    from kllm.modules.control_v2 import build_router
    from kllm.modules.cluster.presenters.dashboard_status import DashboardStatusService
    from kllm.shared.state import ClusterState

    usage_store = UsageStore(
        tmp_path / "usage.sqlite3",
        options=SQLiteOptions(pool_size=capacity, wait_timeout=5),
    )
    network_store = NetworkStore(
        tmp_path,
        options=SQLiteOptions(pool_size=capacity, wait_timeout=5),
    )
    network_store.set_metadata("availability", "ready")
    now = datetime.now(timezone.utc)
    begin = usage_store._meta("cursor_secret")  # force the normal initialized owner
    assert begin
    payload = reading(now)
    rows = [
        {
            "lifetime_id": payload["lifetime_id"],
            "node_id": "worker",
            "observed_at": (now + timedelta(microseconds=index)).isoformat(),
            "received_at": now.isoformat(),
            "payload": json.dumps(
                {
                    **payload,
                    "observed_at": (now + timedelta(microseconds=index)).isoformat(),
                },
                separators=(",", ":"),
            ),
        }
        for index in range(10_000)
    ]
    with usage_store.maintenance() as db:
        db.execute(
            usage_schema.nodes.insert().values(
                node_id="worker", host="fixture-host", role="worker", context="{}"
            )
        )
        db.execute(
            usage_schema.lifetimes.insert().values(
                lifetime_id=payload["lifetime_id"],
                node_id="worker",
                pid=12,
                role="worker",
                started_at=payload["process_started_at"],
                state="running",
            )
        )
        db.execute(usage_schema.readings.insert(), rows)

    app = FastAPI()
    write_sequence = 0
    status_service = DashboardStatusService(
        lambda: ClusterState(node_id="master", role="master"),
        load_config=lambda: {},
        inference_activity=lambda: {"server_ready": False},
        probe_capacity=lambda: {},
        last_plan=lambda _state: None,
        download_progress=dict,
        rpc_cache_status=dict,
        update_progress=dict,
        software_identity=dict,
        default_device="cpu",
        network_directory=lambda: [{"availability": network_store.metadata("availability")}],
    )
    app.include_router(build_router({"status": status_service.status}))

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/usage")
    async def usage_count():
        return {
            "count": await usage_store._store.aread(
                lambda db: db.execute(
                    select(func.count()).select_from(usage_schema.readings)
                ).scalar_one()
            )
        }

    @app.post("/usage")
    async def usage_write():
        nonlocal write_sequence
        write_sequence += 1
        point = reading(now + timedelta(seconds=1, microseconds=write_sequence))
        accepted = await usage_store._store.arun(
            usage_store.record, "worker", "fixture-host", point
        )
        return {"accepted": accepted}

    @app.get("/usage-sync")
    async def usage_sync():
        return {"count": await asyncio.to_thread(usage_store.count)}

    entered, release = threading.Event(), threading.Event()

    def delayed(db):
        entered.set()
        assert release.wait(3)
        return db.execute(select(func.count()).select_from(usage_schema.readings)).scalar_one()

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            timer = threading.Timer(2, release.set)
            timer.start()
            delayed_job = asyncio.create_task(usage_store._store.aread(delayed))
            assert await asyncio.to_thread(entered.wait, 2)
            status = await client.get("/v2/cluster")
            assert status.status_code == 200
            assert status.json()["network_directory"] == [{"availability": "ready"}]

            async def health_probe():
                started = time.monotonic()
                response = await client.get("/health")
                return response, time.monotonic() - started

            health_results = await asyncio.gather(*(health_probe() for _ in range(200)))
            health = [response for response, _ in health_results]
            latencies = sorted(duration for _, duration in health_results)
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[int(len(latencies) * 0.95) - 1]
            assert all(response.status_code == 200 for response in health)
            assert p95 < 1.0
            consumers = await asyncio.gather(
                *(
                    client.get("/usage")
                    if index % 4 == 0
                    else client.post("/usage")
                    if index % 4 == 1
                    else client.get("/usage-sync")
                    if index % 4 == 2
                    else client.get("/v2/cluster")
                    for index in range(20)
                )
            )
            assert all(response.status_code == 200 for response in consumers)
            assert await delayed_job >= 10_000
            retained = await usage_store._store.aread(
                lambda db: db.execute(
                    select(func.count()).select_from(usage_schema.readings)
                ).scalar_one()
            )
            assert retained == 10_005
            print(
                f"responsiveness pool={capacity} probes={len(health)} p50={p50:.4f}s "
                f"p95={p95:.4f}s retained={retained} bytes={usage_store._physical_bytes()} errors=0"
            )
    finally:
        release.set()
        await usage_store.aclose()
        await network_store.aclose()
