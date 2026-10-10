"""Settings HTTP endpoints for the agent composition root."""

from __future__ import annotations

from typing import Any, cast
from kllm.shared.schemas import (
    ConfigBody,
    HfTokenBody,
)

def _host():
    from kllm.agent import runtime as host

    return host

def get_api_token() -> dict[str, Any]:
    """Return loopback API token (local operator helper)."""
    return cast(dict[str, Any], _host()._settings_handlers().api_token())

def get_hf_token() -> dict[str, Any]:
    """Whether an HF token is configured. Never returns the secret."""
    return cast(dict[str, Any], _host()._settings_handlers().hf_token())

def put_hf_token(body: HfTokenBody) -> dict[str, Any]:
    """Save ~/.kllm/hf_token (chmod 600). Empty string deletes the file."""
    return cast(dict[str, Any], _host()._settings_handlers().save_hf_token(body))

def get_config() -> dict[str, Any]:
    return cast(dict[str, Any], _host()._settings_handlers().get_config())

def get_gpus() -> dict[str, Any]:
    """Enumerate local GPUs for pre-start selection."""
    return cast(dict[str, Any], _host()._settings_handlers().get_gpus())

def put_config(body: ConfigBody) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._settings_handlers().save_config(body))

def get_home() -> dict[str, Any]:
    """Data-directory layout for the System Settings > Data directory panel."""
    return cast(dict[str, Any], _host()._settings_handlers().home())