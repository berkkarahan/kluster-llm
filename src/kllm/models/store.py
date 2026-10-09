from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kllm.gguf_meta import read_context_length
from kllm.model_defaults import resolve_model_defaults
from kllm.state import models_dir
from kllm.transfer_manifest import atomic_json, identity, read_json

_TRANSFER_CHUNK = 1024 * 1024
_PROGRESS_MIN_INTERVAL = 0.1


class _Throttle:
    """Rate-limit progress snapshots (~4–10 Hz) unless forced."""

    def __init__(self, interval: float = _PROGRESS_MIN_INTERVAL) -> None:
        self.interval = interval
        self._last = 0.0

    def allow(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if force or (now - self._last) >= self.interval:
            self._last = now
            return True
        return False


def delete_local_model(name: str) -> Path:
    """Delete a GGUF under models_dir(). Rejects paths outside that directory."""
    path = resolve_local_model(name).resolve()
    root = models_dir().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"model is outside models directory: {path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"model not found: {name}")
    path.unlink()
    side = sha256_sidecar_path(path)
    try:
        side.unlink(missing_ok=True)
    except OSError:
        pass
    identity_path = path.with_name(path.name + ".sha256.identity")
    try:
        identity_path.unlink(missing_ok=True)
    except OSError:
        pass
    return path


def ensure_sha256(
    model_path: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Return digest from sidecar or hash the file (optionally with progress)."""
    cached = read_sha256_sidecar(model_path)
    if cached:
        if on_progress is not None:
            total = model_path.stat().st_size
            on_progress(total, total)
        return cached
    total = model_path.stat().st_size
    hasher = hashlib.sha256()
    nbytes = 0
    throttle = _Throttle()
    if on_progress is not None:
        on_progress(0, total)
    with model_path.open("rb") as f:
        while True:
            block = f.read(_TRANSFER_CHUNK)
            if not block:
                break
            hasher.update(block)
            nbytes += len(block)
            if on_progress is not None and throttle.allow(force=nbytes >= total):
                on_progress(nbytes, total)
    digest = hasher.hexdigest()
    write_sha256_sidecar(model_path, digest)
    if on_progress is not None:
        on_progress(total, total)
    return digest


def list_local_models() -> list[dict]:
    """Return local GGUF files under ~/.kllm/models."""
    out: list[dict] = []
    root = models_dir()
    for path in sorted(root.glob("*.gguf")):
        try:
            st = path.stat()
        except OSError:
            continue
        max_context = read_context_length(path)
        entry: dict = {
            "id": path.name,
            "object": "model",
            "owned_by": "kllm",
            "filename": path.name,
            "path": str(path),
            "size_bytes": st.st_size,
        }
        if max_context is not None:
            entry["max_context"] = max_context
        try:
            defaults = resolve_model_defaults(path)
            public = defaults.to_public_dict()
            if (
                public.get("sampling")
                or public.get("chat_template_kwargs")
                or public.get("reasoning")
            ):
                entry["defaults"] = public
        except Exception:
            pass
        out.append(entry)
    return out


def local_model_meta(model_path: Path) -> dict[str, Any]:
    """Size + sidecar digest for skip-if-identical probes (does not hash)."""
    st = model_path.stat()
    return {
        "exists": True,
        "filename": model_path.name,
        "size_bytes": st.st_size,
        "sha256": read_sha256_sidecar(model_path),
    }


def read_sha256_sidecar(model_path: Path) -> str | None:
    if read_json(model_path.with_name(model_path.name + ".sha256.identity")) != identity(
        model_path
    ):
        return None
    side = sha256_sidecar_path(model_path)
    if not side.is_file():
        return None
    try:
        text = side.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text.lower()):
        return None
    return text.lower()


def resolve_local_model(name: str) -> Path:
    path = Path(name)
    if path.is_file():
        return path
    candidate = models_dir() / name
    if candidate.is_file():
        return candidate
    matches = list(models_dir().glob(f"*{name}*"))
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"model not found: {name}")


def sha256_file(path: Path, chunk: int = _TRANSFER_CHUNK) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_sidecar_path(model_path: Path) -> Path:
    return model_path.with_name(model_path.name + ".sha256")


def write_sha256_sidecar(model_path: Path, digest: str) -> None:
    sha256_sidecar_path(model_path).write_text(digest.strip() + "\n", encoding="utf-8")
    atomic_json(model_path.with_name(model_path.name + ".sha256.identity"), identity(model_path))
