"""Atomic file replacement that survives Windows sharing violations.

``os.replace`` maps to ``MoveFileExW(MOVEFILE_REPLACE_EXISTING)`` on Windows,
which fails with ``ERROR_ACCESS_DENIED`` (WinError 5) or
``ERROR_SHARING_VIOLATION`` (WinError 32) whenever another process has the
destination open without ``FILE_SHARE_DELETE``. Python's ``open``/``read_text``
do not share delete, so a reader as brief as a status poll can trip a save.
Those collisions are transient, so retry briefly rather than failing the
request; a read-only destination is the one persistent cause of the same error,
so clear that attribute once before retrying.
"""
from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

_RETRY_SECONDS = 3.0
_RETRY_MAX_DELAY = 0.25
# ERROR_ACCESS_DENIED / ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION.
_WINDOWS_LOCK_ERRORS = frozenset({5, 32, 33})


def _is_transient_lock(exc: PermissionError) -> bool:
    if sys.platform != "win32":
        return False
    return getattr(exc, "winerror", None) in _WINDOWS_LOCK_ERRORS


def _clear_readonly(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if not mode & stat.S_IWRITE:
        try:
            os.chmod(path, mode | stat.S_IWRITE)
        except OSError:
            pass


def replace_file(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """Atomically replace ``dst`` with ``src``, tolerating Windows locks.

    Non-Windows and non-lock ``PermissionError`` values propagate unchanged, so
    POSIX semantics and genuinely unrecoverable errors are not hidden.
    """
    src_path = os.fspath(src)
    dst_path = os.fspath(dst)
    try:
        os.replace(src_path, dst_path)
        return
    except PermissionError as exc:
        if not _is_transient_lock(exc):
            raise
    _clear_readonly(Path(dst_path))
    deadline = time.monotonic() + _RETRY_SECONDS
    delay = 0.01
    while True:
        try:
            os.replace(src_path, dst_path)
            return
        except PermissionError as exc:
            if not _is_transient_lock(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, _RETRY_MAX_DELAY)
