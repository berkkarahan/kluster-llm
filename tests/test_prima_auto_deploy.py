"""Integration tests for Prima auto-deployment engine (Phase 3: M2)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.exceptions import HTTPException

from kllm.modules.inference.entities import PlanBody, RunBody
from kllm.modules.inference.interactors.run_planning import execute_plan
from kllm.modules.primacpp.interactors.distributed_runs import PrimaRuns, DistributedPrimaRuns
from kllm.modules.primacpp.interactors.planner import auto_plan, is_prima_auto_deploy_active


@pytest.fixture
def mock_gguf_model(tmp_path: Path):
    """Create a mock valid GGUF file."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"GGUF" + b"\x00" * 1024 * 1024)
    return model_file


@pytest.fixture
def mock_prima_runtime():
    """Mock a qualified Prima runtime with known capabilities."""
    return {
        "runtime_id": "a" * 64,
        "engine": "primacpp",
        "llama_server": "llama-server",
        "llama_cli": "llama-cli",
        "capabilities": {
            "architectures": ["llama", "qwen2"],
            "features": ["ring_tcp", "explicit_devices_v1"],
            "backends": ["vulkan", "cpu"],
        },
    }


@pytest.fixture
def single_node_members():
    """Single master node cluster state."""
    return {
        "master": {
            "node_id": "master",
            "role": "master",
            "is_master": True,
            "platform": "Windows",
            "architecture": "x86_64",
            "backend": "vulkan",
            "vram_bytes": 16 * 1024**3,
            "total_ram_bytes": 32 * 1024**3,
            "available_ram_bytes": 28 * 1024**3,
            "devices": [{"index": 0, "name": "Vulkan0", "backend": "vulkan"}],
        }
    }


@pytest.fixture
def multi_node_members():
    """Two-node cluster state (master + worker)."""
    return {
        "master": {
            "node_id": "master",
            "role": "master",
            "is_master": True,
            "platform": "Windows",
            "architecture": "x86_64",
            "backend": "vulkan",
            "vram_bytes": 100 * 1024 * 1024,
            "total_ram_bytes": 200 * 1024 * 1024,
            "available_ram_bytes": 200 * 1024 * 1024,
            "devices": [{"index": 0, "name": "Vulkan0", "backend": "vulkan"}],
            "host": "192.168.1.1",
            "lan_host": "192.168.1.1",
        },
        "worker1": {
            "node_id": "worker1",
            "role": "worker",
            "is_master": False,
            "platform": "Windows",
            "architecture": "x86_64",
            "backend": "vulkan",
            "vram_bytes": 8 * 1024**3,
            "total_ram_bytes": 32 * 1024**3,
            "available_ram_bytes": 28 * 1024**3,
            "devices": [{"index": 0, "name": "Vulkan0", "backend": "vulkan"}],
            "host": "192.168.1.2",
            "lan_host": "192.168.1.2",
            "prima_inventory_version": 1,
            "prima_control_version": 1,
            "prima_asset_lease_version": 1,
        },
    }


def test_auto_plan_bypasses_core_placement_limits(monkeypatch, tmp_path, mock_gguf_model, mock_prima_runtime, single_node_members):
    """FR-005 / T012: execute_plan delegates to auto_plan when prima_auto_deploy is True."""
    body = PlanBody(model=str(mock_gguf_model), prima_auto_deploy=True, context=4096)

    # Mock gguf layout
    layout = {
        "architecture": "llama",
        "n_layers": 32,
        "layers": [{"bytes": 100 * 1024 * 1024} for _ in range(32)],
        "input_bytes": 1024 * 1024,
        "output_bytes": 1024 * 1024,
    }
    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: layout)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.model_support.model_architecture", lambda _: "llama")
    monkeypatch.setattr("kllm.modules.runtimes.interactors.selection.snapshot", lambda: mock_prima_runtime)
    monkeypatch.setattr("kllm.modules.models.resolve_local_model", lambda _: mock_gguf_model)

    result = execute_plan(body, single_node_members)
    assert result["fit_status"] == "fits"
    assert result["profiling"]["status"] == "auto_planned"
    assert result["engine"] == "primacpp"
    assert "scheduler" in result


def test_auto_plan_highs_layer_allocation(monkeypatch, tmp_path, mock_gguf_model, mock_prima_runtime, multi_node_members):
    """FR-005, FR-006 / T013, T014: solve_highs_halda_schedule distributes layers across nodes."""
    body = PlanBody(model=str(mock_gguf_model), prima_auto_deploy=True, context=4096)
    layout = {
        "architecture": "llama",
        "n_layers": 32,
        "layers": [{"bytes": 100 * 1024 * 1024} for _ in range(32)],
        "input_bytes": 1024 * 1024,
        "output_bytes": 1024 * 1024,
    }
    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: layout)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.model_support.model_architecture", lambda _: "llama")

    result = auto_plan(body, multi_node_members, model=mock_gguf_model, runtime=mock_prima_runtime)
    assert result["fit_status"] == "fits"
    assert result["profiling"]["status"] == "auto_planned"
    assert result["scheduler"]["node_allocations"]
    alloc_sum = sum(a["w_m"] for a in result["scheduler"]["node_allocations"])
    assert alloc_sum == 32


def test_fast_refusal_zero_byte_model(monkeypatch, tmp_path, mock_prima_runtime, single_node_members):
    """FR-016 / T016b: 0-byte model file immediately returns HTTP 400 PRIMA_MODEL_CORRUPT."""
    zero_byte = tmp_path / "corrupt_empty.gguf"
    zero_byte.write_bytes(b"")

    body = PlanBody(model=str(zero_byte), prima_auto_deploy=True)
    with pytest.raises(HTTPException) as exc_info:
        auto_plan(body, single_node_members, model=zero_byte, runtime=mock_prima_runtime)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail.get("code") == "PRIMA_MODEL_CORRUPT"


def test_fast_refusal_corrupt_model(monkeypatch, tmp_path, mock_prima_runtime, single_node_members):
    """FR-016 / T016b: Corrupt / non-GGUF file returns HTTP 400 PRIMA_MODEL_CORRUPT."""
    corrupt_file = tmp_path / "not_gguf.bin"
    corrupt_file.write_bytes(b"RANDOM_GARBAGE_BYTES_THAT_ARE_NOT_GGUF")

    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: None)

    body = PlanBody(model=str(corrupt_file), prima_auto_deploy=True)
    with pytest.raises(HTTPException) as exc_info:
        auto_plan(body, single_node_members, model=corrupt_file, runtime=mock_prima_runtime)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail.get("code") == "PRIMA_MODEL_CORRUPT"


def test_fast_refusal_unsupported_model_architecture(monkeypatch, tmp_path, mock_gguf_model, mock_prima_runtime, single_node_members):
    """FR-013 / T016: Model architecture not supported by runtime returns HTTP 400 PRIMA_MODEL_UNSUPPORTED."""
    layout = {
        "architecture": "unsupported_mpt_v2",
        "n_layers": 32,
        "layers": [{"bytes": 100 * 1024 * 1024} for _ in range(32)],
    }
    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: layout)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.model_support.model_architecture", lambda _: "unsupported_mpt_v2")

    body = PlanBody(model=str(mock_gguf_model), prima_auto_deploy=True)
    with pytest.raises(HTTPException) as exc_info:
        auto_plan(body, single_node_members, model=mock_gguf_model, runtime=mock_prima_runtime)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail.get("code") == "PRIMA_MODEL_UNSUPPORTED"


def test_capacity_exhaustion_infeasible_schedule(monkeypatch, tmp_path, mock_gguf_model, mock_prima_runtime):
    """FR-005, SC-005 / T013, T019: Infeasible memory returns HTTP 422 PRIMA_INSUFFICIENT_CAPACITY."""
    # Starved members with very low memory
    starved_members = {
        "master": {
            "node_id": "master",
            "role": "master",
            "is_master": True,
            "platform": "Windows",
            "architecture": "x86_64",
            "backend": "vulkan",
            "vram_bytes": 1024,
            "total_ram_bytes": 1024 * 1024,
            "available_ram_bytes": 1024 * 1024,
            "devices": [{"index": 0, "name": "Vulkan0", "backend": "vulkan"}],
        }
    }
    # Large model
    layout = {
        "architecture": "llama",
        "n_layers": 32,
        "layers": [{"bytes": 1024 * 1024 * 1024} for _ in range(32)],
        "input_bytes": 1024 * 1024,
        "output_bytes": 1024 * 1024,
    }
    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: layout)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.model_support.model_architecture", lambda _: "llama")

    body = PlanBody(model=str(mock_gguf_model), prima_auto_deploy=True, context=4096)
    with pytest.raises(HTTPException) as exc_info:
        auto_plan(body, starved_members, model=mock_gguf_model, runtime=mock_prima_runtime)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail.get("code") == "PRIMA_INSUFFICIENT_CAPACITY"
    assert "required_breakdown" in exc_info.value.detail
    assert "available_breakdown" in exc_info.value.detail


def test_single_node_auto_deploy_routing(monkeypatch, tmp_path, mock_gguf_model, mock_prima_runtime, single_node_members):
    """FR-008, SC-003 / T015, T018: Single-node auto deploy routes to local supervisor without network ID."""
    body = RunBody(model=str(mock_gguf_model), prima_auto_deploy=True, context=512)
    layout = {
        "architecture": "llama",
        "n_layers": 4,
        "layers": [{"bytes": 1024 * 1024} for _ in range(4)],
    }
    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: layout)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.model_support.model_architecture", lambda _: "llama")
    monkeypatch.setattr("kllm.modules.runtimes.interactors.selection.snapshot", lambda: mock_prima_runtime)

    plan = auto_plan(body, single_node_members, model=mock_gguf_model, runtime=mock_prima_runtime)
    assert len(plan["prima_topology"]["ranks"]) == 1

    # Mock control and runs
    control = MagicMock()
    control.registry.capture.return_value = mock_prima_runtime
    control.root = tmp_path
    authority = SimpleNamespace(
        master_node_id="master",
        cluster_binding_id="00000000-0000-0000-0000-000000000000",
        master_key_id="0" * 64,
        master_term=1,
        master_session_id="00000000-0000-0000-0000-000000000000",
    )
    control.service.channels.master_authority.return_value = authority
    control.service.state().role = "master"
    control.service.state().infer_port = 8080
    control.service.state().process_pid = None
    control.rank.owner = None
    control.profiles.jobs = {}
    control.jobs = {}
    control.ports = None

    runs = PrimaRuns(control)
    local_launch_called = []
    dist_launch_called = []

    monkeypatch.setattr(runs.local, "launch", lambda b, m, p, mem: local_launch_called.append((b, p)))
    monkeypatch.setattr(runs.distributed, "launch", lambda b, m, p, mem: dist_launch_called.append((b, p)))

    runs.launch(body, mock_gguf_model, plan, single_node_members)
    assert len(local_launch_called) == 1
    assert len(dist_launch_called) == 0
    # Verified: network_id is stripped for local launch
    assert getattr(local_launch_called[0][0], "network_id", None) is None


def test_multi_node_auto_deploy_routing(monkeypatch, tmp_path, mock_gguf_model, mock_prima_runtime, multi_node_members):
    """FR-008, SC-003 / T015, T018: Multi-node auto deploy routes to distributed runner."""
    body = RunBody(model=str(mock_gguf_model), prima_auto_deploy=True, context=512)
    layout = {
        "architecture": "llama",
        "n_layers": 32,
        "layers": [{"bytes": 50 * 1024 * 1024} for _ in range(32)],
    }
    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: layout)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.model_support.model_architecture", lambda _: "llama")
    monkeypatch.setattr("kllm.modules.runtimes.interactors.selection.snapshot", lambda: mock_prima_runtime)

    plan = auto_plan(body, multi_node_members, model=mock_gguf_model, runtime=mock_prima_runtime)
    assert len(plan["prima_topology"]["ranks"]) >= 2

    control = MagicMock()
    control.registry.capture.return_value = mock_prima_runtime
    control.root = tmp_path
    authority = SimpleNamespace(
        master_node_id="master",
        cluster_binding_id="00000000-0000-0000-0000-000000000000",
        master_key_id="0" * 64,
        master_term=1,
        master_session_id="00000000-0000-0000-0000-000000000000",
    )
    control.service.channels.master_authority.return_value = authority
    control.service.state().role = "master"
    control.service.state().process_pid = None
    control.rank.owner = None
    control.profiles.jobs = {}
    control.jobs = {}
    control.ports = None

    runs = PrimaRuns(control)
    local_launch_called = []
    dist_launch_called = []

    monkeypatch.setattr(runs.local, "launch", lambda b, m, p, mem: local_launch_called.append((b, p)))
    monkeypatch.setattr(runs.distributed, "launch", lambda b, m, p, mem: dist_launch_called.append((b, p)))

    runs.launch(body, mock_gguf_model, plan, multi_node_members)
    assert len(dist_launch_called) == 1
    assert len(local_launch_called) == 0


def test_auto_staging_worker_dropout_timeout_cleanup(monkeypatch, tmp_path, mock_gguf_model, mock_prima_runtime, multi_node_members):
    """FR-008, SC-003 / T015: Worker dropout during auto-staging cleans up and raises HTTP 504 PRIMA_STAGING_FAILED."""
    body = RunBody(model=str(mock_gguf_model), prima_auto_deploy=True, context=512)
    layout = {
        "architecture": "llama",
        "n_layers": 32,
        "layers": [{"bytes": 50 * 1024 * 1024} for _ in range(32)],
    }
    monkeypatch.setattr("kllm.modules.models.interactors.gguf_meta.read_model_layout", lambda _: layout)
    monkeypatch.setattr("kllm.modules.primacpp.interactors.model_support.model_architecture", lambda _: "llama")
    monkeypatch.setattr("kllm.modules.runtimes.interactors.selection.snapshot", lambda: mock_prima_runtime)

    plan = auto_plan(body, multi_node_members, model=mock_gguf_model, runtime=mock_prima_runtime)

    control = MagicMock()
    control.registry.capture.return_value = mock_prima_runtime
    control.root = tmp_path
    authority = SimpleNamespace(
        master_node_id="master",
        cluster_binding_id="00000000-0000-0000-0000-000000000000",
        master_key_id="0" * 64,
        master_term=1,
        master_session_id="00000000-0000-0000-0000-000000000000",
    )
    control.service.channels.master_authority.return_value = authority
    control.service.state().role = "master"
    control.service.state().node_id = "master"
    control.jobs = {}
    control.runs.cancelled.is_set.return_value = False
    control.closed = False

    cancel_calls = []

    def mock_call(node, run_id, action, body):
        if action == "prima_inventory":
            from kllm.modules.runtimes.entities import body_digest
            rank_info = body["rank"]
            backend = body.get("backend", "vulkan")
            return {
                "node_id": node,
                "runtime_id": mock_prima_runtime["runtime_id"],
                "source_commit": "1" * 40,
                "recipe_digest": "2" * 64,
                "build_id": "fixture",
                "artifacts_digest": "3" * 64,
                "platform": "Windows",
                "build_backend": backend,
                "backend": backend,
                "allocation_digest": body_digest(rank_info),
                "host": {"os": "Windows", "cpu": "CPU", "physical_cores": 4, "logical_cores": 8, "ram_bytes": 10**9},
                "devices": [
                    {
                        "backend": "vulkan",
                        "name": d,
                        "device_identity": "0" * 32,
                        "driver_identity": "driver1",
                        "physical_index": i,
                        "memory_bytes": 10**9,
                    }
                    for i, d in enumerate(rank_info.get("devices") or (["Vulkan0"] if backend == "vulkan" else []))
                ] if backend == "vulkan" else [],
            }
        if action == "prima_prepare":
            return {"job_id": "remote_job_1", "offsets": {}}
        if action == "prima_asset_chunk":
            # Simulate worker timeout / network dropout
            raise TimeoutError("Connection to worker timed out after 60s")
        if action == "prima_cancel":
            cancel_calls.append((node, run_id))
            return {"status": "cancelled"}
        return {}

    control._call = mock_call

    def mock_observe(runtime_id, rank, backend):
        from kllm.modules.runtimes.entities import body_digest
        from kllm.modules.primacpp.interactors.participants import ParticipantEvidence
        return ParticipantEvidence.model_validate({
            "node_id": rank.node_id,
            "runtime_id": runtime_id,
            "source_commit": "1" * 40,
            "recipe_digest": "2" * 64,
            "build_id": "fixture",
            "artifacts_digest": "3" * 64,
            "platform": "Windows",
            "build_backend": backend,
            "backend": backend,
            "allocation_digest": body_digest(rank.model_dump()),
            "host": {"os": "Windows", "cpu": "CPU", "physical_cores": 4, "logical_cores": 8, "ram_bytes": 10**9},
            "devices": [
                {
                    "backend": "vulkan",
                    "name": d,
                    "device_identity": "0" * 32,
                    "driver_identity": "driver1",
                    "physical_index": i,
                    "memory_bytes": 10**9,
                }
                for i, d in enumerate(rank.devices or (["Vulkan0"] if backend == "vulkan" else []))
            ] if backend == "vulkan" else [],
        })
    control.observe_participant.side_effect = mock_observe

    dist = DistributedPrimaRuns(control)
    with pytest.raises(HTTPException) as exc_info:
        dist._auto_stage(body, mock_gguf_model, plan, multi_node_members, mock_prima_runtime)

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail.get("code") == "PRIMA_STAGING_FAILED"
    assert exc_info.value.detail.get("node_id") == "worker1"
    # Temporary files cleaned up and cancel sent
    assert len(cancel_calls) >= 1


def test_backward_compatibility_flag_off(monkeypatch, tmp_path, mock_gguf_model, single_node_members):
    """FR-011, FR-012, SC-004 / T017: When prima_auto_deploy is False or omitted, legacy manual placement executes."""
    body = PlanBody(model=str(mock_gguf_model), prima_auto_deploy=False)
    monkeypatch.setattr("kllm.shared.config.get_feature_flag", lambda flag: False)

    # In legacy mode with non-existent or unconfigured candidate placement, it does not call auto_plan
    assert is_prima_auto_deploy_active(body) is False
