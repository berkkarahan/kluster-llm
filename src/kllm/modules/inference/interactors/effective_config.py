"""Resolved deployment snapshots and per-protocol generation defaults."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from kllm.modules.inference.interactors.model_defaults import ModelDefaults, overlay_user_inference, resolve_model_defaults
from kllm.modules.inference.interactors.parameter_catalog import GENERATION_FIELDS, PARAMETERS, SAMPLING_DEFAULTS
from kllm.modules.inference.entities import RunSettingsFields, cpu_batch_threads


def deployment_defaults(body: RunSettingsFields, defaults: ModelDefaults) -> ModelDefaults:
    """Keep Kluster's established dashboard defaults behind GGUF recommendations."""
    if body.model_config.get("extra") != "ignore":
        sources = {name: "kluster" for name in SAMPLING_DEFAULTS if name not in defaults.sampling}
        defaults = replace(
            defaults,
            sampling={**SAMPLING_DEFAULTS, **defaults.sampling},
            sources={**sources, **defaults.sources},
        )
    return defaults


def generation_defaults(body: RunSettingsFields, path: Path) -> dict[str, Any]:
    defaults = deployment_defaults(body, resolve_model_defaults(path))
    if body.chat_template != "default" or body.chat_template_text:
        defaults = replace(defaults, chat_template_kwargs={}, reasoning_format=None, jinja=False)
    resolved = overlay_user_inference(
        defaults,
        **{
            name: getattr(body, name)
            for name in (
                "temperature",
                "top_p",
                "top_k",
                "min_p",
                "presence_penalty",
                "repeat_penalty",
                "frequency_penalty",
                "enable_thinking",
            )
        },
    )
    values: dict[str, Any] = dict(resolved.sampling)
    if resolved.chat_template_kwargs:
        values["chat_template_kwargs"] = dict(resolved.chat_template_kwargs)
    if resolved.reasoning_format:
        values["reasoning_format"] = resolved.reasoning_format
    for name in GENERATION_FIELDS - {"enable_thinking"}:
        value = getattr(body, name)
        if value is not None:
            values[name] = value
    tokens = body.extra_args
    if "--chat-template-kwargs" in tokens:
        try:
            value = json.loads(tokens[tokens.index("--chat-template-kwargs") + 1])
        except (ValueError, IndexError) as exc:
            raise ValueError("extra_args: chat-template-kwargs must be a JSON object") from exc
        if not isinstance(value, dict):
            raise ValueError("extra_args: chat-template-kwargs must be a JSON object")
        values["chat_template_kwargs"] = {**values.get("chat_template_kwargs", {}), **value}
    return values


def effective_configuration(
    body: RunSettingsFields, plan: dict[str, Any], path: Path
) -> dict[str, Any]:
    requested = body.snapshot_for_remember()
    from kllm.modules.inference.interactors.run_settings_resolution import resolve_launch_settings

    launch_settings = (
        plan.get("resolved_launch_settings") or resolve_launch_settings(body).to_mapping()
    )
    values = {**requested, **launch_settings}
    generation = generation_defaults(body, path)
    values.update({key: value for key, value in generation.items() if key in PARAMETERS})
    values["context"] = plan.get("context_effective", values["context"])
    if not body.vision:
        values["mmproj"] = None
    if not body.draft_enabled():
        values["draft_model"] = None
    values["threads_batch"] = body.threads_batch or cpu_batch_threads()
    values["draft_max"] = values.get("draft_max") if values.get("draft_max") is not None else 3
    slots = max(1, int(values.get("parallel_slots") or 1))
    values["context_per_request"] = int(values["context"]) // slots
    sources = dict(body._sources)
    model = deployment_defaults(body, resolve_model_defaults(path))
    for name in PARAMETERS:
        sources.setdefault(name, "override" if name in body.model_fields_set else "default")
        if getattr(body, name) is None and name in GENERATION_FIELDS:
            sources[name] = model.sources.get(name, "engine")
    adjustments = {}
    for name in PARAMETERS:
        desired = requested.get(name)
        actual = values.get(name)
        if desired != actual and actual is not None:
            adjustments[name] = (
                "Resolved automatically"
                if desired in (None, "auto")
                else "Required for quantized V cache"
                if name == "flash_attention"
                else "Adjusted to the effective context or micro-batch limit"
                if name in {"batch_size", "micro_batch", "context"}
                else "Resolved by the planner"
            )
    return {
        "schema_version": 1,
        "model": path.name,
        "requested": requested,
        "values": values,
        "sources": sources,
        "adjustments": adjustments,
        "generation_defaults": generation,
    }


def apply_active_defaults(
    body: dict[str, Any], snapshot: dict[str, Any] | None, protocol: str
) -> dict[str, Any]:
    """Client fields win, including equivalent protocol aliases and thinking controls."""
    if not isinstance(body, dict):
        raise ValueError("request must be a JSON object")
    out = dict(body)
    if protocol == "responses":
        if "max_output_tokens" not in out:
            for alias in ("max_completion_tokens", "max_tokens"):
                if alias in out:
                    out["max_output_tokens"] = out[alias]
                    break
    elif (
        "max_tokens" not in out
        and "max_completion_tokens" not in out
        and "max_output_tokens" in out
    ):
        out["max_tokens"] = out.pop("max_output_tokens")
    if protocol != "messages" and "reasoning_budget" in out:
        out.setdefault("reasoning_budget_tokens", out.pop("reasoning_budget"))
    if protocol == "messages":
        unsupported = {
            "min_p",
            "presence_penalty",
            "repeat_penalty",
            "frequency_penalty",
            "seed",
            "reasoning_format",
            "reasoning_budget",
            "reasoning_budget_tokens",
            "thinking_budget_tokens",
        } & body.keys()
        if unsupported:
            raise ValueError(
                f"Messages does not support per-request {', '.join(sorted(unsupported))}; use Chat Completions or deployment defaults"
            )
        if "stop" in out:
            out.setdefault("stop_sequences", out.pop("stop"))
        if "max_tokens" not in out and "max_completion_tokens" in out:
            out["max_tokens"] = out.pop("max_completion_tokens")
        if "reasoning_effort" in out:
            kwargs = dict(out.get("chat_template_kwargs") or {})
            if out["reasoning_effort"] == "none":
                kwargs["enable_thinking"] = False
            else:
                kwargs["reasoning_effort"] = out["reasoning_effort"]
            out["chat_template_kwargs"] = kwargs
    values = (snapshot or {}).get("generation_defaults") or {}
    for name, value in values.items():
        if name == "chat_template_kwargs":
            kwargs = dict(value)
            if "enable_thinking" in out:
                kwargs["enable_thinking"] = out["enable_thinking"]
            reasoning = out.get("reasoning")
            if (
                out.get("reasoning_effort") == "none"
                or isinstance(reasoning, dict)
                and reasoning.get("effort") == "none"
            ):
                kwargs["enable_thinking"] = False
            thinking = out.get("thinking")
            if (
                protocol == "messages"
                and isinstance(thinking, dict)
                and thinking.get("type") in {"enabled", "disabled"}
            ):
                kwargs["enable_thinking"] = thinking["type"] == "enabled"
            kwargs.update(out.get("chat_template_kwargs") or {})
            if kwargs:
                out["chat_template_kwargs"] = kwargs
        elif name == "max_output_tokens":
            if not any(
                key in out for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
            ):
                out["max_output_tokens" if protocol == "responses" else "max_tokens"] = value
        elif name == "stop":
            if "stop" not in out and "stop_sequences" not in out:
                out["stop_sequences" if protocol == "messages" else "stop"] = value
        elif name == "reasoning_effort" and protocol == "responses":
            reasoning = dict(out.get("reasoning") or {})
            reasoning.setdefault("effort", value)
            out["reasoning"] = reasoning
        elif name == "reasoning_effort" and protocol == "messages":
            kwargs = dict(out.get("chat_template_kwargs") or {})
            kwargs.setdefault("reasoning_effort", out.get("reasoning_effort", value))
            out["chat_template_kwargs"] = kwargs
        elif name == "reasoning_budget":
            if protocol != "messages" and not any(
                key in out
                for key in ("reasoning_budget_tokens", "thinking_budget_tokens", "reasoning_budget")
            ):
                out["reasoning_budget_tokens"] = value
        elif protocol == "messages" and name not in {"temperature", "top_p", "top_k"}:
            # The native Messages converter drops these fields; launch defaults apply.
            continue
        elif name not in out:
            out[name] = value
    return out
