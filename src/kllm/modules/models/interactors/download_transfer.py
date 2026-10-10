"""HTTP streaming and atomic file publication for model downloads."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from kllm.shared.exceptions import DownloadCancelledError
from kllm.modules.models.interactors.store import write_sha256_sidecar

ProgressCallback = Callable[[dict[str, Any]], None]
ProgressReporter = Callable[[str, int, int | None, ProgressCallback | None], None]
CompletionReporter = Callable[[str, int | None], None]


def _content_length(headers: Any) -> int:
    try:
        raw = headers.get("Content-Length")
    except Exception:
        raw = None
    if raw is None and isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == "content-length":
                raw = value
                break
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0


def stream_download(
    url: str,
    target: Path,
    *,
    headers: dict[str, Any] | None,
    on_progress: ProgressCallback | None,
    cancel_event: threading.Event | None,
    report_progress: ProgressReporter,
    report_complete: CompletionReporter,
    stream_client: Callable[..., Any] = httpx.stream,
    sidecar_writer: Callable[[Path, str], None] = write_sha256_sidecar,
) -> Path:
    """Stream into a part file, publish its digest, then atomically rename it."""
    part = target.with_name(target.name + ".part")
    filename = target.name
    nbytes = 0
    hasher = hashlib.sha256()
    try:
        with stream_client(
            "GET", url, headers=headers or {}, follow_redirects=True, timeout=5.0
        ) as response:
            response.raise_for_status()
            length = _content_length(response.headers)
            total = length if length > 0 else None
            report_progress(filename, 0, total, on_progress)
            with part.open("wb") as output:
                for chunk in response.iter_bytes():
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelledError("download cancelled")
                    if not chunk:
                        continue
                    output.write(chunk)
                    hasher.update(chunk)
                    nbytes += len(chunk)
                    report_progress(filename, nbytes, total, on_progress)
        part.replace(target)
        sidecar_writer(target, hasher.hexdigest())
        report_complete(filename, total)
    except Exception:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return target
