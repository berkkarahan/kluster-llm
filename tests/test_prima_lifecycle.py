from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
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
from kllm.shared.runtime_resources import NodeResources

# Features a current managed Prima runtime advertises. Capability admission now
# requires the logical context slot, embedding role, batch metadata and compact
# SWA contracts before any placement check, so fixtures present the production set.
BASELINE_FEATURES = [
    "execution_manifest_v1",
    "logical_context_sequences_v1",
    "embedding_role_v1",
    "decode_metadata_v2",
    "compact_swa_v1",
    "explicit_devices_v1",
]


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
        settings={"context": 512, "swa_full": False, "parallel_slots": 4,
                  "prima_kv_sequences": 5},
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
            value.model_copy(update={"split_mode": "row"}), {"features": BASELINE_FEATURES}
        )


@pytest.mark.parametrize("backend", ["vulkan", "Vulkan"])
def test_vulkan_offload_refuses_quantized_kv_cache_before_launch(backend):
    value = manifest()
    value.ranks[1].ngl = 8
    value.ranks[1].devices = ["Vulkan0"]
    value.ranks[1].tensor_split = [1.0]
    value.backends["worker"] = backend
    quantized = value.model_copy(update={"settings": {
        **value.settings, "cache_type_k": "q8_0", "cache_type_v": "q8_0", "flash_attention": "on"}})
    with pytest.raises(ValueError, match="Vulkan backend cannot store a q8_0 K/V cache"):
        require_execution_capabilities(quantized, {"features": BASELINE_FEATURES})
    # f16 storage is writable by the Vulkan copy kernel.
    require_execution_capabilities(value.model_copy(update={"settings": {
        **value.settings, "cache_type_k": "f16", "cache_type_v": "f16"}}),
        {"features": BASELINE_FEATURES})
    # Legacy feature gate and structured vulkan_kv capabilities both admit all 9 KV types.
    require_execution_capabilities(quantized, {"features": [*BASELINE_FEATURES, "vulkan_quantized_kv_v1"]})
    all_kv_types = ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "q5_0", "q5_1", "iq4_nl"]
    vk_caps = {
        "features": [*BASELINE_FEATURES, "vulkan_kv_cache_v1", "vulkan_flash_attn_v1"],
        "vulkan_kv": {
            "write_types": all_kv_types,
            "mul_mat_k_types": all_kv_types,
            "mul_mat_v_types": ["f32", "f16", "bf16"],
            "flash_attn": {"types": all_kv_types, "head_sizes": [64, 80, 96, 112, 128, 192, 256], "sinks": True},
        },
    }
    for kv_type in all_kv_types:
        require_execution_capabilities(
            value.model_copy(update={"settings": {
                **value.settings, "cache_type_k": kv_type, "cache_type_v": kv_type, "flash_attention": "on"}}),
            vk_caps,
        )
        require_execution_capabilities(
            value.model_copy(update={"settings": {
                **value.settings, "cache_type_k": kv_type, "cache_type_v": "f16", "flash_attention": "off"}}),
            vk_caps,
        )
    with pytest.raises(ValueError, match="requires Flash Attention"):
        require_execution_capabilities(
            value.model_copy(update={"settings": {
                **value.settings, "cache_type_k": "q8_0", "cache_type_v": "q8_0", "flash_attention": "off"}}),
            vk_caps,
        )


def test_cpu_ranks_keep_quantized_kv_cache():
    value = manifest()
    require_execution_capabilities(value.model_copy(update={"settings": {
        **value.settings, "cache_type_k": "q8_0", "cache_type_v": "q8_0", "flash_attention": "on"}}),
        {"features": BASELINE_FEATURES})


@pytest.mark.parametrize("reported_backend", ["Vulkan", "vulkan", "VULKAN"])
def test_rank_backend_names_are_case_insensitive_but_do_not_substitute(tmp_path, reported_backend):
    value = manifest()
    value.ranks[1].ngl = 8
    value.ranks[1].devices = ["Vulkan0"]
    value.ranks[1].tensor_split = [1.0]
    value.backends["worker"] = "vulkan"
    runtime = {"capabilities": {"features": list(BASELINE_FEATURES), "backends": [reported_backend]}}
    spawned = []
    supervisor = RankSupervisor(tmp_path, "worker", capture=lambda _: runtime,
        spawn=lambda *_: spawned.append(True) or {"pid": 123, "start_token": "owned"},
        stop=lambda _: True, alive=lambda _: False, identity=lambda _: "owned")
    assert supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)["backend"] == "vulkan"
    assert spawned == [True]
    supervisor.stop(value.run_id)
    runtime["capabilities"]["backends"] = ["CUDA", "CPU"]
    with pytest.raises(ValueError, match="GPU backend is unavailable"):
        supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
    assert spawned == [True]


def test_inventory_ignores_free_memory_but_preserves_physical_capacity_and_identity():
    device = {"name": "Vulkan0", "vram_bytes": 1000, "free_bytes": 900,
              "device_identity": "a" * 32, "driver_identity": "123"}
    before = {"master": {"devices": [device]}}
    after = {"master": {"devices": [{**device, "free_bytes": 100}]}}
    assert inventory_identity(before) == inventory_identity(after)
    assert device["free_bytes"] == 900
    for field, changed in (("vram_bytes", 2000), ("device_identity", "b" * 32), ("driver_identity", "456")):
        assert inventory_identity(before) != inventory_identity({"master": {"devices": [{**device, field: changed}]}})


@pytest.mark.asyncio
async def test_rank_expiry_monitor_survives_late_prima_initialization(monkeypatch):
    from types import SimpleNamespace
    from kllm.modules.managed_network.interactors import service as managed_service
    calls = []
    state = SimpleNamespace(closed=False, runtimes=SimpleNamespace(_prima=None),
                            worker_rpc=SimpleNamespace(expire=lambda: calls.append("worker")))
    def expire():
        calls.append("prima")
        state.closed = True
    async def tick(_):
        if calls:
            state.runtimes._prima = SimpleNamespace(expire=expire)
    monkeypatch.setattr(managed_service.asyncio, "sleep", tick)
    await managed_service.ManagedNetworkService._expire_worker_rpc(state)
    assert calls == ["worker", "worker", "prima"]


def test_asset_collection_requires_all_shards_and_safe_companions(tmp_path):
    first = tmp_path / "model-00001-of-00002.gguf"
    first.write_bytes(b"a")
    with pytest.raises(ValueError, match="Missing"):
        asset_files(first)
    (tmp_path / "model-00002-of-00002.gguf").write_bytes(b"b")
    assert [a.kind for a, _ in asset_files(first)] == ["model", "shard"]
    with pytest.raises(ValueError, match="first GGUF shard"):
        asset_files(tmp_path / "model-00002-of-00002.gguf")
    upper = tmp_path / "MODEL-00001-of-00002.gguf"
    if not upper.exists():
        upper.write_bytes(b"c")
        (tmp_path / "MODEL-00002-of-00002.gguf").write_bytes(b"d")
    with pytest.raises(ValueError, match="conflicting basenames"):
        asset_files(first, {"draft": upper})
    with pytest.raises(ValueError, match="basenames"):
        Asset(name="../model", kind="model", sha256="a" * 64, size=1)


def test_prepared_native_layout_rejects_changed_or_missing_shards(tmp_path):
    from types import SimpleNamespace
    from kllm.modules.primacpp.interactors.control import PrimaControl

    receiver = AssetReceiver(tmp_path / "cache")
    receiver.stager.root.mkdir()
    assets = []
    for kind, name, data in (
        ("model", "model-00001-of-00002.gguf", b"first shard"),
        ("shard", "model-00002-of-00002.gguf", b"second shard"),
    ):
        digest = hashlib.sha256(data).hexdigest()
        asset = Asset(name=name, kind=kind, sha256=digest, size=len(data))
        source, _ = receiver.stager._paths(digest)
        source.write_bytes(data)
        assets.append(asset)
    job_id = str(uuid4())
    control = SimpleNamespace(receiver=receiver, root=tmp_path / "control",
                              lock=threading.RLock(),
                              jobs={job_id: {"job_id": job_id,
                                             "assets": [asset.model_dump() for asset in assets]}})

    model = PrimaControl.cached_model(control, assets[0].sha256, job_id=job_id)
    companion = model.parent / assets[1].name
    assert model.name == assets[0].name and companion.read_bytes() == b"second shard"

    companion.unlink()
    companion.write_bytes(b"other bytes!")
    with pytest.raises(ValueError, match="layout differs"):
        PrimaControl.cached_model(control, assets[0].sha256, job_id=job_id)

    companion.unlink()
    source, _ = receiver.stager._paths(assets[1].sha256)
    source.unlink()
    with pytest.raises(ValueError, match="asset is unavailable"):
        PrimaControl.cached_model(control, assets[0].sha256, job_id=job_id)


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
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=spawn,
        stop=stop,
        alive=alive,
        identity=lambda pid: str(psutil.Process(pid).create_time()),
        clock=lambda: now[0],
    )
    try:
        command_owner = {"sender": "master", "authority_digest": "c" * 64}
        result = supervisor.start(value, tmp_path / "model", lambda: None,
                                  assessment=True, command_owner=command_owner)
        assert (
            supervisor.start(value, tmp_path / "model", lambda: None,
                             assessment=True, command_owner=command_owner)["pid"]
            == result["pid"]
        )
        assert supervisor.status(value.run_id, command_owner=command_owner)["phase"] == "starting"
        for foreign in ({**command_owner, "sender": "other"},
                        {**command_owner, "authority_digest": "d" * 64}):
            with pytest.raises(ValueError, match="command authority"):
                supervisor.start(value, tmp_path / "model", lambda: None,
                                 assessment=True, command_owner=foreign)
            with pytest.raises(ValueError, match="command authority"):
                supervisor.status(value.run_id, command_owner=foreign)
            with pytest.raises(ValueError, match="command authority"):
                supervisor.renew(value.run_id, lambda: None, command_owner=foreign)
            with pytest.raises(ValueError, match="command authority"):
                supervisor.stop(value.run_id, command_owner=foreign)
        persisted = json.loads(supervisor.record_path.read_text())
        assert persisted["command_owner"] == command_owner
        assert len(children) == 1 and alive(result["pid"])
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
                "layers": 8,
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


def test_rank_stop_retains_owner_when_journal_forgetting_fails(tmp_path, monkeypatch):
    value = manifest()
    supervisor = RankSupervisor(tmp_path, "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"}, stop=lambda _: True,
        alive=lambda _: False, identity=lambda _: "owned")
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
    original_save = supervisor._save

    def fail_forgetting():
        if supervisor.owner is None:
            raise OSError("controlled rank journal failure")
        original_save()

    monkeypatch.setattr(supervisor, "_save", fail_forgetting)
    with pytest.raises(OSError, match="rank journal failure"):
        supervisor.stop(value.run_id)
    assert supervisor.owner["phase"] == "cleanup_pending"
    assert json.loads(supervisor.record_path.read_text())["run_id"] == value.run_id
    monkeypatch.setattr(supervisor, "_save", original_save)
    assert supervisor.stop(value.run_id) == {"phase": "idle"}
    assert supervisor.owner is None


def test_rank_start_drains_spawned_child_when_first_journal_write_fails(tmp_path, monkeypatch):
    value = manifest()
    live = [False]
    stopped = []
    resources = NodeResources()

    def spawn(*_):
        live[0] = True
        return {"pid": 123, "start_token": "owned"}

    def stop(pid):
        stopped.append(pid)
        live[0] = False
        return True

    supervisor = RankSupervisor(tmp_path, "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=spawn, stop=stop, alive=lambda _: live[0], identity=lambda _: "owned",
        resources=resources)
    original_save = supervisor._save

    def fail_first_owner():
        if supervisor.owner is not None and supervisor.owner["phase"] == "starting":
            raise OSError("controlled first journal failure")
        original_save()

    monkeypatch.setattr(supervisor, "_save", fail_first_owner)
    with pytest.raises(OSError, match="first journal failure"):
        supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
    assert stopped == [123] and not live[0] and supervisor.owner is None
    assert not resources.busy
    assert json.loads(supervisor.record_path.read_text()) is None


def test_rank_resource_reservation_survives_restart_until_cleanup(tmp_path):
    value = manifest()
    owner = {"sender": "master", "authority_digest": "e" * 64}
    initial_resources = NodeResources()
    arguments = dict(capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"}, stop=lambda _: True,
        alive=lambda _: False, identity=lambda _: "owned")
    initial = RankSupervisor(tmp_path, "worker", resources=initial_resources, **arguments)
    initial.start(value, tmp_path / "model", lambda: None, assessment=True, command_owner=owner)
    assert initial_resources.busy

    restored_resources = NodeResources()
    restored = RankSupervisor(tmp_path, "worker", resources=restored_resources, **arguments)
    assert restored.owner["phase"] == "cleanup_pending" and restored_resources.busy
    with pytest.raises(ValueError, match="resources"):
        restored_resources.acquire(("llamacpp", "other-run"), "run")
    restored.stop(value.run_id, command_owner=owner)
    assert restored.owner is None and not restored_resources.busy


def test_rank_registration_failure_keeps_child_identity_for_owned_cleanup(tmp_path):
    value = manifest()
    resources = NodeResources()
    live = [True]
    stopped = []

    def stop(pid):
        stopped.append(pid)
        live[0] = False
        return True

    supervisor = RankSupervisor(tmp_path, "worker", resources=resources,
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned",
                          "registration_error": "OSError: registry write failed"},
        stop=stop, alive=lambda _: live[0], identity=lambda _: "owned")
    with pytest.raises(RuntimeError, match="child registration failed"):
        supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
    assert stopped == [123] and not live[0]
    assert supervisor.owner is None and not resources.busy
    assert json.loads(supervisor.record_path.read_text()) is None


def test_readiness_requires_complete_owned_post_init_acknowledgement(tmp_path):
    value = manifest()
    owner = {"sender": "master", "authority_digest": "a" * 64}
    identity = ["owned"]
    supervisor = RankSupervisor(tmp_path, "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"}, stop=lambda _: True,
        alive=lambda _: True, identity=lambda _: identity[0])
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True, command_owner=owner)
    log = tmp_path / value.run_id / "rank.log"
    report = {"schema_version": 1, "runtime_id": value.runtime_id, "rank": 1, "ngl": 0,
        "layers": 8, "manifest_mode": True, "manifest_digest": value.digest,
        "phase": "ready", "readiness_stage": "worker_decode_loop"}
    log.write_text("KLLM_PRIMA_RANK_CONFIGURED=" + json.dumps(report) + "\n")
    assert supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner) is None
    encoded = "KLLM_PRIMA_RANK_READY=" + json.dumps(report)
    log.write_text(encoded)
    assert supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner) is None
    log.write_text(encoded + "\n")
    assert supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner) == report
    for changed in ({"rank": True}, {"layers": 4}, {"ngl": 1}, {"manifest_mode": 1},
                    {"runtime_id": "d" * 64}, {"manifest_digest": "e" * 64},
                    {"readiness_stage": "server_request_loop"}, {"phase": "configured"}):
        log.write_text("KLLM_PRIMA_RANK_READY=" + json.dumps({**report, **changed}) + "\n")
        with pytest.raises(ValueError, match="disagrees"):
            supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner)
        assert supervisor.status()["phase"] == "starting"
    log.write_text(encoded + "\n" + encoded + "\n")
    with pytest.raises(ValueError, match="Duplicate"):
        supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner)
    log.write_text(encoded + "\n")
    with pytest.raises(ValueError, match="command authority"):
        supervisor.readiness_report(value.run_id, lambda: None,
            command_owner={**owner, "sender": "other"})
    identity[0] = "reused-pid"
    with pytest.raises(ValueError, match="identity changed"):
        supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner)
    assert supervisor.owner["phase"] == "cleanup_pending"


def test_readiness_refuses_malformed_or_oversized_startup_logs(tmp_path):
    value = manifest()
    supervisor = RankSupervisor(tmp_path, "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"}, stop=lambda _: True,
        alive=lambda _: True, identity=lambda _: "owned")
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
    log = tmp_path / value.run_id / "rank.log"
    log.write_bytes(b"KLLM_PRIMA_RANK_READY=invalid\n")
    with pytest.raises(ValueError, match="Malformed"):
        supervisor.readiness_report(value.run_id, lambda: None)
    log.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="readiness limit"):
        supervisor.readiness_report(value.run_id, lambda: None)


def test_rank_status_does_not_report_a_dead_process_ready(tmp_path):
    value = manifest()
    alive = [True]
    supervisor = RankSupervisor(tmp_path, "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"}, stop=lambda _: True,
        alive=lambda _: alive[0], identity=lambda _: "owned")
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
    supervisor.owner["phase"] = "ready"
    alive[0] = False
    assert supervisor.status(value.run_id)["phase"] == "failed"
    assert supervisor.owner is not None
    assert supervisor.stop(value.run_id)["phase"] == "idle"


def test_rank_settles_an_unreaped_exited_child_instead_of_failing_cleanup(tmp_path):
    value = manifest()
    stopped = []

    def identity(pid):
        # Windows reports a terminated child as alive while its native identity
        # read already reports the exit.
        raise RuntimeError("process_exited")

    supervisor = RankSupervisor(tmp_path, "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"},
        stop=lambda pid: stopped.append(pid) or True, alive=lambda _: True, identity=identity)
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)
    supervisor.owner["phase"] = "ready"
    assert supervisor.status(value.run_id)["phase"] == "failed"
    with pytest.raises(ValueError, match="no longer alive"):
        supervisor.renew(value.run_id, lambda: None)
    assert supervisor.stop(value.run_id) == {"phase": "idle"}
    assert stopped == []  # An unreaped exited child is never signalled.
    assert supervisor.owner is None
    assert json.loads(supervisor.record_path.read_text()) is None


def test_rank_retains_cleanup_when_identity_cannot_be_read(tmp_path):
    value = manifest()
    supervisor = RankSupervisor(tmp_path, "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"}, stop=lambda _: True,
        alive=lambda _: True, identity=lambda _: "owned")
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True)

    def unreadable(pid):
        raise RuntimeError("permission_denied")

    supervisor.identity = unreadable
    with pytest.raises(ValueError, match="identity unavailable"):
        supervisor.stop(value.run_id)
    assert supervisor.status()["phase"] == "cleanup_pending"
    with pytest.raises(ValueError, match="identity unavailable"):
        supervisor.renew(value.run_id, lambda: None)


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
    assert assessment["observation_status"](data) == "failed"
    data["prima"]["cases"][0]["semantic_errors"] = []
    data["baseline"]["cases"][0]["semantic_errors"] = []
    assert assessment["observation_status"](data) == "failed"
    for engine in ("prima", "baseline"):
        data[engine]["cases"] = [{"case": case, "status": 200, "semantic_errors": []}
                                 for case in ("chat", "structured", "tools", "stream")]
    assert assessment["observation_status"](data) == "observed"
    data["prima"]["cases"][0]["semantic_errors"] = ["Empty stream"]
    assert assessment["observation_status"](data) == "failed"


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
        if action == "prima_start":
            rank = next(r for r in value.ranks if r.node_id == node)
            return {"phase": "starting", "run_id": value.run_id, "runtime_id": value.runtime_id,
                "manifest_digest": value.digest, "rank": rank.rank, "ngl": rank.ngl,
                "layers": sum(w.end - w.start for w in rank.layers), "backend": value.backends[node]}
        return {"phase": "idle"}

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


def test_coordinator_retries_cancelled_cleanup_before_releasing_admission(tmp_path):
    value = manifest()
    calls, released = [], []
    attempts = [0]
    def command(node, action, _):
        calls.append((node, action))
        if action == "prima_stop":
            if node == "worker":
                attempts[0] += 1
                if attempts[0] == 1:
                    raise OSError("Worker temporarily unavailable")
            return {"phase": "idle"}
        rank = next(r for r in value.ranks if r.node_id == node)
        return {"phase": "ready", "run_id": value.run_id, "runtime_id": value.runtime_id,
            "manifest_digest": value.digest, "rank": rank.rank, "ngl": rank.ngl,
            "layers": sum(w.end - w.start for w in rank.layers), "backend": value.backends[node]}
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None,
        release=lambda: released.append(True))
    coordinator.start(value)
    with pytest.raises(ValueError, match="cleanup pending"):
        coordinator.stop()
    assert coordinator.active is value and coordinator.started == ["worker"] and not released
    assert coordinator.monitor_once()["phase"] == "idle"
    assert coordinator.active is None and released == [True]
    assert calls.count(("worker", "prima_stop")) == 2
    assert calls.count(("master", "prima_start")) == 1


def test_coordinator_retains_cleanup_state_until_admission_release_succeeds(tmp_path):
    value = manifest()
    attempts = [0]
    def release():
        attempts[0] += 1
        if attempts[0] == 1:
            raise OSError("Admission release temporarily unavailable")
    def command(node, action, _):
        rank = next(r for r in value.ranks if r.node_id == node)
        return {"phase": "idle" if action == "prima_stop" else "ready", "run_id": value.run_id,
            "runtime_id": value.runtime_id, "manifest_digest": value.digest, "rank": rank.rank,
            "ngl": rank.ngl, "layers": sum(w.end - w.start for w in rank.layers), "backend": value.backends[node]}
    coordinator = PrimaCoordinator(tmp_path, command=command,
        admit=lambda _: None, release=release)
    coordinator.start(value)
    with pytest.raises(OSError, match="Admission release"):
        coordinator.stop()
    assert json.loads((tmp_path / "run.json").read_text())["phase"] == "cleanup_pending"
    assert coordinator.monitor_once()["phase"] == "idle"
    assert coordinator.active is None and attempts[0] == 2


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
    from kllm.modules.managed_network.entities import MasterAuthority
    authority = MasterAuthority(cluster_binding_id=uuid4(), master_node_id="master", master_key_id="1" * 64,
        master_term=1, master_session_id=uuid4())
    service = SimpleNamespace(state=lambda: state, channels=SimpleNamespace(sessions={}, master_authority=lambda: authority))
    runtime = {
        "engine": "primacpp",
        "runtime_id": "a" * 64,
        "backend": "cpu",
        "capabilities": {"features": list(BASELINE_FEATURES), "backends": []},
    }
    registry = SimpleNamespace(
        root=tmp_path / "runtimes", capture=lambda _: runtime, _kill=lambda _: None, lock=threading.RLock()
    )

    def plan(*_):
        return {
            "estimated_layers": 1,
            "device_effective": "cpu",
            "cpu_layers": 1,
            "device_allocations": [],
            "fit_status": "fit",
            # The planner resolves the head KV sequence reservation before launch.
            "resolved_launch_settings": {"parallel_slots": 1, "swa_full": False,
                                         "prima_kv_sequences": 2, "checkpoints": 0,
                                         "cache_ram": 0},
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
async def test_real_signed_channel_pushes_only_operation_bound_assets(tmp_path, monkeypatch):
    import asyncio
    import time
    import platform
    from types import SimpleNamespace
    import websockets
    from kllm.modules.managed_network.gateways.store import NetworkStore
    from kllm.modules.managed_network.interactors.identity import ChannelFence, LocalIdentity
    from kllm.modules.managed_network.interactors.network_channel import ChannelSession, SocketWire
    from kllm.modules.managed_network.entities import PeerIdentity
    from kllm.modules.primacpp.interactors.control import PrimaControl
    from kllm.modules.primacpp.interactors.participants import observe_participant
    from kllm.modules.managed_network.entities import NodeAssignment
    from kllm.modules.managed_network.interactors.prima_ports import port_contract
    from kllm.modules.primacpp.entities import PrimaTopology
    from kllm.modules.primacpp.interactors.placement import network_edges
    from prima_port_fixture import FixturePorts

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
    captured = {"engine": "primacpp", "runtime_id": "a" * 64, "platform": platform.system(), "backend": "cpu",
        "commit": "1" * 40, "recipe_digest": "2" * 64, "build_id": "fixture",
        "artifacts": {"fixture.exe": "3" * 64}, "capabilities": {"features": list(BASELINE_FEATURES)}}
    registry = SimpleNamespace(
        root=tmp_path / "worker-runtime",
        lock=threading.RLock(),
        capture=lambda _: captured,
        _kill=lambda _: None,
        running=lambda _: None,
        claim_running=lambda _: None,
        clear_running=lambda **_: None,
    )
    port_events = []
    platform_gateway = SimpleNamespace()
    FixturePorts(ids[0].public_key, "worker", port_events).install(platform_gateway, monkeypatch)
    control = PrimaControl(
        SimpleNamespace(state=lambda: state,
            prima_admission=SimpleNamespace(validate_worker=lambda _: None),
            executor=SimpleNamespace(platform=platform_gateway, preparation={})),
        registry, resolve_model=None, execute_plan=None
    )
    children = []
    ready_trigger = tmp_path / "worker-ready"
    def spawn(_runtime, rank, _model, _manifest_path, log_path, _settings):
        report = {"schema_version": 1, "runtime_id": execution.runtime_id,
            "manifest_digest": execution.digest, "rank": rank.rank, "ngl": rank.ngl,
            "layers": sum(w.end - w.start for w in rank.layers), "manifest_mode": True,
            "phase": "ready", "readiness_stage": "worker_decode_loop"}
        script = "\n".join([
            "import json, sys, time", "from pathlib import Path", "report = json.loads(sys.argv[1])",
            "print('KLLM_PRIMA_RANK_CONFIGURED=' + json.dumps(report), flush=True)",
            "while not Path(sys.argv[2]).exists(): time.sleep(0.01)",
            "print('KLLM_PRIMA_RANK_READY=' + json.dumps(report), flush=True)", "time.sleep(30)"])
        with log_path.open("w", encoding="utf-8") as output:
            child = subprocess.Popen([sys.executable, "-c", script, json.dumps(report), str(ready_trigger)],
                stdout=output, stderr=subprocess.STDOUT)
        children.append(child)
        return {"pid": child.pid, "start_token": str(psutil.Process(child.pid).create_time())}
    def stop(pid):
        child = next(child for child in children if child.pid == pid)
        child.terminate()
        child.wait(timeout=5)
        return True
    control.rank = RankSupervisor(control.root / "rank", "worker", capture=registry.capture,
        spawn=spawn, stop=stop,
        alive=lambda pid: any(child.pid == pid and child.poll() is None for child in children),
        identity=lambda pid: str(psutil.Process(pid).create_time()))
    monkeypatch.setattr("kllm.modules.primacpp.interactors.recipe.require_qualified", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.devices.discover_vulkan_devices", lambda _: [])
    monkeypatch.setattr("kllm.modules.primacpp.interactors.devices.select_vulkan_devices", lambda *_: [])
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

    task = None
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
                # Preparation now carries the signed execution manifest asset; this
                # first job prepares a different runtime identity so it can never
                # authorize the distributed manifest below.
                first_manifest = manifest().model_copy(
                    update={"runtime_id": "e" * 64, "run_id": str(run), "assets": [asset]})
                first_encoded = json.dumps(
                    first_manifest.model_dump(), sort_keys=True, separators=(",", ":")).encode()
                first_manifest_asset = Asset(name="execution.json", kind="manifest",
                    sha256=first_manifest.digest, size=len(first_encoded))
                prepared = await master.request_runtime(
                    run,
                    "prima_prepare",
                    body={
                        "runtime_id": "e" * 64,
                        "operation_id": str(run),
                        "assets": [asset.model_dump(), first_manifest_asset.model_dump()],
                    },
                )
                assert "diagnostics" not in prepared  # Legacy signed replies stay bounded.
                manifest_payload = {
                    "job_id": prepared["job_id"],
                    "chunk": {
                        "sha256": first_manifest_asset.sha256,
                        "offset": 0,
                        "size": first_manifest_asset.size,
                        "data": base64.b64encode(first_encoded).decode(),
                    },
                }
                copied = await master.request_runtime(run, "prima_asset_chunk", body=manifest_payload)
                assert copied["phase"] == "preparing"
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
                from kllm.modules.primacpp.interactors.preparation_metrics import diagnostics_scope, channel_timing
                observed = {}
                with diagnostics_scope(observed, threading.RLock(), lambda: None, "master"):
                    copied = await master.request_runtime(run, "prima_asset_chunk", body=payload,
                        timing=channel_timing("worker", 1))
                assert copied["phase"] == "prepared"
                stages = {r["stage"]: r for r in observed["diagnostics"]["stages"]}
                assert {"channel_sign", "channel_send", "channel_send_with_queue", "channel_ack_wait"} == set(stages)
                assert all(r["timed_calls"] == 1 and r["elapsed_seconds"] >= 0 for r in stages.values())
                receiver_stages = control.job(prepared["job_id"])["diagnostics"]["stages"]
                hashed = sum(r["counters"].get("hashed_bytes", 0) for r in receiver_stages
                             if r["stage"] == "receiver_final_verify")
                assert hashed == len(data) + len(first_encoded)
                assert control.cached_model(asset.sha256).read_bytes() == data
                assert state.process_pid is None
                for action in ("prima_status", "prima_cancel"):
                    refused = await master.request_runtime(uuid4(), action, body={"job_id": prepared["job_id"]})
                    assert refused.get("status") == "failed" and "preparation" in refused["message"]
                    assert control.jobs[prepared["job_id"]]["phase"] == "prepared"
                    for unknown in (str(uuid4()), []):
                        refused = await master.request_runtime(run, action, body={"job_id": unknown})
                        assert refused.get("status") == "failed" and "Unknown" in refused["message"]
                refused = await master.request_runtime(run, "prima_start", body={"manifest_digest": []})
                assert refused.get("status") == "failed" and "Invalid Prima start" in refused["message"]

                execution = manifest().model_copy(update={"assets": [asset]})
                execution.network_id = str(uuid4())
                for index, allocation in enumerate(execution.ranks):
                    allocation.address = f"192.168.234.{index + 1}"
                execution.transfer_edges = network_edges(PrimaTopology(runtime_id=execution.runtime_id,
                    master_node_id="master", total_layers=execution.total_layers, ranks=execution.ranks,
                    data_port=execution.data_port, signal_port=execution.signal_port))
                assignments = [NodeAssignment(network_id=execution.network_id, node_id=r.node_id,
                    adapter_id="4", os_interface="fixture", identity_fingerprint=str(i + 1) * 64,
                    address=r.address, prefix_length=24) for i, r in enumerate(execution.ranks)]
                control.service.executor.preparation[execution.network_id] = SimpleNamespace(participants=assignments)
                execution = ExecutionManifest.model_validate(execution.model_copy(update={"participant_evidence": {
                    r.node_id: observe_participant(captured, r.node_id, r, execution.backends[r.node_id])
                    for r in execution.ranks}}).model_dump())
                encoded = json.dumps(execution.model_dump(), sort_keys=True, separators=(",", ":")).encode()
                manifest_asset = Asset(name="execution.json", kind="manifest",
                    sha256=execution.digest, size=len(encoded))
                # Cached bytes alone must not authorize a native launch.
                control.receiver.stager.prepare(execution.digest, len(encoded), lambda _: [encoded],
                                               check=lambda: None)
                start_body = {"manifest_digest": execution.digest}
                refused = await master.request_runtime(execution.run_id, "prima_start", body=start_body)
                assert refused.get("status") == "failed" and "approved preparation" in refused["message"]
                assert not children

                approved = await master.request_runtime(run, "prima_prepare", body={
                    "runtime_id": execution.runtime_id, "operation_id": str(run),
                    "assets": [asset.model_dump(), manifest_asset.model_dump()]})
                assert approved["phase"] == "prepared"
                renewed = await master.request_runtime(run, "prima_prepare_renew", body={
                    "job_id": approved["job_id"], "progress": 1})
                assert renewed["job_id"] == approved["job_id"] and renewed["lease_progress"] == 1
                unchanged = control.jobs[approved["job_id"]]["expires_at"]
                repeated = await master.request_runtime(run, "prima_prepare_renew", body={
                    "job_id": approved["job_id"], "progress": 1})
                assert repeated["status"] == "failed" and "no new progress" in repeated["message"]
                assert control.jobs[approved["job_id"]]["expires_at"] == unchanged
                original = control.jobs[approved["job_id"]].copy()
                for changed in ({"runtime_id": "e" * 64}, {"sender": "other"},
                                {"authority_digest": "f" * 64}, {"expires_at": 0},
                                {"phase": "preparing"}, {"assets": [manifest_asset.model_dump()]}):
                    control.jobs[approved["job_id"]] = {**original, **changed}
                    refused = await master.request_runtime(execution.run_id, "prima_start", body=start_body)
                    assert refused.get("status") == "failed" and "approved preparation" in refused["message"]
                    assert not children
                control.jobs[approved["job_id"]] = original
                missing_ports = await master.request_runtime(execution.run_id, "prima_start", body=start_body)
                assert missing_ports["status"] == "failed" and "admission is absent" in missing_ports["message"]
                admitted = await master.request_runtime(execution.run_id, "prima_admit_ports", body={
                    "contract": port_contract(execution, "worker", assignments).model_dump(mode="json")})
                assert admitted == {"phase": "admitted", "manifest_digest": execution.digest}
                started = await master.request_runtime(execution.run_id, "prima_start", body=start_body)
                assert started["phase"] == "starting" and len(children) == 1
                assert started["command_owner"]["sender"] == "master"
                status = await master.request_runtime(execution.run_id, "prima_status", body={})
                assert status["pid"] == children[0].pid and status["phase"] == "starting"
                ready_trigger.touch()
                until = time.monotonic() + 5
                while status["phase"] == "starting" and time.monotonic() < until:
                    await asyncio.sleep(0.01)
                    status = await master.request_runtime(execution.run_id, "prima_status", body={})
                assert status["phase"] == "ready" and status["manifest_digest"] == execution.digest
                wrong_run = await master.request_runtime(uuid4(), "prima_status", body={})
                assert wrong_run["status"] == "failed" and "different run" in wrong_run["message"]
                refused = await master.request_runtime(run, "prima_prepare", body={
                    "runtime_id": execution.runtime_id, "operation_id": str(run),
                    "assets": [asset.model_dump(), manifest_asset.model_dump()]})
                assert refused.get("status") == "failed" and "busy" in refused["message"]
                assert children[0].poll() is None
                stopped = await master.request_runtime(execution.run_id, "prima_stop", body={})
                assert stopped["phase"] == "idle" and children[0].poll() is not None
                released = await master.request_runtime(execution.run_id, "prima_release_ports",
                    body={"network_id": execution.network_id})
                assert released == {"phase": "released"} and control.ports.owner is None
                await worker.close()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        control.close()
        for store in stores:
            store.close()


def test_prima_launch_settings_resolve_f16_kv_and_flash_attention_off(monkeypatch):
    from fastapi import HTTPException
    from kllm.modules.inference.entities import RunBody
    from kllm.modules.inference.interactors import run_settings_resolution as rsr

    monkeypatch.setattr(rsr, "snapshot", lambda: {"engine": "primacpp", "runtime_id": "a" * 64})
    resolved = rsr.resolve_launch_settings(RunBody(model="fixture.gguf"))
    assert resolved.flash_attention == "off"
    assert resolved.cache_type_k == "f16"
    assert resolved.cache_type_v == "f16"

    explicit_quant = rsr.resolve_launch_settings(
        RunBody(model="fixture.gguf", cache_type_k="q8_0", cache_type_v="q8_0")
    )
    assert explicit_quant.cache_type_k == "q8_0"
    assert explicit_quant.cache_type_v == "q8_0"
    assert explicit_quant.flash_attention == "on"

    monkeypatch.setattr(
        rsr,
        "snapshot",
        lambda: {
            "engine": "primacpp",
            "runtime_id": "a" * 64,
            "capabilities": {"features": ["vulkan_flash_attn_v1"]},
        },
    )
    fa_resolved = rsr.resolve_launch_settings(RunBody(model="fixture.gguf"))
    assert fa_resolved.cache_type_k == "q8_0"
    assert fa_resolved.cache_type_v == "q8_0"
    assert fa_resolved.flash_attention == "on"

    with pytest.raises(HTTPException) as exc_info:
        rsr.resolve_launch_settings(
            RunBody(model="fixture.gguf", flash_attention="off", cache_type_v="q8_0")
        )
    assert exc_info.value.status_code == 400


def test_single_node_vulkan_generation_profile_populates_required_settings_and_reports_progress(
    tmp_path, monkeypatch
):
    from kllm.modules.primacpp.entities import ProfileRequest
    from kllm.modules.primacpp.interactors import generation_profile as gp

    model_path = tmp_path / "fixture.gguf"
    model_path.write_bytes(b"gguf")
    monkeypatch.setattr(
        gp,
        "inspect_model",
        lambda *_, **kw: {"stored_blocks": 5, "execution_layers": 4},
    )
    seen_manifests = []
    progress_events = []
    real_require = gp.require_execution_capabilities

    def spy_require(manifest_obj, capabilities):
        real_require(manifest_obj, capabilities)
        seen_manifests.append(dict(manifest_obj.settings))
        raise RuntimeError("stop-after-manifest-check")

    monkeypatch.setattr(gp, "require_execution_capabilities", spy_require)
    req = ProfileRequest(
        runtime_id="a" * 64,
        model="fixture.gguf",
        mode="generation",
        backend="vulkan",
        devices=["1" * 32],
        gpu_layers=2,
        context=512,
        threads=4,
        request_id=str(uuid4()),
    )
    runtime = {
        "runtime_id": "a" * 64,
        "engine": "primacpp",
        "backend": "vulkan",
        "llama_server": "llama-server",
        "capabilities": {
            "backends": ["cpu", "vulkan"],
            "features": list(BASELINE_FEATURES),
        },
    }
    selected = [
        {
            "backend": "vulkan",
            "name": "Vulkan0",
            "device_identity": "1" * 32,
            "driver_identity": "drv",
            "physical_index": 0,
            "memory_bytes": 4 * 1024**3,
        }
    ]
    with pytest.raises(RuntimeError, match="stop-after-manifest-check"):
        gp.profile_generation(
            runtime,
            model_path,
            req,
            selected,
            tmp_path,
            check=lambda: None,
            track=lambda *_: None,
            stop=lambda *_: None,
            progress=lambda label, frac, done, total: progress_events.append((label, frac, done, total)),
        )
    assert len(seen_manifests) == 1
    assert seen_manifests[0]["swa_full"] is False
    assert seen_manifests[0]["parallel_slots"] == 1
    assert seen_manifests[0]["prima_kv_sequences"] == 2
    assert len(progress_events) == 1
    assert "Pass 1/2 (CPU)" in progress_events[0][0]


def test_stager_and_load_progress_track_pre_spawn_prima_stages(tmp_path):
    from kllm.modules.inference.interactors import load_progress as lp
    from kllm.modules.primacpp.interactors.staging import ModelStager

    stager = ModelStager(tmp_path / "staging")
    payload = b"hello-prima-staging"
    digest = hashlib.sha256(payload).hexdigest()
    seen_progress = []
    staged = stager.prepare(
        digest,
        len(payload),
        lambda _: [payload[:5], payload[5:]],
        check=lambda: None,
        progress=lambda offset, total: seen_progress.append((offset, total)),
    )
    assert staged.read_bytes() == payload
    assert seen_progress[0] == (0, len(payload))
    assert (5, len(payload)) in seen_progress
    assert seen_progress[-1] == (len(payload), len(payload))

    lp.end()
    try:
        lp.begin("fixture.gguf", None, 1000, detail="Staging model files (1/1): fixture.gguf")
        sample1 = lp.sample(8080)
        assert sample1["phase"] == "starting"
        assert sample1["detail"] == "Staging model files (1/1): fixture.gguf"

        lp.update(bytes_loaded=500, fraction=0.25, phase="starting", detail="Starting Ring-TCP rank 1/1 (master)…")
        sample2 = lp.sample(8080)
        assert sample2["bytes_loaded"] == 500
        assert sample2["fraction"] == pytest.approx(0.25, abs=0.01)
        assert sample2["detail"] == "Starting Ring-TCP rank 1/1 (master)…"

        lp.fail("Rank 0 failed to start")
        terminal = lp.sample(8080)
        assert terminal["phase"] == "error"
        assert "Rank 0 failed to start" in terminal["error"]
    finally:
        lp.end()


