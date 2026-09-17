"""RUN SETTINGS models shared by agent, CLI, and dashboard."""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

AutoInt = Union[int, Literal["auto"]]
AutoStr = Union[str, Literal["auto"]]
MmapMlock = Literal["auto", "mmap", "mlock", "mmap+mlock", "none"]

DEFAULT_CONTEXT = 4096
DEFAULT_CACHE_TYPE = "q8_0"
DEFAULT_THREADS = 8
DEFAULT_MEMORY_PERCENT = 80


class RunSettingsFields(BaseModel):
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
    vision: bool = False
    mmproj: str | None = None
    device: Literal["cpu", "gpu"] | None = None
    headroom: float | None = Field(default=None, ge=0.1, le=1.0)
    vram_percent: int | None = Field(default=None, ge=10, le=100)
    ram_percent: int | None = Field(default=None, ge=10, le=100)
    gpu_ids: list[int] | None = None
    chat_template: str = "default"
    chat_template_text: str | None = None
    mmap_mlock: MmapMlock = "auto"
    checkpoints: AutoInt = "auto"
    cache_ram: AutoInt = "auto"
    threads: int = DEFAULT_THREADS
    extra_args: list[str] = Field(default_factory=list)
    remember: bool = True
    advanced_open: bool = False
    tensor_split: list[float] | None = None

    @field_validator("threads")
    @classmethod
    def threads_min(cls, v: int) -> int:
        if v < 1:
            raise ValueError("threads must be >= 1")
        return v

    @model_validator(mode="after")
    def validate_context_and_percents(self) -> RunSettingsFields:
        if self.tensor_split is not None:
            raise ValueError(
                "tensor_split is not supported on the primary path; use automatic placement"
            )
        if self.vram_percent is not None and self.ram_percent is not None:
            raise ValueError("pass only one of vram_percent or ram_percent")
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
            "vision": self.vision,
            "mmproj": self.mmproj,
            "device": self.device,
            "headroom": self.headroom,
            "vram_percent": self.vram_percent,
            "ram_percent": self.ram_percent,
            "gpu_ids": self.gpu_ids,
            "chat_template": self.chat_template,
            "chat_template_text": self.chat_template_text,
            "mmap_mlock": self.mmap_mlock,
            "checkpoints": norm_auto_int(self.checkpoints),
            "cache_ram": norm_auto_int(self.cache_ram),
            "threads": self.threads,
            "extra_args": list(self.extra_args),
            "remember": self.remember,
            "advanced_open": self.advanced_open,
        }

    def snapshot_for_remember(self) -> dict[str, Any]:
        snap = self.normalized_auto_fields()
        snap.pop("headroom", None)
        return snap

    @classmethod
    def default_snapshot(cls, model: str) -> dict[str, Any]:
        return cls(model=model).normalized_auto_fields()


class PlanBody(RunSettingsFields):
    pass


class RunBody(RunSettingsFields):
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
