"""Accelerator backend hints (metal/cuda/rocm/vulkan/cpu) for cluster membership."""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Sequence

VALID_ACCEL_BACKENDS = frozenset({"metal", "cuda", "rocm", "vulkan", "cpu"})
# User-selectable GPU engines (cpu is device mode, not a backend picker value).
SELECTABLE_ACCEL_BACKENDS = frozenset({"metal", "cuda", "rocm", "vulkan"})

# One accelerator init at a time: a cold Metal/Vulkan start can exceed the
# probe timeout when it contends with a second probe (capacity refresh and RPC
# startup both probe the same managed binary).
# A binary that has never completed a probe gets the longer budget. Killing the
# first exec (macOS Gatekeeper or Metal init) leaves every later probe cold.
_PROBE_TIMEOUT_S = 15
_PROBE_FIRST_TIMEOUT_S = 120
_PROBE_TTL_S = 30.0
_PROBE_CACHE_MAX = 4
_probe_lock = threading.Lock()
_probe_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}

# Shown when ``--list-devices`` is killed before a cold first launch finishes.
PROBE_TIMEOUT_HINT = (
    "First launch of a freshly installed runtime can exceed the probe timeout; "
    "run `llama-server --list-devices` once manually"
)


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
        from kllm.shared import config as kllm_config

        raw = kllm_config.load_config().get("accel_backend")
    except Exception:
        return None
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s not in VALID_ACCEL_BACKENDS:
        return None
    return s


# Planning VRAM for unified-memory nodes, as a share of total RAM.
# Reported macOS VRAM stays at the native 80% probe; this share is planning-only.
UNIFIED_VRAM_FRACTION_DEFAULT = 0.65
UNIFIED_VRAM_FRACTION_MIN = 0.10
UNIFIED_VRAM_FRACTION_MAX = 0.95

# Substrings of GPU names that share host RAM (AMD APUs such as Strix Halo, Intel iGPU).
# Apple Silicon is detected from the metal backend, not from these names.
INTEGRATED_GPU_NAME_MARKERS: tuple[str, ...] = (
    "radeon(tm) graphics",
    "radeon 780m",
    "radeon 880m",
    "radeon 890m",
    "radeon 8040s",
    "radeon 8050s",
    "radeon 8060s",
    "strix",
    "iris xe",
    "uhd graphics",
)


def unified_vram_fraction() -> float:
    """Share of total RAM treated as effective VRAM on unified-memory nodes.

    ``KLLM_UNIFIED_VRAM_FRACTION`` overrides the 0.65 default. Values outside
    0.10–0.95 are clamped; missing or unparsable values keep the default.
    """
    raw = os.environ.get("KLLM_UNIFIED_VRAM_FRACTION", "").strip()
    if not raw:
        return UNIFIED_VRAM_FRACTION_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        return UNIFIED_VRAM_FRACTION_DEFAULT
    if value != value:  # NaN
        return UNIFIED_VRAM_FRACTION_DEFAULT
    return min(UNIFIED_VRAM_FRACTION_MAX, max(UNIFIED_VRAM_FRACTION_MIN, value))


def _gpu_name(gpu: Any) -> str:
    name = gpu.get("name") if isinstance(gpu, dict) else getattr(gpu, "name", None)
    return str(name).strip().lower() if name else ""


def unified_memory_override() -> bool | None:
    """Local ``unified_memory`` config override, or None when unset."""
    try:
        from kllm.shared import config as kllm_config

        raw = kllm_config.load_config().get("unified_memory")
    except Exception:
        return None
    if isinstance(raw, bool):
        return raw
    return None


def is_integrated_gpu(name: str | None, backend: str | None = None) -> bool:
    """True when one adapter shares host RAM (iGPU / APU graphics).

    A discrete card keeps its own VRAM even when the same machine also exposes
    an integrated adapter, so callers classify per device instead of labelling
    the whole node from any single name. ``metal`` is one unified device, not an
    integrated one for selection purposes.
    """
    if str(backend or "").strip().lower() == "metal":
        return False
    text = str(name or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in INTEGRATED_GPU_NAME_MARKERS)


def detect_unified_memory(backend: str | None, gpus: Sequence[Any] | None = None) -> bool:
    """True when the node's usable GPU pool *is* host RAM. Ignores config.

    Metal is always unified. Otherwise every reported device must be an
    integrated adapter: a machine that also has a discrete card keeps
    dedicated VRAM, so it is planned as a discrete node even when an iGPU is
    also present.
    """
    if str(backend or "").strip().lower() == "metal":
        return True
    devices = list(gpus or ())
    return bool(devices) and all(is_integrated_gpu(_gpu_name(gpu), backend) for gpu in devices)


def is_unified_memory(backend: str | None, gpus: Sequence[Any] | None = None) -> bool:
    """Whether this machine's memory is one shared RAM/GPU pool.

    A ``unified_memory`` boolean in ``~/.kllm/config.toml`` wins when set.
    Otherwise Metal is unified, then the GPU-name heuristic.
    """
    override = unified_memory_override()
    if override is not None:
        return override
    return detect_unified_memory(backend, gpus)


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


class ProbeTimeoutError(RuntimeError):
    """``llama-server`` was killed before ``--list-devices`` could finish."""


def probe_timeout_message(binary: str, timeout: float) -> str:
    """Explain a killed capability probe, including the command to warm it."""
    return (
        f"Command {[binary, '--list-devices']!r} timed out after {timeout:g} seconds. "
        "First launch of a freshly installed runtime can exceed the probe timeout; "
        f"run `{binary} --list-devices` once manually"
    )


def binary_capabilities(binary: str, mtime_ns: int, epoch: int) -> dict[str, Any]:
    """Probe one binary's devices with a single-flight, short-TTL cache.

    ``epoch`` keeps the caller's periodic refresh intent; the lock and TTL also
    stop the capacity refresh and an RPC start from launching two accelerator
    probes at the same time. File identity (``mtime_ns``) still invalidates
    after a runtime installation. The first successful probe of a binary may
    take much longer than a later one, so only an identity already in the cache
    keeps the short timeout.
    """
    del epoch
    key = (binary, mtime_ns)
    with _probe_lock:
        cached = _probe_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < _PROBE_TTL_S:
            return cached[1]
        timeout = _PROBE_TIMEOUT_S if key in _probe_cache else _PROBE_FIRST_TIMEOUT_S
        result = _run_capability_probe(binary, timeout=timeout)
        _probe_cache[key] = (time.monotonic(), result)
        if len(_probe_cache) > _PROBE_CACHE_MAX:
            oldest = min(_probe_cache, key=lambda item: _probe_cache[item][0])
            _probe_cache.pop(oldest, None)
        return result


def _run_capability_probe(binary: str, *, timeout: float = _PROBE_TIMEOUT_S) -> dict[str, Any]:
    import subprocess

    kwargs: dict[str, Any] = dict(capture_output=True, text=True, timeout=timeout)
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        devices = subprocess.run([binary, "--list-devices"], **kwargs)
        devices.check_returncode()
        version = subprocess.run([binary, "--version"], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise ProbeTimeoutError(probe_timeout_message(binary, timeout)) from exc
    text = devices.stdout + devices.stderr
    return {
        "devices": parse_devices(text),
        "binary_version": (version.stdout + version.stderr).strip()[-1000:],
        "metal_init_failed": metal_device_init_failed(text),
    }
