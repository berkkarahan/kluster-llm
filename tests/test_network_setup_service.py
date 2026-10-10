from __future__ import annotations

import pytest

from kllm.modules.managed_network.interactors.network_setup import MasterOverlaySetupService, NetworkStateService, WorkerOverlaySetupService
from kllm.shared.exceptions import ExternalServiceError, ResourceUnavailableError
from kllm.shared.state import ClusterState


def _network_state_service(state: ClusterState, calls: list[str]) -> NetworkStateService:
    return NetworkStateService(
        lambda: state,
        reconcile_rpc_bind=lambda bind, **kwargs: ("0.0.0.0", "bind warning"),
        refresh_firewall_warnings=lambda port, bind: [f"firewall {port} {bind}"],
        wifi_path_warning=lambda peer: f"wifi {peer}" if peer else None,
        stop_proxy=lambda: calls.append("stop proxy"),
        teardown_wireguard=lambda: calls.append("teardown wireguard"),
        overlay_wireguard="wireguard",
        overlay_none="none",
        default_rpc_port=50052,
    )


def test_master_overlay_service_builds_config_and_records_tunnel_identity():
    state = ClusterState(node_id="master-1", role="master")
    state.members[state.node_id] = {"role": "master", "host": "10.0.0.2"}
    calls: list[object] = []

    def build_config(**kwargs):
        calls.append(("config", kwargs))
        return "wg configuration"

    service = MasterOverlaySetupService(
        lambda: state,
        tooling_available=lambda: True,
        load_keypair=lambda: ("private", "public"),
        next_address=lambda members, *, master: (
            calls.append(("address", members, master)) or "10.44.0.1"
        ),
        derive_psk=lambda key: f"psk:{key}",
        build_config=build_config,
        bring_up=lambda config: calls.append(("up", config)),
        listen_port=51820,
    )

    service.setup("join-secret", "192.168.1.5")

    assert state.wg_address == "10.44.0.1"
    assert state.wg_public_key == "public"
    assert state.overlay_status == "up 10.44.0.1 (listen 51820)"
    assert state.members[state.node_id] == {
        "role": "master",
        "host": "10.44.0.1",
        "lan_host": "192.168.1.5",
        "wg_address": "10.44.0.1",
        "wg_public_key": "public",
    }
    assert calls == [
        ("address", state.members, True),
        (
            "config",
            {
                "private_key": "private",
                "address": "10.44.0.1",
                "listen_port": 51820,
                "psk": "psk:join-secret",
                "peers": [],
            },
        ),
        ("up", "wg configuration"),
    ]


def test_master_overlay_service_reports_missing_wireguard_tooling():
    service = MasterOverlaySetupService(
        lambda: ClusterState(node_id="master-1"),
        tooling_available=lambda: False,
        load_keypair=lambda: ("private", "public"),
        next_address=lambda *_args, **_kwargs: "10.44.0.1",
        derive_psk=lambda _key: "psk",
        build_config=lambda **_kwargs: "config",
        bring_up=lambda _config: None,
        listen_port=51820,
    )

    with pytest.raises(ResourceUnavailableError) as error:
        service.setup("secret", "192.168.1.5")
    assert (
        str(error.value)
        == "WireGuard tooling not found (wg). Install WireGuard or use overlay=none"
    )


def test_master_overlay_service_wraps_tunnel_start_failure():
    state = ClusterState(node_id="master-1", role="master")
    state.members[state.node_id] = {"role": "master"}
    service = MasterOverlaySetupService(
        lambda: state,
        tooling_available=lambda: True,
        load_keypair=lambda: ("private", "public"),
        next_address=lambda *_args, **_kwargs: "10.44.0.1",
        derive_psk=lambda _key: "psk",
        build_config=lambda **_kwargs: "config",
        bring_up=lambda _config: (_ for _ in ()).throw(OSError("permission denied")),
        listen_port=51820,
    )

    with pytest.raises(ExternalServiceError, match="permission denied"):
        service.setup("secret", "192.168.1.5")


def test_worker_overlay_service_uses_assigned_address_and_peer_set():
    state = ClusterState(node_id="worker-1", role="worker")
    calls: list[object] = []
    service = WorkerOverlaySetupService(
        lambda: state,
        load_keypair=lambda: ("worker-private", "worker-public"),
        derive_psk=lambda key: f"psk:{key}",
        build_config=lambda **kwargs: calls.append(("config", kwargs)) or "worker config",
        bring_up=lambda config: calls.append(("up", config)),
        listen_port=51820,
    )

    address = service.setup(
        "join-key",
        {
            "wg_address": "10.44.0.2",
            "wg_peers": [{"public_key": "master-public", "allowed_ips": "10.44.0.1/32"}],
        },
    )

    assert address == "10.44.0.2"
    assert (state.wg_address, state.overlay_status) == ("10.44.0.2", "up 10.44.0.2")
    assert calls == [
        (
            "config",
            {
                "private_key": "worker-private",
                "address": "10.44.0.2",
                "listen_port": 51820,
                "psk": "psk:join-key",
                "peers": [{"public_key": "master-public", "allowed_ips": "10.44.0.1/32"}],
            },
        ),
        ("up", "worker config"),
    ]


def test_worker_overlay_service_rejects_missing_master_assignment():
    service = WorkerOverlaySetupService(
        lambda: ClusterState(node_id="worker-1", role="worker"),
        load_keypair=lambda: ("private", "public"),
        derive_psk=lambda _key: "psk",
        build_config=lambda **_kwargs: "config",
        bring_up=lambda _config: None,
        listen_port=51820,
    )

    with pytest.raises(
        ValueError,
        match="master did not assign a WireGuard address",
    ):
        service.setup("secret", {})


def test_network_state_service_refreshes_warnings_and_persists_state():
    state = ClusterState(role="worker", master_host="192.168.1.2", rpc_bind="old")
    state.rpc_port = 50053
    calls: list[str] = []
    state.save = lambda: calls.append("save")

    _network_state_service(state, calls).refresh_persisted_warnings()

    assert state.rpc_bind == "0.0.0.0"
    assert state.netsec_warnings == [
        "bind warning",
        "firewall 50053 0.0.0.0",
        "wifi 192.168.1.2",
    ]
    assert calls == ["save"]


def test_network_state_service_reset_tears_down_and_clears_network_state():
    state = ClusterState(role="worker", overlay="wireguard")
    state.cp_proxy_port = 19090
    state.cp_proxy_pid = 123
    state.rpc_bind = "0.0.0.0"
    state.wg_address = "10.44.0.2"
    state.wg_public_key = "public"
    state.overlay_status = "up"
    state.netsec_warnings = ["warning"]
    calls: list[str] = []

    _network_state_service(state, calls).reset()

    assert calls == ["teardown wireguard", "stop proxy"]
    assert state.overlay == "none"
    assert state.cp_proxy_port is None
    assert state.cp_proxy_pid is None
    assert state.rpc_bind is None
    assert state.wg_address is None
    assert state.wg_public_key is None
    assert state.overlay_status is None
    assert state.netsec_warnings == []
