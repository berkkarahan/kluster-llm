"""Run request normalization and auxiliary memory pricing."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm.shared import config as kllm_config
from kllm.modules.inference.interactors.extra_args import ExtraArgsError, sanitize_extra_args
from kllm.modules.models.interactors.gguf_meta import read_model_layout
from kllm.modules.models.interactors.resolved_settings import ResolvedLaunchSettings
from kllm.modules.inference.entities import RunSettingsFields, available_threads
from kllm.modules.runtimes.interactors.selection import snapshot


def merge_and_sanitize_extra(body: RunSettingsFields) -> list[str]:
    try:
        return sanitize_extra_args(body.extra_args)
    except ExtraArgsError as exc:
        raise HTTPException(400, str(exc)) from exc


def resolve_split_mode(body: RunSettingsFields) -> str:
    """Request override takes precedence over the persistent system setting."""
    return body.split_mode or kllm_config.get_split_mode()


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


def resolve_launch_settings(body: RunSettingsFields) -> ResolvedLaunchSettings:
    """Normalize overlapping flags once, before both planning and launch."""
    current_runtime = snapshot() or {}
    prima = current_runtime.get("engine") == "primacpp"
    runtime_caps = (
        current_runtime.get("capabilities")
        if isinstance(current_runtime.get("capabilities"), dict)
        else {}
    )
    runtime_features = set(runtime_caps.get("features") or ())
    prima_vulkan_fa = "vulkan_flash_attn_v1" in runtime_features or isinstance(
        runtime_caps.get("vulkan_kv"), dict
    )
    result: dict[str, Any] = {
        "parallel_slots": 1 if body.parallel_slots == "auto" else int(body.parallel_slots),
        "batch_size": 512 if body.batch_size == "auto" else int(body.batch_size),
        "micro_batch": 128 if body.micro_batch == "auto" else int(body.micro_batch),
        "moe_offload": body.moe_offload,
        "context": body.context,
        "cache_type_k": body.cache_type_k,
        "cache_type_v": body.cache_type_v,
        "flash_attention": body.flash_attention,
        "threads": body.threads,
        "checkpoints": (0 if prima else 32) if body.checkpoints == "auto" else int(body.checkpoints),
        "cache_ram": (0 if prima else 8192) if body.cache_ram == "auto" else int(body.cache_ram),
        "checkpoint_min_step": 8192,
        "split_mode": resolve_split_mode(body),
        "kv_unified": body.parallel_slots == "auto",
        "swa_full": False,
        "speculative": "draft-simple"
        if body.draft_enabled() and body.speculative == "auto"
        else body.speculative,
        "draft_max": 3 if body.draft_max is None else body.draft_max,
        "draft_cache_type_k": "f16",
        "draft_cache_type_v": "f16",
    }
    aliases: dict[str, str] = {
        "-b": "batch_size",
        "--batch-size": "batch_size",
        "-ub": "micro_batch",
        "--ubatch-size": "micro_batch",
        "-np": "parallel_slots",
        "--parallel": "parallel_slots",
        "-c": "context",
        "--ctx-size": "context",
        "-ctk": "cache_type_k",
        "--cache-type-k": "cache_type_k",
        "-ctv": "cache_type_v",
        "--cache-type-v": "cache_type_v",
        "-fa": "flash_attention",
        "--flash-attn": "flash_attention",
        "-t": "threads",
        "--threads": "threads",
        "--ctx-checkpoints": "checkpoints",
        "-ctxcp": "checkpoints",
        "--swa-checkpoints": "checkpoints",
        "--cache-ram": "cache_ram",
        "-cram": "cache_ram",
        "--checkpoint-min-step": "checkpoint_min_step",
        "-cms": "checkpoint_min_step",
        "--spec-type": "speculative",
        "--spec-draft-n-max": "draft_max",
        "--draft-max": "draft_max",
        "--draft": "draft_max",
        "--draft-n": "draft_max",
        "-ctkd": "draft_cache_type_k",
        "--cache-type-k-draft": "draft_cache_type_k",
        "--spec-draft-type-k": "draft_cache_type_k",
        "--spec-draft-type-v": "draft_cache_type_v",
        "-ctvd": "draft_cache_type_v",
        "--cache-type-v-draft": "draft_cache_type_v",
    }
    tokens = merge_and_sanitize_extra(body)
    extra: list[str] = []
    i = 0
    unified_explicit = False
    while i < len(tokens):
        if tokens[i] in ("--kv-unified", "-kvu", "--no-kv-unified", "-no-kvu", "--swa-full", "--swa-compact"):
            if tokens[i] in ("--swa-full", "--swa-compact"):
                if tokens[i] == "--swa-compact" and not prima:
                    raise HTTPException(400, "--swa-compact requires the Prima engine")
                result["swa_full"] = tokens[i] == "--swa-full"
            else:
                result["kv_unified"] = tokens[i] in ("--kv-unified", "-kvu")
                unified_explicit = True
            i += 1
            continue
        key = aliases.get(tokens[i])
        if key:
            if i + 1 == len(tokens):
                raise HTTPException(400, f"missing value for {tokens[i]}")
            value = tokens[i + 1]
            try:
                result[key] = (
                    int(value)
                    if key
                    in (
                        "parallel_slots",
                        "batch_size",
                        "micro_batch",
                        "context",
                        "threads",
                        "checkpoints",
                        "checkpoint_min_step",
                        "cache_ram",
                        "draft_max",
                    )
                    else value
                )
            except ValueError as exc:
                raise HTTPException(400, f"invalid value for {tokens[i]}") from exc
            if key == "parallel_slots" and not unified_explicit:
                result["kv_unified"] = False
            i += 2
        else:
            extra.append(tokens[i])
            i += 1
    for key in ("parallel_slots", "batch_size", "micro_batch", "threads"):
        if result[key] < 1:
            raise HTTPException(400, f"{key} must be positive")
    for key in ("checkpoints", "checkpoint_min_step", "cache_ram", "draft_max"):
        if result[key] < 0:
            raise HTTPException(400, f"{key} must be nonnegative")
    if result["context"] < 512 and not body.context_auto:
        raise HTTPException(400, "context must be >= 512")
    # Deferred import: placement constants are checked only while resolving launch settings.
    from kllm.modules.inference.interactors.placement import WIDTHS

    if any(
        result[k] not in WIDTHS
        for k in ("cache_type_k", "cache_type_v", "draft_cache_type_k", "draft_cache_type_v")
    ):
        raise HTTPException(400, "unsupported cache type")
    if result["flash_attention"] not in ("auto", "on", "off"):
        raise HTTPException(400, "flash attention must be auto, on, or off")
    if prima and result["flash_attention"] == "auto":
        explicit_k = "cache_type_k" in body.model_fields_set or any(
            t in ("-ctk", "--cache-type-k") for t in tokens
        )
        explicit_v = "cache_type_v" in body.model_fields_set or any(
            t in ("-ctv", "--cache-type-v") for t in tokens
        )
        if not prima_vulkan_fa and not explicit_v and result["cache_type_v"] not in ("f32", "f16", "bf16"):
            result["cache_type_v"] = "f16"
        if not prima_vulkan_fa and not explicit_k and result["cache_type_k"] not in ("f32", "f16", "bf16"):
            result["cache_type_k"] = "f16"
        if not prima_vulkan_fa and body.draft_enabled() and result["draft_cache_type_v"] not in ("f32", "f16", "bf16"):
            result["draft_cache_type_v"] = "f16"
        if result["cache_type_v"] in ("f32", "f16", "bf16"):
            result["flash_attention"] = "off"
    quantized_v = result["cache_type_v"] not in ("f32", "f16", "bf16")
    if body.draft_enabled():
        quantized_v |= result["draft_cache_type_v"] not in ("f32", "f16", "bf16")
    if result["flash_attention"] == "off" and quantized_v:
        raise HTTPException(
            400, "quantized V cache requires Flash Attention; enable it or use f16/bf16/f32 V cache"
        )
    if result["flash_attention"] == "auto" and quantized_v:
        result["flash_attention"] = "on"
    if result["threads"] > available_threads():
        raise HTTPException(400, f"threads must be 1–{available_threads()} on the master")
    result["requested_batch_size"] = result["batch_size"]
    result["requested_micro_batch"] = result["micro_batch"]
    result["batch_size"] = min(result["batch_size"], max(2048, result["context"]))
    result["micro_batch"] = min(result["micro_batch"], result["batch_size"], 512)
    result["extra_args"] = extra
    # Prima snapshots stay on each participant's host, including GPU workers.
    # Reserve the full configured ceiling before assigning model layers.
    if prima:
        # The head reserves sequence zero; workers may use fewer sequences.
        # Budget every participant against the head's immutable ceiling.
        if result["parallel_slots"] >= 2147483647:
            raise HTTPException(400, "Prima parallel_slots exceeds its native sequence limit")
        result["prima_kv_sequences"] = result["parallel_slots"] + 1
        if result["cache_ram"] > 2147483647:
            raise HTTPException(400, "Prima cache_ram exceeds its native integer limit")
        result["prompt_cache_bytes"] = result["cache_ram"] * 1024**2
        if result["checkpoints"] * result["parallel_slots"] > 16384 or result["checkpoint_min_step"] > 2147483647:
            raise HTTPException(400, "Prima checkpoint count or spacing exceeds the native limit")
        result["prima_checkpoints"] = bool(result["checkpoints"])
        result["prima_graphs"] = True
    result["speculative"] = {"draft": "draft-simple", "off": "none"}.get(
        result["speculative"], result["speculative"]
    )
    return ResolvedLaunchSettings.from_mapping(result)


def estimate_auxiliary_assets(
    body: RunSettingsFields, settings: ResolvedLaunchSettings
) -> ResolvedLaunchSettings:
    """Price local assets once; keep missing/remote assets visible in the preview."""
    settings_values = settings.to_mapping()
    prima = (snapshot() or {}).get("engine") == "primacpp"
    adapter_bytes = adapter_scratch = 0
    if (snapshot() or {}).get("engine") == "primacpp" and body.draft_enabled():
        settings_values["draft_contexts"] = settings.parallel_slots
        if settings.speculative in ("auto", "draft"):
            settings_values["speculative"] = "draft-simple"
    warnings = []
    weights = 0
    for label, path in (
        ("draft model", body.draft_model if body.draft_enabled() else None),
        ("vision projector", body.mmproj if body.vision else None),
    ):
        if not path:
            continue
        size = _file_size_bytes(path)
        if not size:
            warnings.append(f"Cannot price {label} asset: {path}")
        weights += size
        if label == "draft model":
            settings_values["_draft_layout"] = read_model_layout(path) if size else None
            settings_values["draft_size_bytes"] = size
            if settings_values["_draft_layout"]:
                draft = settings_values["_draft_layout"]
                weights += (
                    sum(draft["layers"]) + draft["input_bytes"] + draft["output_bytes"] - size
                )
    if body.vision and not body.mmproj:
        warnings.append(
            "Vision is enabled without a local projector; projector weights cannot be priced"
        )
    tokens = settings_values["extra_args"]
    assets = {"--lora": 1, "--lora-scaled": 2, "--control-vector": 1, "--control-vector-scaled": 2}
    # All other flags remain launchable, but only known memory-neutral flags can
    # be excluded from the confidence check. Unknown flags must never look priced.
    neutral = {
        "--temp",
        "--top-p",
        "--top-k",
        "--min-p",
        "--repeat-penalty",
        "--presence-penalty",
        "--frequency-penalty",
        "--seed",
        "-s",
        "--threads-batch",
        "-tb",
        "--no-warmup",
        "--reasoning-format",
        "--reasoning-budget",
        "--chat-template-kwargs",
        "--verbose",
        "--log-verbosity",
        "--metrics",
        "--no-webui",
        "--control-vector-layer-range",
    }
    if prima:
        # Prima's estimator already reserves projection and pooling storage.
        neutral.update({"--embedding", "--embeddings", "--pooling"})
    i = 0
    while i < len(tokens):
        flag = tokens[i]
        if flag in assets:
            arity = assets[flag]
            if i + arity >= len(tokens) or tokens[i + 1].startswith("-"):
                raise HTTPException(400, f"missing asset value for {flag}")
            if arity == 2:
                try:
                    if not math.isfinite(float(tokens[i + 2])):
                        raise ValueError("non-finite scale")
                except ValueError as exc:
                    raise HTTPException(400, f"invalid scale for {flag}") from exc
            size = _file_size_bytes(tokens[i + 1])
            if prima and flag in ("--lora", "--lora-scaled"):
                from kllm.modules.primacpp.interactors.adapters import adapter_reservation
                from pathlib import Path

                path = Path(tokens[i + 1]).expanduser()
                if not path.is_file() or path.is_symlink():
                    raise HTTPException(400, "Prima LoRA requires a complete regular local adapter file")
                reserve = adapter_reservation(path)
                adapter_bytes += reserve["storage_bytes"]
                adapter_scratch += reserve["scratch_per_token"]
                i += arity + 1
                continue
            weights += size
            if not size:
                warnings.append(f"Cannot price {flag} asset: {tokens[i + 1]}")
            i += arity + 1
            continue
        if flag.startswith("-") and flag not in neutral:
            warnings.append(f"Memory effect of extra argument {flag} is not priced")
        i += 1
    if settings.speculative not in ("auto", "none", "draft-simple"):
        warnings.append(f"Speculative mode {settings.speculative} runtime is not fully priced")
    if settings.speculative == "draft-simple" and not body.draft_model:
        warnings.append("Draft speculation has no local draft model to price")
    settings_values["prima_adapter_bytes"] = adapter_bytes
    settings_values["prima_adapter_scratch_per_token"] = adapter_scratch
    settings_values["auxiliary_weights_bytes"] = weights
    settings_values["vision_runtime"] = bool(body.vision)
    settings_values["estimate_warnings"] = warnings
    return ResolvedLaunchSettings.from_mapping(settings_values)
