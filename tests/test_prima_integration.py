"""Prima.cpp upstream-ring integration contracts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kllm.run_planning import prima_ring_topology
from kllm.run_settings import RunBody


def test_prima_ring_is_master_first_and_workers_are_stable() -> None:
    members = {
        "w-z": {"node_id": "w-z", "role": "worker", "host": "10.0.0.3"},
        "master": {"node_id": "master", "role": "master", "host": "10.0.0.1"},
        "w-a": {"node_id": "w-a", "role": "worker", "host": "10.0.0.2"},
    }
    topology = prima_ring_topology(members)
    assert [(row["rank"], row["node"]["node_id"]) for row in topology] == [
        (0, "master"),
        (1, "w-a"),
        (2, "w-z"),
    ]
    assert [row["next_host"] for row in topology] == ["10.0.0.2", "10.0.0.3", "10.0.0.1"]


def test_prima_requires_distributed_ring() -> None:
    with pytest.raises(Exception, match="at least one worker"):
        prima_ring_topology({"m": {"node_id": "m", "role": "master", "host": "10.0.0.1"}})


def test_prima_settings_are_persistable() -> None:
    body = RunBody(
        model="toy.gguf",
        engine="prima",
        prima_data_port=9100,
        prima_signal_port=10100,
        prima_prefetch=False,
        prima_force_prefetch=True,
        prima_gpu_mem_gb=7.5,
    )
    snap = body.normalized_auto_fields()
    assert snap["engine"] == "prima"
    assert snap["prima_data_port"] == 9100
    assert snap["prima_signal_port"] == 10100
    assert snap["prima_gpu_mem_gb"] == 7.5


@pytest.fixture()
def prima_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib
    import os

    import kllm.agent as agent_mod
    import kllm.auth as auth_mod
    import kllm.state as state_mod

    importlib.reload(state_mod)
    importlib.reload(auth_mod)
    model_dir = Path(os.environ["KLLM_HOME"]) / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "toy.gguf").write_bytes(b"x")
    agent_mod.STATE = state_mod.ClusterState.load()
    agent_mod.STATE.role = "master"
    agent_mod.STATE.node_id = "m"
    agent_mod.STATE.join_key = "test-key"
    agent_mod.STATE.members = {
        "m": {"node_id": "m", "role": "master", "host": "10.0.0.1", "backend": "metal"},
        "w": {"node_id": "w", "role": "worker", "host": "10.0.0.2", "control_port": 18765, "backend": "cuda"},
    }
    return TestClient(agent_mod.app), agent_mod


def test_prima_run_starts_workers_then_rank_zero(prima_client) -> None:
    client, agent = prima_client
    plan = {
        "fit_status": "fits", "warning": None, "tensor_split_csv": "0.5,0.5", "ngl": 1,
        "context_effective": 4096,
    }
    launched = {"pid": 99, "argv": [], "binary": "prima/llama-server", "role": "prima-master"}
    with patch.object(agent, "execute_plan", return_value=plan), patch.object(
        agent, "_start_prima_workers", return_value=[{"ok": True, "rank": 1}]
    ) as workers, patch.object(agent.native, "start_prima_master", return_value=launched) as start, patch.object(
        agent.native, "process_alive", return_value=True
    ):
        response = client.post("/v1/run", json={"model": "toy.gguf", "engine": "prima"})
    assert response.status_code == 200
    assert response.json()["workers"] == [{"ok": True, "rank": 1}]
    workers.assert_called_once()
    start.assert_called_once()
    assert agent.STATE.process_role == "prima-master"
