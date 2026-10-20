from __future__ import annotations

import kllm.agent.runtime as agent_runtime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    # Re-import agent state against the temp home
    import importlib

    import kllm.agent as agent_mod
    import kllm.shared.state as state_mod

    importlib.reload(state_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    test_client = TestClient(agent_mod.app)
    yield test_client, agent_mod
    test_client.close()


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
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["members"], int)
    assert "w1" in body.get("cluster_members", {})


def test_generate_join_key_unique():
    from kllm.shared.state import generate_join_key

    a = generate_join_key()
    b = generate_join_key()
    assert a != b
    assert len(a) >= 8


def test_discover_includes_key_id(client, monkeypatch):
    c, agent = client
    from kllm.modules.cluster.interactors.discovery import DiscoveredMaster, join_key_id

    kid = join_key_id("cluster-secret")

    def fake_browse(timeout: float = 2.0, key: str | None = None, trace=None):
        masters = [
            DiscoveredMaster(name="kllm-a", host="192.168.1.10", port=18765, key_id=kid),
            DiscoveredMaster(
                name="kllm-b", host="192.168.1.11", port=18765, key_id="ffffffffffff0000"
            ),
        ]
        if key is not None:
            want = join_key_id(key)
            masters = [m for m in masters if m.key_id == want]
        return masters

    monkeypatch.setattr(agent.discovery, "browse_masters", fake_browse)
    r = c.get("/v1/discover")
    assert r.status_code == 200
    masters = r.json()["masters"]
    assert len(masters) == 2
    assert masters[0]["key_id"] == kid

    r2 = c.get("/v1/discover", params={"key": "cluster-secret"})
    assert r2.status_code == 200
    assert len(r2.json()["masters"]) == 1
    assert r2.json()["masters"][0]["host"] == "192.168.1.10"


@pytest.mark.unit
def test_join_discovers_master_by_key(client, monkeypatch):
    c, agent = client
    monkeypatch.setattr(agent_runtime, "_ensure_master_control_route", lambda *_args: [])
    from unittest.mock import patch

    from kllm.modules.cluster.interactors.discovery import DiscoveredMaster, join_key_id

    kid = join_key_id("secret")

    def fake_browse(timeout: float = 2.0, key: str | None = None, port: int | None = None, trace=None):
        masters = [DiscoveredMaster(name="kllm-m", host="192.168.10.1", port=18765, key_id=kid)]
        if key is not None:
            want = join_key_id(key)
            masters = [m for m in masters if m.key_id == want]
        return masters

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            worker_id = agent.STATE.node_id
            return {
                "ok": True,
                "accepted": True,
                "members": 2,
                "cluster_members": {
                    "kllm-m": {"node_id": "kllm-m", "host": "192.168.10.1", "role": "master"},
                    worker_id: {
                        "node_id": worker_id,
                        "host": "192.168.10.2",
                        "role": "worker",
                    },
                },
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

        def get(self, *a, **k):
            return type(
                "HealthResponse",
                (),
                {
                    "status_code": 200,
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"control_protocol": 2},
                },
            )()

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
        patch.object(agent.native, "start_worker", return_value={"pid": 99}),
        patch("httpx.Client", FakeClient),
    ):
        r = c.post("/v1/join", json={"key": "secret", "overlay": "none"})
    assert r.status_code == 200
    assert r.json()["master"] == "192.168.10.1"
    assert agent.STATE.master_host == "192.168.10.1"


def test_join_no_matching_master_returns_404(client, monkeypatch):
    c, agent = client

    monkeypatch.setattr(
        agent.discovery,
        "browse_masters",
        lambda timeout=2.0, key=None, port=None, trace=None: [],
    )
    r = c.post("/v1/join", json={"key": "missing", "overlay": "none"})
    assert r.status_code == 404
    assert (
        "no matching master" in r.text.lower() or "no matching master" in r.json()["detail"].lower()
    )


def test_join_multiple_matching_masters_returns_409(client, monkeypatch):
    c, agent = client
    from kllm.modules.cluster.interactors.discovery import DiscoveredMaster, join_key_id

    kid = join_key_id("dup-key")

    def fake_browse(timeout: float = 2.0, key: str | None = None, port: int | None = None, trace=None):
        return [
            DiscoveredMaster(name="a", host="10.0.0.1", port=18765, key_id=kid),
            DiscoveredMaster(name="b", host="10.0.0.2", port=18765, key_id=kid),
        ]

    monkeypatch.setattr(agent.discovery, "browse_masters", fake_browse)
    r = c.post("/v1/join", json={"key": "dup-key", "overlay": "none"})
    assert r.status_code == 409
    assert "multiple masters" in r.json()["detail"].lower()
