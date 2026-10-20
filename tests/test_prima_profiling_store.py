"""Unit tests for SQLite ProfilingStore schema, persistence, and crash reconciliation."""

import time
from uuid import uuid4

import pytest

from kllm.modules.primacpp.entities import DeviceProfile
from kllm.modules.primacpp.gateways.store import ProfilingStore


@pytest.fixture
def store(tmp_path):
    s = ProfilingStore(tmp_path)
    yield s
    s.close()


def test_create_and_query_profiling_run(store):
    run_id = str(uuid4())
    req_id = str(uuid4())
    run_data = {
        "run_id": run_id,
        "request_id": req_id,
        "request_digest": "req_digest_123",
        "runtime_id": "a" * 64,
        "model_path": "/tmp/model.gguf",
        "model_sha256": "b" * 64,
        "network_id": None,
        "status": "queued",
        "current_stage": "materialize",
        "include_profile": True,
        "plan_digest": "c" * 64,
        "settings": {"model": "model.gguf", "context": 2048},
    }
    stages_data = [
        {"stage_name": "materialize", "stage_order": 1, "status": "running"},
        {"stage_name": "asset_sync", "stage_order": 2, "status": "pending"},
        {"stage_name": "node_profile", "stage_order": 3, "status": "pending"},
        {"stage_name": "link_profile", "stage_order": 4, "status": "pending"},
        {"stage_name": "plan_qualification", "stage_order": 5, "status": "pending"},
    ]
    nodes_data = [
        {"node_id": "master1", "role": "master", "rank_index": 0, "assigned_address": "127.0.0.1", "backend": "vulkan"},
        {"node_id": "worker1", "role": "worker", "rank_index": 1, "assigned_address": "192.168.1.100", "backend": "cpu"},
    ]

    created = store.create_run(run_data, stages_data, nodes_data)
    assert created["run_id"] == run_id
    assert created["status"] == "queued"
    assert created["settings"]["model"] == "model.gguf"

    # Query by run_id
    fetched = store.get_run(run_id)
    assert fetched is not None
    assert fetched["request_id"] == req_id
    assert fetched["include_profile"] is True

    # Query by request_id
    by_req = store.get_run_by_request_id(req_id)
    assert by_req is not None
    assert by_req["run_id"] == run_id

    # Query stages
    stages = store.get_stages(run_id)
    assert len(stages) == 5
    assert stages[0]["stage_name"] == "materialize"
    assert stages[0]["status"] == "running"
    assert stages[1]["stage_name"] == "asset_sync"
    assert stages[1]["status"] == "pending"

    # Query nodes
    nodes = store.get_nodes(run_id)
    assert len(nodes) == 2
    assert {n["node_id"] for n in nodes} == {"master1", "worker1"}


def test_stage_transitions_and_progress(store):
    run_id = str(uuid4())
    req_id = str(uuid4())
    store.create_run(
        {
            "run_id": run_id,
            "request_id": req_id,
            "request_digest": "digest",
            "runtime_id": "r" * 64,
            "plan_digest": "p" * 64,
        },
        [
            {"stage_name": "materialize", "stage_order": 1, "status": "pending"},
            {"stage_name": "asset_sync", "stage_order": 2, "status": "pending"},
        ],
        [{"node_id": "master", "role": "master", "rank_index": 0}],
    )

    # Transition to running
    store.transition_stage(run_id, "materialize", "running", {"fraction": 0.5, "label": "Planning…"})
    stage = store.get_stage(run_id, "materialize")
    assert stage["status"] == "running"
    assert stage["progress_fraction"] == 0.5
    assert stage["progress_label"] == "Planning…"
    assert stage["started_at"] is not None

    # Transition to completed
    store.transition_stage(run_id, "materialize", "completed", {"fraction": 1.0, "label": "Done"})
    stage = store.get_stage(run_id, "materialize")
    assert stage["status"] == "completed"
    assert stage["finished_at"] is not None

    # Reset stage for retry
    store.reset_stage(run_id, "materialize", "pending")
    stage = store.get_stage(run_id, "materialize")
    assert stage["status"] == "pending"
    assert stage["attempt"] == 2


def test_node_task_updates(store):
    run_id = str(uuid4())
    store.create_run(
        {"run_id": run_id, "request_id": str(uuid4()), "request_digest": "d", "runtime_id": "r", "plan_digest": "p"},
        [{"stage_name": "asset_sync", "stage_order": 1, "status": "running"}],
        [{"node_id": "worker1", "role": "worker", "rank_index": 0}],
    )

    store.update_node_task(
        run_id,
        "asset_sync",
        "worker1",
        "running",
        {"fraction": 0.25, "bytes_done": 250, "bytes_total": 1000},
        details={"chunk_offset": 250},
    )

    tasks = store.get_node_tasks(run_id, "asset_sync")
    assert len(tasks) == 1
    assert tasks[0]["node_id"] == "worker1"
    assert tasks[0]["bytes_done"] == 250
    assert tasks[0]["details"]["chunk_offset"] == 250

    # Update to completed
    store.update_node_task(
        run_id,
        "asset_sync",
        "worker1",
        "completed",
        {"fraction": 1.0, "bytes_done": 1000, "bytes_total": 1000},
    )
    tasks = store.get_node_tasks(run_id, "asset_sync")
    assert tasks[0]["status"] == "completed"
    assert tasks[0]["bytes_done"] == 1000


def test_node_and_link_measurements(store):
    run_id = str(uuid4())
    store.create_run(
        {"run_id": run_id, "request_id": str(uuid4()), "request_digest": "d", "runtime_id": "r", "plan_digest": "p"},
        [],
        [],
    )

    # Record node profile
    profile = DeviceProfile(
        runtime_id="a" * 64,
        model_sha256="b" * 64,
        node_id="master1",
        hardware_digest="c" * 64,
        settings_digest="d" * 64,
        backend="vulkan",
        measured_at=time.time(),
        cpu_layer_ms=2.5,
        gpu_layer_ms=0.5,
        disk_read_bytes_s=2500000000.0,
        memory_available_bytes=34359738368,
    )
    store.record_node_profile(run_id, "master1", profile)
    profiles = store.get_node_profiles(run_id)
    assert "master1" in profiles
    assert profiles["master1"]["cpu_layer_ms"] == 2.5
    assert profiles["master1"]["backend"] == "vulkan"

    # Record link profile
    link_meas = {
        "grant_id": "grant-1",
        "network_id": "net-lan",
        "source_address": "10.0.0.1",
        "target_address": "10.0.0.2",
        "latency_ms": 0.45,
        "bandwidth_bytes_s": 1250000000.0,
        "samples": 3,
        "sample_seconds": [0.01, 0.01, 0.01],
        "receipt": {"signed_by": "master1"},
    }
    store.record_link_profile(run_id, "master1", "worker1", link_meas)
    links = store.get_link_profiles(run_id)
    assert len(links) == 1
    assert links[0]["source_node_id"] == "master1"
    assert links[0]["target_node_id"] == "worker1"
    assert links[0]["latency_ms"] == 0.45
    assert links[0]["bandwidth_bytes_s"] == 1250000000.0


def test_qualification_and_logs(store):
    run_id = str(uuid4())
    store.create_run(
        {"run_id": run_id, "request_id": str(uuid4()), "request_digest": "d", "runtime_id": "r", "plan_digest": "p"},
        [],
        [],
    )

    # Record qualification
    qual = {
        "fit_status": "fit",
        "predicted_decode_ms": 35.2,
        "scheduler_strategy": "highs_halda",
        "qualification_passed": True,
        "scheduler_report": {"strategy": "highs_halda", "total_step_ms": 35.2},
    }
    store.record_qualification(run_id, qual)
    res = store.get_qualification(run_id)
    assert res is not None
    assert res["qualification_passed"] is True
    assert res["predicted_decode_ms"] == 35.2
    assert res["scheduler_report"]["strategy"] == "highs_halda"

    # Append logs
    store.append_log(run_id, "INFO", "lifecycle", "Run started", stage="materialize")
    store.append_log(run_id, "ERROR", "network", "TCP handshake timed out", stage="link_profile", node="worker1")

    logs = store.get_logs(run_id)
    assert len(logs) == 2
    assert logs[0]["message"] == "Run started"

    error_logs = store.get_logs(run_id, level="ERROR")
    assert len(error_logs) == 1
    assert error_logs[0]["node_id"] == "worker1"


def test_reconcile_on_startup_crash(store):
    run_id = str(uuid4())
    store.create_run(
        {
            "run_id": run_id,
            "request_id": str(uuid4()),
            "request_digest": "d",
            "runtime_id": "r",
            "plan_digest": "p",
            "status": "running",
        },
        [
            {"stage_name": "materialize", "stage_order": 1, "status": "completed"},
            {"stage_name": "asset_sync", "stage_order": 2, "status": "completed"},
            {"stage_name": "node_profile", "stage_order": 3, "status": "running"},
            {"stage_name": "link_profile", "stage_order": 4, "status": "pending"},
        ],
        [],
    )

    reconciled = store.reconcile_on_startup()
    assert run_id in reconciled

    # Verify run transitioned to failed
    run = store.get_run(run_id)
    assert run["status"] == "failed"
    assert "restarted" in run["error_message"]

    # Verify stage 1 and 2 remain completed!
    stages = store.get_stages(run_id)
    stage_by_name = {s["stage_name"]: s for s in stages}
    assert stage_by_name["materialize"]["status"] == "completed"
    assert stage_by_name["asset_sync"]["status"] == "completed"

    # Verify stage 3 transitioned to failed
    assert stage_by_name["node_profile"]["status"] == "failed"
    assert "restarted" in stage_by_name["node_profile"]["error_message"]

    # Verify stage 4 remains pending
    assert stage_by_name["link_profile"]["status"] == "pending"
