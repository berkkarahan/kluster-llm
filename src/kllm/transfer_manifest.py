"""Atomic JSON helpers for GGUF sha256 identity sidecars."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


def identity(path: Path) -> dict:
    s = path.stat()
    return dict(
        size=s.st_size,
        mtime_ns=s.st_mtime_ns,
        ctime_ns=s.st_ctime_ns,
        device=s.st_dev,
        inode=s.st_ino,
    )


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
