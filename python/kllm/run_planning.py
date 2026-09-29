"""Plan/run helpers shared by the agent (001-plan-run-params)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm import config as kllm_config
from kllm import models as model_ops
from kllm import native, http_clients
from kllm.gguf_meta import read_model_layout
from kllm.cluster_log import llama_server_log_path
from kllm.extra_args import ExtraArgsError, sanitize_extra_args
from kllm.model_defaults import merge_defaults_into_extra_args, overlay_user_inference, resolve_model_defaults
from kllm.gguf_meta import read_inference_metadata
from kllm.accel import llama_device_for_backend
from kllm.run_settings import RunBody, RunSettingsFields
from kllm.run_settings import available_threads
from kllm.chat_templates import template_launch
from kllm.state import DEFAULT_CONTROL_PORT, DEFAULT_RPC_PORT

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


def resolve_split_mode(body: RunSettingsFields) -> str:
    """Request override takes precedence over the persistent system setting."""
    return body.split_mode or kllm_config.get_split_mode()


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


def cluster_nodes(members: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    for m in ordered_members(members):
        lat = m.get("latency_ms")
        devices = _member_devices(m)
        nodes.append(
            {
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
            }
        )
    return nodes


def merge_and_sanitize_extra(body: RunSettingsFields) -> list[str]:
    try:
        return sanitize_extra_args(body.extra_args)
    except ExtraArgsError as exc:
        raise HTTPException(400, str(exc)) from exc


def _file_size_bytes(path: str | None) -> int:
    if not path:
        return 0
    try:
        p = Path(path)
        if p.is_file():
            return int(p.stat().st_size)
    except OSError:
        return 0
    return 0


def _parallel_slots_for_estimate(value: int | str) -> int:
    if value == "auto":
        return 1
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def resolve_launch_settings(body: RunSettingsFields) -> dict[str, Any]:
    """Normalize overlapping flags once, before both planning and launch."""
    result = {"parallel_slots": 1 if body.parallel_slots == "auto" else int(body.parallel_slots),
              "batch_size": 512 if body.batch_size == "auto" else int(body.batch_size),
              "micro_batch": 128 if body.micro_batch == "auto" else int(body.micro_batch),
              "context": body.context, "cache_type_k": body.cache_type_k,
              "cache_type_v": body.cache_type_v, "flash_attention": body.flash_attention,
              "threads": body.threads,
              "checkpoints": 32 if body.checkpoints == "auto" else int(body.checkpoints),
               "cache_ram": 8192 if body.cache_ram == "auto" else int(body.cache_ram),
               "split_mode": resolve_split_mode(body)}
    aliases = {"-b": "batch_size", "--batch-size": "batch_size", "-ub": "micro_batch",
               "--ubatch-size": "micro_batch", "-np": "parallel_slots", "--parallel": "parallel_slots",
               "-c": "context", "--ctx-size": "context", "-ctk": "cache_type_k",
               "--cache-type-k": "cache_type_k", "-ctv": "cache_type_v", "--cache-type-v": "cache_type_v",
               "-fa": "flash_attention", "--flash-attn": "flash_attention", "-t": "threads", "--threads": "threads",
               "--ctx-checkpoints": "checkpoints", "-ctxcp": "checkpoints", "--swa-checkpoints": "checkpoints",
               "--cache-ram": "cache_ram", "-cram": "cache_ram"}
    tokens = merge_and_sanitize_extra(body)
    extra = []
    i = 0
    while i < len(tokens):
        key = aliases.get(tokens[i])
        if key:
            if i + 1 == len(tokens):
                raise HTTPException(400, f"missing value for {tokens[i]}")
            value = tokens[i+1]
            try:
                result[key] = int(value) if key in ("parallel_slots", "batch_size", "micro_batch", "context", "threads", "checkpoints", "cache_ram") else value
            except ValueError as exc:
                raise HTTPException(400, f"invalid value for {tokens[i]}") from exc
            i += 2
        else:
            extra.append(tokens[i])
            i += 1
    for key in ("parallel_slots", "batch_size", "micro_batch", "threads"):
        if result[key] < 1:
            raise HTTPException(400, f"{key} must be positive")
    if result["context"] < 512 and not body.context_auto:
        raise HTTPException(400, "context must be >= 512")
    # Deferred import: placement constants are checked only while resolving launch settings.
    from kllm.placement import WIDTHS
    if any(result[k] not in WIDTHS for k in ("cache_type_k", "cache_type_v")):
        raise HTTPException(400, "unsupported cache type")
    if result["flash_attention"] not in ("auto", "on", "off"):
        raise HTTPException(400, "flash attention must be auto, on, or off")
    if result["threads"] > available_threads():
        raise HTTPException(400, f"threads must be 1–{available_threads()} on the master")
    result["micro_batch"] = min(result["micro_batch"], result["batch_size"])
    result["extra_args"] = extra
    return result


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
    settings = resolve_launch_settings(body)
    layout = read_model_layout(path)
    if body.gpu_ids is not None:
        for node in nodes:
            if node["is_master"]:
                node["devices"] = [d for d in node["devices"] if d["index"] in body.gpu_ids]
                if len(node["devices"]) != len(set(body.gpu_ids)):
                    raise HTTPException(400, "gpu_ids includes unavailable devices")
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
    settings["context"] = result.get("context_effective", settings["context"])
    result["split_mode"] = settings["split_mode"]
    validate_split_mode(settings, result)
    result["resolved_launch_settings"] = settings
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
    settings = plan_result.get("resolved_launch_settings") or resolve_launch_settings(body)
    ctx = int(plan_result.get("context_effective") or settings["context"])
    template = template_launch(body.chat_template, body.chat_template_text, body.model)
    # Deferred import: dataclasses.replace is only needed when overlaying model defaults.
    from dataclasses import replace
    model_defaults = resolve_model_defaults(model_path)
    if body.chat_template != "default" or body.chat_template_text:
        model_defaults = replace(model_defaults, chat_template_kwargs={}, reasoning_format=None, jinja=False)
    defaults = overlay_user_inference(
        model_defaults,
        temperature=body.temperature,
        top_p=body.top_p,
        top_k=body.top_k,
        min_p=body.min_p,
        presence_penalty=body.presence_penalty,
        repeat_penalty=body.repeat_penalty,
        enable_thinking=body.enable_thinking,
    )
    merged_extra = merge_defaults_into_extra_args(list(settings["extra_args"]), defaults)
    launch: dict[str, Any] = {
        "model": str(model_path),
        "tensor_split": plan_result["tensor_split_csv"],
        "split_mode": settings["split_mode"],
        "ngl": plan_result["ngl"],
        "context": ctx,
        "threads": settings["threads"],
        # Native start_master extracts micro_batch as i32 (str "2" raises TypeError).
        "micro_batch": settings["micro_batch"],
        "parallel": str(settings["parallel_slots"]),
        "batch_size": str(settings["batch_size"]),
        "cache_type_k": settings["cache_type_k"],
        "cache_type_v": settings["cache_type_v"],
        "load_mode": body.mmap_mlock,
        "ctx_checkpoints": auto_launch_i32(settings["checkpoints"]),
        "cache_ram": str(settings["cache_ram"]),
        "port": infer_port,
        # Prefer plan rpc_endpoints only — never fall back to every joined worker
        # (empty list means local-only; omit --rpc). Legacy callers may still pass
        # rpc_servers when the plan predates the key.
        "rpc_servers": (
            list(plan_result["rpc_endpoints"])
            if "rpc_endpoints" in plan_result
            else list(rpc_servers)
        ),
        "repo_root": repo_root,
        "extra_args": merged_extra + ["--flash-attn", settings["flash_attention"], "--fit", "off"],
        "mmproj_auto": body.vision and not body.mmproj,
    }
    launch.update(defaults.to_launch_dict())
    if body.mmproj:
        launch["mmproj"] = body.mmproj
    if body.draft_model:
        launch["draft_model"] = body.draft_model
    launch.update({k: v for k, v in template.items() if k != "jinja"})
    if "jinja" in template:
        if template["jinja"]:
            launch["jinja"] = True
            launch["extra_args"] = [x for x in launch["extra_args"] if x != "--no-jinja"]
        else:
            launch["jinja"] = False
            launch["extra_args"] = [x for x in launch["extra_args"] if x != "--jinja"] + ["--no-jinja"]
    if "device_order" in plan_result:
        launch["extra_args"] += ["--device", ",".join(plan_result["device_order"]) or "none"]
    elif gpu_env:
        launch["env"] = gpu_env
    launch["log_path"] = str(llama_server_log_path())
    return launch
