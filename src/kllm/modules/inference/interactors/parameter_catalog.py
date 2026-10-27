"""Shared configuration names, scopes and llama-server argument aliases."""

from __future__ import annotations

from typing import Any

# Public API name -> (Kluster CLI flag, engine aliases, section, memory impact).
PARAMETERS: dict[str, tuple[str, tuple[str, ...], str, bool]] = {
    "context": ("--context", ("--ctx-size", "-c"), "capacity", True),
    "context_auto": ("--context-auto", (), "capacity", True),
    "parallel_slots": ("--parallel", ("--parallel", "-np"), "capacity", True),
    "batch_size": ("--batch-size", ("--batch-size", "-b"), "performance", True),
    "micro_batch": ("--micro-batch", ("--ubatch-size", "-ub"), "performance", True),
    "threads": ("--threads", ("--threads", "-t"), "performance", False),
    "threads_batch": ("--threads-batch", ("--threads-batch", "-tb"), "performance", False),
    "cache_type_k": ("--cache-type-k", ("--cache-type-k", "-ctk"), "memory", True),
    "cache_type_v": ("--cache-type-v", ("--cache-type-v", "-ctv"), "memory", True),
    "flash_attention": ("--flash-attention", ("--flash-attn", "-fa"), "memory", True),
    "mmap_mlock": ("--load-mode", ("--load-mode", "-lm"), "memory", True),
    "checkpoints": (
        "--ctx-checkpoints",
        ("--ctx-checkpoints", "-ctxcp", "--swa-checkpoints"),
        "memory",
        True,
    ),
    "cache_ram": ("--cache-ram", ("--cache-ram", "-cram"), "memory", True),
    "speculative": ("--speculative", ("--spec-type",), "features", True),
    "draft_model": ("--draft-model", (), "features", True),
    "draft_max": (
        "--draft-max",
        ("--spec-draft-n-max", "--draft-max", "--draft", "--draft-n"),
        "features",
        True,
    ),
    "moe_offload": ("--moe-offload", (), "features", True),
    "vision": ("--vision", (), "features", True),
    "mmproj": ("--mmproj", (), "features", True),
    "chat_template": ("--chat-template", (), "features", False),
    "chat_template_text": ("--chat-template-text", (), "features", False),
    "temperature": ("--temperature", ("--temp", "--temperature"), "generation", False),
    "top_p": ("--top-p", ("--top-p",), "generation", False),
    "top_k": ("--top-k", ("--top-k",), "generation", False),
    "min_p": ("--min-p", ("--min-p",), "generation", False),
    "presence_penalty": ("--presence-penalty", ("--presence-penalty",), "generation", False),
    "repeat_penalty": ("--repeat-penalty", ("--repeat-penalty",), "generation", False),
    "frequency_penalty": ("--frequency-penalty", ("--frequency-penalty",), "generation", False),
    "seed": ("--seed", ("--seed", "-s"), "generation", False),
    "max_output_tokens": (
        "--max-output-tokens",
        ("--predict", "--n-predict", "-n"),
        "generation",
        False,
    ),
    "stop": ("--stop", (), "generation", False),
    "enable_thinking": ("--enable-thinking", (), "features", False),
    "reasoning_effort": ("--reasoning-effort", ("--reasoning-effort",), "features", False),
    "reasoning_budget": ("--reasoning-budget", ("--reasoning-budget",), "features", False),
    "reasoning_format": ("--reasoning-format", ("--reasoning-format",), "features", False),
    "device": ("--device", (), "placement", True),
    "gpu_ids": ("--gpu-id", (), "placement", True),
    "vram_percent": ("--vram-percent", (), "memory", True),
    "ram_percent": ("--ram-percent", (), "memory", True),
    "vram_weight": ("--vram-weight", (), "placement", True),
    "latency_weight": ("--latency-weight", (), "placement", True),
    "required_nodes": ("--require-node", (), "placement", True),
    "node_priority": ("--node-priority", (), "placement", True),
    "placement_strategy": ("--placement-strategy", (), "placement", True),
    "node_shares": ("--node-share", (), "placement", True),
    "split_mode": ("--split-mode", (), "placement", True),
    "extra_args": ("--extra-arg", (), "expert", True),
}

ALIASES = {alias: name for name, (_, aliases, _, _) in PARAMETERS.items() for alias in aliases}
INTEGER_FIELDS = {
    "context",
    "parallel_slots",
    "batch_size",
    "micro_batch",
    "threads",
    "threads_batch",
    "checkpoints",
    "cache_ram",
    "draft_max",
    "top_k",
    "seed",
    "max_output_tokens",
    "reasoning_budget",
}
FLOAT_FIELDS = {
    "temperature",
    "top_p",
    "min_p",
    "presence_penalty",
    "repeat_penalty",
    "frequency_penalty",
}
GENERATION_FIELDS = {
    name for name, (_, _, group, _) in PARAMETERS.items() if group == "generation"
} | {"enable_thinking", "reasoning_effort", "reasoning_budget", "reasoning_format"}
CONTROL_FIELDS = {
    "model",
    "tensor_split",
    "network_id",
    "settings_source",
    "remember",
    "advanced_open",
    "prima_auto_deploy",
}
SAMPLING_DEFAULTS = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "min_p": 0.05,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.1,
    "frequency_penalty": 0.0,
}


def canonicalize_parameters(data: dict[str, Any], *, legacy: bool = False) -> dict[str, Any]:
    """Absorb known engine aliases without leaving contradictory visible controls."""
    from kllm.modules.inference.interactors.extra_args import sanitize_extra_args

    result = dict(data)
    if "extra_args" not in result:
        return result
    tokens = sanitize_extra_args(result.get("extra_args") or [])
    remaining: list[str] = []
    seen: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        name = ALIASES.get(tokens[i])
        if name is None:
            remaining.append(tokens[i])
            i += 1
            continue
        flag = tokens[i]
        if i + 1 >= len(tokens):
            raise ValueError(f"extra_args: missing value for {flag}")
        raw = tokens[i + 1]
        try:
            value = (
                int(raw) if name in INTEGER_FIELDS else float(raw) if name in FLOAT_FIELDS else raw
            )
        except ValueError as exc:
            raise ValueError(f"extra_args: invalid value for {flag}") from exc
        if not legacy and name in result and result[name] != value:
            raise ValueError(
                f"{name}: conflicting control and extra argument {flag}; use the configuration control"
            )
        if not legacy and name in seen and seen[name] != value:
            raise ValueError(f"{name}: conflicting extra arguments")
        seen[name] = value
        result[name] = value
        i += 2
    result["extra_args"] = remaining
    return result


def parameter_descriptors(schema: dict[str, Any], threads_max: int) -> list[dict[str, Any]]:
    descriptors = []
    for name, (cli, aliases, group, memory) in PARAMETERS.items():
        entry = dict(schema["properties"].get(name, {}))
        entry.update(
            {
                "id": name,
                "cli": cli,
                "engine_flags": list(aliases),
                "section": group,
                "scope": "generation_default" if name in GENERATION_FIELDS else "deployment",
                "memory_affecting": memory,
                "applies": "future requests in the next deployment"
                if name in GENERATION_FIELDS
                else "next deployment",
            }
        )
        if name in {"threads", "threads_batch"}:
            entry["maximum"] = threads_max
        descriptors.append(entry)
    return descriptors
