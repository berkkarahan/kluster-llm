"""Accelerator backend hints (metal/cuda/rocm/vulkan/cpu) for cluster membership."""

from __future__ import annotations

import os

VALID_ACCEL_BACKENDS = frozenset({"metal", "cuda", "rocm", "vulkan", "cpu"})


def accel_backend_override() -> str | None:
    """Optional ``KLLM_ACCEL_BACKEND`` env forces reported/offload backend."""
    raw = os.environ.get("KLLM_ACCEL_BACKEND", "").strip().lower()
    if not raw:
        return None
    if raw not in VALID_ACCEL_BACKENDS:
        return None
    return raw


def effective_accel_backend(probed: str | None) -> str:
    """Apply env override so macOS Vulkan workers match a Vulkan master cluster."""
    override = accel_backend_override()
    if override:
        return override
    if probed:
        return str(probed).strip().lower()
    return "cpu"
