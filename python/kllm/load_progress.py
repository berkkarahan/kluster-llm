"""Load-progress sampling for the active inference process.

Mirrors Unsloth Studio's ``/api/inference/load-progress``: while a model load
is in flight the UI polls this endpoint and renders a real bar plus phase
labels ("starting" → "loading" → "ready") instead of a frozen spinner.

The master's llama-server pages GGUF shards into RAM (mmap) before pushing
layers to VRAM / RPC workers, so the signal we sample is:

* process liveness — a crash mid-load is an immediate ``error``;
* the server's RSS (psutil when available) against the local model file size;
* a rolling rate / ETA over RSS growth (same window idea as download progress);
* the upstream ``/health`` probe — once it answers 200 the load is ``ready``.

RSS is a lower bound on "weights resident" in a cluster split (remote layers
live on workers), so callers must treat the fraction as indicative, not exact.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

try:  # psutil is optional; degrade to health-only phases without it
    import psutil
except ImportError:  # pragma: no cover - depends on environment
    psutil = None  # type: ignore[assignment]

_RATE_WINDOW = 15.0  # seconds — matches Unsloth's transfer estimator window

_lock = threading.Lock()
# Active load descriptor, or None when nothing is loading.
_active: dict[str, Any] | None = None
# Rolling (monotonic_ts, rss_bytes) samples for rate / ETA while loading.
_samples: deque[tuple[float, int]] = deque()


def begin(model_name: str, pid: int, total_bytes: int) -> None:
    """Arm progress sampling for a freshly launched inference process."""
    global _active
    with _lock:
        _active = {
            "model": model_name,
            "pid": int(pid),
            "total_bytes": max(0, int(total_bytes)),
            "started_at": time.time(),
        }
        _samples.clear()


def end() -> None:
    """Clear the active load (called on stop / eject / ready)."""
    global _active
    with _lock:
        _active = None
        _samples.clear()


def _rss_bytes(pid: int) -> int | None:
    if psutil is None:
        return None
    try:
        proc = psutil.Process(pid)
        return int(proc.memory_info().rss)
    except (psutil.NoSuchProcess, psutil.AccessDenied):  # pragma: no cover
        return None


def _health_ok(infer_port: int) -> bool:
    try:
        with httpx.Client(timeout=1.0) as client:
            r = client.get(f"http://127.0.0.1:{infer_port}/health")
            return r.status_code == 200
    except Exception:
        return False


def _record_sample(nbytes: int) -> tuple[float | None, float | None]:
    """Append an RSS sample and return (rate_bps, eta_seconds)."""
    now = time.monotonic()
    while _samples and now - _samples[0][0] > _RATE_WINDOW:
        _samples.popleft()
    _samples.append((now, nbytes))
    if len(_samples) < 2:
        return None, None
    t0, b0 = _samples[0]
    t1, b1 = _samples[-1]
    dt = t1 - t0
    if dt <= 0:
        return None, None
    rate = (b1 - b0) / dt
    if rate <= 0:
        return None, None
    return rate, None  # eta filled by caller once total is known


def sample(infer_port: int) -> dict[str, Any]:
    """One load-progress reading for the active load (idle → ``phase=None``)."""
    with _lock:
        active = dict(_active) if _active is not None else None
    if active is None:
        return {
            "phase": None,
            "model": None,
            "bytes_loaded": 0,
            "bytes_total": 0,
            "fraction": 0.0,
            "rate_bps": None,
            "eta_seconds": None,
        }

    pid = active["pid"]
    total = active["total_bytes"]

    if not _alive(pid):
        return {
            "phase": "error",
            "model": active["model"],
            "bytes_loaded": 0,
            "bytes_total": total,
            "fraction": 0.0,
            "rate_bps": None,
            "eta_seconds": None,
            "error": "inference process exited during load",
        }

    if _health_ok(infer_port):
        return {
            "phase": "ready",
            "model": active["model"],
            "bytes_loaded": total,
            "bytes_total": total,
            "fraction": 1.0,
            "rate_bps": None,
            "eta_seconds": None,
        }

    rss = _rss_bytes(pid)
    loaded = rss if rss is not None else 0
    fraction = min(1.0, loaded / total) if total > 0 else 0.0
    phase = "loading" if loaded > 0 else "starting"
    with _lock:
        # Drop samples if begin()/end() raced us away.
        if _active is None or _active.get("pid") != pid:
            rate, eta = None, None
        else:
            rate, _ = _record_sample(loaded)
            eta = (
                max(0.0, (total - loaded) / rate)
                if rate is not None and rate > 0 and total > loaded
                else None
            )
    return {
        "phase": phase,
        "model": active["model"],
        "bytes_loaded": loaded,
        "bytes_total": total,
        "fraction": round(fraction, 4),
        "rate_bps": rate,
        "eta_seconds": eta,
    }


def _alive(pid: int) -> bool:
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:  # pragma: no cover
            pass
    try:
        import os

        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
