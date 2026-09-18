"""Accelerator backend hints (metal/cuda/rocm/vulkan/cpu) for cluster membership."""

from __future__ import annotations

import os
import sys
from typing import Any, Sequence

VALID_ACCEL_BACKENDS = frozenset({"metal", "cuda", "rocm", "vulkan", "cpu"})
# User-selectable GPU engines (cpu is device mode, not a backend picker value).
SELECTABLE_ACCEL_BACKENDS = frozenset({"metal", "cuda", "rocm", "vulkan"})


def accel_backend_override() -> str | None:
    """Optional ``KLLM_ACCEL_BACKEND`` env forces reported/offload backend."""
    raw = os.environ.get("KLLM_ACCEL_BACKEND", "").strip().lower()
    if not raw:
        return None
    if raw not in VALID_ACCEL_BACKENDS:
        return None
    return raw


def accel_backend_from_config() -> str | None:
    """Optional ``accel_backend`` from ``~/.kllm/config.toml`` (null = auto)."""
    try:
        from kllm import config as kllm_config

        raw = kllm_config.load_config().get("accel_backend")
    except Exception:
        return None
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s not in VALID_ACCEL_BACKENDS:
        return None
    return s


def _gpu_names(gpus: Sequence[dict[str, Any]] | None) -> str:
    if not gpus:
        return ""
    parts: list[str] = []
    for g in gpus:
        name = g.get("name") if isinstance(g, dict) else None
        if name:
            parts.append(str(name).lower())
    return " ".join(parts)


def allowed_accel_backends(
    probed: str | None,
    gpus: Sequence[dict[str, Any]] | None = None,
    *,
    platform: str | None = None,
) -> list[str]:
    """Vendor-appropriate backend choices for the UI selector.

    - macOS → metal, vulkan
    - NVIDIA (probe or name) → cuda, vulkan
    - AMD (probe or name) → rocm, vulkan
    - otherwise vulkan-only when probe is vulkan; empty when cpu-only
    """
    plat = platform if platform is not None else sys.platform
    probed_s = (probed or "cpu").strip().lower()
    names = _gpu_names(gpus)

    if plat == "darwin" or probed_s == "metal":
        return ["metal", "vulkan"]

    nvidia = (
        probed_s == "cuda"
        or "nvidia" in names
        or "geforce" in names
        or "quadro" in names
        or "tesla" in names
        or "rtx " in names
    )
    amd = (
        probed_s == "rocm"
        or "amd" in names
        or "radeon" in names
        or "instinct" in names
    )
    if nvidia:
        return ["cuda", "vulkan"]
    if amd:
        return ["rocm", "vulkan"]
    if probed_s == "vulkan":
        return ["vulkan"]
    return []


def effective_accel_backend(probed: str | None) -> str:
    """Precedence: ``KLLM_ACCEL_BACKEND`` → config ``accel_backend`` → probe."""
    override = accel_backend_override()
    if override:
        return override
    cfg = accel_backend_from_config()
    if cfg:
        return cfg
    if probed:
        return str(probed).strip().lower()
    return "cpu"
