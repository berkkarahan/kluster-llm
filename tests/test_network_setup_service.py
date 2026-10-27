from __future__ import annotations

import pytest

from kllm.modules.managed_network.interactors.network_setup import NetworkStateService
from kllm.shared.state import ClusterState


def _network_state_service(state: ClusterState, calls: list[str], teardown=None) -> NetworkStateService:
    return NetworkStateService(
        lambda: state,
        reconcile_rpc_bind=lambda bind, **kwargs: ("192.168.1.3", "bind warning"),
        refresh_firewall_warnings=lambda port, bind: [f"firewall {port} {bind}"],
        wifi_path_warning=lambda peer: f"wifi {peer}" if peer else None,
        stop_proxy=lambda: calls.append("stop proxy"),
        teardown_legacy_tunnel=teardown or (lambda: calls.append("teardown legacy tunnel")),
        default_rpc_port=50052,
    )


def test_network_state_service_refreshes_lan_warnings_and_persists_state():
    state = ClusterState(role="worker", master_host="192.168.1.2", rpc_bind="old")
    state.rpc_port = 50053
    calls: list[str] = []
    state.save = lambda: calls.append("save")

    _network_state_service(state, calls).refresh_persisted_warnings()

    assert state.rpc_bind == "192.168.1.3"
    assert state.netsec_warnings == [
        "bind warning", "firewall 50053 192.168.1.3", "wifi 192.168.1.2"
    ]
    assert calls == ["save"]


def test_legacy_state_is_not_silently_rebound():
    state = ClusterState(role="worker", overlay="wireguard", rpc_bind="10.66.87.2")
    calls: list[str] = []
    state.save = lambda: calls.append("save")
    _network_state_service(state, calls).refresh_persisted_warnings()
    assert state.rpc_bind == "10.66.87.2"
    assert "stop/leave" in state.netsec_warnings[0]
    assert calls == ["save"]


def test_network_state_service_reset_tears_down_legacy_tunnel_before_clearing_state():
    state = ClusterState(role="worker", overlay="wireguard", rpc_bind="10.66.87.2")
    state.cp_proxy_port = 19090
    state.wg_address = "10.66.87.2"
    calls: list[str] = []
    _network_state_service(state, calls).reset()
    assert calls == ["teardown legacy tunnel", "stop proxy"]
    assert state.overlay == "none"
    assert state.rpc_bind is None
    assert state.wg_address is None
    assert state.cp_proxy_port is None


def test_legacy_teardown_failure_preserves_state_and_proxy():
    state = ClusterState(role="worker", overlay="wireguard", rpc_bind="10.66.87.2")
    calls: list[str] = []
    def fail():
        raise RuntimeError("stop kllm0 manually")
    with pytest.raises(RuntimeError, match="stop kllm0 manually"):
        _network_state_service(state, calls, teardown=fail).reset()
    assert state.overlay == "wireguard"
    assert state.rpc_bind == "10.66.87.2"
    assert calls == []
