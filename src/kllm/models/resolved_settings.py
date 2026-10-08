"""Immutable, normalized inference settings shared by planning and launch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from kllm.core.placement_models import ModelLayout
from kllm.exceptions import ValidationError


@dataclass(frozen=True)
class ResolvedLaunchSettings:
    """Validated launch settings after resolving request and extra-arg overrides."""

    parallel_slots: int
    batch_size: int
    micro_batch: int
    moe_offload: int | str
    context: int
    cache_type_k: str
    cache_type_v: str
    flash_attention: str
    threads: int
    checkpoints: int
    cache_ram: int
    split_mode: str
    kv_unified: bool
    swa_full: bool
    speculative: str
    draft_max: int
    draft_cache_type_k: str
    draft_cache_type_v: str
    requested_batch_size: int
    requested_micro_batch: int
    extra_args: tuple[str, ...]
    draft_size_bytes: int = 0
    draft_layout: ModelLayout | None = None
    vision_runtime: bool = False
    auxiliary_weights_bytes: int = 0
    estimate_warnings: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> ResolvedLaunchSettings:
        """Adapt the legacy internal mapping at the parser boundary."""
        warnings = values.get("estimate_warnings", ())
        extra_args = values.get("extra_args", ())
        raw_layout = values.get("_draft_layout")
        if not isinstance(extra_args, (list, tuple)):
            raise ValidationError("resolved extra_args must be a sequence")
        if not isinstance(warnings, (list, tuple)):
            raise ValidationError("resolved estimate_warnings must be a sequence")
        if raw_layout is not None and not isinstance(raw_layout, Mapping):
            raise ValidationError("resolved draft layout must be a mapping")
        try:
            return cls(
                parallel_slots=int(values["parallel_slots"]),
                batch_size=int(values["batch_size"]),
                micro_batch=int(values["micro_batch"]),
                moe_offload=(
                    values["moe_offload"]
                    if values.get("moe_offload") == "auto"
                    else int(values.get("moe_offload", "auto"))
                ),
                context=int(values["context"]),
                cache_type_k=str(values["cache_type_k"]),
                cache_type_v=str(values["cache_type_v"]),
                flash_attention=str(values["flash_attention"]),
                threads=int(values["threads"]),
                checkpoints=int(values["checkpoints"]),
                cache_ram=int(values["cache_ram"]),
                split_mode=str(values["split_mode"]),
                kv_unified=bool(values["kv_unified"]),
                swa_full=bool(values["swa_full"]),
                speculative=str(values["speculative"]),
                draft_max=int(values["draft_max"]),
                draft_cache_type_k=str(values["draft_cache_type_k"]),
                draft_cache_type_v=str(values["draft_cache_type_v"]),
                requested_batch_size=int(values.get("requested_batch_size", values["batch_size"])),
                requested_micro_batch=int(
                    values.get("requested_micro_batch", values["micro_batch"])
                ),
                extra_args=tuple(str(arg) for arg in extra_args),
                draft_size_bytes=int(values.get("draft_size_bytes", 0)),
                draft_layout=ModelLayout.from_mapping(raw_layout),
                vision_runtime=bool(values.get("vision_runtime", False)),
                auxiliary_weights_bytes=int(values.get("auxiliary_weights_bytes", 0)),
                estimate_warnings=tuple(str(warning) for warning in warnings),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("resolved launch settings are incomplete or invalid") from exc

    def to_mapping(self) -> dict[str, Any]:
        """Serialize settings for native, HTTP, and persisted-state boundaries."""
        return {
            "parallel_slots": self.parallel_slots,
            "batch_size": self.batch_size,
            "micro_batch": self.micro_batch,
            "moe_offload": self.moe_offload,
            "context": self.context,
            "cache_type_k": self.cache_type_k,
            "cache_type_v": self.cache_type_v,
            "flash_attention": self.flash_attention,
            "threads": self.threads,
            "checkpoints": self.checkpoints,
            "cache_ram": self.cache_ram,
            "split_mode": self.split_mode,
            "kv_unified": self.kv_unified,
            "swa_full": self.swa_full,
            "speculative": self.speculative,
            "draft_max": self.draft_max,
            "draft_cache_type_k": self.draft_cache_type_k,
            "draft_cache_type_v": self.draft_cache_type_v,
            "requested_batch_size": self.requested_batch_size,
            "requested_micro_batch": self.requested_micro_batch,
            "extra_args": list(self.extra_args),
            "draft_size_bytes": self.draft_size_bytes,
            "_draft_layout": (
                self.draft_layout.to_mapping() if self.draft_layout is not None else None
            ),
            "vision_runtime": self.vision_runtime,
            "auxiliary_weights_bytes": self.auxiliary_weights_bytes,
            "estimate_warnings": list(self.estimate_warnings),
        }
