"""Plan/run helpers shared by the agent (001-plan-run-params)."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm import config as kllm_config
from kllm import models as model_ops
from kllm import native
from kllm.extra_args import ExtraArgsError, sanitize_extra_args
from kllm.run_settings import RunBody, RunSettingsFields
from kllm.state import DEFAULT_RPC_PORT, ensure_home

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
    """TCP-check each RPC endpoint; raise HTTPException if any peer is down."""
    unreachable: list[str] = []
    for ep in endpoints:
        host, _, port_s = ep.rpartition(":")
        if not host or not port_s:
            unreachable.append(ep)
            continue
        try:
            port = int(port_s)
        except ValueError:
            unreachable.append(ep)
            continue
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                pass
        except OSError:
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


def cluster_nodes(members: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    for m in ordered_members(members):
        lat = m.get("latency_ms")
        nodes.append(
            {
                "node_id": m["node_id"],
                "total_ram_bytes": m.get("total_ram_bytes", 0),
                "available_ram_bytes": m.get("available_ram_bytes", 0),
                "vram_bytes": m.get("vram_bytes"),
                "backend": m.get("backend", "unknown"),
                "unified_memory": m.get("backend") == "metal",
                "latency_ms": float(lat) if lat is not None else None,
                "role": m.get("role", "worker"),
                "is_master": m.get("role") == "master",
            }
        )
    return nodes


def merge_and_sanitize_extra(body: RunSettingsFields) -> list[str]:
    try:
        return sanitize_extra_args(body.extra_args)
    except ExtraArgsError as exc:
        raise HTTPException(400, str(exc)) from exc


def execute_plan(body: RunSettingsFields, members: dict[str, Any]) -> dict[str, Any]:
    path = model_ops.resolve_local_model(body.model)
    nodes = cluster_nodes(members)
    if not nodes:
        raise HTTPException(400, "no cluster members")
    device = kllm_config.resolve_device(body.device)
    headroom = resolve_headroom(body)
    merge_and_sanitize_extra(body)
    try:
        result = native.plan_split(
            nodes=nodes,
            model_size_bytes=path.stat().st_size,
            context=body.context,
            context_auto=body.context_auto,
            headroom=headroom,
            device=device,
            vram_weight=body.vram_weight,
            latency_weight=body.latency_weight,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    warning = None
    if result.get("fit_status") == "tight":
        warning = result.get("hint") or "Plan is tight against cluster budget"
    result["warning"] = warning
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


def auto_launch_value(value: int | str, *, numeric_fallback: int = 2) -> str:
    if value == "auto":
        return str(numeric_fallback if numeric_fallback > 0 else -1)
    return str(int(value))


def auto_launch_i32(value: int | str) -> str:
    if value == "auto":
        return "-1"
    return str(int(value))


def build_master_launch(
    body: RunBody,
    plan_result: dict[str, Any],
    *,
    model_path: Path,
    rpc_servers: list[str],
    infer_port: int,
    repo_root: str | None,
    gpu_env: dict[str, str],
    extra_args: list[str],
) -> dict[str, Any]:
    ctx = int(plan_result.get("context_effective") or body.context)
    chat_template_file = None
    chat_template = body.chat_template
    if body.chat_template_text:
        chat_template_file = str(ensure_home() / f"chat-template-{body.model}.jinja")
        Path(chat_template_file).write_text(body.chat_template_text, encoding="utf-8")
    launch: dict[str, Any] = {
        "model": str(model_path),
        "tensor_split": plan_result["tensor_split_csv"],
        "ngl": plan_result["ngl"],
        "context": ctx,
        "threads": body.threads,
        "micro_batch": auto_launch_value(body.micro_batch),
        "parallel": auto_launch_i32(body.parallel_slots),
        "batch_size": auto_launch_i32(body.batch_size),
        "cache_type_k": body.cache_type_k,
        "cache_type_v": body.cache_type_v,
        "load_mode": body.mmap_mlock,
        "ctx_checkpoints": auto_launch_i32(body.checkpoints),
        "cache_ram": auto_launch_i32(body.cache_ram),
        "port": infer_port,
        "rpc_servers": rpc_servers,
        "repo_root": repo_root,
        "extra_args": extra_args,
        "mmproj_auto": body.vision and not body.mmproj,
    }
    if body.mmproj:
        launch["mmproj"] = body.mmproj
    if body.draft_model:
        launch["draft_model"] = body.draft_model
    if chat_template_file:
        launch["chat_template_file"] = chat_template_file
    elif chat_template and chat_template != "default":
        launch["chat_template"] = chat_template
    if gpu_env:
        launch["env"] = gpu_env
    return launch
