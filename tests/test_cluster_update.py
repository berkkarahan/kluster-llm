"""Tests for master-orchestrated worker cluster update."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.agent as agent_mod
    import kllm.cluster_update as update_mod
    import kllm.state as state_mod

    importlib.reload(state_mod)
    importlib.reload(update_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    update_mod.clear_update_progress()
    return TestClient(agent_mod.app), agent_mod, update_mod


def test_choose_rebuild_script_install_when_pin_changes():
    from kllm.cluster_update import choose_rebuild_script

    assert choose_rebuild_script("b1", "b2") == "install"
    assert choose_rebuild_script("b1\n", "b1") == "build"
    assert choose_rebuild_script("", "b9") == "install"


def test_repo_root_respects_env(tmp_path, monkeypatch):
    import kllm.state as state_mod

    repo = tmp_path / "kluster-llm"
    (repo / "crates" / "kllm-native").mkdir(parents=True)
    monkeypatch.setenv("KLLM_REPO_ROOT", str(repo))
    assert state_mod.repo_root() == Path(str(repo))


def test_software_identity_reads_pin(tmp_path):
    from kllm.cluster_update import software_identity

    root = tmp_path / "repo"
    root.mkdir()
    (root / "llama-release").write_text("b12345\n", encoding="utf-8")
    ident = software_identity(root)
    assert ident["llama_release"] == "b12345"
    assert ident["git_head"] is None


def test_fanout_marks_workers_and_calls_request(client, monkeypatch):
    _c, agent, update_mod = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    agent.STATE.members = {
        "w1": {
            "node_id": "w1",
            "host": "192.168.1.2",
            "lan_host": "192.168.1.2",
            "control_port": 18765,
            "role": "worker",
        }
    }

    calls: list[tuple[str, int]] = []

    def fake_request(host, port, join_key, **_kwargs):
        calls.append((host, port))
        assert join_key == "secret-key"
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(update_mod, "request_worker_update", fake_request)
    out = update_mod.fanout_worker_updates(
        list(agent.STATE.members.values()),
        "secret-key",
    )
    assert out["ok"] is True
    assert calls == [("192.168.1.2", 18765)]
    snap = update_mod.get_update_progress()
    assert snap is not None
    assert snap["workers"][0]["phase"] == "accepted"


def test_master_update_endpoint_fans_out(client, monkeypatch):
    c, agent, update_mod = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    agent.STATE.members = {
        "w1": {
            "node_id": "w1",
            "host": "10.0.0.2",
            "lan_host": "10.0.0.2",
            "control_port": 18765,
            "role": "worker",
        }
    }
    monkeypatch.setattr(
        update_mod,
        "request_worker_update",
        lambda *a, **k: {"ok": True, "accepted": True},
    )
    r = c.post("/v1/cluster/update")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["workers"]) == 1


def test_worker_update_requires_join_key(client, monkeypatch, tmp_path):
    c, agent, update_mod = client
    agent.STATE.role = "worker"
    agent.STATE.join_key = "secret-key"
    agent.STATE.master_host = "10.0.0.1"
    agent.STATE.node_id = "w1"

    repo = tmp_path / "repo"
    (repo / "crates" / "kllm-native").mkdir(parents=True)
    (repo / ".git").mkdir()
    monkeypatch.setenv("KLLM_REPO_ROOT", str(repo))
    monkeypatch.setattr(update_mod, "spawn_updater", lambda: 4242)

    r = c.post("/v1/cluster/update", headers={"X-Kllm-Key": "wrong"})
    assert r.status_code == 401

    r = c.post("/v1/cluster/update", headers={"X-Kllm-Key": "secret-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["updater_pid"] == 4242
    reconnect = json.loads(update_mod.reconnect_path().read_text(encoding="utf-8"))
    assert reconnect["join_key"] == "secret-key"
    assert reconnect["master_host"] == "10.0.0.1"


def test_worker_update_rejects_missing_git(client, monkeypatch, tmp_path):
    c, agent, update_mod = client
    agent.STATE.role = "worker"
    agent.STATE.join_key = "secret-key"
    agent.STATE.master_host = "10.0.0.1"
    repo = tmp_path / "repo"
    (repo / "crates" / "kllm-native").mkdir(parents=True)
    monkeypatch.setenv("KLLM_REPO_ROOT", str(repo))
    r = c.post("/v1/cluster/update", headers={"X-Kllm-Key": "secret-key"})
    assert r.status_code == 400
    assert "git" in r.text.lower()


def test_update_progress_auth(client):
    c, agent, _update = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    r = c.post(
        "/v1/cluster/update-progress",
        json={"node_id": "w1", "phase": "pulling"},
        headers={"X-Kllm-Key": "wrong"},
    )
    assert r.status_code == 401

    r = c.post(
        "/v1/cluster/update-progress",
        json={"node_id": "w1", "phase": "pulling"},
        headers={"X-Kllm-Key": "secret-key"},
    )
    assert r.status_code == 200


def test_apply_worker_progress_aggregates(client):
    _c, _agent, update_mod = client
    update_mod.clear_update_progress()
    update_mod._init_update_progress(
        [{"node_id": "w1", "host": "h1"}, {"node_id": "w2", "host": "h2"}]
    )
    update_mod.apply_worker_progress(node_id="w1", phase="done")
    snap = update_mod.apply_worker_progress(node_id="w2", phase="done")
    assert snap is not None
    assert snap["phase"] == "done"
    assert snap["pct"] == 100


def test_git_ensure_clean_rejects_dirty(tmp_path, monkeypatch):
    from kllm import cluster_update as update_mod

    root = tmp_path / "repo"
    root.mkdir()

    def fake_run(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = " M python/kllm/cli.py\n"
        m.stderr = ""
        return m

    monkeypatch.setattr(update_mod.subprocess, "run", fake_run)
    with pytest.raises(update_mod.UpdateError, match="dirty"):
        update_mod._git_ensure_clean(root)


def test_status_includes_update_and_software(client, monkeypatch):
    c, agent, update_mod = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "k"
    update_mod._init_update_progress([])
    monkeypatch.setattr(
        "kllm.agent.native.probe_capacity",
        lambda: {"total_ram_bytes": 1, "available_ram_bytes": 1, "vram_bytes": None, "backend": "cpu"},
    )
    r = c.get("/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert "update" in body
    assert "software" in body
