"""Content-bound transfer manifests and atomic progress records."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

PIECE_SIZE = 8 * 1024 * 1024
_lock = threading.Lock()


def identity(path: Path) -> dict:
    s = path.stat()
    return dict(size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                device=s.st_dev, inode=s.st_ino)


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


def manifest_for(path: Path, piece_size: int = PIECE_SIZE, on_progress=None) -> dict:
    with _lock:
        before = identity(path)
        cache = path.with_name(path.name + ".manifest.json")
        old = read_json(cache)
        if old.get("identity") == before and old.get("piece_size") == piece_size:
            try:
                validate(old, before["size"], old.get("sha256", ""))
            except (ValueError, TypeError):
                pass
            else:
                if on_progress:
                    on_progress(before["size"], before["size"])
                return old
        whole = hashlib.sha256()
        pieces = []
        done = 0
        with path.open("rb") as f:
            while block := f.read(piece_size):
                whole.update(block)
                pieces.append(hashlib.sha256(block).hexdigest())
                done += len(block)
                if on_progress:
                    on_progress(done, before["size"])
        if before != identity(path):
            raise ValueError("source changed while building transfer manifest")
        manifest = dict(version=1, filename=path.name, identity=before,
                        size=done, sha256=whole.hexdigest(), piece_size=piece_size, pieces=pieces)
        atomic_json(cache, manifest)
        return manifest


def validate(manifest: dict, size: int, digest: str) -> None:
    piece_size = manifest.get("piece_size", 0)
    if (manifest.get("version") != 1 or manifest.get("size") != size
            or manifest.get("sha256") != digest or not isinstance(piece_size, int)
            or not 0 < piece_size <= PIECE_SIZE):
        raise ValueError("invalid transfer manifest")
    hashes = [digest, *manifest.get("pieces", [])]
    if len(hashes) - 1 != (size + piece_size - 1) // piece_size:
        raise ValueError("invalid manifest piece count")
    if any(not isinstance(h, str) or len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in hashes):
        raise ValueError("invalid manifest hash")
