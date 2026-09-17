from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.state as state_mod
    import kllm.agent as agent_mod
    import kllm.netsec as netsec_mod

    importlib.reload(state_mod)
    importlib.reload(netsec_mod)
    importlib.reload(agent_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    return TestClient(agent_mod.app), agent_mod, netsec_mod


def test_pick_rpc_bind_host_explicit_wins():
    from kllm.netsec import pick_rpc_bind_host

    assert pick_rpc_bind_host("10.1.2.3") == "10.1.2.3"
    assert pick_rpc_bind_host("0.0.0.0") == "0.0.0.0"


def test_pick_rpc_bind_host_default_not_wildcard(monkeypatch):
    from kllm import netsec

    monkeypatch.setattr(netsec, "_list_ipv4_addresses", lambda: ["127.0.0.1", "192.168.10.5", "8.8.8.8"])
    assert netsec.pick_rpc_bind_host(None) == "192.168.10.5"


def test_derive_wg_psk_stable():
    from kllm.netsec import derive_wg_psk

    a = derive_wg_psk("cluster-secret")
    b = derive_wg_psk("cluster-secret")
    c = derive_wg_psk("other-secret")
    assert a == b
    assert a != c
    # WireGuard PSK is 32 bytes → 44 chars base64 with padding
    assert len(a) == 44


def test_normalize_overlay():
    from kllm.netsec import normalize_overlay

    assert normalize_overlay(None) == "none"
    assert normalize_overlay("WireGuard") == "wireguard"
    with pytest.raises(ValueError):
        normalize_overlay("tailscale")


def test_next_wg_address_skips_used():
    from kllm.netsec import next_wg_address

    assert next_wg_address({}, master=True) == "10.66.87.1"
    members = {"a": {"wg_address": "10.66.87.2"}, "b": {"wg_address": "10.66.87.3"}}
    assert next_wg_address(members, master=False) == "10.66.87.4"


def test_status_includes_netsec_fields(client):
    c, agent, _ = client
    agent.STATE.rpc_bind = "192.168.10.2"
    agent.STATE.overlay = "none"
    agent.STATE.overlay_status = "disabled"
    r = c.get("/v1/status")
    assert r.status_code == 200
    data = r.json()
    assert data["rpc_bind"] == "192.168.10.2"
    assert data["overlay"] == "none"
    assert data["overlay_status"] == "disabled"
    assert "netsec_warnings" in data


def test_start_sets_rpc_bind_not_wildcard(client):
    c, agent, netsec_mod = client

    class FakeAdv:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    with (
        patch.object(agent.discovery, "MasterAdvertiser", FakeAdv),
        patch.object(agent.native, "probe_capacity", return_value={
            "total_ram_bytes": 8,
            "available_ram_bytes": 4,
            "vram_bytes": None,
            "backend": "cpu",
        }),
        patch.object(netsec_mod, "ensure_rpc_firewall", return_value={"ok": True, "applied": False, "warning": None}),
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.10.1"),
    ):
        r = c.post("/v1/start", json={"key": "k", "overlay": "none"})
    assert r.status_code == 200
    data = r.json()
    assert data["rpc_bind"] == "192.168.10.1"
    assert data["overlay"] == "none"
    assert agent.STATE.members[agent.STATE.node_id]["host"] == "192.168.10.1"
    assert agent.STATE.members[agent.STATE.node_id]["lan_host"] == "192.168.10.1"


def test_start_wireguard_fails_without_tooling(client):
    c, agent, netsec_mod = client

    class FakeAdv:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    with (
        patch.object(agent.discovery, "MasterAdvertiser", FakeAdv),
        patch.object(agent.native, "probe_capacity", return_value={
            "total_ram_bytes": 8,
            "available_ram_bytes": 4,
            "vram_bytes": None,
            "backend": "cpu",
        }),
        patch.object(netsec_mod, "ensure_rpc_firewall", return_value={"ok": True, "applied": False, "warning": None}),
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.10.1"),
        patch.object(netsec_mod, "wg_tooling_available", return_value=False),
    ):
        r = c.post("/v1/start", json={"key": "k", "overlay": "wireguard"})
    assert r.status_code == 400
    assert "WireGuard" in r.text


def test_join_binds_private_host_not_wildcard(client):
    c, agent, netsec_mod = client
    captured = {}

    def fake_start_worker(**kwargs):
        captured.update(kwargs)
        return {"pid": 4242}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "members": 2}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return FakeResp()

    with (
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.10.2"),
        patch.object(netsec_mod, "ensure_rpc_firewall", return_value={"ok": True, "applied": True, "warning": None}),
        patch.object(agent.native, "probe_capacity", return_value={
            "total_ram_bytes": 8,
            "available_ram_bytes": 4,
            "vram_bytes": 1,
            "backend": "vulkan",
        }),
        patch.object(agent.native, "start_worker", side_effect=fake_start_worker),
        patch("httpx.Client", FakeClient),
    ):
        r = c.post(
            "/v1/join",
            json={
                "key": "secret",
                "master_host": "192.168.10.1",
                "master_port": 18765,
                "rpc_port": 50052,
                "overlay": "none",
            },
        )
    assert r.status_code == 200
    assert captured["host"] == "192.168.10.2"
    assert captured["host"] != "0.0.0.0"
    assert r.json()["rpc_bind"] == "192.168.10.2"


def test_register_assigns_wg_when_overlay_on(client):
    c, agent, netsec_mod = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    agent.STATE.overlay = "wireguard"
    agent.STATE.wg_address = "10.66.87.1"
    agent.STATE.wg_public_key = "MASTERPUBKEY========"
    agent.STATE.members[agent.STATE.node_id] = {
        "node_id": agent.STATE.node_id,
        "host": "10.66.87.1",
        "lan_host": "192.168.10.1",
        "role": "master",
    }

    with (
        patch.object(netsec_mod, "add_peer_to_running", return_value={"ok": True}),
        patch.object(netsec_mod, "bring_up_wireguard", return_value={"ok": True}),
        patch.object(netsec_mod, "load_or_create_keypair", return_value=("priv", "MASTERPUBKEY========")),
    ):
        r = c.post(
            "/v1/register",
            json={
                "node_id": "w1",
                "host": "192.168.10.2",
                "lan_host": "192.168.10.2",
                "wg_public_key": "WORKERPUBKEY========",
                "total_ram_bytes": 1,
                "available_ram_bytes": 1,
                "vram_bytes": 1,
                "backend": "vulkan",
            },
            headers={"X-Kllm-Key": "secret-key"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["wg_address"] == "10.66.87.2"
    assert agent.STATE.members["w1"]["host"] == "10.66.87.2"
    assert agent.STATE.members["w1"]["lan_host"] == "192.168.10.2"
    assert data["wg_peers"][0]["public_key"] == "MASTERPUBKEY========"
