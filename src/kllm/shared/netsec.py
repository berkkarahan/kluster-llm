"""RPC bind hardening and optional WireGuard overlay helpers."""

from __future__ import annotations

from typing import Any

from kllm.shared.wireguard_config import (
    WG_IFACE as WG_IFACE,
    WG_LISTEN_PORT as WG_LISTEN_PORT,
    WG_PREFIX as WG_PREFIX,
    WG_PSK_INFO as WG_PSK_INFO,
    WG_SUBNET as WG_SUBNET,
    _hkdf_expand as _hkdf_expand,
    _hkdf_extract as _hkdf_extract,
    build_wg_conf as build_wg_conf,
    derive_wg_psk as derive_wg_psk,
    next_wg_address as next_wg_address,
)
from kllm.shared.wireguard_runtime import (
    add_peer_to_running as add_peer_to_running,
    bring_up_wireguard as bring_up_wireguard,
    find_wg_bin as find_wg_bin,
    find_wg_quick as find_wg_quick,
    generate_wg_keypair as generate_wg_keypair,
    load_or_create_keypair as load_or_create_keypair,
    tear_down_wireguard as tear_down_wireguard,
    wg_conf_path as wg_conf_path,
    wg_dir as wg_dir,
    wg_tooling_available as wg_tooling_available,
)
from kllm.modules.cluster.interactors.network_discovery import (
    _IFCONFIG_INET_RE as _IFCONFIG_INET_RE,
    _collect_ifconfig_ipv4 as _collect_ifconfig_ipv4,
    _collect_ipv4_networks_from_ifconfig as _collect_ipv4_networks_from_ifconfig,
    _list_ipv4_addresses as _list_ipv4_addresses,
    _list_ipv4_networks as _list_ipv4_networks,
    _netmask_to_prefix as _netmask_to_prefix,
    _network_for_ip as _network_for_ip,
    _rank_bind_candidate as _rank_bind_candidate,
    _wifi_route_darwin as _wifi_route_darwin,
    _wifi_route_linux as _wifi_route_linux,
    _wifi_route_windows as _wifi_route_windows,
    is_usable_lan_peer as is_usable_lan_peer,
    local_ip_toward as local_ip_toward,
    wifi_path_warning as wifi_path_warning,
)


def pick_rpc_bind_host(preferred: str | None = None, *, toward: str | None = None) -> str:
    """Compatibility boundary preserving injectable host-discovery helpers."""
    from kllm.modules.cluster.interactors.network_discovery import pick_rpc_bind_host as resolve_bind_host

    return resolve_bind_host(
        preferred,
        toward=toward,
        local_ip_resolver=local_ip_toward,
        address_provider=_list_ipv4_addresses,
    )


def is_local_ipv4(ip: str | None) -> bool:
    """Check local address membership through the current discovery adapter."""
    from kllm.modules.cluster.interactors.network_discovery import is_local_ipv4 as check_local_address

    return check_local_address(ip, _list_ipv4_addresses())


def should_apply_rpc_bind_hint(hint: str, current: str, local_ips: list[str] | None = None) -> bool:
    """Compatibility boundary using netsec's injectable address provider."""
    from kllm.modules.cluster.interactors.network_discovery import should_apply_rpc_bind_hint as validate_hint

    return validate_hint(hint, current, local_ips or _list_ipv4_addresses())


OVERLAY_NONE = "none"
OVERLAY_WIREGUARD = "wireguard"
OVERLAY_CHOICES = (OVERLAY_NONE, OVERLAY_WIREGUARD)


def reconcile_rpc_bind(
    bind: str | None, *, toward: str | None, overlay: str
) -> tuple[str | None, str | None]:
    """Replace a persisted bind that is not a local address. Returns (bind, warning)."""
    if overlay == OVERLAY_WIREGUARD:
        return bind, None
    if not bind or bind in ("0.0.0.0", "127.0.0.1"):
        return bind, None
    if is_usable_lan_peer(bind) and is_local_ipv4(bind):
        return bind, None
    new = pick_rpc_bind_host(toward=toward)
    return new, f"rpc_bind {bind} is not a local address; using {new}"


def ensure_rpc_firewall(port: int, bind_host: str) -> dict[str, Any]:
    """Compatibility boundary for platform firewall setup."""
    from kllm.shared.firewall import ensure_rpc_firewall as ensure

    return ensure(port, bind_host)


def refresh_rpc_firewall_warnings(port: int, bind_host: str) -> list[str]:
    """Re-run macOS/Linux/Windows RPC firewall helper; return active warnings only."""
    fw = ensure_rpc_firewall(port, bind_host)
    if fw.get("warning"):
        return [str(fw["warning"])]
    return []


def normalize_overlay(value: str | None) -> str:
    if not value:
        return OVERLAY_NONE
    v = value.strip().lower()
    if v not in OVERLAY_CHOICES:
        raise ValueError(f"overlay must be one of {OVERLAY_CHOICES}, got {value!r}")
    return v
