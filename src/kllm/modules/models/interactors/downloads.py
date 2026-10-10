"""Download lifecycle state, transfer progress, and model streaming."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from huggingface_hub import hf_hub_url, list_repo_files
from huggingface_hub.utils import build_hf_headers

from kllm.shared.hf_token import resolve_hf_token
from kllm.shared.exceptions import DownloadInProgressError
from kllm.shared.state import models_dir
from kllm.modules.models.interactors.download_progress import DownloadProgress
from kllm.modules.models.interactors.download_transfer import (
    DownloadCancelledError as DownloadCancelledError,
    ProgressCallback,
    _content_length as _content_length,
    stream_download,
)
from kllm.modules.models.interactors.query import _is_hf_website, _is_http_url, parse_model_search_query, resolve_hf_filename as _resolve_hf_filename


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def resolve_hf_filename(repo_id: str, suffix: str, *, token: str | None = None) -> str:
    """Resolve a Hub filename through the replaceable Hub listing client."""
    return _resolve_hf_filename(repo_id, suffix, token=token, list_files=list_repo_files)


_DOWNLOAD_LOCK = threading.Lock()
_DOWNLOAD_PROGRESS: DownloadProgress | None = None
_DOWNLOAD_LAST_TERMINAL: DownloadProgress | None = None
_DOWNLOAD_CANCEL: threading.Event | None = None
# Rolling (monotonic_time, bytes) samples for transfer-rate / ETA estimation.
_DOWNLOAD_SAMPLES: deque[tuple[float, int]] = deque()
_RATE_WINDOW = 10.0

_TERMINAL_DOWNLOAD_PHASES = frozenset({"complete", "error", "cancelled"})

_PROGRESS_MIN_INTERVAL = 0.1


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[:180]


def _pct(nbytes: int, total: int | None) -> int:
    if not total:
        return 0
    return min(100, int(nbytes * 100 / total))


def get_download_progress() -> dict[str, Any] | None:
    """In-memory snapshot, or None when idle. Not persisted."""
    return _DOWNLOAD_PROGRESS.to_mapping() if _DOWNLOAD_PROGRESS is not None else None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def begin_download(source: str, dest_name: str | None = None) -> dict[str, Any]:
    """Reserve a download operation before its background runner starts."""
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL
    if _DOWNLOAD_PROGRESS is not None and _DOWNLOAD_PROGRESS.phase not in _TERMINAL_DOWNLOAD_PHASES:
        raise DownloadInProgressError("a download is already in progress")
    if not _DOWNLOAD_LOCK.acquire(blocking=False):
        raise DownloadInProgressError("a download is already in progress")
    stamp = _timestamp()
    filename = _safe_name(dest_name or Path(urlparse(source).path).name or "model.gguf")
    _DOWNLOAD_CANCEL = threading.Event()
    _DOWNLOAD_PROGRESS = DownloadProgress(
        operation_id=str(uuid.uuid4()),
        filename=filename,
        bytes=0,
        total=None,
        pct=0,
        phase="queued",
        rate_bps=None,
        eta_seconds=None,
        started_at=stamp,
        updated_at=stamp,
        finished_at=None,
    )
    return _DOWNLOAD_PROGRESS.to_mapping()


def _reset_download_state() -> None:
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL, _DOWNLOAD_LAST_TERMINAL
    if _DOWNLOAD_PROGRESS is not None and _DOWNLOAD_PROGRESS.phase in _TERMINAL_DOWNLOAD_PHASES:
        _DOWNLOAD_LAST_TERMINAL = _DOWNLOAD_PROGRESS
    _DOWNLOAD_PROGRESS = None
    _DOWNLOAD_CANCEL = None
    _DOWNLOAD_SAMPLES.clear()
    if _DOWNLOAD_LOCK.locked():
        try:
            _DOWNLOAD_LOCK.release()
        except RuntimeError:
            pass


def cancel_download() -> bool:
    """Signal the in-flight download loop to stop. True when a job was running."""
    ev = _DOWNLOAD_CANCEL
    if ev is None:
        return False
    ev.set()
    return True


def _set_progress(
    filename: str,
    nbytes: int,
    total: int | None,
    on_progress: ProgressCallback | None = None,
) -> None:
    global _DOWNLOAD_PROGRESS
    now = time.monotonic()
    if total is not None and total > 0:
        while _DOWNLOAD_SAMPLES and now - _DOWNLOAD_SAMPLES[0][0] > _RATE_WINDOW:
            _DOWNLOAD_SAMPLES.popleft()
        _DOWNLOAD_SAMPLES.append((now, nbytes))
        rate = _transfer_rate(nbytes)
        eta = (
            max(0.0, (total - nbytes) / rate)
            if rate is not None and rate > 0 and total > nbytes
            else None
        )
    else:
        rate, eta = None, None
    previous = _DOWNLOAD_PROGRESS
    stamp = _timestamp()
    _DOWNLOAD_PROGRESS = DownloadProgress(
        operation_id=previous.operation_id if previous else str(uuid.uuid4()),
        filename=filename,
        bytes=nbytes,
        total=total,
        pct=_pct(nbytes, total),
        phase="downloading",
        rate_bps=rate,
        eta_seconds=eta,
        started_at=previous.started_at if previous else stamp,
        updated_at=stamp,
        finished_at=None,
    )
    if on_progress is not None:
        on_progress(_DOWNLOAD_PROGRESS.to_mapping())


def _transfer_rate(current_bytes: int) -> float | None:
    """Rolling transfer rate over the recent sample window; None when unmeasurable."""
    samples = _DOWNLOAD_SAMPLES
    if len(samples) < 2:
        return None
    t0, b0 = samples[0]
    t1, b1 = samples[-1]
    dt = t1 - t0
    if dt <= 0.05 or b1 <= b0:
        return None
    return (b1 - b0) / dt


def finalize_download_progress(filename: str, total: int | None) -> None:
    """Mark the active download complete so pollers see a terminal phase."""
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_LAST_TERMINAL
    if _DOWNLOAD_PROGRESS is None or _DOWNLOAD_PROGRESS.filename != filename:
        return
    stamp = _timestamp()
    snap = replace(
        _DOWNLOAD_PROGRESS,
        phase="complete",
        rate_bps=None,
        eta_seconds=None,
        updated_at=stamp,
        finished_at=stamp,
        bytes=total if total else _DOWNLOAD_PROGRESS.bytes,
        pct=100 if total else _DOWNLOAD_PROGRESS.pct,
    )
    _DOWNLOAD_PROGRESS = snap
    _DOWNLOAD_LAST_TERMINAL = snap


def fail_download_progress(filename: str, error: str) -> None:
    """Mark the active download failed with a message."""
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_LAST_TERMINAL
    if _DOWNLOAD_PROGRESS is None or _DOWNLOAD_PROGRESS.filename != filename:
        return
    stamp = _timestamp()
    snap = replace(
        _DOWNLOAD_PROGRESS,
        phase="error",
        rate_bps=None,
        eta_seconds=None,
        error=error,
        updated_at=stamp,
        finished_at=stamp,
    )
    _DOWNLOAD_PROGRESS = snap
    _DOWNLOAD_LAST_TERMINAL = snap


def download_job_snapshot() -> dict[str, Any] | None:
    """Job-level view for /v1/models/download-progress (None when idle)."""
    snap = _DOWNLOAD_PROGRESS or _DOWNLOAD_LAST_TERMINAL
    if snap is None:
        return None
    out = snap.to_mapping()
    out["state"] = "running" if snap.phase in {"queued", "downloading"} else snap.phase
    return out


def download_progress_records() -> dict[str, dict[str, Any] | None]:
    """Return the active operation separately from its retained terminal outcome."""
    global _DOWNLOAD_LAST_TERMINAL
    snap = download_job_snapshot()
    if snap is not None and snap.get("state") in _TERMINAL_DOWNLOAD_PHASES:
        if _DOWNLOAD_PROGRESS is not None:
            _DOWNLOAD_LAST_TERMINAL = _DOWNLOAD_PROGRESS
        snap = None
    terminal = _DOWNLOAD_LAST_TERMINAL.to_mapping() if _DOWNLOAD_LAST_TERMINAL else None
    if terminal is not None and _DOWNLOAD_LAST_TERMINAL is not None:
        terminal["state"] = _DOWNLOAD_LAST_TERMINAL.phase
    return {
        "current": snap,
        "last_terminal": terminal,
    }


def set_download_terminal(
    filename: str,
    total: int | None,
    phase: str,
    error: str | None = None,
    operation_id: str | None = None,
) -> None:
    """Publish a terminal snapshot for an async (thread-pool) download.

    ``download_model`` clears its progress state on return, so background
    runners publish the terminal state themselves; pollers then see
    complete/error before the runner forgets it.
    """
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_LAST_TERMINAL
    previous = _DOWNLOAD_PROGRESS
    if operation_id is not None and previous is not None and previous.operation_id != operation_id:
        return
    stamp = _timestamp()
    snap = DownloadProgress(
        operation_id=(
            operation_id or (previous.operation_id if previous else None) or str(uuid.uuid4())
        ),
        filename=filename,
        bytes=total if total and phase == "complete" else 0,
        total=total,
        pct=100 if phase == "complete" else 0,
        phase=phase,
        rate_bps=None,
        eta_seconds=None,
        started_at=previous.started_at if previous else stamp,
        updated_at=stamp,
        finished_at=stamp,
        error=error,
    )
    _DOWNLOAD_PROGRESS = snap
    _DOWNLOAD_LAST_TERMINAL = snap


def _hub_headers(token: str | None) -> dict[str, str]:
    return build_hf_headers(token=token if token else False)


def _stream_to_models(
    url: str,
    dest_name: str | None,
    *,
    headers: dict[str, Any] | None = None,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    parsed = urlparse(url)
    name = dest_name or Path(parsed.path).name or "model.gguf"
    filename = _safe_name(name)
    return stream_download(
        url,
        models_dir() / filename,
        headers=headers,
        on_progress=on_progress,
        cancel_event=cancel_event,
        report_progress=_set_progress,
        report_complete=finalize_download_progress,
        stream_client=httpx.stream,
    )


def download_model(
    source: str,
    dest_name: str | None = None,
    *,
    on_progress: ProgressCallback | None = None,
    hub_url_client: Callable[..., str] = hf_hub_url,
    filename_resolver: Callable[..., str] = resolve_hf_filename,
    token_resolver: Callable[[], str | None] = resolve_hf_token,
) -> Path:
    """Download a GGUF from Hugging Face repo id, HF URL, or direct HTTP(S) URL."""
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL
    reserved = (
        _DOWNLOAD_LOCK.locked()
        and _DOWNLOAD_PROGRESS is not None
        and _DOWNLOAD_PROGRESS.phase == "queued"
    )
    if not reserved and not _DOWNLOAD_LOCK.acquire(blocking=False):
        raise DownloadInProgressError("a download is already in progress")
    if reserved:
        cancel_event = _DOWNLOAD_CANCEL
        if cancel_event is None:  # pragma: no cover - reservation always creates one
            raise RuntimeError("download reservation is missing its cancellation event")
    else:
        _DOWNLOAD_PROGRESS = None  # A retained terminal must not become this operation.
        cancel_event = threading.Event()
        _DOWNLOAD_CANCEL = cancel_event
    keep_snapshot = reserved
    try:
        models_dir().mkdir(parents=True, exist_ok=True)
        token = token_resolver()

        if _is_http_url(source):
            if _is_hf_website(source):
                parsed = parse_model_search_query(source)
                repo_id = parsed.repo_id
                filename = parsed.filename
                if not repo_id or not filename:
                    raise ValueError(
                        "Repository and tree links are not file downloads. "
                        "Search for a supported file, then download that result."
                    )
                revision = parsed.revision or "main"
                url = hub_url_client(repo_id, filename, revision=revision)
                name = dest_name or _basename(filename)
                return _stream_to_models(
                    url,
                    name,
                    headers=_hub_headers(token),
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                )
            return _stream_to_models(
                source,
                dest_name,
                headers={},
                on_progress=on_progress,
                cancel_event=cancel_event,
            )

        if ":" in source:
            repo_id, suffix = source.split(":", 1)
            filename = filename_resolver(repo_id, suffix, token=token)
            url = hub_url_client(repo_id, filename)
            name = dest_name or Path(filename).name
            return _stream_to_models(
                url,
                name,
                headers=_hub_headers(token),
                on_progress=on_progress,
                cancel_event=cancel_event,
            )

        raise ValueError(
            "source must be an HTTP(S) URL or 'org/repo:filename.gguf' / "
            "'org/repo:Q4_K_M' Hugging Face ref"
        )
    except DownloadCancelledError:
        snap = get_download_progress()
        if snap is not None:
            fail_download_progress(snap["filename"], "cancelled")
            if _DOWNLOAD_PROGRESS is not None:
                _DOWNLOAD_PROGRESS = replace(_DOWNLOAD_PROGRESS, phase="cancelled")
        keep_snapshot = True  # let pollers see the terminal cancelled state
        raise
    except Exception as exc:
        snap = get_download_progress()
        if snap is not None:
            fail_download_progress(snap["filename"], str(exc))
        raise
    finally:
        if not keep_snapshot:
            _DOWNLOAD_PROGRESS = None
        _DOWNLOAD_CANCEL = None
        _DOWNLOAD_SAMPLES.clear()
        _DOWNLOAD_LOCK.release()
