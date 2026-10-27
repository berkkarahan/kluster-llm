"""Loopback API token for inference gateway + harness connect."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from kllm.shared.state import ensure_home, home_dir


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


_BEARER_PATTERN = re.compile(r"^bearer\s+(.+)$", re.IGNORECASE)


def verify_api_token(provided: str | None) -> bool:
    if not provided:
        return False
    stripped = provided.strip()
    if not stripped:
        return False
    # Bearer prefix optional (case-insensitive per RFC 7235 / RFC 6750)
    match = _BEARER_PATTERN.match(stripped)
    raw = match.group(1).strip() if match else stripped
    return secrets.compare_digest(raw, ensure_api_token())
