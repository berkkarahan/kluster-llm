"""Adversarial stress test suite for Milestones 1 & 2 Backend implementations.

Covers:
1. Node identification edge cases:
   - Case-insensitive IDs, hostnames, and prefixes.
   - Nodes with identical hostnames or IPs.
   - Pinned required nodes that are genuinely offline vs online nodes with various alias representations.
2. Stage retry state machine and SQLite concurrency:
   - Requesting retry on a completed run.
   - Requesting retry for an invalid stage name.
   - Concurrent retry requests for the same run.
   - Verifying that retrying stage 4 (link_profile) strictly preserves stage 2 (asset_sync)
     and stage 3 (node_profile) data without re-executing them.
   - Simulating daemon crash with active running stages and verifying reconcile_on_startup()
     marks them failed-resumable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import pytest
from starlette.testclient import TestClient

from kllm.modules.inference.entities import PlanBody
from kllm.modules.inference.interactors.core_placement import (
    plan_devices,
    resolve_canonical_node_id,
)
from kllm.modules.inference.interactors.placement_models import (
    PlacementDeviceInput,
    PlacementNodeInput,
    PlacementSettings,
)
from kllm.modules.managed_network.entities import MasterAuthority
from kllm.modules.primacpp.entities import (
    STAGE_ORDERS,
    STAGE_SEQUENCE,
    PrimaPlan,
    ProfilingRunStatus,
    ProfilingStageName,
    RankAllocation,
    StageStatus,
)
from kllm.modules.primacpp.gateways.store import ProfilingStore
from kllm.modules.primacpp.interactors import (
    stage_asset_sync,
    stage_link_profile,
    stage_node_profile,
)
from kllm.modules.primacpp.interactors.control import PrimaControl
from kllm.modules.primacpp.interactors.planner import candidates
from kllm.modules.runtimes.router_http import build_router
from kllm.shared.exceptions import ValidationError
from test_prima_lifecycle import BASELINE_FEATURES


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def mock_control_setup(tmp_path):
    """Sets up a fully configured PrimaControl and ProfilingCoordinator in isolation."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF_MOCK_DATA" * 4096)
    authority = MasterAuthority(
        cluster_binding_id=uuid4(),
        master_node_id="master_node_01",
        master_key_id="1" * 64,
        master_term=1,
        master_session_id=uuid4(),
    )
    state = SimpleNamespace(
        role="master",
        node_id="master_node_01",
        process_pid=None,
        members={
            "master_node_01": {
                "role": "master",
                "node_id": "master_node_01",
                "hostname": "master1",
                "host": "192.168.1.10",
                "lan_host": "10.0.0.1",
                "backend": "cpu",
            },
            "worker_node_a83ac5b6": {
                "role": "worker",
                "node_id": "worker_node_a83ac5b6",
                "hostname": "worker1",
                "host": "192.168.1.20",
                "lan_host": "10.0.0.2",
                "backend": "vulkan",
            },
        },
    )
    service = SimpleNamespace(
        state=lambda: state,
        channels=SimpleNamespace(sessions={}, master_authority=lambda: authority),
    )
    runtime = {
        "engine": "primacpp",
        "runtime_id": "a" * 64,
        "backend": "cpu",
        "capabilities": {"features": list(BASELINE_FEATURES), "backends": []},
    }
    registry = SimpleNamespace(
        root=tmp_path / "runtimes",
        capture=lambda _: runtime,
        _kill=lambda _: None,
        lock=threading.RLock(),
    )
    plan = {
        "estimated_layers": 2,
        "device_effective": "cpu",
        "cpu_layers": 2,
        "device_allocations": [],
        "fit_status": "fit",
        "resolved_launch_settings": {
            "parallel_slots": 1,
            "swa_full": False,
            "prima_kv_sequences": 2,
            "checkpoints": 0,
            "cache_ram": 0,
        },
    }
    control = PrimaControl(
        service,
        registry,
        resolve_model=lambda _: model,
        execute_plan=lambda *_: plan,
    )
    yield control, registry, service
    control.close()


def wait_for(predicate, timeout=8.0, step=0.02):
    until = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= until:
            raise TimeoutError("Condition not satisfied before timeout")
        time.sleep(step)


# -----------------------------------------------------------------------------
# Part 1: Node Identification Edge Cases
# -----------------------------------------------------------------------------

def test_case_insensitive_node_id_and_hostname_resolution():
    """Verify resolve_canonical_node_id handles case variations across IDs, hostnames, and prefixes."""
    master = PlacementNodeInput(
        node_id="049f6c77",
        is_master=True,
        backend="cpu",
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint=None,
        latency_ms=0.0,
        devices=(),
        hostname="MasterHostOne",
        host="192.168.1.10",
        lan_host="10.0.0.1",
        aliases=("primary_node", "MASTER"),
    )
    worker = PlacementNodeInput(
        node_id="a83ac5b6",
        is_master=False,
        backend="vulkan",
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint="192.168.1.20:50052",
        latency_ms=1.2,
        devices=(PlacementDeviceInput(device="GPU 0", backend="vulkan", vram_bytes=16 * 1024**3, free_bytes=14 * 1024**3, reclaimable_bytes=0),),
        hostname="GpuWorkerAlpha",
        host="192.168.1.20",
        lan_host="10.0.0.2",
        aliases=("worker_fast",),
    )
    nodes = [master, worker]

    # 1. Exact matches with opposite casing
    assert resolve_canonical_node_id("049F6C77", nodes) == master.node_id
    assert resolve_canonical_node_id("A83AC5B6", nodes) == worker.node_id
    assert resolve_canonical_node_id("a83ac5b6", nodes) == worker.node_id

    # 2. Hostname matching with case insensitivity
    assert resolve_canonical_node_id("masterhostone", nodes) == master.node_id
    assert resolve_canonical_node_id("MASTERHOSTONE", nodes) == master.node_id
    assert resolve_canonical_node_id("gpuworkeralpha", nodes) == worker.node_id
    assert resolve_canonical_node_id("GPUWORKERALPHA", nodes) == worker.node_id

    # 3. Role alias matching with case insensitivity
    assert resolve_canonical_node_id("master", nodes) == master.node_id
    assert resolve_canonical_node_id("MASTER", nodes) == master.node_id
    assert resolve_canonical_node_id("MaStEr", nodes) == master.node_id

    # 4. Aliases matching with case insensitivity
    assert resolve_canonical_node_id("PRIMARY_NODE", nodes) == master.node_id
    assert resolve_canonical_node_id("WORKER_FAST", nodes) == worker.node_id

    # 5. Prefix matching (>= 6 characters) with case insensitivity
    assert resolve_canonical_node_id("049f6c", nodes) == master.node_id
    assert resolve_canonical_node_id("049F6C", nodes) == master.node_id
    assert resolve_canonical_node_id("a83ac5", nodes) == worker.node_id
    assert resolve_canonical_node_id("A83AC5", nodes) == worker.node_id
    assert resolve_canonical_node_id("a83ac5b6_extended", nodes) == worker.node_id


def test_identical_hostnames_or_ips():
    """Verify system behavior when multiple cluster nodes share identical hostnames or IP addresses."""
    # Scenario: Two distinct worker machines accidentally share hostname 'worker-box' or run on the same NAT IP
    node1 = PlacementNodeInput(
        node_id="worker_node_alpha_1111",
        is_master=False,
        backend="vulkan",
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint="192.168.1.50:50052",
        latency_ms=1.0,
        devices=(PlacementDeviceInput(device="GPU 0", backend="vulkan", vram_bytes=16 * 1024**3, free_bytes=14 * 1024**3, reclaimable_bytes=0),),
        hostname="worker-box",
        host="192.168.1.50",
        lan_host="192.168.1.50",
    )
    node2 = PlacementNodeInput(
        node_id="worker_node_beta_2222",
        is_master=False,
        backend="vulkan",
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint="192.168.1.50:50053",
        latency_ms=1.1,
        devices=(PlacementDeviceInput(device="GPU 1", backend="vulkan", vram_bytes=16 * 1024**3, free_bytes=14 * 1024**3, reclaimable_bytes=0),),
        hostname="worker-box",
        host="192.168.1.50",
        lan_host="192.168.1.50",
    )
    master = PlacementNodeInput(
        node_id="master_node_0000",
        is_master=True,
        backend="cpu",
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint=None,
        latency_ms=0.0,
        devices=(),
        hostname="master",
        host="192.168.1.10",
        lan_host="192.168.1.10",
    )
    nodes = [master, node1, node2]

    # 1. Disambiguation by unique node_id
    assert resolve_canonical_node_id("worker_node_alpha_1111", nodes) == node1.node_id
    assert resolve_canonical_node_id("worker_node_beta_2222", nodes) == node2.node_id

    # 2. Shared hostname resolution picks the first matching candidate without crashing
    resolved = resolve_canonical_node_id("worker-box", nodes)
    assert resolved in (node1.node_id, node2.node_id)

    # 3. Both nodes can be required simultaneously using their explicit unique IDs
    settings = PlacementSettings(split_mode="layer")
    plan = plan_devices(
        nodes=nodes,
        size=1024 * 1024 * 100,
        layout=None,
        settings=settings,
        context=2048,
        headroom=0.1,
        device="auto",
        vram_weight=1.0,
        latency_weight=1.0,
        required_nodes=["worker_node_alpha_1111", "worker_node_beta_2222"],
    )
    assert set(plan.node_ids) >= {node1.node_id, node2.node_id}

    # 4. Invariant check: PrimaTopology enforces distinct private node addresses for multi-node rings
    with pytest.raises(ValueError, match="Distributed ranks require distinct private node addresses"):
        PrimaPlan(
            runtime_id="b" * 64,
            master_node_id=master.node_id,
            total_layers=2,
            ranks=[
                RankAllocation(
                    node_id=master.node_id,
                    role="master",
                    rank=0,
                    address="192.168.1.10",
                    next_node_id=node1.node_id,
                    layers=[{"start": 0, "end": 1}],
                    ngl=0,
                ),
                RankAllocation(
                    node_id=node1.node_id,
                    role="worker",
                    rank=1,
                    address="192.168.1.50",
                    next_node_id=node2.node_id,
                    layers=[],
                    ngl=0,
                ),
                RankAllocation(
                    node_id=node2.node_id,
                    role="worker",
                    rank=2,
                    address="192.168.1.50",  # Duplicate address in multi-rank ring!
                    next_node_id=master.node_id,
                    layers=[{"start": 1, "end": 2}],
                    ngl=0,
                ),
            ],
        )


def test_pinned_offline_nodes_vs_online_alias_representations():
    """Verify that genuinely offline nodes raise clear ValidationError, while online nodes

    with various aliases are accepted without false 409 errors.
    """
    master = PlacementNodeInput(
        node_id="master1_id_001",
        is_master=True,
        backend="cpu",
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint=None,
        latency_ms=0.0,
        devices=(),
        hostname="master1",
        host="192.168.1.10",
        lan_host="10.0.0.1",
        aliases=("master", "main_box"),
    )
    worker = PlacementNodeInput(
        node_id="worker_a83ac5b6_gpu",
        is_master=False,
        backend="vulkan",
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint="192.168.1.20:50052",
        latency_ms=1.0,
        devices=(PlacementDeviceInput(device="GPU 0", backend="vulkan", vram_bytes=16 * 1024**3, free_bytes=14 * 1024**3, reclaimable_bytes=0),),
        hostname="worker_gpu_host",
        host="192.168.1.20",
        lan_host="10.0.0.2",
        aliases=("a83ac5b6", "vulkan_accelerator"),
    )
    nodes = [master, worker]
    settings = PlacementSettings(split_mode="layer")

    # 1. Genuinely offline node alone: must fail citing exact missing node
    with pytest.raises(ValidationError, match="required nodes are unavailable: offline_node_999"):
        plan_devices(
            nodes=nodes,
            size=1024 * 1024,
            layout=None,
            settings=settings,
            context=2048,
            headroom=0.1,
            device="auto",
            vram_weight=1.0,
            latency_weight=1.0,
            required_nodes=["offline_node_999"],
        )

    # 2. Mixed online and offline nodes: only the offline node is flagged
    with pytest.raises(ValidationError, match="required nodes are unavailable: dead_node_xxx"):
        plan_devices(
            nodes=nodes,
            size=1024 * 1024,
            layout=None,
            settings=settings,
            context=2048,
            headroom=0.1,
            device="auto",
            vram_weight=1.0,
            latency_weight=1.0,
            required_nodes=["master1", "a83ac5b6", "dead_node_xxx"],
        )

    # 3. All valid online alias forms: must all succeed without throwing ValidationError
    valid_alias_combinations = [
        ["master1", "worker_gpu_host"],
        ["master", "a83ac5b6"],
        ["main_box", "vulkan_accelerator"],
        ["192.168.1.10", "192.168.1.20"],
        ["10.0.0.1", "10.0.0.2"],
        ["master1_id", "worker_a83ac5"],  # prefix matching
        ["MASTER1", "A83AC5B6"],          # uppercase
    ]
    for req_nodes in valid_alias_combinations:
        plan = plan_devices(
            nodes=nodes,
            size=1024 * 1024,
            layout=None,
            settings=settings,
            context=2048,
            headroom=0.1,
            device="auto",
            vram_weight=1.0,
            latency_weight=1.0,
            required_nodes=req_nodes,
        )
        assert set(plan.node_ids) == {master.node_id, worker.node_id}


# -----------------------------------------------------------------------------
# Part 2: Stage Retry State Machine and SQLite Concurrency
# -----------------------------------------------------------------------------

def test_retry_for_invalid_stage_name(mock_control_setup):
    """Verify requesting retry for an invalid stage name raises ValueError and leaves SQLite unchanged."""
    control, registry, service = mock_control_setup
    store: ProfilingStore = control.coordinator.store

    # Create a dummy failed run in SQLite
    run_id = str(uuid4())
    req_id = str(uuid4())
    run_data = {
        "run_id": run_id,
        "request_id": req_id,
        "request_digest": "digest_abc",
        "runtime_id": "a" * 64,
        "status": "failed",
        "current_stage": "link_profile",
        "plan_digest": "plan_digest_xyz",
        "settings": {"model": "model"},
    }
    stages_data = [
        {"stage_name": s.value, "stage_order": STAGE_ORDERS[s], "status": "completed" if STAGE_ORDERS[s] < 4 else "failed"}
        for s in STAGE_SEQUENCE
    ]
    nodes_data = [{"node_id": "master_node_01", "role": "master"}]
    store.create_run(run_data, stages_data, nodes_data)

    initial_stages = store.get_stages(run_id)
    initial_run = store.get_run(run_id)

    # 1. Request retry with completely bogus stage name
    with pytest.raises(ValueError, match="Stage 'completely_invalid_stage' is invalid for run"):
        control.coordinator.retry_run(run_id, stage_name="completely_invalid_stage")

    # 2. Verify SQLite state was not corrupted or modified
    assert store.get_stages(run_id) == initial_stages
    assert store.get_run(run_id)["status"] == initial_run["status"]
    assert store.get_run(run_id)["current_stage"] == initial_run["current_stage"]

    # 3. Test through FastAPI HTTP router: verify it returns HTTP 409
    app = FastAPI()
    app.include_router(build_router(lambda: registry, lambda: control, service.state, token_verifier=lambda _: True))
    with TestClient(app, client=("127.0.0.1", 12)) as client:
        response = client.post(
            f"/v2/cluster/prima-preparations/{run_id}/retry",
            headers={"origin": "http://testserver"},
            json={"stage_name": "unknown_bogus_stage"},
        )
        assert response.status_code == 409
        assert "is invalid for run" in response.json()["detail"]["message"]


def test_retry_on_completed_run(mock_control_setup, monkeypatch):
    """Verify requesting retry on a completed run works cleanly when re-executing a specific stage."""
    control, registry, service = mock_control_setup
    store: ProfilingStore = control.coordinator.store

    # Mock profile jobs
    profile_result = {
        "job_id": "prof-1",
        "phase": "prepared",
        "profile": {"cpu_layer_ms": 1.2, "output_token_ms": 0.5, "prompt_ms": 0.1, "devices": []},
        "diagnostics": {"stages": []},
    }
    monkeypatch.setattr(control.profiles, "start", lambda *args, **kwargs: profile_result)
    monkeypatch.setattr(control.profiles, "job", lambda *args, **kwargs: profile_result)

    body = PlanBody(model="model")
    plan = control.plan("a" * 64, body)
    req_id = str(uuid4())

    job = control.start_preparation("a" * 64, body, plan["plan_digest"], req_id, include_profile=False)
    run_id = job["job_id"]

    # Wait for run to complete
    wait_for(lambda: control.job(run_id)["phase"] == "prepared")
    assert control.job(run_id)["phase"] == "prepared"

    # Verify run is marked completed in SQLite
    run_record = store.get_run(run_id)
    assert run_record["status"] in ("completed", "prepared")

    # 1. Requesting retry while a run is RUNNING raises ValueError
    # Temporarily set status to running
    store.update_run(run_id, status=ProfilingRunStatus.RUNNING.value)
    with pytest.raises(ValueError, match="is already actively running"):
        control.coordinator.retry_run(run_id, stage_name="link_profile")

    # Restore status to completed
    store.update_run(run_id, status=ProfilingRunStatus.COMPLETED.value)

    # 2. Retrying a specific stage (link_profile) on a completed run
    retried = control.retry_preparation(run_id, stage_name="link_profile")
    assert retried["status"] == ProfilingRunStatus.RUNNING.value
    assert retried["retrying_stage"] == "link_profile"
    assert "asset_sync" in retried["preserved_stages"]
    assert "materialize" in retried["preserved_stages"]

    # Wait for retry to settle
    wait_for(lambda: control.job(run_id)["phase"] == "prepared")
    assert control.job(run_id)["phase"] == "prepared"


def test_concurrent_retry_requests_and_sqlite_concurrency(mock_control_setup):
    """Stress-test concurrent retry requests for the same run across multiple threads.

    Verifies SQLite transaction serialization and ensures no deadlocks, corruption, or phantom executions.
    """
    control, registry, service = mock_control_setup
    store: ProfilingStore = control.coordinator.store

    # Create a failed run
    run_id = str(uuid4())
    req_id = str(uuid4())
    run_data = {
        "run_id": run_id,
        "request_id": req_id,
        "request_digest": "digest_conc",
        "runtime_id": "a" * 64,
        "status": ProfilingRunStatus.FAILED.value,
        "current_stage": ProfilingStageName.LINK_PROFILE.value,
        "plan_digest": "plan_digest_conc",
        "settings": {"model": "model"},
    }
    stages_data = [
        {"stage_name": s.value, "stage_order": STAGE_ORDERS[s], "status": StageStatus.COMPLETED.value if STAGE_ORDERS[s] < 4 else StageStatus.FAILED.value}
        for s in STAGE_SEQUENCE
    ]
    nodes_data = [{"node_id": "master_node_01", "role": "master"}]
    store.create_run(run_data, stages_data, nodes_data)

    # Prevent _prepare from executing heavy work during concurrency test
    control._prepare = lambda *args, **kwargs: None

    results = []
    errors = []

    def perform_retry(thread_idx: int):
        try:
            res = control.coordinator.retry_run(run_id, stage_name="link_profile")
            return ("SUCCESS", res)
        except Exception as exc:
            return ("ERROR", exc)

    # Dispatch 8 concurrent retries
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(perform_retry, i) for i in range(8)]
        for f in as_completed(futures):
            status, payload = f.result()
            if status == "SUCCESS":
                results.append(payload)
            else:
                errors.append(payload)

    # At least one request must succeed
    assert len(results) >= 1
    # Any rejected concurrent callers must fail with clean 'already actively running' rather than database corruption
    for err in errors:
        assert "is already actively running" in str(err) or isinstance(err, ValueError)

    # Verify SQLite store integrity: stage attempt should have cleanly incremented
    stage = store.get_stage(run_id, "link_profile")
    assert stage is not None
    assert stage["attempt"] >= 2


def test_retry_stage4_strictly_preserves_stage2_and_stage3_data(mock_control_setup, monkeypatch):
    """Empirically verify that retrying Stage 4 (link_profile) strictly preserves

    Stage 2 (asset_sync) and Stage 3 (node_profile) data without calling them again.
    """
    control, registry, service = mock_control_setup
    store: ProfilingStore = control.coordinator.store

    call_counts = {
        "materialize": 0,
        "asset_sync": 0,
        "node_profile": 0,
        "link_profile": 0,
    }

    # Intercept stage executions
    orig_sync = stage_asset_sync.execute_asset_sync
    orig_node = stage_node_profile.execute_node_profile
    orig_link = stage_link_profile.execute_link_profile

    def spy_asset_sync(*args, **kwargs):
        call_counts["asset_sync"] += 1
        return orig_sync(*args, **kwargs)

    def spy_node_profile(*args, **kwargs):
        call_counts["node_profile"] += 1
        # Record mock node profile
        store.record_node_profile(
            args[1],  # run_id
            "master_node_01",
            {"prompt_ms": 10.5, "cpu_layer_ms": 1.5, "devices": []},
        )
        return orig_node(*args, **kwargs)

    first_link_attempt = True
    def spy_link_profile(*args, **kwargs):
        nonlocal first_link_attempt
        call_counts["link_profile"] += 1
        if first_link_attempt:
            first_link_attempt = False
            raise RuntimeError("Injected adversarial failure in link profiling")
        return orig_link(*args, **kwargs)

    monkeypatch.setattr(stage_asset_sync, "execute_asset_sync", spy_asset_sync)
    monkeypatch.setattr(stage_node_profile, "execute_node_profile", spy_node_profile)
    monkeypatch.setattr(stage_link_profile, "execute_link_profile", spy_link_profile)

    profile_result = {
        "job_id": "prof-1",
        "phase": "prepared",
        "profile": {"cpu_layer_ms": 1.2, "output_token_ms": 0.5, "prompt_ms": 0.1, "devices": []},
        "diagnostics": {"stages": []},
    }
    monkeypatch.setattr(control.profiles, "start", lambda *args, **kwargs: profile_result)
    monkeypatch.setattr(control.profiles, "job", lambda *args, **kwargs: profile_result)

    body = PlanBody(model="model")
    plan = control.plan("a" * 64, body)
    req_id = str(uuid4())

    job = control.start_preparation("a" * 64, body, plan["plan_digest"], req_id, include_profile=True)
    run_id = job["job_id"]

    # Wait for first attempt to fail at link_profile
    wait_for(lambda: control.job(run_id)["phase"] == "failed")

    # Verify call counts after initial run
    assert call_counts["asset_sync"] == 1
    assert call_counts["node_profile"] == 1
    assert call_counts["link_profile"] == 1

    # Verify stage 2 and 3 state before retry
    stage_map = {s["stage_name"]: s for s in store.get_stages(run_id)}
    assert stage_map["asset_sync"]["status"] == StageStatus.COMPLETED.value
    assert stage_map["node_profile"]["status"] in (StageStatus.COMPLETED.value, StageStatus.SKIPPED.value)
    assert stage_map["link_profile"]["status"] == StageStatus.FAILED.value

    node_profiles_before = store.get_node_profiles(run_id)

    # Perform retry of stage 4 (link_profile)
    retried_job = control.retry_preparation(run_id, stage_name="link_profile")
    assert retried_job["job_id"] == run_id

    # Wait for retry to finish successfully
    wait_for(lambda: control.job(run_id)["phase"] == "prepared")

    # CRITICAL INVARIANT: Asset Sync and Node Profile were NOT re-executed!
    assert call_counts["asset_sync"] == 1, "execute_asset_sync was re-executed during stage 4 retry!"
    assert call_counts["node_profile"] == 1, "execute_node_profile was re-executed during stage 4 retry!"
    assert call_counts["link_profile"] == 2, "execute_link_profile was not re-executed!"

    # Verify node profiles in SQLite are identical and preserved
    node_profiles_after = store.get_node_profiles(run_id)
    assert node_profiles_after == node_profiles_before

    # Verify logs indicate preservation of completed stages
    logs = store.get_logs(run_id)
    messages = [log["message"] for log in logs]
    assert any("Preserving completed Stage 2: Asset Sync" in m for m in messages)
    assert any("Preserving completed Stage 3: Node Profile" in m for m in messages)


def test_simulate_daemon_crash_with_active_stages_reconcile_on_startup(mock_control_setup):
    """Simulate a daemon crash with active running stages and verify reconcile_on_startup()

    marks interrupted stages and runs as failed-resumable while keeping completed stages intact.
    """
    control, registry, service = mock_control_setup
    store: ProfilingStore = control.coordinator.store

    # 1. Run A: Crashed mid-execution during Stage 2 (asset_sync)
    run_a_id = str(uuid4())
    store.create_run(
        {
            "run_id": run_a_id,
            "request_id": str(uuid4()),
            "request_digest": "digest_a",
            "runtime_id": "a" * 64,
            "status": ProfilingRunStatus.RUNNING.value,
            "current_stage": ProfilingStageName.ASSET_SYNC.value,
            "plan_digest": "plan_a",
            "settings": {"model": "model"},
        },
        [
            {"stage_name": "materialize", "stage_order": 1, "status": StageStatus.COMPLETED.value},
            {"stage_name": "asset_sync", "stage_order": 2, "status": StageStatus.RUNNING.value, "progress_fraction": 0.42},
            {"stage_name": "node_profile", "stage_order": 3, "status": StageStatus.PENDING.value},
            {"stage_name": "link_profile", "stage_order": 4, "status": StageStatus.PENDING.value},
            {"stage_name": "plan_qualification", "stage_order": 5, "status": StageStatus.PENDING.value},
        ],
        [{"node_id": "master_node_01", "role": "master"}],
    )

    # 2. Run B: Crashed mid-execution during Stage 4 (link_profile)
    run_b_id = str(uuid4())
    store.create_run(
        {
            "run_id": run_b_id,
            "request_id": str(uuid4()),
            "request_digest": "digest_b",
            "runtime_id": "a" * 64,
            "status": ProfilingRunStatus.RUNNING.value,
            "current_stage": ProfilingStageName.LINK_PROFILE.value,
            "plan_digest": "plan_b",
            "settings": {"model": "model"},
        },
        [
            {"stage_name": "materialize", "stage_order": 1, "status": StageStatus.COMPLETED.value},
            {"stage_name": "asset_sync", "stage_order": 2, "status": StageStatus.COMPLETED.value},
            {"stage_name": "node_profile", "stage_order": 3, "status": StageStatus.COMPLETED.value},
            {"stage_name": "link_profile", "stage_order": 4, "status": StageStatus.RUNNING.value},
            {"stage_name": "plan_qualification", "stage_order": 5, "status": StageStatus.PENDING.value},
        ],
        [{"node_id": "master_node_01", "role": "master"}],
    )

    # 3. Run C: Already completed run (must remain untouched)
    run_c_id = str(uuid4())
    store.create_run(
        {
            "run_id": run_c_id,
            "request_id": str(uuid4()),
            "request_digest": "digest_c",
            "runtime_id": "a" * 64,
            "status": ProfilingRunStatus.COMPLETED.value,
            "current_stage": ProfilingStageName.PLAN_QUALIFICATION.value,
            "plan_digest": "plan_c",
            "settings": {"model": "model"},
        },
        [
            {"stage_name": s.value, "stage_order": STAGE_ORDERS[s], "status": StageStatus.COMPLETED.value}
            for s in STAGE_SEQUENCE
        ],
        [{"node_id": "master_node_01", "role": "master"}],
    )

    # Simulate daemon reboot: invoke reconcile_on_startup
    reconciled = control.coordinator.reconcile_on_startup()
    assert run_a_id in reconciled
    assert run_b_id in reconciled
    assert run_c_id not in reconciled

    # Check Run A
    run_a = store.get_run(run_a_id)
    assert run_a["status"] == ProfilingRunStatus.FAILED.value
    assert "ready for resumption" in run_a["error_message"]
    stages_a = {s["stage_name"]: s for s in store.get_stages(run_a_id)}
    assert stages_a["materialize"]["status"] == StageStatus.COMPLETED.value  # Preserved!
    assert stages_a["asset_sync"]["status"] == StageStatus.FAILED.value     # Reconciled!
    assert "ready for resumption" in stages_a["asset_sync"]["error_message"]
    assert stages_a["node_profile"]["status"] == StageStatus.PENDING.value

    # Check Run B
    run_b = store.get_run(run_b_id)
    assert run_b["status"] == ProfilingRunStatus.FAILED.value
    stages_b = {s["stage_name"]: s for s in store.get_stages(run_b_id)}
    assert stages_b["materialize"]["status"] == StageStatus.COMPLETED.value
    assert stages_b["asset_sync"]["status"] == StageStatus.COMPLETED.value
    assert stages_b["node_profile"]["status"] == StageStatus.COMPLETED.value
    assert stages_b["link_profile"]["status"] == StageStatus.FAILED.value

    # Check Run C (Untouched completed run)
    run_c = store.get_run(run_c_id)
    assert run_c["status"] == ProfilingRunStatus.COMPLETED.value
    stages_c = {s["stage_name"]: s for s in store.get_stages(run_c_id)}
    assert all(s["status"] == StageStatus.COMPLETED.value for s in stages_c.values())


def test_retry_cancelled_run_and_nonexistent_run(mock_control_setup):
    """Test retrying a cancelled run and attempting to retry a non-existent run ID."""
    control, registry, service = mock_control_setup
    store: ProfilingStore = control.coordinator.store

    # 1. Non-existent run ID raises KeyError
    non_existent_id = str(uuid4())
    with pytest.raises(KeyError, match=f"Profiling run {non_existent_id} not found"):
        control.coordinator.retry_run(non_existent_id)

    # 2. Test through FastAPI router: non-existent run returns HTTP 404
    app = FastAPI()
    app.include_router(build_router(lambda: registry, lambda: control, service.state, token_verifier=lambda _: True))
    with TestClient(app, client=("127.0.0.1", 12)) as client:
        resp = client.post(
            f"/v2/cluster/prima-preparations/{non_existent_id}/retry",
            headers={"origin": "http://testserver"},
        )
        assert resp.status_code == 404

    # 3. Create a cancelled run
    run_id = str(uuid4())
    store.create_run(
        {
            "run_id": run_id,
            "request_id": str(uuid4()),
            "request_digest": "digest_cancel",
            "runtime_id": "a" * 64,
            "status": ProfilingRunStatus.CANCELLED.value,
            "current_stage": ProfilingStageName.NODE_PROFILE.value,
            "plan_digest": "plan_cancel",
            "settings": {"model": "model"},
        },
        [
            {"stage_name": "materialize", "stage_order": 1, "status": StageStatus.COMPLETED.value},
            {"stage_name": "asset_sync", "stage_order": 2, "status": StageStatus.COMPLETED.value},
            {"stage_name": "node_profile", "stage_order": 3, "status": StageStatus.CANCELLED.value},
            {"stage_name": "link_profile", "stage_order": 4, "status": StageStatus.PENDING.value},
            {"stage_name": "plan_qualification", "stage_order": 5, "status": StageStatus.PENDING.value},
        ],
        [{"node_id": "master_node_01", "role": "master"}],
    )
    control.cancelled.add(run_id)

    # Mock _prepare to avoid long background execution
    control._prepare = lambda *args, **kwargs: None

    # Retrying a cancelled run should clear cancelled flag and resume from node_profile
    retried = control.retry_preparation(run_id, stage_name="node_profile")
    assert retried["status"] == ProfilingRunStatus.RUNNING.value
    assert run_id not in control.cancelled
    assert "materialize" in retried["preserved_stages"]
    assert "asset_sync" in retried["preserved_stages"]


def test_planner_candidates_subset_pruning_with_mixed_case_and_aliases():
    """Verify planner.candidates prunes optional worker subsets without dropping or erroring

    on required nodes with mixed case, hostnames, and IP aliases.
    """
    members = {
        "master_01": {
            "node_id": "master_01",
            "role": "master",
            "hostname": "master_box",
            "host": "192.168.1.10",
            "lan_host": "10.0.0.1",
            "backend": "cpu",
        },
        "worker_alpha": {
            "node_id": "worker_alpha",
            "role": "worker",
            "hostname": "gpu_node_1",
            "host": "192.168.1.21",
            "lan_host": "10.0.0.21",
            "backend": "vulkan",
        },
        "worker_beta": {
            "node_id": "worker_beta",
            "role": "worker",
            "hostname": "gpu_node_2",
            "host": "192.168.1.22",
            "lan_host": "10.0.0.22",
            "backend": "vulkan",
        },
        "worker_gamma": {
            "node_id": "worker_gamma",
            "role": "worker",
            "hostname": "gpu_node_3",
            "host": "192.168.1.23",
            "lan_host": "10.0.0.23",
            "backend": "vulkan",
        },
    }

    # Pin master by role and worker_alpha by hostname with uppercase
    body = PlanBody(
        model="model",
        required_nodes=["MASTER", "GPU_NODE_1"],
    )

    plans_evaluated = []
    def fake_execute_plan(candidate_body, inventory):
        # Verify required nodes are always in candidate inventory
        req_nodes = getattr(candidate_body, "required_nodes", ())
        plans_evaluated.append((set(inventory.keys()), tuple(req_nodes)))
        return {
            "fit_status": "fit",
            "device_allocations": [],
            "cpu_layers": 1,
            "ngl": 1,
            "tensor_split_csv": "1.0",
            "tensor_overrides": [],
        }

    results = candidates(body, members, fake_execute_plan, limit=16)
    assert len(results) >= 1

    # In all evaluated candidate inventories, master_01 and worker_alpha must be present
    for inv_keys, req_nodes in plans_evaluated:
        assert "master_01" in inv_keys
        assert "worker_alpha" in inv_keys


def test_sqlite_high_contention_concurrent_reads_and_writes(mock_control_setup):
    """Stress-test SQLite database under high-concurrency read and write bursts."""
    control, registry, service = mock_control_setup
    store: ProfilingStore = control.coordinator.store

    run_id = str(uuid4())
    store.create_run(
        {
            "run_id": run_id,
            "request_id": str(uuid4()),
            "request_digest": "burst_digest",
            "runtime_id": "a" * 64,
            "status": "running",
            "current_stage": "asset_sync",
            "plan_digest": "burst_plan",
            "settings": {"model": "model"},
        },
        [{"stage_name": s.value, "stage_order": STAGE_ORDERS[s], "status": "pending"} for s in STAGE_SEQUENCE],
        [{"node_id": "master_node_01", "role": "master"}],
    )

    errors = []

    def writer_worker(idx: int):
        try:
            for i in range(10):
                store.append_log(run_id, "INFO", "stress", f"Burst write log {idx}:{i}")
                store.update_node_task(
                    run_id,
                    "asset_sync",
                    f"node_{idx % 3}",
                    "running",
                    {"fraction": 0.5, "bytes_done": (idx + 1) * 1024, "bytes_total": 100 * 1024},
                )
        except Exception as exc:
            errors.append(("WRITER_ERROR", exc))

    def reader_worker(idx: int):
        try:
            for i in range(15):
                _ = store.get_run(run_id)
                _ = store.get_stages(run_id)
                _ = store.get_logs(run_id, limit=20)
                _ = store.get_node_tasks(run_id)
        except Exception as exc:
            errors.append(("READER_ERROR", exc))

    threads = []
    # 6 writers and 8 readers running concurrently
    for w in range(6):
        threads.append(threading.Thread(target=writer_worker, args=(w,)))
    for r in range(8):
        threads.append(threading.Thread(target=reader_worker, args=(r,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    # Zero database locked or thread contention errors
    assert len(errors) == 0, f"Encountered concurrency errors: {errors}"

    # Verify write consistency
    logs = store.get_logs(run_id, limit=1000)
    assert len(logs) == 60  # 6 writers * 10 logs each

