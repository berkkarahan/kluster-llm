"""HTTP streaming and atomic file publication for model downloads."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from kllm.shared.exceptions import DownloadCancelledError
from kllm.modules.models.interactors.store import write_sha256_sidecar

ProgressCallback = Callable[[dict[str, Any]], None]
ProgressReporter = Callable[[str, int, int | None, ProgressCallback | None], None]
CompletionReporter = Callable[[str, int | None], None]

DEFAULT_STREAM_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=30.0)
MAX_RETRIES = 3


class DownloadError(Exception):
    """Raised when a model download stream fails."""


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
    timeout: httpx.Timeout | float = DEFAULT_STREAM_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> Path:
    """Stream into a part file, publish its digest, then atomically rename it."""
    part = target.with_name(target.name + ".part")
    filename = target.name
    req_headers = dict(headers or {})
    retries = 0

    while True:
        offset = part.stat().st_size if part.exists() else 0
        if offset > 0:
            req_headers["Range"] = f"bytes={offset}-"
        else:
            req_headers.pop("Range", None)

        try:
            with stream_client(
                "GET", url, headers=req_headers, follow_redirects=True, timeout=timeout
            ) as response:
                if response.status_code == 416:
                    try:
                        part.unlink()
                    except OSError:
                        pass
                    offset = 0
                    req_headers.pop("Range", None)
                    continue

                response.raise_for_status()

                is_partial = response.status_code == 206
                if not is_partial:
                    offset = 0
                    open_mode = "wb"
                else:
                    open_mode = "ab"

                length = _content_length(response.headers)
                total = (length + offset) if length > 0 else None
                report_progress(filename, offset, total, on_progress)

                with part.open(open_mode) as output:
                    try:
                        chunk_iter = response.iter_bytes(chunk_size=65536)
                    except TypeError:
                        chunk_iter = response.iter_bytes()

                    for chunk in chunk_iter:
                        if cancel_event is not None and cancel_event.is_set():
                            raise DownloadCancelledError("download cancelled")
                        if not chunk:
                            continue
                        output.write(chunk)
                        offset += len(chunk)
                        report_progress(filename, offset, total, on_progress)

            part.replace(target)
            hasher = hashlib.sha256()
            with target.open("rb") as f:
                while chunk := f.read(1048576):
                    hasher.update(chunk)
            sidecar_writer(target, hasher.hexdigest())
            report_complete(filename, total)
            return target

        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as err:
            retries += 1
            if retries > max_retries:
                # Do NOT unlink part on network timeout or disconnect to allow range resume
                raise DownloadError(f"Download stream failed after {max_retries} retries: {err}") from err
            time.sleep(min(2 ** (retries - 1), 5))
            continue
        except DownloadCancelledError:
            try:
                part.unlink()
            except OSError:
                pass
            raise
        except Exception:
            try:
                part.unlink()
            except OSError:
                pass
            raise
