"""Cluster overlay setup operations shared by lifecycle orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kllm.shared.exceptions import ExternalServiceError, ResourceUnavailableError
from kllm.shared.state import ClusterState


class MasterOverlaySetupService:
    """Configure WireGuard for a master and record its node-facing addresses."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        *,
        tooling_available: Callable[[], bool],
        load_keypair: Callable[[], tuple[str, str]],
        next_address: Callable[..., str],
        derive_psk: Callable[[str], str],
        build_config: Callable[..., str],
        bring_up: Callable[[str], Any],
        listen_port: int,
    ) -> None:
        self._state = state
        self._tooling_available = tooling_available
        self._load_keypair = load_keypair
        self._next_address = next_address
        self._derive_psk = derive_psk
        self._build_config = build_config
        self._bring_up = bring_up
        self._listen_port = listen_port

    def setup(self, join_key: str, lan_host: str) -> None:
        """Bring up the master tunnel and update the local member record."""
        if not self._tooling_available():
            raise ResourceUnavailableError(
                "WireGuard tooling not found (wg). Install WireGuard or use overlay=none"
            )

        state = self._state()
        try:
            private_key, public_key = self._load_keypair()
            wg_address = self._next_address(state.members, master=True)
            psk = self._derive_psk(join_key)
            config = self._build_config(
                private_key=private_key,
                address=wg_address,
                listen_port=self._listen_port,
                psk=psk,
                peers=[],
            )
            self._bring_up(config)
        except Exception as exc:
            raise ExternalServiceError(str(exc)) from exc

        state.wg_address = wg_address
        state.wg_public_key = public_key
        state.overlay_status = f"up {wg_address} (listen {self._listen_port})"
        member = state.members[state.node_id]
        member.update(
            lan_host=lan_host,
            wg_address=wg_address,
            wg_public_key=public_key,
            host=wg_address,
        )


class WorkerOverlaySetupService:
    """Configure the worker tunnel from the address and peers assigned by master."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        *,
        load_keypair: Callable[[], tuple[str, str]],
        derive_psk: Callable[[str], str],
        build_config: Callable[..., str],
        bring_up: Callable[[str], Any],
        listen_port: int,
    ) -> None:
        self._state = state
        self._load_keypair = load_keypair
        self._derive_psk = derive_psk
        self._build_config = build_config
        self._bring_up = bring_up
        self._listen_port = listen_port

    def setup(self, join_key: str, registration: dict[str, Any]) -> str:
        """Bring up the assigned tunnel and record its status on the worker."""
        address = registration.get("wg_address")
        if not isinstance(address, str) or not address:
            raise ValueError(
                "master did not assign a WireGuard address (is master overlay=wireguard?)"
            )
        peers = registration.get("wg_peers") or []
        state = self._state()
        try:
            private_key, _public_key = self._load_keypair()
            psk = self._derive_psk(join_key)
            config = self._build_config(
                private_key=private_key,
                address=address,
                listen_port=self._listen_port,
                psk=psk,
                peers=peers,
            )
            self._bring_up(config)
        except Exception as exc:
            raise ExternalServiceError(str(exc)) from exc
        state.wg_address = address
        state.overlay_status = f"up {address}"
        return address


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
        teardown_wireguard: Callable[[], Any],
        overlay_wireguard: str,
        overlay_none: str,
        default_rpc_port: int,
    ) -> None:
        self._state = state
        self._reconcile_rpc_bind = reconcile_rpc_bind
        self._refresh_firewall_warnings = refresh_firewall_warnings
        self._wifi_path_warning = wifi_path_warning
        self._stop_proxy = stop_proxy
        self._teardown_wireguard = teardown_wireguard
        self._overlay_wireguard = overlay_wireguard
        self._overlay_none = overlay_none
        self._default_rpc_port = default_rpc_port

    def refresh_persisted_warnings(self) -> None:
        """Recompute warnings that may have gone stale across an agent restart."""
        state = self._state()
        if state.role not in ("master", "worker"):
            return

        warnings: list[str] = []
        bind, bind_warning = self._reconcile_rpc_bind(
            state.rpc_bind,
            toward=state.master_host,
            overlay=state.overlay or self._overlay_none,
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
        if state.overlay == self._overlay_wireguard:
            self._teardown_wireguard()
        self.drop_proxy()
        state.rpc_bind = None
        state.overlay = self._overlay_none
        state.wg_address = None
        state.wg_public_key = None
        state.overlay_status = None
        state.netsec_warnings = []
