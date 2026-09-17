from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    # Re-import agent state against the temp home
    import importlib

    import kllm.state as state_mod
    import kllm.agent as agent_mod

    importlib.reload(state_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    return TestClient(agent_mod.app), agent_mod


def test_health(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_join_key_rejects_bad_key(client):
    c, agent = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    r = c.post(
        "/v1/register",
        json={
            "node_id": "w1",
            "host": "10.0.0.2",
            "total_ram_bytes": 1,
            "available_ram_bytes": 1,
            "vram_bytes": 1,
            "backend": "vulkan",
        },
        headers={"X-Kllm-Key": "wrong"},
    )
    assert r.status_code == 401


def test_join_key_accepts_good_key(client):
    c, agent = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    r = c.post(
        "/v1/register",
        json={
            "node_id": "w1",
            "host": "10.0.0.2",
            "total_ram_bytes": 8 * 1024**3,
            "available_ram_bytes": 4 * 1024**3,
            "vram_bytes": 12 * 1024**3,
            "backend": "vulkan",
        },
        headers={"X-Kllm-Key": "secret-key"},
    )
    assert r.status_code == 200
    assert "w1" in agent.STATE.members


def test_generate_join_key_unique():
    from kllm.state import generate_join_key

    a = generate_join_key()
    b = generate_join_key()
    assert a != b
    assert len(a) >= 8
