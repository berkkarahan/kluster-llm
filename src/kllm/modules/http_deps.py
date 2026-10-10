"""FastAPI dependencies that forward to the agent runtime after it has loaded."""

from __future__ import annotations

from fastapi import Header


def require_key(x_kllm_key: str | None = Header(default=None)) -> None:
    """Match the historical join-key dependency signature."""
    from kllm.agent.runtime import _require_key

    _require_key(x_kllm_key)


def require_api_token(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> None:
    """Match the historical API-token dependency signature."""
    from kllm.agent.runtime import _require_api_token

    _require_api_token(authorization, x_api_key)
