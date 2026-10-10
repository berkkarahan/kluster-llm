"""RUN SETTINGS models shared by agent, CLI, and dashboard."""

from __future__ import annotations

import os
import math
from typing import Any, Literal, Union, Annotated

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

AutoInt = Union[int, Literal["auto"]]
PositiveAutoInt = Union[Annotated[int, Field(ge=1)], Literal["auto"]]
NonnegativeAutoInt = Union[Annotated[int, Field(ge=0)], Literal["auto"]]
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
        return max(
            1,
            len(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else (os.cpu_count() or 1),
        )


def cpu_batch_threads() -> int:
    """Threads for prompt processing and the worker RPC CPU device.

    Generation stays at ``min(DEFAULT_THREADS, available CPUs)`` so a busy GPU
    decode is not oversubscribed. Prompt processing and ``rpc-server --threads``
    (binary default 8) use the physical cores inside the current affinity, and
    never go below that generation count.
    """
    logical = available_threads()
    physical: int | None
    try:
        import psutil

        counted = psutil.cpu_count(logical=False)
        physical = int(counted) if counted else None
    except (ImportError, OSError, ValueError):
        physical = None
    if not physical or physical < 1:
        physical = max(1, logical // 2) if logical > 1 else 1
    generation = min(DEFAULT_THREADS, logical)
    return max(generation, min(logical, physical))


class RunSettingsFields(BaseModel):
    """Validated request settings; handlers derive normalized copies at boundaries."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    _sources: dict[str, str] = PrivateAttr(default_factory=dict)

    model: str
    context: int = DEFAULT_CONTEXT
    context_auto: bool = False
    cache_type_k: str = DEFAULT_CACHE_TYPE
    cache_type_v: str = DEFAULT_CACHE_TYPE
    speculative: AutoStr = "auto"
    draft_model: str | None = None
    parallel_slots: PositiveAutoInt = "auto"
    batch_size: PositiveAutoInt = "auto"
    micro_batch: PositiveAutoInt = "auto"
    moe_offload: NonnegativeAutoInt = "auto"
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
    required_nodes: list[str] = Field(default_factory=list)
    node_priority: list[str] = Field(default_factory=list)
    # Automatic placement: fill workers before the master and give the master
    # only the trailing layers workers cannot hold.
    just_enough_master: bool = False
    mmap_mlock: MmapMlock = "auto"
    checkpoints: NonnegativeAutoInt = "auto"
    cache_ram: NonnegativeAutoInt = "auto"
    threads: int = Field(default_factory=lambda: min(DEFAULT_THREADS, available_threads()))
    threads_batch: int | None = Field(default=None, ge=1)
    draft_max: int | None = Field(default=None, ge=0)
    extra_args: list[str] = Field(default_factory=list)
    # Inference sampling / thinking — None means "auto" (GGUF + heuristic defaults).
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0.0, le=1.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    repeat_penalty: float | None = Field(default=None, ge=0.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    seed: int | None = Field(default=None, ge=-1, le=4294967295)
    max_output_tokens: int | None = Field(default=None, ge=-1)
    stop: list[str] | None = None
    enable_thinking: bool | None = None
    reasoning_effort: str | None = None
    reasoning_budget: int | None = Field(default=None, ge=-1)
    reasoning_format: Literal["auto", "none", "deepseek", "deepseek-legacy"] | None = None
    remember: bool = False
    advanced_open: bool = False
    tensor_split: list[float] | None = None
    split_mode: Literal["layer", "row", "tensor"] | None = None

    @model_validator(mode="before")
    @classmethod
    def canonical_parameters(cls, value: Any) -> Any:
        from kllm.modules.inference.interactors.parameter_catalog import canonicalize_parameters

        if isinstance(value, dict):
            values = canonicalize_parameters(
                value, legacy=cls.model_config.get("extra") == "ignore"
            )
            if values.get("mmproj") and "vision" not in values:
                values["vision"] = True
            if values.get("draft_model") and "speculative" not in values:
                values["speculative"] = "draft"
            return values
        return value

    def draft_enabled(self) -> bool:
        return bool(
            self.draft_model
            and (self.speculative == "auto" or self.speculative.startswith("draft"))
        )

    @field_validator("max_output_tokens")
    @classmethod
    def output_limit(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError(
                "max_output_tokens must be positive, -1 (unlimited), or null (inherit)"
            )
        return value

    @field_validator("stop")
    @classmethod
    def stop_strings(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not item or "\0" in item for item in value):
            raise ValueError("stop must contain nonempty literal strings without NUL")
        return value

    @field_validator("threads")
    @classmethod
    def threads_min(cls, v: int) -> int:
        if not 1 <= v <= available_threads():
            raise ValueError(f"threads must be 1–{available_threads()} on the master")
        return v

    @field_validator("moe_offload")
    @classmethod
    def moe_offload_nonnegative(cls, v: AutoInt) -> AutoInt:
        if v != "auto" and int(v) < 0:
            raise ValueError("moe_offload must be 'auto' or a non-negative integer")
        return v

    @model_validator(mode="after")
    def validate_context_and_percents(self) -> RunSettingsFields:
        if any(not node.strip() or node != node.strip() for node in self.required_nodes):
            raise ValueError("required_nodes must contain nonempty node IDs without whitespace")
        if len(set(self.required_nodes)) != len(self.required_nodes):
            raise ValueError("required_nodes must contain unique node IDs")
        if self.required_nodes and (
            self.node_shares is not None or self.split_mode not in (None, "layer")
        ):
            raise ValueError("required_nodes require automatic layer placement")
        if any(not node.strip() or node != node.strip() for node in self.node_priority) or len(
            set(self.node_priority)
        ) != len(self.node_priority):
            raise ValueError(
                "node_priority must contain unique nonempty node IDs without whitespace"
            )
        if self.node_priority and (
            self.node_shares is not None or self.split_mode not in (None, "layer")
        ):
            raise ValueError("node_priority requires automatic layer placement")
        if self.just_enough_master and (
            self.node_shares is not None or self.split_mode not in (None, "layer")
        ):
            raise ValueError("just_enough_master requires automatic layer placement")
        if self.tensor_split is not None:
            raise ValueError(
                "tensor_split is not supported on the primary path; use automatic placement"
            )
        if self.threads_batch is not None and self.threads_batch > available_threads():
            raise ValueError(f"threads_batch must be 1–{available_threads()} on the master")
        if self.vram_weight + self.latency_weight <= 0:
            raise ValueError("vram_weight and latency_weight cannot both be zero")
        if self.node_shares is not None:
            if self.device == "cpu" or self.split_mode not in (None, "layer"):
                raise ValueError("manual node shares require GPU layer placement")
            if not self.node_shares or any(
                not math.isfinite(v) or v < 0 for v in self.node_shares.values()
            ):
                raise ValueError("node shares must be finite nonnegative numbers")
            if abs(sum(self.node_shares.values()) - 1) > 0.001:
                raise ValueError("node shares must total 1")
        from kllm.modules.inference.interactors.chat_templates import validate_selection

        validate_selection(self.chat_template, self.chat_template_text)
        if (
            self.chat_template in ("llama3", "phi3", "gemma", "mistral-v3-tekken")
            and self.enable_thinking is not None
        ):
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
            "moe_offload": norm_auto_int(self.moe_offload),
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
            "required_nodes": list(self.required_nodes),
            "node_priority": list(self.node_priority),
            "just_enough_master": bool(self.just_enough_master),
            "mmap_mlock": self.mmap_mlock,
            "checkpoints": norm_auto_int(self.checkpoints),
            "cache_ram": norm_auto_int(self.cache_ram),
            "threads": self.threads,
            "threads_batch": self.threads_batch,
            "draft_max": self.draft_max,
            "extra_args": list(self.extra_args),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "presence_penalty": self.presence_penalty,
            "repeat_penalty": self.repeat_penalty,
            "frequency_penalty": self.frequency_penalty,
            "seed": self.seed,
            "max_output_tokens": self.max_output_tokens,
            "stop": self.stop,
            "enable_thinking": self.enable_thinking,
            "reasoning_effort": self.reasoning_effort,
            "reasoning_budget": self.reasoning_budget,
            "reasoning_format": self.reasoning_format,
            "remember": self.remember,
            "advanced_open": self.advanced_open,
            "split_mode": self.split_mode,
        }

    def snapshot_for_remember(self) -> dict[str, Any]:
        snap = self.normalized_auto_fields()
        snap.pop("headroom", None)
        snap.pop("remember", None)
        snap.pop("advanced_open", None)
        return snap

    @classmethod
    def default_snapshot(cls, model: str) -> dict[str, Any]:
        return cls(model=model).normalized_auto_fields()


class NetworkRequestFields(RunSettingsFields):
    # Request-only: normalization and remembered settings deliberately enumerate
    # the reusable fields above, so this selection never becomes a saved default.
    network_id: str | None = None
    settings_source: Literal["saved", "defaults"] = "saved"

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


class LegacyPlanBody(PlanBody):
    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)
    settings_source: Literal["saved", "defaults"] = "defaults"
    remember: bool = True


class LegacyRunBody(RunBody):
    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)
    settings_source: Literal["saved", "defaults"] = "defaults"
    remember: bool = True


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
