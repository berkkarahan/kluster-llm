"""Persisted Hugging Face Hub token (~/.kllm/hf_token)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from kllm.state import ensure_home, home_dir

HfTokenSource = Literal["file", "env"]


def hf_token_path() -> Path:
    return home_dir() / "hf_token"


def read_hf_token_file() -> str | None:
    path = hf_token_path()
    if not path.is_file():
        return None
    token = path.read_text(encoding="utf-8").strip()
    return token or None


def env_hf_token() -> str | None:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        raw = os.environ.get(key)
        if raw and raw.strip():
            return raw.strip()
    return None


def resolve_hf_token() -> str | None:
    """Precedence: saved file → HF_TOKEN → HUGGING_FACE_HUB_TOKEN."""
    return read_hf_token_file() or env_hf_token()


def hf_token_status() -> dict[str, Any]:
    if read_hf_token_file():
        return {"configured": True, "source": "file"}
    if env_hf_token():
        return {"configured": True, "source": "env"}
    return {"configured": False, "source": None}


def save_hf_token(token: str | None) -> dict[str, Any]:
    """Save token to ~/.kllm/hf_token (chmod 600). Empty/None deletes the file."""
    ensure_home()
    path = hf_token_path()
    cleaned = (token or "").strip()
    if not cleaned:
        if path.is_file():
            path.unlink()
        return hf_token_status()
    path.write_text(cleaned + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {"configured": True, "source": "file"}
