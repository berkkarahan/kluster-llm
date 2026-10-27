"""Cluster LAN network state operations shared by lifecycle orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kllm.shared.state import ClusterState


class NetworkStateService:
    """Reconcile persisted network warnings and clear runtime network state."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        *,
        reconcile_rpc_bind: Callable[..., tuple[str | None, str | None]],
        refresh_firewall_warnings: Callable[[int, str], list[str]],
        wifi_path_warning: Callable[[str | None], str | None],
        stop_proxy: Callable[[], Any],
        teardown_legacy_tunnel: Callable[[], None],
        default_rpc_port: int,
    ) -> None:
        self._state = state
        self._reconcile_rpc_bind = reconcile_rpc_bind
        self._refresh_firewall_warnings = refresh_firewall_warnings
        self._wifi_path_warning = wifi_path_warning
        self._stop_proxy = stop_proxy
        self._teardown_legacy_tunnel = teardown_legacy_tunnel
        self._default_rpc_port = default_rpc_port

    def refresh_persisted_warnings(self) -> None:
        """Recompute warnings that may have gone stale across an agent restart."""
        state = self._state()
        if state.role not in ("master", "worker"):
            return
        if state.overlay == "wireguard":
            state.netsec_warnings = [
                "Legacy WireGuard cluster: stop/leave it on each node before starting a LAN-only cluster"
            ]
            state.save()
            return

        warnings: list[str] = []
        bind, bind_warning = self._reconcile_rpc_bind(
            state.rpc_bind,
            toward=state.master_host,
        )
        if bind_warning:
            state.rpc_bind = bind
            warnings.append(bind_warning)

        bind = state.rpc_bind
        if bind:
            warnings.extend(
                self._refresh_firewall_warnings(state.rpc_port or self._default_rpc_port, bind)
            )

        peer = state.master_host if state.role == "worker" else None
        if state.role == "master":
            for member in state.members.values():
                if (member or {}).get("role") == "master":
                    continue
                peer = (member or {}).get("lan_host") or (member or {}).get("host")
                if peer:
                    break
        wifi_warning = self._wifi_path_warning(peer)
        if wifi_warning:
            warnings.append(wifi_warning)

        state.netsec_warnings = warnings
        state.save()

    def drop_proxy(self) -> None:
        """Stop the control-plane proxy and clear its persisted handle."""
        state = self._state()
        self._stop_proxy()
        state.cp_proxy_port = None
        state.cp_proxy_pid = None

    def reset(self) -> None:
        """Tear down network helpers and return network state to idle defaults."""
        state = self._state()
        if state.overlay == "wireguard":
            self._teardown_legacy_tunnel()
        self.drop_proxy()
        state.rpc_bind = None
        state.overlay = "none"
        state.wg_address = None
        state.wg_public_key = None
        state.overlay_status = None
        state.netsec_warnings = []
