"""Build the native master launch request from a validated plan."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from kllm.chat_templates import template_launch
from kllm.cluster_log import llama_server_log_path
from kllm.model_defaults import (
    merge_defaults_into_extra_args,
    overlay_user_inference,
    resolve_model_defaults,
)
from kllm.models.resolved_settings import ResolvedLaunchSettings
from kllm.run_settings import RunBody, cpu_batch_threads
from kllm.effective_config import deployment_defaults


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
    """Create the argument record consumed by the native process adapter."""
    raw_settings = plan_result.get("resolved_launch_settings")
    if raw_settings is None:
        from kllm.run_settings_resolution import resolve_launch_settings

        settings = resolve_launch_settings(body)
    else:
        settings = ResolvedLaunchSettings.from_mapping(raw_settings)
    context = int(plan_result.get("context_effective") or settings.context)
    template = template_launch(body.chat_template, body.chat_template_text, body.model)
    model_defaults = deployment_defaults(body, resolve_model_defaults(model_path))
    if body.chat_template != "default" or body.chat_template_text:
        model_defaults = replace(
            model_defaults, chat_template_kwargs={}, reasoning_format=None, jinja=False
        )
    defaults = overlay_user_inference(
        model_defaults,
        temperature=body.temperature,
        top_p=body.top_p,
        top_k=body.top_k,
        min_p=body.min_p,
        presence_penalty=body.presence_penalty,
        repeat_penalty=body.repeat_penalty,
        frequency_penalty=body.frequency_penalty,
        enable_thinking=body.enable_thinking,
    )
    merged_extra = merge_defaults_into_extra_args(list(settings.extra_args), defaults)
    for name, flag in (
        ("threads_batch", "--threads-batch"), ("seed", "--seed"),
        ("max_output_tokens", "--n-predict"),
        ("reasoning_effort", "--reasoning-effort"), ("reasoning_budget", "--reasoning-budget"),
        ("reasoning_format", "--reasoning-format"),
    ):
        value = getattr(body, name)
        if value is not None:
            if flag in merged_extra:
                index = merged_extra.index(flag)
                del merged_extra[index:index + 2]
            merged_extra += [flag, str(value)]
    if body.threads_batch is None and "--threads-batch" not in merged_extra:
        # Unset means "same as --threads" in llama-server, which keeps prefill
        # on the generation cap. Physical cores are faster for CPU prompt work.
        merged_extra += ["--threads-batch", str(cpu_batch_threads())]
    launch: dict[str, Any] = {
        "model": str(model_path),
        "tensor_split": plan_result["tensor_split_csv"],
        "split_mode": settings.split_mode,
        "ngl": plan_result["ngl"],
        "context": context,
        "threads": settings.threads,
        # Native start_master extracts micro_batch as i32 (str "2" raises TypeError).
        "micro_batch": settings.micro_batch,
        "parallel": str(settings.parallel_slots),
        "batch_size": str(settings.batch_size),
        "cache_type_k": settings.cache_type_k,
        "cache_type_v": settings.cache_type_v,
        "load_mode": body.mmap_mlock,
        "ctx_checkpoints": auto_launch_i32(settings.checkpoints),
        "cache_ram": str(settings.cache_ram),
        "port": infer_port,
        # Empty planned endpoints means local-only and must suppress --rpc.
        "rpc_servers": (
            list(plan_result["rpc_endpoints"])
            if "rpc_endpoints" in plan_result
            else list(rpc_servers)
        ),
        "repo_root": repo_root,
        "extra_args": merged_extra
        + [
            "--flash-attn",
            settings.flash_attention,
            "--fit",
            "off",
            "--kv-unified" if settings.kv_unified else "--no-kv-unified",
        ],
        "mmproj_auto": body.vision and not body.mmproj,
    }
    launch.update(defaults.to_launch_dict())
    if settings.swa_full:
        launch["extra_args"] += ["--swa-full"]
    if settings.speculative != "auto":
        launch["extra_args"] += ["--spec-type", settings.speculative]
    if body.draft_enabled():
        launch["extra_args"] += [
            "--spec-draft-n-max",
            str(settings.draft_max),
            "--cache-type-k-draft",
            settings.draft_cache_type_k,
            "--cache-type-v-draft",
            settings.draft_cache_type_v,
        ]
    if body.vision and body.mmproj:
        launch["mmproj"] = body.mmproj
    if body.draft_enabled():
        launch["draft_model"] = body.draft_model
    launch.update({key: value for key, value in template.items() if key != "jinja"})
    if "jinja" in template:
        if template["jinja"]:
            launch["jinja"] = True
            launch["extra_args"] = [arg for arg in launch["extra_args"] if arg != "--no-jinja"]
        else:
            launch["jinja"] = False
            launch["extra_args"] = [
                arg for arg in launch["extra_args"] if arg != "--jinja"
            ] + ["--no-jinja"]
    for pattern in plan_result.get("tensor_overrides") or []:
        launch["extra_args"] += ["--override-tensor", pattern]
    if plan_result.get("n_cpu_moe") is not None:
        launch["extra_args"] += ["--n-cpu-moe", str(plan_result["n_cpu_moe"])]
    if "device_order" in plan_result:
        launch["extra_args"] += ["--device", ",".join(plan_result["device_order"]) or "none"]
    elif gpu_env:
        launch["env"] = gpu_env
    launch["log_path"] = str(llama_server_log_path())
    return launch
