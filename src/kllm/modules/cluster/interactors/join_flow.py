"""Join steps that do not read agent module globals tests patch."""

from __future__ import annotations

import platform
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException

from kllm.modules.cluster.interactors import discovery
from kllm.shared import netsec
from kllm.shared.config import CONTROL_PROTOCOL_VERSION
from kllm.shared.state import DEFAULT_CONTROL_PORT
from kllm.modules.cluster.presenters.heartbeat_payload import local_hostname


def require_join_overlay(overlay_raw: str) -> str:
    """Normalize a join overlay and reject WireGuard when the tools are missing."""
    try:
        overlay = netsec.normalize_overlay(overlay_raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if overlay == netsec.OVERLAY_WIREGUARD and not netsec.wg_tooling_available():
        raise HTTPException(
            400,
            "WireGuard tooling not found (wg). Install WireGuard or use overlay=none",
        )
    return overlay


def resolve_master_endpoint(master_host: str | None, master_port: int, key: str) -> tuple[str, int]:
    """Use the given master, or the single LAN master that matches the join key."""
    if master_host:
        return master_host, master_port
    try:
        found = discovery.browse_masters(
            timeout=2.5, key=key, port=master_port or DEFAULT_CONTROL_PORT
        )
    except Exception as exc:
        raise HTTPException(
            502,
            f"master discovery failed: {exc}; pass master_host or check the join key",
        ) from exc
    if not found:
        raise HTTPException(
            404,
            "no matching master discovered on LAN; pass master_host or check join key",
        )
    if len(found) > 1:
        hosts = ", ".join(f"{m.host}:{m.port}" for m in found)
        raise HTTPException(
            409,
            f"multiple masters match join key; pass master_host ({hosts})",
        )
    return found[0].host, found[0].port


def build_register_payload(
    *,
    node_id: str,
    lan_host: str,
    control_port: int,
    rpc_port: int,
    cap: dict[str, Any],
    vram: Any,
    backend: str,
    gpus: list[dict[str, Any]],
    gpu_ids: list[int] | None,
    wg_public_key: str | None,
    git_head: Any,
    llama_release: Any,
) -> dict[str, Any]:
    """Build the typed cluster-node registration payload."""
    return {
        "node_id": node_id,
        "host": lan_host,
        "hostname": local_hostname(),
        "lan_host": lan_host,
        "control_port": control_port,
        "rpc_port": rpc_port,
        "total_ram_bytes": cap["total_ram_bytes"],
        "available_ram_bytes": cap["available_ram_bytes"],
        "vram_bytes": vram,
        "backend": backend,
        "actual_backend": backend,
        "requested_backend": cap.get("requested_backend"),
        "devices": gpus,
        "unified_memory": cap.get("unified_memory", False),
        "binary_version": cap.get("binary_version"),
        "gpu_ids": gpu_ids,
        "wg_public_key": wg_public_key,
        "wg_listen_port": netsec.WG_LISTEN_PORT,
        "git_head": git_head,
        "llama_release": llama_release,
        "platform": platform.system() or None,
        "architecture": platform.machine() or None,
    }


def post_register(
    base: str,
    payload: dict[str, Any],
    key: str,
    master_host: str,
    master_port: int,
    on_transport_error: Callable[[], None],
) -> dict[str, Any]:
    """Negotiate the peer protocol then register through the v2 resource API."""
    try:
        with httpx.Client(timeout=15.0) as client:
            health = client.get(f"{base}/health")
            health.raise_for_status()
            try:
                health_payload = health.json()
            except ValueError as exc:
                raise HTTPException(502, "master returned an invalid health response") from exc
            if not isinstance(health_payload, dict):
                raise HTTPException(502, "master returned an invalid health response")
            peer_protocol = health_payload.get("control_protocol")
            if peer_protocol != CONTROL_PROTOCOL_VERSION:
                raise HTTPException(
                    426,
                    "incompatible master control protocol; upgrade the master and all "
                    "cluster nodes together",
                )
            resp = client.post(
                f"{base}/v2/cluster/nodes/register",
                json=payload,
                headers={"X-Kllm-Key": key},
            )
    except httpx.TimeoutException as exc:
        on_transport_error()
        raise HTTPException(
            504,
            f"master at {master_host}:{master_port} did not respond in time; "
            f"ensure `kllm start --key …` is running on the master and try "
            f"`curl http://{master_host}:{master_port}/health` from this machine",
        ) from exc
    except httpx.RequestError as exc:
        on_transport_error()
        detail = (
            f"cannot reach master at {master_host}:{master_port}: {exc}; "
            f"ensure `kllm start --key …` is running on the master and try "
            f"`curl http://{master_host}:{master_port}/health` from this machine"
        )
        err_text = str(exc).lower()
        if "connection reset" in err_text or "errno 54" in err_text:
            detail += (
                ". Connection reset usually means the master agent is down, "
                "crashed, or a firewall closed TCP "
                f"{master_port} (not macOS Local Network / errno 65)"
            )
        raise HTTPException(502, detail) from exc
    if resp.status_code == 401:
        raise HTTPException(401, "invalid join key")
    if resp.status_code >= 400:
        try:
            err_payload = resp.json()
            detail = err_payload.get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError as exc:
        raise HTTPException(502, "master returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise HTTPException(502, "master returned an invalid response")
    return payload


def cached_llama_pack_warning(backend: str) -> str | None:
    """Install a cached llama pack. Return a warning, or None when nothing should be recorded."""
    try:
        from kllm.shared.llama_pack import PackNotInstallable, install_cached_llama_pack

        install_cached_llama_pack(backend=str(backend), force=False)
    except PackNotInstallable as exc:
        return str(exc)
    except Exception:
        return None
    return None
