"""Plan/run helpers shared by the agent (001-plan-run-params)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm import config as kllm_config
from kllm import models as model_ops
from kllm import native, http_clients
from kllm.accel import llama_device_for_backend
from kllm.gguf_meta import read_model_layout
from kllm.run_settings import RunBody, RunSettingsFields
from kllm.core.placement_models import PlacementNodeInput
from kllm.state import DEFAULT_CONTROL_PORT, DEFAULT_RPC_PORT
from kllm.run_settings_resolution import (
    _file_size_bytes as _file_size_bytes,
    estimate_auxiliary_assets as estimate_auxiliary_assets,
    merge_and_sanitize_extra as merge_and_sanitize_extra,
    resolve_launch_settings as resolve_launch_settings,
    resolve_split_mode as resolve_split_mode,
)
from kllm.run_launch import (
    auto_launch_i32 as auto_launch_i32,
    auto_launch_value as auto_launch_value,
    build_master_launch as build_master_launch,
)

# Prefer cluster `host` (WG overlay address when set) over LAN — matches worker RPC bind.
_RPC_CONNECT_TIMEOUT_S = 2.0
_POST_SPAWN_ALIVE_CHECKS = 3
_POST_SPAWN_ALIVE_INTERVAL_S = 0.15


def member_rpc_host(member: dict[str, Any] | None) -> str:
    """Host used for RPC dial and latency probes (host, then lan_host)."""
    m = member or {}
    return str(m.get("host") or m.get("lan_host") or "127.0.0.1")


def ordered_members(members: dict[str, Any]) -> list[dict[str, Any]]:
    """Workers in registration order, then master — matches llama.cpp RPC device order."""
    workers: list[dict[str, Any]] = []
    master: dict[str, Any] | None = None
    for m in members.values():
        src = dict(m or {})
        if src.get("role") == "master":
            master = src
        else:
            workers.append(src)
    out = workers
    if master is not None:
        out = [*workers, master]
    return out


def rpc_server_endpoints(members: dict[str, Any]) -> list[str]:
    endpoints: list[str] = []
    for m in ordered_members(members):
        if m.get("role") == "worker":
            host = member_rpc_host(m)
            endpoints.append(f"{host}:{m.get('rpc_port', DEFAULT_RPC_PORT)}")
    return endpoints


def ensure_rpc_peers_reachable(
    endpoints: list[str], *, timeout_s: float = _RPC_CONNECT_TIMEOUT_S
) -> None:
    """Confirm each worker still has rpc-server tracked.

    Do not raw-TCP-connect :50052. Some llama.cpp rpc-server builds tear down the
    listener after a non-RPC handshake/close, which made `kllm run` kill the
    peers it was probing.
    """
    del timeout_s
    unreachable: list[str] = []
    for ep in endpoints:
        host, _, _port_s = ep.rpartition(":")
        if not host:
            unreachable.append(ep)
            continue
        try:
            # Kept inside the probe try so a missing httpx marks this peer unreachable.
            import httpx

            url = f"http://{host}:{DEFAULT_CONTROL_PORT}/v1/status"
            del httpx
            resp = http_clients.control_client().get(url, timeout=3.0)
            if resp.status_code != 200:
                unreachable.append(ep)
                continue
            data = resp.json()
            if not data.get("process_pid") or data.get("process_role") != "worker":
                unreachable.append(ep)
        except Exception:
            unreachable.append(ep)
    if unreachable:
        joined = ", ".join(unreachable)
        raise HTTPException(
            503,
            f"RPC peer(s) unreachable: {joined}; ensure workers joined with rpc-server running",
        )


def ensure_master_alive_after_spawn(pid: int | None) -> None:
    """Fail Run if llama-server exits immediately after spawn."""
    if not pid:
        raise HTTPException(500, "llama-server failed to start (no pid)")
    for _ in range(_POST_SPAWN_ALIVE_CHECKS):
        if not native.process_alive(int(pid)):
            raise HTTPException(
                500,
                "llama-server exited immediately after start; check RPC peers and model path",
            )
        time.sleep(_POST_SPAWN_ALIVE_INTERVAL_S)
    if not native.process_alive(int(pid)):
        raise HTTPException(
            500,
            "llama-server exited immediately after start; check RPC peers and model path",
        )


def resolve_headroom(body: RunSettingsFields) -> float:
    device = kllm_config.resolve_device(body.device)
    if body.headroom is not None:
        return float(body.headroom)
    if body.vram_percent is not None:
        return kllm_config.resolve_memory_fraction("gpu", override_percent=body.vram_percent)
    if body.ram_percent is not None:
        return kllm_config.resolve_memory_fraction("cpu", override_percent=body.ram_percent)
    return kllm_config.resolve_memory_fraction(device)




def _member_devices(member: dict[str, Any]) -> list[dict[str, Any]]:
    devices = list(member.get("devices") or [])
    if devices:
        return devices
    if not member.get("unified_memory"):
        return devices
    vram = int(member.get("vram_bytes") or 0)
    if vram <= 0:
        return devices
    pref = member.get("requested_backend") or member.get("actual_backend") or member.get("backend")
    device_name, backend = llama_device_for_backend(str(pref or "metal"))
    return [
        {
            "index": 0,
            "device": device_name,
            "name": device_name,
            "backend": backend,
            "vram_bytes": vram,
            "free_bytes": vram,
        }
    ]


def cluster_nodes(members: dict[str, Any]) -> list[PlacementNodeInput]:
    nodes: list[PlacementNodeInput] = []
    for m in ordered_members(members):
        lat = m.get("latency_ms")
        devices = _member_devices(m)
        devices = [
            {**device, "index": device.get("index", index)}
            for index, device in enumerate(devices)
        ]
        nodes.append(
            PlacementNodeInput.from_mapping({
                "node_id": m["node_id"],
                "total_ram_bytes": m.get("total_ram_bytes", 0),
                "available_ram_bytes": m.get("available_ram_bytes", 0),
                "vram_bytes": m.get("vram_bytes"),
                "backend": m.get("backend", "unknown"),
                "unified_memory": m.get("unified_memory", m.get("backend") == "metal"),
                "devices": devices,
                "reclaimable_ram_bytes": m.get("reclaimable_ram_bytes", 0),
                "rpc_endpoint": f"{member_rpc_host(m)}:{m.get('rpc_port', DEFAULT_RPC_PORT)}",
                "latency_ms": float(lat) if lat is not None else None,
                "role": m.get("role", "worker"),
                "is_master": m.get("role") == "master",
            })
        )
    return nodes












def validate_split_mode(settings: dict[str, Any], result: dict[str, Any]) -> None:
    mode = settings["split_mode"]
    backends = {str(a.get("backend")) for a in result.get("device_allocations") or []}
    if mode in ("row", "tensor") and len(backends) > 1:
        raise HTTPException(
            400,
            f"split_mode={mode} requires devices from one backend; use layer for mixed backends",
        )
    if mode in ("row", "tensor") and result.get("rpc_endpoints"):
        raise HTTPException(
            400,
            f"split_mode={mode} is not supported with RPC workers; use layer",
        )
    if mode == "tensor":
        cache_types = {settings["cache_type_k"], settings["cache_type_v"]}
        if not cache_types <= {"f32", "f16", "bf16"}:
            raise HTTPException(
                400,
                "split_mode=tensor requires f32, f16, or bf16 KV cache types",
            )
        if settings["flash_attention"] == "off":
            raise HTTPException(400, "split_mode=tensor requires Flash Attention")


def execute_plan(body: RunSettingsFields, members: dict[str, Any]) -> dict[str, Any]:
    path = model_ops.resolve_local_model(body.model)
    nodes = cluster_nodes(members)
    if not nodes:
        raise HTTPException(400, "no cluster members")
    device = kllm_config.resolve_device(body.device)
    headroom = resolve_headroom(body)
    resolved_settings = estimate_auxiliary_assets(body, resolve_launch_settings(body))
    settings = resolved_settings.to_mapping()
    layout = read_model_layout(path)
    if body.gpu_ids is not None:
        for index, node in enumerate(nodes):
            if node.is_master:
                selected_devices = tuple(d for d in node.devices if d.index in body.gpu_ids)
                if len(selected_devices) != len(set(body.gpu_ids)):
                    raise HTTPException(400, "gpu_ids includes unavailable devices")
                nodes[index] = PlacementNodeInput(
                    node_id=node.node_id,
                    is_master=node.is_master,
                    backend=node.backend,
                    total_ram_bytes=node.total_ram_bytes,
                    available_ram_bytes=node.available_ram_bytes,
                    reclaimable_ram_bytes=node.reclaimable_ram_bytes,
                    unified_memory=node.unified_memory,
                    rpc_endpoint=node.rpc_endpoint,
                    latency_ms=node.latency_ms,
                    devices=selected_devices,
                )
    try:
        result = native.plan_split(
            nodes=nodes,
            model_size_bytes=path.stat().st_size,
            context=settings["context"],
            context_auto=body.context_auto,
            headroom=headroom,
            device=device,
            vram_weight=body.vram_weight,
            latency_weight=body.latency_weight,
            cache_type_k=settings["cache_type_k"],
            cache_type_v=settings["cache_type_v"],
            parallel_slots=settings["parallel_slots"],
            model_layout=layout,
            launch_settings=settings,
            mmproj_size_bytes=_file_size_bytes(body.mmproj),
            draft_model_size_bytes=_file_size_bytes(body.draft_model),
            vision=bool(body.vision),
            node_shares=body.node_shares,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    warning = None
    if result.get("fit_status") == "tight":
        warning = result.get("hint") or "Plan is tight against cluster budget"
    result["warning"] = warning
    # Deferred import: placement normalization belongs to the planning path.
    from kllm.placement import PlacementSettings, effective_launch_settings

    typed_settings = effective_launch_settings(
        PlacementSettings.from_mapping(settings),
        result.get("context_effective", settings["context"]),
    )
    result["split_mode"] = typed_settings.split_mode
    serialized_settings = dict(settings)
    serialized_settings.update(typed_settings.to_mapping())
    validate_split_mode(serialized_settings, result)
    result["resolved_launch_settings"] = {
        key: value for key, value in serialized_settings.items() if not key.startswith("_")
    }
    return result


def validate_vision_for_run(body: RunBody) -> None:
    if not body.vision:
        return
    if body.mmproj:
        p = Path(body.mmproj)
        if p.is_file():
            return
    raise HTTPException(
        400,
        "vision is enabled but no usable mmproj path was provided; set mmproj or disable vision",
    )
