"""Loopback API token for inference gateway + harness connect."""

from __future__ import annotations

import secrets
from pathlib import Path

from kllm.state import ensure_home, home_dir


def api_token_path() -> Path:
    return home_dir() / "api_token"


def ensure_api_token() -> str:
    ensure_home()
    path = api_token_path()
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = f"sk-kllm-{secrets.token_urlsafe(24)}"
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def verify_api_token(provided: str | None) -> bool:
    if not provided:
        return False
    # Bearer prefix optional
    raw = provided.removeprefix("Bearer ").strip()
    return secrets.compare_digest(raw, ensure_api_token())
