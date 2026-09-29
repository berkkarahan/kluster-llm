from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kllm.membership import (
    HEARTBEAT_INTERVAL_S,
    LIVENESS_TIMEOUT_S,
    cluster_snapshot,
    on_membership_grow,
    on_membership_shrink,
    sweep_liveness,
)
from kllm.state import ClusterState


STATUS_KEYS = (
    "role",
    "node_id",
    "members",
    "join_key",
    "rpc_bind",
    "overlay",
    "last_plan",
    "inference_up",
    "apply_error",
    "apply_warning",
    "download",
)

REGISTER_BODY = {
    "node_id": "w1",
    "host": "10.0.0.2",
    "control_port": 18765,
    "rpc_port": 50052,
    "total_ram_bytes": 8 * 1024**3,
    "available_ram_bytes": 4 * 1024**3,
    "vram_bytes": 12 * 1024**3,
    "backend": "vulkan",
}


def test_usage_identity_survives_membership_snapshot_and_disconnect():
    state = ClusterState(role="master", node_id="master")
    state.members = {
        "master": {"node_id": "master", "role": "master", "last_seen": 100},
        "w1": {"node_id": "w1", "role": "worker", "host": "same", "process_pid": 12,
               "process_started_at": "2026-09-28T12:00:00Z", "last_seen": 100},
    }
    assert cluster_snapshot(state.members)["w1"]["process_started_at"] == "2026-09-28T12:00:00Z"
    assert sweep_liveness(state, now=121) == ["w1"]
    assert "w1" not in state.members


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.state as state_mod
    import kllm.agent as agent_mod

    importlib.reload(state_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    return TestClient(agent_mod.app), agent_mod


def _start_master(c, agent, *, node_id="testnode"):
    agent.STATE.node_id = node_id
    with (
        patch.object(
            agent.native,
            "list_gpus",
            return_value={"backend": "cpu", "gpus": []},
        ),
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 32 * 1024**3,
                "available_ram_bytes": 16 * 1024**3,
                "vram_bytes": None,
                "backend": "cpu",
            },
        ),
        patch.object(agent.netsec, "pick_rpc_bind_host", return_value="10.0.0.1"),
        patch.object(
            agent.netsec,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(agent.discovery, "MasterAdvertiser") as adv,
    ):
        adv.return_value.start = lambda: None
        adv.return_value.stop = lambda: None
        r = c.post("/v1/start", json={"overlay": "none", "key": "secret-key"})
    assert r.status_code == 200
    return r.json()


def _register_worker(c, *, node_id="w1", key="secret-key"):
    body = {**REGISTER_BODY, "node_id": node_id, "host": f"10.0.0.{node_id[-1] if node_id[-1].isdigit() else 2}"}
    return c.post("/v1/register", json=body, headers={"X-Kllm-Key": key})


def _fake_plan(**overrides):
    base = {
        "tensor_split": [1.0],
        "tensor_split_csv": "1.0000",
        "budgets_bytes": [1],
        "node_ids": ["testnode"],
        "total_budget_bytes": 10_000_000_000,
        "model_size_bytes": 1024,
        "kv_estimate_bytes": 1000,
        "estimated_layers": 32,
        "ngl": 32,
        "split_mode": "layer",
        "device_requested": "gpu",
        "device_effective": "gpu",
        "node_devices": [],
        "fit_status": "fits",
        "estimated_bytes": 5000,
        "budget_bytes": 10_000_000_000,
        "hint": "",
        "context_effective": 4096,
        "warning": None,
    }
    base.update(overrides)
    return base


def test_constants():
    assert HEARTBEAT_INTERVAL_S == 2
    assert LIVENESS_TIMEOUT_S == 20


def test_status_contract_keys(client):
    c, _ = client
    r = c.get("/v1/status")
    assert r.status_code == 200
    data = r.json()
    for key in STATUS_KEYS:
        assert key in data
    assert data["role"] in ("idle", "master", "worker")
    assert data["overlay"] in ("none", "wireguard") or data["overlay"] is None
    assert data["apply_error"] is None
    assert data["apply_warning"] is None


def test_master_only_members_and_stop_clears_apply(client):
    c, agent = client
    _start_master(c, agent)
    r = c.get("/v1/status")
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "master"
    assert data["join_key"] == "secret-key"
    members = data["members"]
    assert agent.STATE.node_id in members
    assert members[agent.STATE.node_id]["role"] == "master"
    assert len(members) >= 1

    agent.STATE.apply_error = "stale"
    agent.STATE.apply_warning = "tight"
    r = c.post("/v1/stop")
    assert r.status_code == 200
    r = c.get("/v1/status")
    data = r.json()
    assert data["role"] == "idle"
    assert data["members"] == {}
    assert data["apply_error"] is None
    assert data["apply_warning"] is None
    assert data["join_key"] is None
    assert data["last_plan"] is None


def test_idle_status_clears_stale_last_plan(client):
    c, agent = client
    agent.STATE.role = "idle"
    agent.STATE.members = {}
    agent.STATE.last_plan = {"node_ids": [agent.STATE.node_id, "phantom"], "tensor_split": [0.6, 0.4]}
    agent.STATE.save()
    r = c.get("/v1/status")
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "idle"
    assert data["last_plan"] is None
    assert agent.STATE.last_plan is None


def test_register_keeps_member_count_adds_cluster_members(client):
    c, agent = client
    _start_master(c, agent)
    r = _register_worker(c)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["members"] == 2
    assert isinstance(body["members"], int)
    assert "cluster_members" in body
    assert "w1" in body["cluster_members"]
    assert agent.STATE.node_id in body["cluster_members"]
    assert "last_plan" in body
    assert "apply_error" in body
    assert "apply_warning" in body
    assert body["cluster_members"]["w1"]["role"] == "worker"
    assert body["cluster_members"]["w1"]["host"]


def test_heartbeat_snapshot_contract(client):
    c, agent = client
    _start_master(c, agent)
    _register_worker(c)
    r = c.post(
        "/v1/heartbeat",
        json=REGISTER_BODY,
        headers={"X-Kllm-Key": "secret-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["accepted"] is True
    assert "w1" in body["cluster_members"]
    assert "last_plan" in body
    assert "apply_error" in body
    assert "apply_warning" in body


def test_failed_join_does_not_write_member(client):
    c, agent = client
    _start_master(c, agent)
    before = dict(agent.STATE.members)

    r = c.post("/v1/register", json=REGISTER_BODY, headers={"X-Kllm-Key": "wrong"})
    assert r.status_code == 401
    assert agent.STATE.members == before
    assert "w1" not in agent.STATE.members

    agent.STATE.role = "idle"
    r = c.post("/v1/register", json=REGISTER_BODY, headers={"X-Kllm-Key": "secret-key"})
    assert r.status_code == 400
    assert "w1" not in agent.STATE.members


def test_join_applies_cluster_members(client, monkeypatch):
    c, agent = client
    from kllm.discovery import DiscoveredMaster, join_key_id

    kid = join_key_id("secret")
    snapshot = {
        "master1": {"node_id": "master1", "host": "192.168.10.1", "role": "master"},
        agent.STATE.node_id: {
            "node_id": agent.STATE.node_id,
            "host": "192.168.10.2",
            "role": "worker",
        },
    }

    def fake_browse(timeout: float = 2.0, key: str | None = None, port: int = 18765):
        return [DiscoveredMaster(name="kllm-m", host="192.168.10.1", port=18765, key_id=kid)]

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "members": 2,
                "cluster_members": snapshot,
                "last_plan": None,
                "apply_error": None,
                "apply_warning": None,
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, *a, **k):
            assert "192.168.10.1:18765" in url
            return FakeResp()

    monkeypatch.setattr(agent.discovery, "browse_masters", fake_browse)
    with (
        patch.object(agent.netsec, "pick_rpc_bind_host", return_value="192.168.10.2"),
        patch.object(
            agent.netsec,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 8,
                "available_ram_bytes": 4,
                "vram_bytes": 1,
                "backend": "vulkan",
            },
        ),
        patch.object(agent.native, "list_gpus", return_value={"backend": "cpu", "gpus": []}),
        patch.object(agent.native, "start_worker", return_value={"pid": 99}),
        patch("httpx.Client", FakeClient),
    ):
        r = c.post("/v1/join", json={"key": "secret", "overlay": "none"})
    assert r.status_code == 200
    status = c.get("/v1/status").json()
    assert status["role"] == "worker"
    assert status["join_key"] is None
    assert set(status["members"]) == set(snapshot)
    assert status["members"][agent.STATE.node_id]["role"] == "worker"


def test_liveness_sweep_drops_stale_worker_not_master(client):
    c, agent = client
    _start_master(c, agent)
    now = time.time()
    agent.STATE.members["w1"] = {
        "node_id": "w1",
        "host": "10.0.0.2",
        "role": "worker",
        "last_seen": now - (LIVENESS_TIMEOUT_S + 1),
    }
    agent.STATE.members["w2"] = {
        "node_id": "w2",
        "host": "10.0.0.3",
        "role": "worker",
        "last_seen": now,
    }
    agent.STATE.save()

    dropped = sweep_liveness(agent.STATE, now=now)
    assert dropped == ["w1"]
    assert "w1" not in agent.STATE.members
    assert "w2" in agent.STATE.members
    assert agent.STATE.node_id in agent.STATE.members

    agent.STATE.members["w1"] = {
        "node_id": "w1",
        "host": "10.0.0.2",
        "role": "worker",
        "last_seen": now - (LIVENESS_TIMEOUT_S + 5),
    }
    r = c.get("/v1/status")
    assert "w1" not in r.json()["members"]
    assert agent.STATE.node_id in r.json()["members"]


def test_heartbeat_rejected_after_drop(client):
    c, agent = client
    _start_master(c, agent)
    r = c.post(
        "/v1/heartbeat",
        json=REGISTER_BODY,
        headers={"X-Kllm-Key": "secret-key"},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is False
    assert "w1" not in r.json()["cluster_members"]


def test_master_join_key_null_on_worker_and_idle_after_leave(client, monkeypatch):
    c, agent = client
    _start_master(c, agent)
    r = c.get("/v1/status")
    assert r.json()["join_key"] == "secret-key"
    assert r.json()["role"] == "master"

    agent.STATE.role = "worker"
    agent.STATE.join_key = "secret-key"
    r = c.get("/v1/status")
    assert r.json()["join_key"] is None
    assert r.json()["role"] == "worker"

    r = c.post("/v1/leave")
    assert r.status_code == 200
    r = c.get("/v1/status")
    assert r.json()["role"] == "idle"
    assert r.json()["members"] == {}
    assert r.json()["join_key"] is None


def test_shrink_fits_restarts_serving(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    models = Path(tmp_path / "kllm-home" / "models")
    models.mkdir(parents=True, exist_ok=True)
    (models / "toy.gguf").write_bytes(b"x" * 1024)

    state = ClusterState(
        role="master",
        node_id="master1",
        join_key="k",
        process_pid=111,
        process_role="master",
        rpc_bind="10.0.0.1",
        overlay="none",
        members={
            "master1": {
                "node_id": "master1",
                "host": "10.0.0.1",
                "role": "master",
                "backend": "cpu",
            },
        },
        last_settings={"model": "toy.gguf", "context": 4096, "threads": 8},
        last_plan=_fake_plan(node_ids=["master1", "w1"]),
    )
    plan = _fake_plan(node_ids=["master1"], fit_status="fits")
    with (
        patch("kllm.membership.execute_plan", return_value=plan) as ep,
        patch("kllm.membership.native.stop_process"),
        patch("kllm.membership.native.start_master", return_value={"pid": 222}) as sm,
        patch("kllm.membership.runtime.clear_children"),
        patch("kllm.membership.runtime.register_child"),
    ):
        on_membership_shrink(state)
    ep.assert_called_once()
    sm.assert_called_once()
    assert state.apply_error is None
    assert state.last_plan["node_ids"] == ["master1"]
    assert "w1" not in state.last_plan["node_ids"]
    assert state.process_pid == 222
    assert state.role == "master"
    assert state.rpc_bind == "10.0.0.1"


def test_shrink_tight_and_overflow(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    models = Path(tmp_path / "kllm-home" / "models")
    models.mkdir(parents=True, exist_ok=True)
    (models / "toy.gguf").write_bytes(b"x" * 1024)

    tight_state = ClusterState(
        role="master",
        node_id="master1",
        join_key="k",
        process_pid=111,
        process_role="master",
        rpc_bind="10.0.0.1",
        overlay="none",
        members={"master1": {"node_id": "master1", "host": "10.0.0.1", "role": "master"}},
        last_settings={"model": "toy.gguf", "context": 4096, "threads": 8},
    )
    tight_plan = _fake_plan(node_ids=["master1"], fit_status="tight", hint="tight against budget", warning="tight against budget")
    with (
        patch("kllm.membership.execute_plan", return_value=tight_plan),
        patch("kllm.membership.native.stop_process"),
        patch("kllm.membership.native.start_master", return_value={"pid": 333}),
        patch("kllm.membership.runtime.clear_children"),
        patch("kllm.membership.runtime.register_child"),
    ):
        on_membership_shrink(tight_state)
    assert tight_state.apply_warning
    assert tight_state.apply_error is None
    assert tight_state.process_pid == 333
    assert tight_state.role == "master"
    assert tight_state.rpc_bind == "10.0.0.1"

    overflow_state = ClusterState(
        role="master",
        node_id="master1",
        join_key="k",
        process_pid=111,
        process_role="master",
        rpc_bind="10.0.0.1",
        overlay="none",
        members={"master1": {"node_id": "master1", "host": "10.0.0.1", "role": "master"}},
        last_settings={"model": "toy.gguf", "context": 4096, "threads": 8},
    )
    overflow_plan = _fake_plan(node_ids=["master1"], fit_status="overflow", hint="does not fit remaining machines")
    with (
        patch("kllm.membership.execute_plan", return_value=overflow_plan),
        patch("kllm.membership.native.stop_process") as sp,
        patch("kllm.membership.native.start_master") as sm,
        patch("kllm.membership.runtime.clear_children"),
    ):
        on_membership_shrink(overflow_state)
    sm.assert_not_called()
    sp.assert_called()
    assert overflow_state.role == "master"
    assert overflow_state.rpc_bind == "10.0.0.1"
    assert overflow_state.join_key == "k"
    assert overflow_state.process_pid is None
    assert overflow_state.apply_error
    assert "does not fit" in overflow_state.apply_error.lower()
    assert overflow_state.last_plan["node_ids"] == ["master1"]


def test_manual_worker_loss_does_not_relaunch_or_show_stale_devices(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    state = ClusterState(role="master", node_id="master1", process_pid=111, process_role="master",
                         members={"master1": {"node_id": "master1", "role": "master"}},
                         last_settings={"model": "toy.gguf", "node_shares": {"master1": .5, "worker": .5}},
                         last_plan={"node_ids": ["worker", "master1"],
                                    "device_allocations": [{"node_id": "worker"}],
                                    "rpc_endpoints": ["worker:50052"]})
    with (patch("kllm.membership.execute_plan", side_effect=ValueError("node shares must match members")),
          patch("kllm.membership.native.stop_process") as stopped,
          patch("kllm.membership.native.start_master") as started,
          patch("kllm.membership.runtime.clear_children")):
        on_membership_shrink(state)
    stopped.assert_called_once()
    started.assert_not_called()
    assert state.process_pid is None
    assert "device_allocations" not in state.last_plan
    assert "rpc_endpoints" not in state.last_plan
    assert state.last_plan["fit_status"] == "overflow"


def test_shrink_plan_only_does_not_start(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    models = Path(tmp_path / "kllm-home" / "models")
    models.mkdir(parents=True, exist_ok=True)
    (models / "toy.gguf").write_bytes(b"x" * 1024)

    state = ClusterState(
        role="master",
        node_id="master1",
        join_key="k",
        process_pid=None,
        process_role=None,
        members={"master1": {"node_id": "master1", "host": "10.0.0.1", "role": "master"}},
        last_settings={"model": "toy.gguf", "context": 4096, "threads": 8},
    )
    plan = _fake_plan(node_ids=["master1"], fit_status="fits")
    with (
        patch("kllm.membership.execute_plan", return_value=plan),
        patch("kllm.membership.native.start_master") as sm,
    ):
        on_membership_shrink(state)
    sm.assert_not_called()
    assert state.process_pid is None
    assert state.last_plan["node_ids"] == ["master1"]
    assert state.apply_error is None

    overflow_state = ClusterState(
        role="master",
        node_id="master1",
        process_pid=None,
        members={"master1": {"node_id": "master1", "host": "10.0.0.1", "role": "master"}},
        last_settings={"model": "toy.gguf", "context": 4096, "threads": 8},
    )
    overflow_plan = _fake_plan(node_ids=["master1"], fit_status="overflow", hint="does not fit")
    with (
        patch("kllm.membership.execute_plan", return_value=overflow_plan),
        patch("kllm.membership.native.start_master") as sm,
    ):
        on_membership_shrink(overflow_state)
    sm.assert_not_called()
    assert overflow_state.process_pid is None
    assert overflow_state.apply_error
    assert "does not fit" in overflow_state.apply_error.lower()


def test_shrink_without_last_settings_is_membership_only(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    state = ClusterState(
        role="master",
        node_id="master1",
        members={"master1": {"node_id": "master1", "host": "h", "role": "master"}},
        last_settings=None,
        last_plan={"node_ids": ["master1", "w1"]},
        process_pid=None,
    )
    with patch("kllm.membership.execute_plan") as ep:
        on_membership_shrink(state)
    ep.assert_not_called()
    assert state.last_plan["node_ids"] == ["master1", "w1"]


def test_grow_clears_stale_shrink_overflow(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    models = Path(tmp_path / "kllm-home" / "models")
    models.mkdir(parents=True, exist_ok=True)
    (models / "toy.gguf").write_bytes(b"x" * 1024)

    state = ClusterState(
        role="master",
        node_id="master1",
        join_key="k",
        process_pid=None,
        members={
            "master1": {
                "node_id": "master1",
                "host": "10.0.0.1",
                "role": "master",
                "vram_bytes": 16 * 1024**3,
                "backend": "vulkan",
            },
            "w1": {
                "node_id": "w1",
                "host": "10.0.0.2",
                "role": "worker",
                "vram_bytes": 38 * 1024**3,
                "backend": "vulkan",
            },
        },
        last_settings={"model": "toy.gguf", "context": 4096, "threads": 8},
        apply_error="does not fit remaining cluster capacity: cluster budget too small: need ~46353 MB (model+KV), have ~16703 MB",
    )
    plan = _fake_plan(
        node_ids=["w1", "master1"],
        fit_status="fits",
        budget_bytes=56_000_000_000,
        estimated_bytes=46_000_000_000,
    )
    with patch("kllm.membership.execute_plan", return_value=plan):
        on_membership_grow(state)
    assert state.apply_error is None
    assert state.last_plan["fit_status"] == "fits"
    assert state.last_plan["node_ids"] == ["w1", "master1"]


def test_register_clears_stale_apply_error(client):
    c, agent = client
    _start_master(c, agent)
    agent.STATE.apply_error = "does not fit remaining cluster capacity: have ~16703 MB"
    agent.STATE.last_settings = {"model": "toy.gguf", "context": 4096, "threads": 8}
    plan = _fake_plan(
        node_ids=[agent.STATE.node_id, "w1"],
        fit_status="fits",
        budget_bytes=56_000_000_000,
    )
    with patch("kllm.membership.execute_plan", return_value=plan):
        r = _register_worker(c)
    assert r.status_code == 200
    assert agent.STATE.apply_error is None
    body = r.json()
    assert body.get("apply_error") is None


def test_unregister_triggers_shrink(client):
    c, agent = client
    _start_master(c, agent)
    _register_worker(c)
    agent.STATE.last_settings = {"model": "toy.gguf", "context": 4096, "threads": 8}
    agent.STATE.process_pid = None
    plan = _fake_plan(node_ids=[agent.STATE.node_id], fit_status="fits")
    with patch("kllm.membership.execute_plan", return_value=plan):
        r = c.post("/v1/unregister", json={"node_id": "w1"}, headers={"X-Kllm-Key": "secret-key"})
    assert r.status_code == 200
    assert "w1" not in agent.STATE.members
    assert agent.STATE.last_plan["node_ids"] == [agent.STATE.node_id]


def test_cluster_snapshot_shapes_node():
    snap = cluster_snapshot(
        {
            "n1": {
                "node_id": "n1",
                "host": "10.0.0.1",
                "role": "master",
                "backend": "metal",
                "gpu_ids": [0],
                "total_ram_bytes": 1,
                "vram_bytes": 2,
            }
        }
    )
    assert snap["n1"]["node_id"] == "n1"
    assert snap["n1"]["host"] == "10.0.0.1"
    assert snap["n1"]["role"] == "master"
    assert snap["n1"]["backend"] == "metal"


def test_worker_heartbeat_unreachable_stays_worker(client):
    c, agent = client
    agent.STATE.role = "worker"
    agent.STATE.master_host = "10.0.0.1"
    agent.STATE.master_port = 18765
    agent.STATE.join_key = "secret-key"
    agent.STATE.members = {
        "m": {"node_id": "m", "host": "10.0.0.1", "role": "master"},
        agent.STATE.node_id: {"node_id": agent.STATE.node_id, "host": "10.0.0.2", "role": "worker"},
    }
    with patch("httpx.Client", side_effect=OSError("down")):
        agent._worker_post_heartbeat()
    assert agent.STATE.role == "worker"
    assert agent.STATE.node_id in agent.STATE.members
    assert any("unreachable" in w for w in (agent.STATE.netsec_warnings or []))


def test_worker_heartbeat_precedes_capacity_refresh(client):
    _, agent = client
    agent.STATE.role = "worker"
    events: list[str] = []

    with (
        patch.object(agent, "_worker_post_heartbeat", side_effect=lambda: events.append("heartbeat")),
        patch.object(agent, "_schedule_local_capacity_refresh", side_effect=lambda: events.append("refresh")),
    ):
        agent._tick_membership()

    assert events == ["heartbeat", "refresh"]


def test_capacity_refresh_allows_only_one_probe(client):
    _, agent = client
    started = []

    class FakeThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            started.append(self.target)

    agent._capacity_refresh_inflight = False
    with patch.object(agent.threading, "Thread", FakeThread):
        agent._schedule_local_capacity_refresh()
        agent._schedule_local_capacity_refresh()

    assert len(started) == 1
    # Finish the captured background task so it resets module state for tests.
    with patch.object(agent, "_refresh_local_capacity"):
        started[0]()
    assert not agent._capacity_refresh_inflight


def test_worker_accepted_false_local_leave(client):
    c, agent = client
    agent.STATE.role = "worker"
    agent.STATE.master_host = "10.0.0.1"
    agent.STATE.master_port = 18765
    agent.STATE.join_key = "secret-key"
    agent.STATE.members = {agent.STATE.node_id: {"node_id": agent.STATE.node_id, "role": "worker"}}
    agent.STATE.process_pid = 5
    agent.STATE.process_role = "worker"

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "ok": True,
                "accepted": False,
                "cluster_members": {"m": {"node_id": "m", "host": "h", "role": "master"}},
                "last_plan": None,
                "apply_error": None,
                "apply_warning": None,
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return FakeResp()

    class FakeControl:
        def post(self, *a, **k):
            return FakeResp()

    with (
        patch.object(agent, "_ensure_master_control_route", return_value=[]),
        patch.object(agent.http_clients, "control_client", return_value=FakeControl()),
        patch.object(agent.native, "stop_process"),
        patch.object(agent.netsec, "tear_down_wireguard"),
    ):
        agent._worker_post_heartbeat()
    assert agent.STATE.role == "idle"
    assert agent.STATE.members == {}
