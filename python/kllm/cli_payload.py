"""Map CLI flags to agent plan/run JSON bodies."""

from __future__ import annotations

from typing import Any


def build_plan_run_payload(
    *,
    model: str,
    context: int = 4096,
    context_auto: bool = False,
    device: str | None = None,
    headroom: float | None = None,
    vram_percent: int | None = None,
    ram_percent: int | None = None,
    vram_weight: float = 0.6,
    latency_weight: float = 0.4,
    cache_type_k: str = "q8_0",
    cache_type_v: str = "q8_0",
    speculative: str = "auto",
    draft_model: str | None = None,
    parallel: str = "auto",
    batch_size: str = "auto",
    micro_batch: str = "auto",
    vision: bool = False,
    mmproj: str | None = None,
    chat_template: str = "default",
    chat_template_file: str | None = None,
    load_mode: str = "auto",
    ctx_checkpoints: str = "auto",
    cache_ram: str = "auto",
    threads: int = 8,
    extra_args: list[str] | None = None,
    remember: bool = True,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    min_p: float | None = None,
    presence_penalty: float | None = None,
    repeat_penalty: float | None = None,
    enable_thinking: bool | None = None,
) -> dict[str, Any]:
    chat_text = None
    if chat_template_file:
        from pathlib import Path

        chat_text = Path(chat_template_file).read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "model": model,
        "context": context,
        "context_auto": context_auto,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
        "speculative": speculative,
        "draft_model": draft_model,
        "parallel_slots": parallel,
        "batch_size": batch_size,
        "micro_batch": micro_batch,
        "vision": vision,
        "mmproj": mmproj,
        "chat_template": chat_template,
        "chat_template_text": chat_text,
        "mmap_mlock": load_mode,
        "checkpoints": ctx_checkpoints,
        "cache_ram": cache_ram,
        "threads": threads,
        "extra_args": list(extra_args or []),
        "remember": remember,
        "advanced_open": False,
    }
    if device:
        payload["device"] = device
    if headroom is not None:
        payload["headroom"] = headroom
    if vram_percent is not None:
        payload["vram_percent"] = vram_percent
    if ram_percent is not None:
        payload["ram_percent"] = ram_percent
    payload["vram_weight"] = vram_weight
    payload["latency_weight"] = latency_weight
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if top_k is not None:
        payload["top_k"] = top_k
    if min_p is not None:
        payload["min_p"] = min_p
    if presence_penalty is not None:
        payload["presence_penalty"] = presence_penalty
    if repeat_penalty is not None:
        payload["repeat_penalty"] = repeat_penalty
    if enable_thinking is not None:
        payload["enable_thinking"] = enable_thinking
    return payload
