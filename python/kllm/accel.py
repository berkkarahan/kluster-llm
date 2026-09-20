"""Accelerator backend hints (metal/cuda/rocm/vulkan/cpu) for cluster membership."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
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
        return ["rocm", "vulkan"] if plat.startswith("linux") else ["vulkan"]
    if probed_s == "vulkan":
        return ["vulkan"]
    return []


def effective_accel_backend(probed: str | None) -> str:
    """A requested backend cannot relabel the capabilities of an installed binary."""
    return str(probed or "cpu").strip().lower()


def requested_backend() -> str | None:
    return accel_backend_override() or accel_backend_from_config()


def parse_devices(output: str) -> list[dict[str, Any]]:
    import re
    result = []
    for name, description, total, free in re.findall(
        r"^\s*(\w+): (.*?) \((\d+) MiB, (\d+) MiB free\)", output, re.M
    ):
        prefix = re.sub(r"\d+$", "", name).lower()
        backend = {"hip": "rocm", "rocm": "rocm", "cuda": "cuda", "metal": "metal", "vulkan": "vulkan"}.get(prefix)
        if backend:
            result.append(dict(index=len(result), device=name, name=description,
                               backend=backend, vram_bytes=int(total) * 1024**2,
                               free_bytes=int(free) * 1024**2))
    return result


@lru_cache(maxsize=8)
def binary_capabilities(binary: str, mtime_ns: int, epoch: int) -> dict[str, Any]:
    """Cache capability probes; file identity invalidates after installation."""
    import subprocess
    del mtime_ns, epoch
    kwargs = dict(capture_output=True, text=True, timeout=15)
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    devices = subprocess.run([binary, "--list-devices"], **kwargs)
    devices.check_returncode()
    version = subprocess.run([binary, "--version"], **kwargs)
    return {"devices": parse_devices(devices.stdout + devices.stderr),
            "binary_version": (version.stdout + version.stderr).strip()[-1000:]}
