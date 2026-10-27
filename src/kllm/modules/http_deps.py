"""FastAPI dependencies for route authorization without composition root imports."""

from __future__ import annotations

import secrets
from fastapi import Header, HTTPException

from kllm.shared import state_ref
from kllm.shared.auth import verify_api_token


def require_key(x_kllm_key: str | None = Header(default=None)) -> None:
    """Validate master join key in constant time against current cluster state."""
    state = state_ref.current()
    if state is None or state.role != "master":
        return
    if not state.join_key:
        raise HTTPException(503, "master has no join key")
    if not secrets.compare_digest(x_kllm_key or "", state.join_key or ""):
        raise HTTPException(401, "invalid join key")


def require_api_token(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> None:
    """Validate API bearer token or x-api-key header."""
    token = authorization or x_api_key
    if not verify_api_token(token):
        raise HTTPException(401, "invalid or missing API token")
