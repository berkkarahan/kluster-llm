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
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

try:  # psutil is optional; degrade to health-only phases without it
    import psutil
except ImportError:  # pragma: no cover - depends on environment
    psutil = None

_RATE_WINDOW = 15.0  # seconds — matches Unsloth's transfer estimator window

_lock = threading.Lock()
# Active load descriptor, or None when nothing is loading.
_active: dict[str, Any] | None = None
_latest_terminal: dict[str, Any] | None = None
# Rolling (monotonic_ts, rss_bytes) samples for rate / ETA while loading.
_samples: deque[tuple[float, int]] = deque()


def begin(
    model_name: str,
    pid: int | None,
    total_bytes: int,
    *,
    detail: str | None = None,
) -> None:
    """Arm progress sampling for a freshly launched or staging inference run."""
    global _active
    with _lock:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _active = {
            "operation_id": str(uuid.uuid4()),
            "model": model_name,
            "pid": int(pid) if pid is not None else None,
            "total_bytes": max(0, int(total_bytes)),
            "bytes_loaded": 0,
            "fraction": 0.0,
            "phase": "starting",
            "detail": detail,
            "started_at": stamp,
            "updated_at": stamp,
            "finished_at": None,
        }
        _samples.clear()


def update(
    *,
    pid: int | None = None,
    bytes_loaded: int | None = None,
    total_bytes: int | None = None,
    fraction: float | None = None,
    phase: str | None = None,
    detail: str | None = None,
) -> None:
    """Update active load progress during pre-spawn staging, rank startup, or loading."""
    with _lock:
        if _active is None or _active.get("phase") in {"ready", "error", "cancelled"}:
            return
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if pid is not None:
            _active["pid"] = int(pid)
        if total_bytes is not None:
            _active["total_bytes"] = max(0, int(total_bytes))
        if bytes_loaded is not None:
            loaded = max(0, int(bytes_loaded))
            _active["bytes_loaded"] = loaded
            _record_sample(loaded)
        if fraction is not None:
            _active["fraction"] = round(max(0.0, min(1.0, float(fraction))), 4)
        if phase is not None:
            _active["phase"] = phase
        if detail is not None:
            _active["detail"] = detail
        _active["updated_at"] = stamp


def fail(error: str) -> None:
    """Transition the active load into an error state."""
    with _lock:
        active = dict(_active) if _active is not None else None
    if active is not None and active.get("phase") not in {"ready", "error", "cancelled"}:
        _terminal_snapshot(
            active,
            "error",
            int(active.get("bytes_loaded") or 0),
            float(active.get("fraction") or 0.0),
            error,
        )


def end() -> None:
    """Clear the active load (called on stop / eject / ready)."""
    global _active, _latest_terminal
    with _lock:
        if _active is not None:
            if _active.get("phase") in {"ready", "error", "cancelled"}:
                _latest_terminal = dict(_active)
            else:
                stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                _latest_terminal = {
                    **_active,
                    "phase": "cancelled",
                    "updated_at": stamp,
                    "finished_at": stamp,
                    "bytes_loaded": 0,
                    "fraction": 0.0,
                    "rate_bps": None,
                    "eta_seconds": None,
                }
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
        if _latest_terminal is not None:
            return dict(_latest_terminal)
        return {
            "operation_id": None,
            "phase": None,
            "model": None,
            "bytes_loaded": 0,
            "bytes_total": 0,
            "fraction": 0.0,
            "rate_bps": None,
            "eta_seconds": None,
            "detail": None,
            "started_at": None,
            "updated_at": None,
            "finished_at": None,
        }

    with _lock:
        still_current = (
            _active is not None and _active.get("operation_id") == active["operation_id"]
        )
    if not still_current:
        return sample(infer_port)

    if active.get("phase") in {"ready", "error", "cancelled"}:
        return active

    pid = active.get("pid")
    total = int(active.get("total_bytes") or 0)
    reported_loaded = int(active.get("bytes_loaded") or 0)
    reported_fraction = float(active.get("fraction") or 0.0)
    detail = active.get("detail")

    if pid is None:
        loaded = reported_loaded
        fraction = (
            reported_fraction
            if reported_fraction > 0
            else (min(1.0, loaded / total) if total > 0 else 0.0)
        )
        phase = active.get("phase") or ("loading" if loaded > 0 else "starting")
        with _lock:
            if len(_samples) >= 2:
                t0, b0 = _samples[0]
                t1, b1 = _samples[-1]
                dt = t1 - t0
                rate = (b1 - b0) / dt if dt > 0 and b1 > b0 else None
            else:
                rate = None
            eta = (
                max(0.0, (total - loaded) / rate)
                if rate is not None and rate > 0 and total > loaded
                else None
            )
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "operation_id": active["operation_id"],
            "phase": phase,
            "model": active["model"],
            "bytes_loaded": loaded,
            "bytes_total": total,
            "fraction": round(fraction, 4),
            "rate_bps": rate,
            "eta_seconds": eta,
            "detail": detail,
            "started_at": active["started_at"],
            "updated_at": stamp,
            "finished_at": None,
        }

    if not _alive(pid):
        terminal = _terminal_snapshot(
            active, "error", 0, 0.0, "inference process exited during load"
        )
        return terminal if terminal is not None else sample(infer_port)

    if _health_ok(infer_port):
        terminal = _terminal_snapshot(active, "ready", total, 1.0)
        return terminal if terminal is not None else sample(infer_port)

    rss = _rss_bytes(pid)
    loaded = max(rss if rss is not None else 0, reported_loaded)
    raw_fraction = min(1.0, loaded / total) if total > 0 else 0.0
    fraction = max(reported_fraction, raw_fraction)
    phase = "loading" if loaded > 0 or reported_fraction > 0 else "starting"
    with _lock:
        # Drop samples if begin()/end() raced us away.
        if _active is None or _active.get("operation_id") != active["operation_id"]:
            rate, eta = None, None
        else:
            rate, _ = _record_sample(loaded)
            eta = (
                max(0.0, (total - loaded) / rate)
                if rate is not None and rate > 0 and total > loaded
                else None
            )
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "operation_id": active["operation_id"],
        "phase": phase,
        "model": active["model"],
        "bytes_loaded": loaded,
        "bytes_total": total,
        "fraction": round(fraction, 4),
        "rate_bps": rate,
        "eta_seconds": eta,
        "detail": detail,
        "started_at": active["started_at"],
        "updated_at": stamp,
        "finished_at": None,
    }


def _terminal_snapshot(
    active: dict[str, Any],
    phase: str,
    loaded: int,
    fraction: float,
    error: str | None = None,
) -> dict[str, Any] | None:
    global _active, _latest_terminal
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    snap = {
        "operation_id": active["operation_id"],
        "phase": phase,
        "model": active["model"],
        "bytes_loaded": loaded,
        "bytes_total": active["total_bytes"],
        "fraction": fraction,
        "rate_bps": None,
        "eta_seconds": None,
        "detail": active.get("detail"),
        "started_at": active["started_at"],
        "updated_at": stamp,
        "finished_at": stamp,
    }
    if error:
        snap["error"] = error
    with _lock:
        if _active is None or _active.get("operation_id") != active["operation_id"]:
            return None
        _active = snap
        _latest_terminal = dict(snap)
    return snap


def records(infer_port: int) -> dict[str, dict[str, Any] | None]:
    """Return the active load separately from its retained terminal outcome."""
    progress = sample(infer_port)
    if progress.get("phase") in {"ready", "error", "cancelled"}:
        return {"current": None, "last_terminal": progress}
    if progress.get("phase") is None:
        return {
            "current": None,
            "last_terminal": dict(_latest_terminal) if _latest_terminal else None,
        }
    return {
        "current": progress,
        "last_terminal": dict(_latest_terminal) if _latest_terminal else None,
    }


def _alive(pid: int) -> bool:
    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
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
