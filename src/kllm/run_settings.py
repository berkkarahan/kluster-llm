"""RUN SETTINGS models shared by agent, CLI, and dashboard."""

from __future__ import annotations

import os
import math
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AutoInt = Union[int, Literal["auto"]]
AutoStr = Union[str, Literal["auto"]]
MmapMlock = Literal["auto", "mmap", "mlock", "mmap+mlock", "none"]

DEFAULT_CONTEXT = 4096
DEFAULT_CACHE_TYPE = "q8_0"
DEFAULT_THREADS = 8
DEFAULT_MEMORY_PERCENT = 80


def available_threads() -> int:
    """Logical CPUs available to the master process, respecting affinity."""
    try:
        import psutil
        return max(1, len(psutil.Process().cpu_affinity()))
    except (AttributeError, OSError, ValueError):
        return max(1, len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1))


class RunSettingsFields(BaseModel):
    """Validated request settings; handlers derive normalized copies at boundaries."""

    model_config = ConfigDict(frozen=True)

    model: str
    context: int = DEFAULT_CONTEXT
    context_auto: bool = False
    cache_type_k: str = DEFAULT_CACHE_TYPE
    cache_type_v: str = DEFAULT_CACHE_TYPE
    speculative: AutoStr = "auto"
    draft_model: str | None = None
    parallel_slots: AutoInt = "auto"
    batch_size: AutoInt = "auto"
    micro_batch: AutoInt = "auto"
    flash_attention: Literal["auto", "on", "off"] = "auto"
    vision: bool = False
    mmproj: str | None = None
    device: Literal["cpu", "gpu"] | None = None
    headroom: float | None = Field(default=None, ge=0.1, le=1.0)
    vram_percent: int | None = Field(default=None, ge=10, le=100)
    ram_percent: int | None = Field(default=None, ge=10, le=100)
    vram_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    latency_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    gpu_ids: list[int] | None = None
    chat_template: str = "default"
    chat_template_text: str | None = None
    node_shares: dict[str, float] | None = None
    mmap_mlock: MmapMlock = "auto"
    checkpoints: AutoInt = "auto"
    cache_ram: AutoInt = "auto"
    threads: int = Field(default_factory=lambda: min(DEFAULT_THREADS, available_threads()))
    extra_args: list[str] = Field(default_factory=list)
    # Inference sampling / thinking — None means "auto" (GGUF + heuristic defaults).
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0.0, le=1.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    repeat_penalty: float | None = Field(default=None, ge=0.0, le=2.0)
    enable_thinking: bool | None = None
    remember: bool = True
    advanced_open: bool = False
    tensor_split: list[float] | None = None
    split_mode: Literal["layer", "row", "tensor"] | None = None

    @field_validator("threads")
    @classmethod
    def threads_min(cls, v: int) -> int:
        if not 1 <= v <= available_threads():
            raise ValueError(f"threads must be 1–{available_threads()} on the master")
        return v

    @model_validator(mode="after")
    def validate_context_and_percents(self) -> RunSettingsFields:
        if self.tensor_split is not None:
            raise ValueError(
                "tensor_split is not supported on the primary path; use automatic placement"
            )
        if self.vram_percent is not None and self.ram_percent is not None:
            raise ValueError("pass only one of vram_percent or ram_percent")
        if self.vram_weight + self.latency_weight <= 0:
            raise ValueError("vram_weight and latency_weight cannot both be zero")
        if self.node_shares is not None:
            if self.device == "cpu" or self.split_mode not in (None, "layer"):
                raise ValueError("manual node shares require GPU layer placement")
            if not self.node_shares or any(not math.isfinite(v) or v < 0 for v in self.node_shares.values()):
                raise ValueError("node shares must be finite nonnegative numbers")
            if abs(sum(self.node_shares.values()) - 1) > 0.001:
                raise ValueError("node shares must total 1")
        from kllm.chat_templates import validate_selection
        validate_selection(self.chat_template, self.chat_template_text)
        if self.chat_template in ("llama3", "phi3", "gemma", "mistral-v3-tekken") and self.enable_thinking is not None:
            raise ValueError("legacy chat templates do not support thinking overrides")
        return self

    def normalized_auto_fields(self) -> dict[str, Any]:
        """Wire-format normalization (research R10): auto-capable fields as 'auto' string."""

        def norm_auto_int(v: AutoInt) -> int | str:
            return "auto" if v == "auto" else int(v)

        return {
            "context": int(self.context),
            "context_auto": bool(self.context_auto),
            "cache_type_k": self.cache_type_k,
            "cache_type_v": self.cache_type_v,
            "speculative": self.speculative if self.speculative != "auto" else "auto",
            "draft_model": self.draft_model,
            "parallel_slots": norm_auto_int(self.parallel_slots),
            "batch_size": norm_auto_int(self.batch_size),
            "micro_batch": norm_auto_int(self.micro_batch),
            "flash_attention": self.flash_attention,
            "vision": self.vision,
            "mmproj": self.mmproj,
            "device": self.device,
            "headroom": self.headroom,
            "vram_percent": self.vram_percent,
            "ram_percent": self.ram_percent,
            "vram_weight": float(self.vram_weight),
            "latency_weight": float(self.latency_weight),
            "gpu_ids": self.gpu_ids,
            "chat_template": self.chat_template,
            "chat_template_text": self.chat_template_text,
            "node_shares": self.node_shares,
            "mmap_mlock": self.mmap_mlock,
            "checkpoints": norm_auto_int(self.checkpoints),
            "cache_ram": norm_auto_int(self.cache_ram),
            "threads": self.threads,
            "extra_args": list(self.extra_args),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "presence_penalty": self.presence_penalty,
            "repeat_penalty": self.repeat_penalty,
            "enable_thinking": self.enable_thinking,
            "remember": self.remember,
            "advanced_open": self.advanced_open,
            "split_mode": self.split_mode,
        }

    def snapshot_for_remember(self) -> dict[str, Any]:
        snap = self.normalized_auto_fields()
        snap.pop("headroom", None)
        return snap

    @classmethod
    def default_snapshot(cls, model: str) -> dict[str, Any]:
        return cls(model=model).normalized_auto_fields()


class NetworkRequestFields(RunSettingsFields):
    # Request-only: normalization and remembered settings deliberately enumerate
    # the reusable fields above, so this selection never becomes a saved default.
    network_id: str | None = None

    @field_validator("network_id")
    @classmethod
    def canonical_network_id(cls, value: str | None) -> str | None:
        from uuid import UUID
        if value is not None and str(UUID(value)) != value:
            raise ValueError("network_id must be a canonical UUID")
        return value


class PlanBody(NetworkRequestFields):
    pass


class RunBody(NetworkRequestFields):
    pass


def validation_errors(body: RunSettingsFields) -> dict[str, str]:
    try:
        RunSettingsFields.model_validate(body.model_dump())
        return {}
    except Exception as exc:
        msg = str(exc)
        if "context" in msg.lower():
            return {"context": msg}
        if "threads" in msg.lower():
            return {"threads": msg}
        if "vram_percent" in msg or "ram_percent" in msg:
            return {"memory": msg}
        if "tensor_split" in msg:
            return {"tensor_split": msg}
        return {"detail": msg}
