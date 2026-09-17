"""Plan/run helpers shared by the agent (001-plan-run-params)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm import config as kllm_config
from kllm import models as model_ops
from kllm import native
from kllm.extra_args import ExtraArgsError, sanitize_extra_args
from kllm.run_settings import RunBody, RunSettingsFields
from kllm.state import ensure_home


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
    for m in members.values():
        nodes.append(
            {
                "node_id": m["node_id"],
                "total_ram_bytes": m.get("total_ram_bytes", 0),
                "available_ram_bytes": m.get("available_ram_bytes", 0),
                "vram_bytes": m.get("vram_bytes"),
                "backend": m.get("backend", "unknown"),
                "unified_memory": m.get("backend") == "metal",
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
