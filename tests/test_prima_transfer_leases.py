"""Progress-bound grants/approvals and asynchronous worker asset I/O."""

import asyncio
import base64
import hashlib
import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.managed_network.interactors.identity import SignedRuntimeCommand
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.control import PrimaControl
from kllm.modules.primacpp.interactors.execution import Asset, ExecutionManifest, Segment
from kllm.modules.primacpp.interactors.transfers import (
    CHUNK_BYTES,
    PreparationLeases,
    PushReceiver,
    TransferGrants,
)


def asset_file(tmp_path, size=CHUNK_BYTES * 3):
    data = b"a" * size
    path = tmp_path / "model.gguf"
    path.write_bytes(data)
    return Asset(
        name=path.name, kind="model", sha256=hashlib.sha256(data).hexdigest(), size=size
    ), path


def test_exact_acknowledgements_keep_a_slow_transfer_live_beyond_initial_lease(tmp_path):
    asset, path = asset_file(tmp_path)
    now = [0.0]
    grants = TransferGrants(clock=lambda: now[0])
    operation = str(uuid4())
    grants.grant("worker", operation, asset, path, ttl_s=1)
    offset = 0
    for _ in range(3):
        reply = grants.chunk("worker", operation, asset.sha256, offset)
        now[0] += 0.6
        offset += len(base64.b64decode(reply["data"]))
        grants.acknowledge("worker", operation, asset.sha256, offset)
    assert now[0] > 1 and offset == asset.size
    with pytest.raises(ValueError, match="progress"):
        grants.acknowledge("worker", operation, asset.sha256, offset)
    grants.revoke(operation)
    with pytest.raises(ValueError, match="grant"):
        grants.chunk("worker", operation, asset.sha256, 0)


@pytest.mark.parametrize("changed", ["expired", "node", "operation", "offset", "bool", "file"])
def test_invalid_acknowledgement_never_extends_source_grant(tmp_path, changed):
    asset, path = asset_file(tmp_path)
    now = [0.0]
    grants = TransferGrants(clock=lambda: now[0])
    operation = str(uuid4())
    grants.grant("worker", operation, asset, path, ttl_s=1)
    grants.chunk("worker", operation, asset.sha256, 0)
    node, run, offset = "worker", operation, CHUNK_BYTES
    if changed == "expired":
        now[0] = 1
    elif changed == "node":
        node = "other"
    elif changed == "operation":
        run = str(uuid4())
    elif changed == "offset":
        offset -= 1
    elif changed == "bool":
        offset = True
    else:
        path.write_bytes(b"b" * asset.size)
    with pytest.raises(ValueError):
        grants.acknowledge(node, run, asset.sha256, offset)
    assert grants.grants["worker", operation, asset.sha256]["deadline"] == 1


def test_reading_without_acknowledgement_does_not_renew_and_idle_master_stops_renewing(tmp_path):
    asset, path = asset_file(tmp_path)
    now = [0.0]
    grants = TransferGrants(clock=lambda: now[0])
    operation = str(uuid4())
    grants.grant("worker", operation, asset, path, ttl_s=1)
    grants.chunk("worker", operation, asset.sha256, 0)
    now[0] = 1
    with pytest.raises(ValueError, match="expired"):
        grants.chunk("worker", operation, asset.sha256, 0)
    renewed = []

    def renew(node, job, progress):
        renewed.append((node, progress))
        return {"job_id": job, "phase": "prepared", "lease_progress": progress}

    leases = PreparationLeases(renew, clock=lambda: now[0], interval=1)
    leases.enroll("first", "job1")
    leases.enroll("second", "job2")
    now[0] += 1
    leases.advance(10)
    now[0] += 50
    leases.check()
    assert renewed == [("first", 10), ("second", 10)]
    leases.advance(1)
    assert renewed[-2:] == [("first", 11), ("second", 11)]


@pytest.fixture
def worker(tmp_path, monkeypatch):
    import kllm.modules.primacpp.interactors.control as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    control = object.__new__(PrimaControl)
    control.root = tmp_path
    control.lock = threading.RLock()
    from kllm.shared.runtime_resources import NodeResources
    control.resources = NodeResources()
    control.preparation_reservations = {}
    control.preparation_futures = {}
    control.jobs = {}
    control.closed = False
    control.cancelled = set()
    control.push_receiver = PushReceiver(tmp_path / "assets")
    from kllm.modules.primacpp.interactors.transfers import AssetReceiver
    control.receiver = AssetReceiver(tmp_path / "assets")
    control.registry = SimpleNamespace(capture=lambda _: {})
    control.rank = SimpleNamespace(owner=None)
    control.ports = SimpleNamespace(owner=None)
    control.service = SimpleNamespace(
        state=lambda: SimpleNamespace(node_id="worker", role="worker", process_pid=None)
    )
    authority = {
        "cluster_binding_id": str(uuid4()),
        "master_node_id": "master",
        "master_key_id": "a" * 64,
        "master_term": 1,
        "master_session_id": str(uuid4()),
        "version": 1,
        "type": "rpc_command",
        "channel_id": str(uuid4()),
        "sender_node_id": "master",
        "target_node_id": "worker",
        "sequence": 1,
    }

    def command(action, run, body):
        return SignedRuntimeCommand.model_validate(
            {
                **authority,
                "request_id": str(uuid4()),
                "run_id": str(run),
                "action": action,
                "ttl_ms": 60000,
                "body": body,
            }
        )

    return control, now, command


async def prepare(worker, asset):
    control, _, command = worker
    run = uuid4()
    # Signed preparation now always carries the signed execution manifest asset
    # and the worker verifies the received bytes against that exact contract.
    execution = ExecutionManifest(
        runtime_id="a" * 64,
        run_id=str(run),
        plan_digest="b" * 64,
        total_layers=1,
        ranks=[RankAllocation(node_id="worker", role="master", rank=0, address="127.0.0.1",
                              next_node_id="worker", layers=[LayerRange(start=0, end=1)], ngl=0)],
        assets=[asset],
        settings={"context": 512, "swa_full": False, "parallel_slots": 4,
                  "prima_kv_sequences": 5},
        inventory_digest="d" * 64,
        segments=[Segment(start=0, end=1, node_id="worker")],
        backends={"worker": "cpu"},
    )
    encoded = json.dumps(execution.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    manifest_asset = Asset(name="execution.json", kind="manifest",
                           sha256=execution.digest, size=len(encoded))
    result = await control.execute(
        command(
            "prima_prepare",
            run,
            {"runtime_id": "a" * 64, "operation_id": str(run),
             "assets": [asset.model_dump(), manifest_asset.model_dump()]},
        ),
        time.monotonic() + 5,
        lambda: None,
    )
    manifest_chunk = {
        "job_id": result["job_id"],
        "chunk": {
            "sha256": manifest_asset.sha256,
            "offset": 0,
            "size": manifest_asset.size,
            "data": base64.b64encode(encoded).decode(),
        },
    }
    await control.execute(
        command("prima_asset_chunk", run, manifest_chunk), time.monotonic() + 5, lambda: None
    )
    return run, result["job_id"]


@pytest.mark.asyncio
async def test_authenticated_chunk_progress_outlives_initial_worker_approval(worker, tmp_path):
    control, now, command = worker
    asset, _ = asset_file(tmp_path, CHUNK_BYTES * 4)
    run, job = await prepare(worker, asset)
    first_deadline = control.jobs[job]["expires_at"]
    assert control.resources.busy
    for offset in range(0, asset.size, CHUNK_BYTES):
        now[0] += 499
        reply = await control.execute(
            command(
                "prima_asset_chunk",
                run,
                {
                    "job_id": job,
                    "chunk": {
                        "sha256": asset.sha256,
                        "offset": offset,
                        "size": asset.size,
                        "data": base64.b64encode(b"a" * CHUNK_BYTES).decode(),
                    },
                },
            ),
            time.monotonic() + 5,
            lambda: None,
        )
        assert reply["offset"] == offset + CHUNK_BYTES
        assert control.resources.busy == (reply["offset"] < asset.size)
    assert now[0] > first_deadline and control.jobs[job]["phase"] == "prepared"
    assert control.push_receiver.offset(asset) == asset.size


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", [None, "repeat", "expired", "cancelled", "run", "authority", "bool"]
)
async def test_signed_approval_renewal_requires_fresh_owned_progress(worker, tmp_path, changed):
    control, now, command = worker
    asset, _ = asset_file(tmp_path)
    run, job = await prepare(worker, asset)
    request = command("prima_prepare_renew", run, {"job_id": job, "progress": 1})
    if changed == "repeat":
        await control.execute(request, time.monotonic() + 5, lambda: None)
    elif changed == "expired":
        now[0] = control.jobs[job]["expires_at"]
    elif changed == "cancelled":
        control.cancel(job)
    elif changed == "run":
        request.run_id = uuid4()
    elif changed == "authority":
        request.master_session_id = uuid4()
    elif changed == "bool":
        request.body["progress"] = True
    previous = control.jobs[job]["expires_at"]
    if changed is None:
        now[0] += 599
        result = await control.execute(request, time.monotonic() + 5, lambda: None)
        assert result["lease_progress"] == 1 and result["expires_at"] > previous
    else:
        with pytest.raises(ValueError):
            await control.execute(request, time.monotonic() + 5, lambda: None)
        assert control.jobs[job]["expires_at"] == previous


@pytest.mark.asyncio
async def test_worker_cache_verification_does_not_block_command_event_loop(
    worker, tmp_path, monkeypatch
):
    control, _, _ = worker
    asset, _ = asset_file(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    def slow(_):
        entered.set()
        assert finish.wait(3)
        return 0

    monkeypatch.setattr(control.push_receiver, "offset", slow)
    task = asyncio.create_task(prepare(worker, asset))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set() and not task.done()
        finish.set()
        await task
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
