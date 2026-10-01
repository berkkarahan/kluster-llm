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


def selectable_accel_backends() -> list[str]:
    """Every RPC engine a node may prefer locally (cluster may mix backends)."""
    return ["metal", "cuda", "rocm", "vulkan"]


def allowed_accel_backends(
    probed: str | None,
    gpus: Sequence[dict[str, Any]] | None = None,
    *,
    platform: str | None = None,
) -> list[str]:
    """Backend choices for the dashboard / config (heterogeneous clusters)."""
    del probed, gpus, platform
    return selectable_accel_backends()


def llama_device_for_backend(backend: str) -> tuple[str, str]:
    """Map membership backend to llama.cpp ``--list-devices`` id."""
    b = str(backend or "vulkan").strip().lower()
    if b == "hip":
        b = "rocm"
    # llama.cpp v0.5+ labels Metal as MTL0 (older builds used Metal / Metal0).
    device = {
        "metal": "MTL0",
        "vulkan": "Vulkan0",
        "cuda": "CUDA0",
        "rocm": "HIP0",
    }.get(b, "Vulkan0")
    norm = b if b in {"metal", "vulkan", "cuda", "rocm"} else "vulkan"
    return device, norm


def effective_accel_backend(probed: str | None) -> str:
    """A requested backend cannot relabel the capabilities of an installed binary."""
    return str(probed or "cpu").strip().lower()


def requested_backend() -> str | None:
    return accel_backend_override() or accel_backend_from_config()


def platform_preferred_backend(
    supported: Sequence[str],
    *,
    platform: str | None = None,
) -> str:
    """Platform default when auto-selecting among installed GPU backends."""
    plat = platform or sys.platform
    if plat == "darwin":
        return "metal" if "metal" in supported else (supported[0] if supported else "cpu")
    if "cuda" in supported:
        return "cuda"
    if plat.startswith("linux") and "rocm" in supported:
        return "rocm"
    if "vulkan" in supported:
        return "vulkan"
    return supported[0] if supported else "cpu"


def resolve_effective_backend(
    *,
    probed: str,
    supported: Sequence[str],
    requested: str | None,
    platform: str | None = None,
) -> tuple[str, str | None]:
    """Pick runtime backend from hardware probe + installed binary capabilities.

    Config/env ``requested`` is honored only when that backend is actually installed.
    Otherwise fall back to probed hardware (e.g. metal on any Apple Silicon Mac),
    then platform preference, then any installed backend.
    """
    probed_n = str(probed or "cpu").strip().lower()
    supported_set = frozenset(str(b).strip().lower() for b in supported)
    preferred = platform_preferred_backend(supported, platform=platform)

    def _pick(*candidates: str) -> str | None:
        for candidate in candidates:
            c = str(candidate or "").strip().lower()
            if c and c in supported_set:
                return c
        return None

    if requested:
        req = str(requested).strip().lower()
        if req in supported_set:
            return req, None
        fallback = _pick(probed_n, preferred, *supported)
        if fallback:
            return fallback, (
                f"requested {req} is not installed on this binary; "
                f"using {fallback} ({len(supported)} backend(s) available)"
            )
        return "cpu", f"requested {req} is not installed; no GPU backends available"

    fallback = _pick(probed_n, preferred, *supported)
    return (fallback or "cpu"), None


def devices_for_backend(
    installed: Sequence[dict[str, Any]],
    backend: str,
) -> list[dict[str, Any]]:
    """Filter ``--list-devices`` entries for the resolved effective backend."""
    b = str(backend or "cpu").strip().lower()
    if b == "cpu":
        return []
    return [dict(d) for d in installed if str(d.get("backend") or "").lower() == b]


def parse_devices(output: str) -> list[dict[str, Any]]:
    import re

    result: list[dict[str, Any]] = []
    for name, description, total, free in re.findall(
        r"^\s*(\w+): (.*?) \((\d+) MiB, (\d+) MiB free\)", output, re.M
    ):
        prefix = re.sub(r"\d+$", "", name).lower()
        backend = {
            "hip": "rocm",
            "rocm": "rocm",
            "cuda": "cuda",
            "metal": "metal",
            "mtl": "metal",
            "vulkan": "vulkan",
        }.get(prefix)
        if backend:
            result.append(
                dict(
                    index=len(result),
                    device=name,
                    name=description,
                    backend=backend,
                    vram_bytes=int(total) * 1024**2,
                    free_bytes=int(free) * 1024**2,
                )
            )
    return result


def metal_device_init_failed(output: str) -> bool:
    """True when Metal is linked but ``MTLCreateSystemDefaultDevice`` returned nil.

    llama.cpp still registers a zombie device printed as ``: (0 MiB, 0 MiB free)``
    with an empty id — distinct from a healthy ``MTL0: Apple M…`` line.
    """
    import re

    return bool(re.search(r"^\s*:\s*\(\d+ MiB,", output, re.M))


@lru_cache(maxsize=8)
def binary_capabilities(binary: str, mtime_ns: int, epoch: int) -> dict[str, Any]:
    """Cache capability probes; file identity invalidates after installation."""
    import subprocess

    del mtime_ns, epoch
    kwargs: dict[str, Any] = dict(capture_output=True, text=True, timeout=15)
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    devices = subprocess.run([binary, "--list-devices"], **kwargs)
    devices.check_returncode()
    version = subprocess.run([binary, "--version"], **kwargs)
    text = devices.stdout + devices.stderr
    return {
        "devices": parse_devices(text),
        "binary_version": (version.stdout + version.stderr).strip()[-1000:],
        "metal_init_failed": metal_device_init_failed(text),
    }
