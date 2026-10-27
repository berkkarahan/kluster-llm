"""Independent stage retry, startup crash reconciliation, and HTTP endpoints integration tests."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import pytest
from starlette.testclient import TestClient

from kllm.modules.inference.entities import PlanBody
from kllm.modules.managed_network.entities import MasterAuthority
from kllm.modules.primacpp.entities import (
    STAGE_ORDERS,
    STAGE_SEQUENCE,
    ProfilingRunStatus,
    ProfilingStageName,
    StageStatus,
)
from kllm.modules.primacpp.interactors import stage_link_profile
from kllm.modules.primacpp.interactors.control import PrimaControl
from kllm.modules.runtimes.router_http import build_router
from test_prima_lifecycle import BASELINE_FEATURES


@pytest.fixture
def test_setup(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"deterministic bytes" * 8192)
    authority = MasterAuthority(
        cluster_binding_id=uuid4(),
        master_node_id="master",
        master_key_id="1" * 64,
        master_term=1,
        master_session_id=uuid4(),
    )
    state = SimpleNamespace(
        role="master",
        node_id="master",
        process_pid=None,
        members={"master": {"role": "master", "host": "127.0.0.1", "backend": "cpu"}},
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
        "estimated_layers": 1,
        "device_effective": "cpu",
        "cpu_layers": 1,
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
    control = PrimaControl(service, registry, resolve_model=lambda _: model, execute_plan=lambda *_: plan)
    yield control, registry, service
    control.close()


def wait_for(predicate, timeout=8.0):
    until = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= until:
            raise TimeoutError("Condition not satisfied before timeout")
        time.sleep(0.02)


def test_independent_stage_retry_preserves_earlier_completed_stages(test_setup, monkeypatch):
    """Test that when a later stage fails, retrying it preserves completed earlier stages

    without repeating asset transfer or earlier benchmarks.
    """
    control, registry, service = test_setup

    # Mock profile jobs so node_profile completes cleanly
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

    # First attempt: Stage 4 (link_profile) fails
    original_link = stage_link_profile.execute_link_profile
    first_call = True

    def failing_link_profile(*args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            raise RuntimeError("Simulated Ring-TCP link measurement timeout")
        return original_link(*args, **kwargs)

    monkeypatch.setattr(stage_link_profile, "execute_link_profile", failing_link_profile)

    request_id = str(uuid4())
    job = control.start_preparation("a" * 64, body, plan["plan_digest"], request_id, include_profile=True)
    run_id = job["job_id"]

    # Wait for first attempt to fail
    wait_for(lambda: control.job(run_id)["phase"] == "failed")
    failed_status = control.job(run_id)
    assert failed_status["phase"] == "failed"
    assert "Simulated Ring-TCP" in failed_status["error"]

    # Verify state in SQLite store: stages 1, 2, 3 must be COMPLETED, stage 4 FAILED
    stages = control.coordinator.store.get_stages(run_id)
    stage_map = {s["stage_name"]: s for s in stages}
    assert stage_map["materialize"]["status"] == StageStatus.COMPLETED.value
    assert stage_map["asset_sync"]["status"] == StageStatus.COMPLETED.value
    assert stage_map["node_profile"]["status"] in (StageStatus.COMPLETED.value, StageStatus.SKIPPED.value)
    assert stage_map["link_profile"]["status"] == StageStatus.FAILED.value
    assert stage_map["link_profile"]["attempt"] == 1
    assert stage_map["plan_qualification"]["status"] == StageStatus.PENDING.value

    run_record = control.coordinator.store.get_run(run_id)
    assert run_record["status"] == ProfilingRunStatus.FAILED.value
    assert run_record["current_stage"] == ProfilingStageName.LINK_PROFILE.value

    # Second attempt: Retry stage 4.
    # The monkeypatched function will now succeed (first_call is False).
    retried_job = control.retry_preparation(run_id, stage_name="link_profile")
    assert retried_job["job_id"] == run_id

    # Wait for retry to finish
    wait_for(lambda: control.job(run_id)["phase"] == "prepared")
    final_status = control.job(run_id)
    assert final_status["phase"] == "prepared"

    # Verify in SQLite: earlier stages remained attempt 1, stage 4 is attempt 2 and completed
    stages_after = control.coordinator.store.get_stages(run_id)
    stage_map_after = {s["stage_name"]: s for s in stages_after}
    assert stage_map_after["materialize"]["status"] == StageStatus.COMPLETED.value
    assert stage_map_after["materialize"]["attempt"] == 1
    assert stage_map_after["asset_sync"]["status"] == StageStatus.COMPLETED.value
    assert stage_map_after["asset_sync"]["attempt"] == 1
    assert stage_map_after["link_profile"]["status"] in (StageStatus.COMPLETED.value, StageStatus.SKIPPED.value)
    assert stage_map_after["link_profile"]["attempt"] == 2
    assert stage_map_after["plan_qualification"]["status"] == StageStatus.COMPLETED.value

    # Verify structured logs confirm skipping earlier stages
    logs = control.coordinator.store.get_logs(run_id)
    log_messages = [log_entry["message"] for log_entry in logs]
    assert any("Preserving completed Stage 1: Materialize" in m for m in log_messages)
    assert any("Preserving completed Stage 2: Asset Sync" in m for m in log_messages)
    assert any("Retrying run" in m and "link_profile" in m for m in log_messages)

    # Verify coverage query
    coverage = control.preparation_coverage(run_id)
    assert coverage["run_id"] == run_id
    assert coverage["coverage_status"] in ("profiled", "unmeasured")


def test_startup_crash_reconciliation_and_resumption(test_setup, monkeypatch):
    """Test that startup crash reconciliation transitions in-progress stages and runs to failed

    and enables resumption from the interrupted stage.
    """
    control, registry, service = test_setup
    store = control.coordinator.store

    profile_result = {
        "job_id": "prof-1",
        "phase": "prepared",
        "profile": {"cpu_layer_ms": 1.2, "output_token_ms": 0.5, "prompt_ms": 0.1, "devices": []},
        "diagnostics": {"stages": []},
    }
    monkeypatch.setattr(control.profiles, "start", lambda *args, **kwargs: profile_result)
    monkeypatch.setattr(control.profiles, "job", lambda *args, **kwargs: profile_result)

    # Create an interrupted run in SQLite (as if agent died mid-execution)
    run_id = str(uuid4())
    req_id = str(uuid4())
    now = time.time() - 100

    stages_data = [
        {"stage_name": ProfilingStageName.MATERIALIZE.value, "stage_order": 1, "status": StageStatus.COMPLETED.value, "attempt": 1},
        {"stage_name": ProfilingStageName.ASSET_SYNC.value, "stage_order": 2, "status": StageStatus.COMPLETED.value, "attempt": 1},
        {"stage_name": ProfilingStageName.NODE_PROFILE.value, "stage_order": 3, "status": StageStatus.RUNNING.value, "attempt": 1, "started_at": now},
        {"stage_name": ProfilingStageName.LINK_PROFILE.value, "stage_order": 4, "status": StageStatus.PENDING.value, "attempt": 1},
        {"stage_name": ProfilingStageName.PLAN_QUALIFICATION.value, "stage_order": 5, "status": StageStatus.PENDING.value, "attempt": 1},
    ]
    nodes_data = [
        {"node_id": "master", "role": "master", "rank_index": 0, "assigned_address": "127.0.0.1", "backend": "cpu"}
    ]
    run_data = {
        "run_id": run_id,
        "request_id": req_id,
        "request_digest": "digest123",
        "runtime_id": "a" * 64,
        "model_path": "model",
        "model_sha256": "sha123",
        "status": ProfilingRunStatus.RUNNING.value,
        "current_stage": ProfilingStageName.NODE_PROFILE.value,
        "include_profile": True,
        "plan_digest": "plan_digest123",
        "command_owner": "owner",
        "settings": {"model": "model"},
        "created_at": now,
        "started_at": now,
        "updated_at": now,
    }
    store.create_run(run_data, stages_data, nodes_data)

    # Ensure run is initially 'running'
    assert store.get_run(run_id)["status"] == "running"

    # Simulate restart: trigger reconcile_on_startup
    reconciled = control.coordinator.reconcile_on_startup()
    assert run_id in reconciled

    # Verify reconciliation changed run and running stage to failed (resumable)
    rec_run = store.get_run(run_id)
    assert rec_run["status"] == "failed"
    assert "restarted" in rec_run["error_message"].lower()

    rec_stages = {s["stage_name"]: s for s in store.get_stages(run_id)}
    assert rec_stages["materialize"]["status"] == StageStatus.COMPLETED.value
    assert rec_stages["asset_sync"]["status"] == StageStatus.COMPLETED.value
    assert rec_stages["node_profile"]["status"] == StageStatus.FAILED.value
    assert "restarted" in rec_stages["node_profile"]["error_message"].lower()
    assert rec_stages["link_profile"]["status"] == StageStatus.PENDING.value

    # Verify control.job sees the reconciled failed status
    job_view = control.job(run_id)
    assert job_view["phase"] == "failed"
    assert "restarted" in job_view["error"].lower()

    # Verify retry without stage_name picks the first failed stage (node_profile)
    resumed = control.retry_preparation(run_id)
    assert resumed["job_id"] == run_id
    assert resumed["phase"] in ("preparing", "running", "profiling")


def test_idempotent_preparation_submission(test_setup):
    """Test idempotency: re-submitting with identical request_id returns existing run."""
    control, registry, service = test_setup
    body = PlanBody(model="model")
    plan = control.plan("a" * 64, body)

    request_id = str(uuid4())
    first = control.start_preparation("a" * 64, body, plan["plan_digest"], request_id)
    run_id = first["job_id"]

    second = control.start_preparation("a" * 64, body, plan["plan_digest"], request_id)
    assert second["job_id"] == run_id

    # Re-using request_id with different payload raises ValueError
    different_body = PlanBody(model="model", context=2048)
    with pytest.raises(ValueError, match="reused with different inputs"):
        control.start_preparation("a" * 64, different_body, plan["plan_digest"], request_id)


def test_http_rest_endpoints(test_setup, monkeypatch):
    """Test HTTP REST endpoints: POST, GET, coverage, retry, logs, cancel."""
    control, registry, service = test_setup
    app = FastAPI()
    app.include_router(
        build_router(
            lambda: registry,
            lambda: control,
            lambda: service.state(),
            token_verifier=lambda token: token == "valid-token",
        )
    )

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

    with TestClient(app, client=("127.0.0.1", 12)) as client:
        headers = {"Authorization": "valid-token"}
        req_id = str(uuid4())
        payload = {
            "runtime_id": "a" * 64,
            "plan_digest": plan["plan_digest"],
            "request_id": req_id,
            "settings": body.model_dump(),
            "include_profile": True,
        }

        # 1. POST /v2/cluster/prima-preparations -> 202 Accepted
        resp = client.post("/v2/cluster/prima-preparations", json=payload, headers=headers)
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert "job_id" in data
        assert "stages" in data
        assert len(data["stages"]) == 5
        run_id = data["job_id"]

        # 2. GET /v2/cluster/prima-preparations/{id} -> 200 OK
        resp_get = client.get(f"/v2/cluster/prima-preparations/{run_id}", headers=headers)
        assert resp_get.status_code == 200
        get_data = resp_get.json()
        assert get_data["job_id"] == run_id
        assert "progress" in get_data
        assert "local_chunk_bytes" in get_data

        # 3. GET /v2/cluster/prima-preparations/{id}/coverage -> 200 OK
        resp_cov = client.get(f"/v2/cluster/prima-preparations/{run_id}/coverage", headers=headers)
        assert resp_cov.status_code == 200
        cov_data = resp_cov.json()
        assert cov_data["run_id"] == run_id
        assert "coverage_status" in cov_data
        assert "link_matrix" in cov_data

        # 4. GET /v2/cluster/prima-preparations/{id}/logs -> 200 OK
        resp_logs = client.get(f"/v2/cluster/prima-preparations/{run_id}/logs", headers=headers)
        assert resp_logs.status_code == 200
        logs_data = resp_logs.json()
        assert "logs" in logs_data
        assert "text" in logs_data
        assert isinstance(logs_data["logs"], list)

        # 5. POST /v2/cluster/prima-preparations/{id}/cancel -> 200 OK
        resp_cancel = client.post(f"/v2/cluster/prima-preparations/{run_id}/cancel", headers=headers)
        assert resp_cancel.status_code == 200
        cancel_data = resp_cancel.json()
        assert cancel_data["phase"] == "cancelled"

        # 6. POST /v2/cluster/prima-preparations/{id}/retry -> 202 Accepted
        resp_retry = client.post(
            f"/v2/cluster/prima-preparations/{run_id}/retry?stage=materialize",
            headers=headers,
        )
        assert resp_retry.status_code == 202

        # 7. GET /v2/cluster/prima-preparations/nonexistent -> 404 Not Found
        resp_404 = client.get("/v2/cluster/prima-preparations/00000000-0000-0000-0000-000000000000", headers=headers)
        assert resp_404.status_code == 404


def test_retry_validation_and_active_conflicts(test_setup, monkeypatch):
    """Test retry error paths: actively running run (409) and nonexistent run (404)."""
    control, registry, service = test_setup
    app = FastAPI()
    app.include_router(
        build_router(
            lambda: registry,
            lambda: control,
            lambda: service.state(),
            token_verifier=lambda token: token == "valid-token",
        )
    )

    with TestClient(app, client=("127.0.0.1", 12)) as client:
        headers = {"Authorization": "valid-token"}
        unknown_id = str(uuid4())

        # Retrying nonexistent run raises KeyError -> 404
        with pytest.raises(KeyError, match="not found"):
            control.retry_preparation(unknown_id)

        resp_404 = client.post(f"/v2/cluster/prima-preparations/{unknown_id}/retry", headers=headers)
        assert resp_404.status_code == 404

        # Simulate actively running run in store
        store = control.coordinator.store
        run_id = str(uuid4())
        now = time.time()
        stages_data = [
            {"stage_name": s.value, "stage_order": STAGE_ORDERS[s], "status": StageStatus.PENDING.value}
            for s in STAGE_SEQUENCE
        ]
        nodes_data = [{"node_id": "master", "role": "master", "rank_index": 0, "assigned_address": "127.0.0.1", "backend": "cpu"}]
        run_data = {
            "run_id": run_id,
            "request_id": str(uuid4()),
            "request_digest": "digest_run",
            "runtime_id": "a" * 64,
            "model_path": "model",
            "model_sha256": "sha",
            "status": ProfilingRunStatus.RUNNING.value,
            "current_stage": ProfilingStageName.MATERIALIZE.value,
            "include_profile": False,
            "plan_digest": "plan_digest",
            "command_owner": "owner",
            "settings": {"model": "model"},
            "created_at": now,
            "started_at": now,
            "updated_at": now,
        }
        store.create_run(run_data, stages_data, nodes_data)

        # Retrying an actively running run raises ValueError -> 409
        with pytest.raises(ValueError, match="already actively running"):
            control.retry_preparation(run_id)

        resp_409 = client.post(f"/v2/cluster/prima-preparations/{run_id}/retry", headers=headers)
        assert resp_409.status_code == 409

