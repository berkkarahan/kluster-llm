from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.agent as agent_mod
    import kllm.netsec as netsec_mod
    import kllm.state as state_mod

    importlib.reload(state_mod)
    importlib.reload(netsec_mod)
    importlib.reload(agent_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    test_client = TestClient(agent_mod.app)
    yield test_client, agent_mod, netsec_mod
    test_client.close()


def test_pick_rpc_bind_host_explicit_wins():
    from kllm.netsec import pick_rpc_bind_host

    assert pick_rpc_bind_host("10.1.2.3") == "10.1.2.3"
    assert pick_rpc_bind_host("0.0.0.0") == "0.0.0.0"


def test_pick_rpc_bind_host_default_not_wildcard(monkeypatch):
    from kllm import network_discovery

    monkeypatch.setattr(
        network_discovery, "_list_ipv4_addresses", lambda: ["127.0.0.1", "192.168.10.5", "8.8.8.8"]
    )
    monkeypatch.setattr(network_discovery, "local_ip_toward", lambda remote: None)
    assert network_discovery.pick_rpc_bind_host(None) == "192.168.10.5"


def test_pick_rpc_bind_host_prefers_rfc1918_over_cgnat(monkeypatch):
    from kllm import network_discovery

    monkeypatch.setattr(
        network_discovery,
        "_list_ipv4_addresses",
        lambda: ["100.64.0.1", "192.168.68.66"],
    )
    monkeypatch.setattr(network_discovery, "local_ip_toward", lambda remote: None)
    assert network_discovery.pick_rpc_bind_host(None) == "192.168.68.66"


def test_collect_ifconfig_ipv4_parses_macos_output():
    from kllm import network_discovery as netsec

    sample = """
lo0: flags=8049
	inet 127.0.0.1 netmask 0xff000000
en0: flags=8863
	inet 192.168.68.66 netmask 0xfffffc00 broadcast 192.168.71.255
utun4: flags=8051
	inet 100.64.0.1 --> 100.64.0.1 netmask 0xffff0000
"""
    found: set[str] = set()
    with patch.object(netsec.subprocess, "check_output", return_value=sample):
        netsec._collect_ifconfig_ipv4(found)
    assert "192.168.68.66" in found
    assert "100.64.0.1" in found
    assert "127.0.0.1" in found


def test_pick_rpc_bind_host_toward_beats_lexicographic_private(monkeypatch):
    from kllm import network_discovery

    # 192.168.235.1 sorts before 192.168.68.63 as strings — the old bug.
    monkeypatch.setattr(
        network_discovery,
        "_list_ipv4_addresses",
        lambda: ["192.168.235.1", "192.168.68.63"],
    )
    monkeypatch.setattr(network_discovery, "local_ip_toward", lambda remote: "192.168.68.63")
    assert network_discovery.pick_rpc_bind_host(None, toward="192.168.68.62") == "192.168.68.63"


def test_pick_rpc_bind_host_default_route_beats_vmware_lex_order(monkeypatch):
    from kllm import network_discovery

    monkeypatch.setattr(
        network_discovery,
        "_list_ipv4_addresses",
        lambda: ["192.168.235.1", "192.168.68.63"],
    )

    def fake_toward(remote: str) -> str | None:
        if remote == "8.8.8.8":
            return "192.168.68.63"
        return None

    monkeypatch.setattr(network_discovery, "local_ip_toward", fake_toward)
    assert network_discovery.pick_rpc_bind_host(None) == "192.168.68.63"


def test_is_usable_lan_peer():
    from kllm.netsec import is_usable_lan_peer

    assert is_usable_lan_peer("192.168.68.63")
    assert not is_usable_lan_peer("127.0.0.1")
    assert not is_usable_lan_peer("0.0.0.0")
    assert not is_usable_lan_peer("169.254.1.1")
    assert not is_usable_lan_peer("testclient")
    assert not is_usable_lan_peer(None)


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


def test_firewall_darwin_already_permitted(monkeypatch):
    from kllm import firewall as netsec

    monkeypatch.setattr(netsec, "_resolve_rpc_server_path", lambda: "/opt/rpc-server")
    monkeypatch.setattr(netsec, "_macos_app_firewall_permits", lambda _p: True)
    called = {"run": 0}

    def fail_run(*a, **k):
        called["run"] += 1
        raise AssertionError("should not run socketfilterfw when already permitted")

    monkeypatch.setattr(netsec, "_macos_socketfilterfw_run", fail_run)
    monkeypatch.setattr(netsec.Path, "is_file", lambda _p: True)
    out = netsec._firewall_darwin(50052, "192.168.68.62")
    assert out == {"ok": True, "applied": False, "warning": None}
    assert called["run"] == 0


def test_firewall_darwin_applies_add_and_unblock(monkeypatch):
    from kllm import firewall as netsec

    monkeypatch.setattr(netsec, "_resolve_rpc_server_path", lambda: "/opt/rpc-server")
    permits = {"n": 0}

    def fake_permits(_p):
        permits["n"] += 1
        return permits["n"] > 1

    monkeypatch.setattr(netsec, "_macos_app_firewall_permits", fake_permits)
    calls: list[list[str]] = []

    def fake_run(args):
        calls.append(list(args))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(netsec, "_macos_socketfilterfw_run", fake_run)
    monkeypatch.setattr(netsec.Path, "is_file", lambda _p: True)
    out = netsec._firewall_darwin(50052, "192.168.68.62")
    assert out == {"ok": True, "applied": True, "warning": None}
    assert calls == [["--add", "/opt/rpc-server"], ["--unblockapp", "/opt/rpc-server"]]


def test_firewall_darwin_missing_binary_warns(monkeypatch):
    from kllm import firewall as netsec

    monkeypatch.setattr(netsec, "_resolve_rpc_server_path", lambda: None)
    monkeypatch.setattr(netsec.Path, "is_file", lambda _p: True)
    out = netsec._firewall_darwin(50052, "192.168.68.62")
    assert out["ok"] is True
    assert out["applied"] is False
    assert "192.168.68.62" in out["warning"]
    assert "50052" in out["warning"]


def test_firewall_windows_reuses_existing_rule(monkeypatch):
    from kllm import firewall as netsec

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # First call is existence check → rule present
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(netsec.subprocess, "run", fake_run)
    out = netsec._firewall_windows(50052)
    assert out == {"ok": True, "applied": True, "warning": None}
    assert len(calls) == 1
    assert "Get-NetFirewallRule" in " ".join(calls[0])


def test_firewall_windows_access_denied_hint(monkeypatch):
    from kllm import firewall as netsec

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "Get-NetFirewallRule" in joined:
            return MagicMock(returncode=1, stdout="", stderr="")
        return MagicMock(
            returncode=1,
            stdout="",
            stderr="New-NetFirewallRule : Access is denied.\nFullyQualifiedErrorId : Windows System Error 5",
        )

    monkeypatch.setattr(netsec.subprocess, "run", fake_run)
    out = netsec._firewall_windows(50052)
    assert out["ok"] is False
    assert out["applied"] is False
    assert "Administrator" in out["warning"]
    assert "New-NetFirewallRule" in out["warning"]
    assert "50052" in out["warning"]
    assert "Access is denied" not in out["warning"]


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
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 8,
                "available_ram_bytes": 4,
                "vram_bytes": None,
                "backend": "cpu",
            },
        ),
        patch.object(
            netsec_mod,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
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
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 8,
                "available_ram_bytes": 4,
                "vram_bytes": None,
                "backend": "cpu",
            },
        ),
        patch.object(
            netsec_mod,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.10.1"),
        patch.object(netsec_mod, "wg_tooling_available", return_value=False),
    ):
        r = c.post("/v1/start", json={"key": "k", "overlay": "wireguard"})
    assert r.status_code == 400
    assert "WireGuard" in r.text


def test_join_missing_native_returns_503(client):
    c, agent, netsec_mod = client

    with (
        patch.object(
            agent.native,
            "probe_capacity",
            side_effect=RuntimeError("kllm._native extension is not built"),
        ),
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.68.66"),
    ):
        r = c.post(
            "/v1/join",
            json={
                "key": "secret",
                "master_host": "192.168.68.63",
                "master_port": 18765,
                "overlay": "none",
            },
        )
    assert r.status_code == 503
    assert agent.native.install_recovery_command() in r.text
    if sys.platform != "darwin":
        assert "--backend metal" not in r.text


@pytest.mark.unit
def test_join_binds_private_host_not_wildcard(client, monkeypatch):
    c, agent, netsec_mod = client
    monkeypatch.setattr(agent, "_ensure_master_control_route", lambda *_args: [])
    captured = {}

    def fake_start_worker(**kwargs):
        captured.update(kwargs)
        return {"pid": 4242}

    def fake_pick(preferred, toward=None):
        captured["toward"] = toward
        return "192.168.10.2"

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

        def post(self, *a, **k):
            return FakeResp()

    with (
        patch.object(netsec_mod, "pick_rpc_bind_host", side_effect=fake_pick),
        patch.object(
            netsec_mod,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": True, "warning": None},
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
    assert captured["toward"] == "192.168.10.1"
    assert "host" not in captured
    assert agent.STATE.process_pid is None
    assert r.json()["rpc_bind"] == "192.168.10.2"


@pytest.mark.unit
def test_wireguard_join_uses_master_assignment_for_worker_tunnel(client, monkeypatch):
    c, agent, netsec_mod = client
    monkeypatch.setattr(agent, "_ensure_master_control_route", lambda *_args: [])
    configurations = []
    started = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "members": 2,
                "wg_address": "10.66.87.2",
                "wg_peers": [
                    {"public_key": "MASTERPUBKEY========", "allowed_ips": "10.66.87.1/32"}
                ],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return type(
                "HealthResponse",
                (),
                {
                    "status_code": 200,
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"control_protocol": 2},
                },
            )()

        def post(self, *args, **kwargs):
            return FakeResp()

    def start_worker(**kwargs):
        started.update(kwargs)
        return {"pid": 4242}

    with (
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.10.2"),
        patch.object(netsec_mod, "ensure_rpc_firewall", return_value={"warning": None}),
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
        patch.object(agent.native, "list_gpus", return_value={"backend": "vulkan", "gpus": []}),
        patch.object(agent.native, "start_worker", side_effect=start_worker),
        patch.object(
            netsec_mod, "load_or_create_keypair", return_value=("worker-private", "worker-public")
        ),
        patch.object(netsec_mod, "derive_wg_psk", return_value="shared-psk"),
        patch.object(
            netsec_mod,
            "build_wg_conf",
            side_effect=lambda **kwargs: configurations.append(kwargs) or "wireguard-config",
        ),
        patch.object(netsec_mod, "bring_up_wireguard") as bring_up,
        patch("httpx.Client", FakeClient),
    ):
        response = c.post(
            "/v1/join",
            json={
                "key": "secret",
                "master_host": "192.168.10.1",
                "master_port": 18765,
                "rpc_port": 50052,
                "overlay": "wireguard",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["wg_address"] == "10.66.87.2"
    assert response.json()["overlay_status"] == "up 10.66.87.2"
    assert agent.STATE.wg_address == "10.66.87.2"
    assert started == {}
    assert agent.STATE.rpc_bind == "10.66.87.2"
    assert agent.STATE.process_pid is None
    assert configurations[-1] == {
        "private_key": "worker-private",
        "address": "10.66.87.2",
        "listen_port": netsec_mod.WG_LISTEN_PORT,
        "psk": "shared-psk",
        "peers": [{"public_key": "MASTERPUBKEY========", "allowed_ips": "10.66.87.1/32"}],
    }
    bring_up.assert_called_once_with("wireguard-config")


@pytest.mark.unit
def test_join_honors_rpc_bind_hint_from_master(client, monkeypatch):
    c, agent, netsec_mod = client
    monkeypatch.setattr(agent, "_ensure_master_control_route", lambda *_args: [])
    captured = {}

    def fake_start_worker(**kwargs):
        captured.update(kwargs)
        return {"pid": 4242}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "members": 2, "rpc_bind_hint": "192.168.68.63"}

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

        def post(self, *a, **k):
            return FakeResp()

    with (
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.235.1"),
        patch.object(
            netsec_mod, "_list_ipv4_addresses", return_value=["192.168.235.1", "192.168.68.63"]
        ),
        patch.object(
            netsec_mod,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": True, "warning": None},
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
        patch.object(agent.native, "start_worker", side_effect=fake_start_worker),
        patch("httpx.Client", FakeClient),
    ):
        r = c.post(
            "/v1/join",
            json={
                "key": "secret",
                "master_host": "192.168.68.62",
                "master_port": 18765,
                "rpc_port": 50052,
                "overlay": "none",
            },
        )
    assert r.status_code == 200, r.text
    assert captured == {}
    assert r.json()["rpc_bind"] == "192.168.68.63"
    assert any("corrected" in w for w in (r.json().get("netsec_warnings") or []))


@pytest.mark.unit
def test_join_ignores_rpc_bind_hint_that_is_not_local(client, monkeypatch):
    c, agent, netsec_mod = client
    monkeypatch.setattr(agent, "_ensure_master_control_route", lambda *_args: [])
    captured = {}

    def fake_start_worker(**kwargs):
        captured.update(kwargs)
        return {"pid": 4242}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "members": 2, "rpc_bind_hint": "192.168.68.56"}

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

        def post(self, *a, **k):
            return FakeResp()

    with (
        patch.object(netsec_mod, "pick_rpc_bind_host", return_value="192.168.68.51"),
        patch.object(netsec_mod, "_list_ipv4_addresses", return_value=["192.168.68.51"]),
        patch.object(
            netsec_mod,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": True, "warning": None},
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
        patch.object(agent.native, "start_worker", side_effect=fake_start_worker),
        patch("httpx.Client", FakeClient),
    ):
        r = c.post(
            "/v1/join",
            json={
                "key": "secret",
                "master_host": "192.168.68.59",
                "master_port": 18765,
                "rpc_port": 50052,
                "overlay": "none",
            },
        )
    assert r.status_code == 200, r.text
    assert captured == {}
    assert r.json()["rpc_bind"] == "192.168.68.51"


def test_should_apply_rpc_bind_hint_requires_local_ip():
    from kllm.netsec import should_apply_rpc_bind_hint

    local = ["192.168.68.51"]
    assert should_apply_rpc_bind_hint("192.168.68.51", "192.168.235.1", local)
    assert not should_apply_rpc_bind_hint("192.168.68.56", "192.168.68.51", local)
    assert not should_apply_rpc_bind_hint("192.168.68.51", "192.168.68.51", local)


def test_reconcile_rpc_bind_replaces_foreign_address(monkeypatch):
    from kllm import netsec

    monkeypatch.setattr(netsec, "_list_ipv4_addresses", lambda: ["192.168.68.51"])
    monkeypatch.setattr(
        netsec, "pick_rpc_bind_host", lambda preferred=None, toward=None: "192.168.68.51"
    )
    bind, warn = netsec.reconcile_rpc_bind("192.168.68.56", toward="192.168.68.59", overlay="none")
    assert bind == "192.168.68.51"
    assert warn and "192.168.68.56" in warn


def test_control_client_keeps_idle_sockets():
    from kllm import http_clients

    assert http_clients._CONTROL_LIMITS.keepalive_expiry == 120.0
    http_clients.close()
    client = http_clients.control_client()
    assert client.timeout.connect == 5.0 or client.timeout is not None
    http_clients.close()


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
        patch.object(
            netsec_mod, "load_or_create_keypair", return_value=("priv", "MASTERPUBKEY========")
        ),
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


def test_netmask_to_prefix():
    from kllm import netsec

    assert netsec._netmask_to_prefix("0xfffffc00") == 22
    assert netsec._netmask_to_prefix("255.255.255.0") == 24
    assert netsec._netmask_to_prefix("0xff000000") == 8
    assert netsec._netmask_to_prefix("0xffffffff") == 32
    assert netsec._netmask_to_prefix("not-a-mask") is None


def test_collect_ipv4_networks_from_ifconfig():
    import ipaddress

    from kllm import netsec

    out = (
        "en0: flags=8863<UP,BROADCAST> mtu 1500\n"
        "\tinet 192.168.68.57 netmask 0xfffffc00 broadcast 192.168.71.255\n"
        "en1: flags=8863<UP,BROADCAST> mtu 1500\n"
        "\tinet 10.0.0.5 netmask 0xffffff00 broadcast 10.0.0.255\n"
        "lo0: flags=8049<UP,LOOPBACK>\n"
        "\tinet 127.0.0.1 netmask 0xff000000\n"
        "\tinet6 fe80::1%lo0 prefixlen 64 scopeid 0x1\n"
    )
    nets = netsec._collect_ipv4_networks_from_ifconfig(out)
    assert ipaddress.ip_network("192.168.68.0/22") in nets
    assert ipaddress.ip_network("10.0.0.0/24") in nets


def test_list_ipv4_networks_parses_darwin_ifconfig(monkeypatch):
    import ipaddress

    from kllm import network_discovery as netsec

    monkeypatch.setattr(netsec.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        netsec.subprocess,
        "check_output",
        lambda *a, **k: "inet 192.168.68.57 netmask 0xfffffc00 broadcast 192.168.71.255\n",
    )
    nets = netsec._list_ipv4_networks()
    assert ipaddress.ip_network("192.168.68.0/22") in nets


def test_list_ipv4_networks_parses_linux_ip(monkeypatch):
    import ipaddress

    from kllm import network_discovery as netsec

    monkeypatch.setattr(netsec.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        netsec.subprocess,
        "check_output",
        lambda *a, **k: "2: eno1    inet 192.168.68.57/22 brd 192.168.71.255 scope global eno1\n",
    )
    nets = netsec._list_ipv4_networks()
    assert ipaddress.ip_network("192.168.68.0/22") in nets


def test_list_ipv4_networks_parses_windows(monkeypatch):
    import ipaddress

    from kllm import network_discovery as netsec

    monkeypatch.setattr(netsec.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        netsec.subprocess,
        "check_output",
        lambda *a, **k: "192.168.68.57/22\r\n10.0.0.5/24\r\n",
    )
    nets = netsec._list_ipv4_networks()
    assert ipaddress.ip_network("192.168.68.0/22") in nets
    assert ipaddress.ip_network("10.0.0.0/24") in nets
