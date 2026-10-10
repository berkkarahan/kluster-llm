from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psutil
import pytest

from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.execution import (
    Asset,
    ExecutionManifest,
    Segment,
    asset_files,
    require_execution_capabilities,
)
from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor, PrimaCoordinator
from kllm.modules.primacpp.interactors.transfers import (
    TransferGrants,
    AssetReceiver,
    PushReceiver,
    CHUNK_BYTES,
)
from kllm.modules.primacpp.interactors.planner import inventory_identity
from kllm.modules.runtimes.interactors.registry import file_digest


def manifest():
    return ExecutionManifest(
        runtime_id="a" * 64,
        run_id=str(uuid4()),
        plan_digest="b" * 64,
        total_layers=8,
        ranks=[
            RankAllocation(
                node_id="master",
                role="master",
                rank=0,
                address="127.0.0.1",
                next_node_id="worker",
                layers=[],
                ngl=0,
            ),
            RankAllocation(
                node_id="worker",
                role="worker",
                rank=1,
                address="127.0.0.2",
                next_node_id="master",
                layers=[LayerRange(start=0, end=8)],
                ngl=0,
            ),
        ],
        assets=[Asset(name="model.gguf", kind="model", sha256="c" * 64, size=1)],
        settings={"context": 512},
        inventory_digest="d" * 64,
        segments=[Segment(start=0, end=8, node_id="worker")],
        backends={"master": "cpu", "worker": "cpu"},
    )


def test_manifest_covers_zero_transformer_master_and_rejects_ownership_drift():
    value = manifest()
    assert value.ranks[0].layers == []
    assert ExecutionManifest.model_validate_json(value.model_dump_json()).digest == value.digest
    changed = value.model_dump()
    changed["segments"][0]["node_id"] = "master"
    with pytest.raises(ValueError, match="ownership"):
        ExecutionManifest.model_validate(changed)
    assert inventory_identity({"a": {"last_seen": 1, "role": "worker"}}) == inventory_identity(
        {"a": {"last_seen": 2, "role": "worker"}}
    )
    assert inventory_identity({"a": {"devices": [1]}}) != inventory_identity(
        {"a": {"devices": [2]}}
    )


def test_native_capabilities_do_not_claim_unimplemented_placements():
    value = manifest()
    with pytest.raises(ValueError, match="Upgrade"):
        require_execution_capabilities(value, {"features": []})
    with pytest.raises(ValueError, match="row"):
        require_execution_capabilities(
            value.model_copy(update={"split_mode": "row"}), {"features": ["execution_manifest_v1"]}
        )


def test_asset_collection_requires_all_shards_and_safe_companions(tmp_path):
    first = tmp_path / "model-00001-of-00002.gguf"
    first.write_bytes(b"a")
    with pytest.raises(ValueError, match="Missing"):
        asset_files(first)
    (tmp_path / "model-00002-of-00002.gguf").write_bytes(b"b")
    assert [a.kind for a, _ in asset_files(first)] == ["model", "shard"]
    with pytest.raises(ValueError, match="basenames"):
        Asset(name="../model", kind="model", sha256="a" * 64, size=1)


def test_transfer_grants_are_peer_operation_and_time_bound(tmp_path):
    path = tmp_path / "weights.gguf"
    path.write_bytes(b"abc")
    asset = Asset(name=path.name, kind="model", sha256=hashlib.sha256(b"abc").hexdigest(), size=3)
    now = [0.0]
    grants = TransferGrants(clock=lambda: now[0])
    operation = str(uuid4())
    grants.grant("worker", operation, asset, path, ttl_s=1)
    for node, run in [("other", operation), ("worker", str(uuid4()))]:
        with pytest.raises(ValueError, match="grant"):
            grants.chunk(node, run, asset.sha256, 0)
    assert base64.b64decode(grants.chunk("worker", operation, asset.sha256, 0)["data"]) == b"abc"
    now[0] = 2
    with pytest.raises(ValueError, match="expired"):
        grants.chunk("worker", operation, asset.sha256, 0)


def test_signed_push_receiver_resumes_and_refuses_corruption(tmp_path):
    data = b"x" * (CHUNK_BYTES + 1)
    asset = Asset(
        name="model.gguf", kind="model", sha256=hashlib.sha256(data).hexdigest(), size=len(data)
    )
    receiver = PushReceiver(tmp_path / "assets")

    def reply(offset, chunk):
        return {
            "sha256": asset.sha256,
            "size": asset.size,
            "offset": offset,
            "data": base64.b64encode(chunk).decode(),
        }

    assert receiver.push(asset, reply(0, data[:CHUNK_BYTES]), lambda: None) == CHUNK_BYTES
    receiver = PushReceiver(tmp_path / "assets")
    assert receiver.offset(asset) == CHUNK_BYTES
    with pytest.raises(ValueError, match="offset"):
        receiver.push(asset, reply(0, b"z"), lambda: None)
    assert receiver.push(asset, reply(CHUNK_BYTES, b"x"), lambda: None) == len(data)
    assert receiver.offset(asset) == len(data)
    wrong = Asset(name="bad", kind="template", sha256="f" * 64, size=1)
    with pytest.raises(ValueError, match="digest mismatch"):
        receiver.push(
            wrong, {"sha256": "f" * 64, "size": 1, "offset": 0, "data": "YQ=="}, lambda: None
        )
    assert receiver.offset(wrong) == 0


def test_rank_supervisor_controls_a_real_child_and_expires_lease(tmp_path):
    now = [0.0]
    children = []

    def spawn(*_):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        children.append(child)
        return {"pid": child.pid, "start_token": str(psutil.Process(child.pid).create_time())}

    def alive(pid):
        return any(c.pid == pid and c.poll() is None for c in children)

    def stop(pid):
        child = next(c for c in children if c.pid == pid)
        child.terminate()
        child.wait(timeout=5)
        return True

    value = manifest()
    supervisor = RankSupervisor(
        tmp_path,
        "worker",
        capture=lambda _: {"capabilities": {"features": ["execution_manifest_v1"]}},
        spawn=spawn,
        stop=stop,
        alive=alive,
        identity=lambda pid: str(psutil.Process(pid).create_time()),
        clock=lambda: now[0],
    )
    try:
        result = supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
        assert (
            supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)["pid"]
            == result["pid"]
        )
        with pytest.raises(ValueError, match="different run"):
            supervisor.stop(str(uuid4()))
        with pytest.raises(ValueError, match="acknowledgement"):
            supervisor.ready(value.run_id, {"rank": 0}, lambda: None)
        supervisor.ready(
            value.run_id,
            {
                "runtime_id": value.runtime_id,
                "rank": 1,
                "ngl": 0,
                "manifest_mode": True,
                "manifest_digest": value.digest,
            },
            lambda: None,
        )
        now[0] = 61
        assert supervisor.expire()["phase"] == "idle"
        assert not alive(result["pid"])
    finally:
        for c in children:
            if c.poll() is None:
                c.kill()
                c.wait(timeout=5)


def test_cleanup_retains_foreign_process_identity(tmp_path):
    value = manifest()
    supervisor = RankSupervisor(
        tmp_path,
        "worker",
        capture=lambda _: {},
        spawn=None,
        stop=lambda _: pytest.fail("foreign child killed"),
        alive=lambda _: True,
        identity=lambda _: "new",
    )
    supervisor.owner = {
        "run_id": value.run_id,
        "pid": 123,
        "start_token": "old",
        "phase": "starting",
    }
    with pytest.raises(ValueError, match="identity changed"):
        supervisor.stop(value.run_id)
    assert supervisor.status()["phase"] == "cleanup_pending"
    with pytest.raises(ValueError, match="identity changed"):
        supervisor.renew(value.run_id, lambda: None)
    with pytest.raises(ValueError, match="identity changed"):
        supervisor.ready(value.run_id, {}, lambda: None)


def test_assessment_requires_verified_baseline_and_reports_http_failures(tmp_path, monkeypatch):
    import runpy

    assessment = runpy.run_path(str(Path(__file__).with_name("prima_assessment.py")))
    binary = tmp_path / "baseline"
    binary.write_bytes(b"baseline executable")

    def reported(commit):
        return subprocess.CompletedProcess([], 0, "version: build 1, commit " + commit, "")

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: reported("1234567"))
    with pytest.raises(ValueError, match="Baseline binary"):
        assessment["capture_baseline"](binary)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: reported("7fe450e"))
    assert assessment["capture_baseline"](binary)["sha256"] == file_digest(binary)
    data = {
        "equal_http_statuses": True,
        "prima": {"cases": [{"status": 404}]},
        "baseline": {"cases": [{"status": 404}]},
    }
    assert assessment["observation_status"](data) == "failed"
    data["prima"]["cases"][0]["status"] = 200
    data["baseline"]["cases"][0]["status"] = 200
    assert assessment["observation_status"](data) == "observed"


def test_concurrent_control_initialization_keeps_one_owner(tmp_path, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from types import SimpleNamespace

    from kllm.modules.primacpp.interactors import control
    from kllm.modules.runtimes.interactors.runtime_control import RuntimeControlService

    instances = []
    closed = []

    class TestControl:
        def __init__(self, *_args, **_kwargs):
            time.sleep(0.01)
            instances.append(self)

        def close(self):
            closed.append(self)

    monkeypatch.setattr(control, "PrimaControl", TestControl)
    registry = SimpleNamespace(lock=threading.RLock(), close=lambda: None)
    service = RuntimeControlService(None, registry, lambda: "cpu")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.prima, range(24)))
    assert len(instances) == 1 and all(result is instances[0] for result in results)
    asyncio.run(service.close())
    assert closed == instances
    with pytest.raises(ValueError, match="closing"):
        _ = service.prima


def test_coordinator_refuses_bad_readiness_and_stops_all_started_ranks(tmp_path):
    value = manifest()
    actions = []

    def command(node, action, _):
        actions.append((node, action))
        return {"phase": "starting"} if action == "prima_start" else {"phase": "idle"}

    coordinator = PrimaCoordinator(
        tmp_path, command=command, admit=lambda _: None, release=lambda: None
    )
    with pytest.raises(ValueError, match="readiness"):
        coordinator.start(value)
    assert ("worker", "prima_stop") in actions and ("master", "prima_stop") in actions
    assert coordinator.active is None


def test_model_pull_detects_wrong_chunk_identity(tmp_path):
    asset = Asset(name="model", kind="model", sha256="a" * 64, size=1)
    with pytest.raises(ValueError, match="identity"):
        AssetReceiver(tmp_path).receive(asset, lambda *_: {"sha256": "b" * 64}, lambda: None)


@pytest.mark.integration
def test_real_native_manifest_check_preserves_zero_rank(tmp_path):
    binary = os.environ.get("KLLM_TEST_PRIMA_SERVER")
    if not binary:
        pytest.skip("Set KLLM_TEST_PRIMA_SERVER to the compiled Prima server")
    path = tmp_path / "execution.json"
    path.write_text(manifest().model_dump_json())
    r = subprocess.run(
        [binary, "--execution-manifest", str(path), "--rank", "0", "--check-execution-manifest"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode == 0, r.stderr
    assert (
        json.loads(r.stdout.split("KLLM_PRIMA_MANIFEST_CHECK=", 1)[1])["autonomous_scheduling"]
        is False
    )
    changed = manifest().model_dump()
    changed["split_mode"] = "row"
    path.write_text(json.dumps(changed))
    r = subprocess.run(
        [binary, "--execution-manifest", str(path), "--rank", "0", "--check-execution-manifest"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode != 0 and "cannot execute" in r.stderr


def test_master_preparation_is_read_only_until_requested_and_stages_assets(tmp_path):
    import time
    from types import SimpleNamespace
    from kllm.modules.inference.entities import PlanBody
    from kllm.modules.primacpp.interactors.control import PrimaControl

    model = tmp_path / "model.gguf"
    model.write_bytes(b"controlled model bytes")
    state = SimpleNamespace(
        role="master",
        node_id="master",
        process_pid=None,
        members={"master": {"role": "master", "host": "127.0.0.1", "backend": "cpu"}},
    )
    service = SimpleNamespace(state=lambda: state, channels=SimpleNamespace(sessions={}))
    runtime = {
        "engine": "primacpp",
        "runtime_id": "a" * 64,
        "backend": "cpu",
        "capabilities": {"features": ["execution_manifest_v1"], "backends": []},
    }
    registry = SimpleNamespace(
        root=tmp_path / "runtimes", capture=lambda _: runtime, _kill=lambda _: None
    )

    def plan(*_):
        return {
            "estimated_layers": 1,
            "device_effective": "cpu",
            "cpu_layers": 1,
            "device_allocations": [],
            "fit_status": "fit",
        }

    control = PrimaControl(service, registry, resolve_model=lambda _: model, execute_plan=plan)
    try:
        body = PlanBody(model="model.gguf")
        preview = control.plan("a" * 64, body)
        assert not control.root.exists()
        request = str(uuid4())
        job = control.start_preparation("a" * 64, body, preview["plan_digest"], request)
        until = time.monotonic() + 3
        while (
            control.job(job["job_id"])["phase"] not in {"prepared", "failed"}
            and time.monotonic() < until
        ):
            time.sleep(0.01)
        finished = control.job(job["job_id"])
        assert finished["phase"] == "prepared", finished
        assert (
            control.start_preparation("a" * 64, body, preview["plan_digest"], request)["job_id"]
            == job["job_id"]
        )
        assert control.cached_model(finished["manifest_digest"]).is_file()
        assert state.process_pid is None
        model.write_bytes(b"changed model")
        stale = control.start_preparation("a" * 64, body, preview["plan_digest"], str(uuid4()))
        until = time.monotonic() + 3
        while (
            control.job(stale["job_id"])["phase"] not in {"prepared", "failed"}
            and time.monotonic() < until
        ):
            time.sleep(0.01)
        assert "plan changed" in control.job(stale["job_id"])["error"]
    finally:
        control.close()


@pytest.mark.asyncio
async def test_real_signed_channel_pushes_only_operation_bound_assets(tmp_path):
    import asyncio
    from types import SimpleNamespace
    import websockets
    from kllm.modules.managed_network.gateways.store import NetworkStore
    from kllm.modules.managed_network.interactors.identity import ChannelFence, LocalIdentity
    from kllm.modules.managed_network.interactors.network_channel import ChannelSession, SocketWire
    from kllm.modules.managed_network.entities import PeerIdentity
    from kllm.modules.primacpp.interactors.control import PrimaControl

    stores = [NetworkStore(tmp_path / name) for name in ("master", "worker")]
    ids = [
        LocalIdentity(tmp_path / name, name, store)
        for name, store in zip(("master", "worker"), stores)
    ]
    authority = (
        stores[0]
        .startup_authority("master", ids[0].key_id)
        .model_copy(update={"accepted_channel_id": uuid4()})
    )
    peers = [
        PeerIdentity(
            **identity.public(authority.cluster_binding_id).model_dump(), expected_role=role
        )
        for identity, role in zip(ids, ("master", "worker"))
    ]
    for i in (0, 1):
        stores[i].pair(peers[1 - i])
        stores[i].accept_authority(authority, peers[1 - i].node_id)
    state = SimpleNamespace(role="worker", node_id="worker", process_pid=None, members={})
    registry = SimpleNamespace(
        root=tmp_path / "worker-runtime",
        capture=lambda _: {"engine": "primacpp"},
        _kill=lambda _: None,
    )
    control = PrimaControl(
        SimpleNamespace(state=lambda: state), registry, resolve_model=None, execute_plan=None
    )
    future = asyncio.get_running_loop().create_future()

    async def serve(socket):
        session = ChannelSession(
            SocketWire(socket),
            ids[0],
            ChannelFence(stores[0], peers[1], authority, "worker", "master"),
            master=True,
        )
        future.set_result(session)
        await session.pump()

    async def handle(command, deadline):
        try:
            return await control.execute(
                command, deadline, lambda: stores[1].check_channel("master", authority)
            )
        except ValueError as error:
            return {"status": "failed", "message": str(error)}

    try:
        async with websockets.serve(serve, "127.0.0.1", 0) as server:
            async with websockets.connect(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", proxy=None
            ) as socket:
                worker = ChannelSession(
                    SocketWire(socket),
                    ids[1],
                    ChannelFence(stores[1], peers[0], authority, "master", "worker"),
                    master=False,
                    handler=handle,
                )
                task = asyncio.create_task(worker.pump())
                master = await future
                data = b"signed model bytes"
                asset = Asset(
                    name="model.gguf",
                    kind="model",
                    sha256=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                )
                run = uuid4()
                prepared = await master.request_runtime(
                    run,
                    "prima_prepare",
                    body={
                        "runtime_id": "a" * 64,
                        "operation_id": str(run),
                        "assets": [asset.model_dump()],
                    },
                )
                payload = {
                    "job_id": prepared["job_id"],
                    "chunk": {
                        "sha256": asset.sha256,
                        "offset": 0,
                        "size": asset.size,
                        "data": base64.b64encode(data).decode(),
                    },
                }
                refused = await master.request_runtime(uuid4(), "prima_asset_chunk", body=payload)
                assert refused["status"] == "failed"
                copied = await master.request_runtime(run, "prima_asset_chunk", body=payload)
                assert copied["phase"] == "prepared"
                assert control.cached_model(asset.sha256).read_bytes() == data
                assert state.process_pid is None
                await worker.close()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        control.close()
        for store in stores:
            store.close()
