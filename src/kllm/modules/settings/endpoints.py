"""Settings HTTP endpoints for the agent composition root."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from fastapi import APIRouter
from kllm.shared.schemas import (
    ConfigBody,
    HfTokenBody,
)

_settings_handlers_provider: Callable[[], Any] | None = None


def set_settings_handlers_provider(provider: Callable[[], Any]) -> None:
    global _settings_handlers_provider
    _settings_handlers_provider = provider


def _get_handlers() -> Any:
    if _settings_handlers_provider is not None:
        return _settings_handlers_provider()
    raise RuntimeError("settings handlers provider is not configured")


def get_api_token() -> dict[str, Any]:
    """Return loopback API token (local operator helper)."""
    return cast(dict[str, Any], _get_handlers().api_token())


def get_hf_token() -> dict[str, Any]:
    """Whether an HF token is configured. Never returns the secret."""
    return cast(dict[str, Any], _get_handlers().hf_token())


def put_hf_token(body: HfTokenBody) -> dict[str, Any]:
    """Save ~/.kllm/hf_token (chmod 600). Empty string deletes the file."""
    return cast(dict[str, Any], _get_handlers().save_hf_token(body))


def get_config() -> dict[str, Any]:
    return cast(dict[str, Any], _get_handlers().get_config())


def get_gpus() -> dict[str, Any]:
    """Enumerate local GPUs for pre-start selection."""
    return cast(dict[str, Any], _get_handlers().get_gpus())


def put_config(body: ConfigBody) -> dict[str, Any]:
    return cast(dict[str, Any], _get_handlers().save_config(body))


def get_home() -> dict[str, Any]:
    """Data-directory layout for the System Settings > Data directory panel."""
    return cast(dict[str, Any], _get_handlers().home())


def create_settings_router(handlers: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/api-token")
    @router.get("/credentials/api-token")
    def _api_token() -> dict[str, Any]:
        return handlers.api_token()

    @router.get("/v1/hf-token")
    @router.get("/credentials/huggingface")
    def _hf_token() -> dict[str, Any]:
        return handlers.hf_token()

    @router.put("/v1/hf-token")
    @router.put("/credentials/huggingface")
    def _put_hf_token(body: HfTokenBody) -> dict[str, Any]:
        return handlers.save_hf_token(body)

    @router.get("/v1/config")
    @router.get("/node/config")
    def _config() -> dict[str, Any]:
        return handlers.get_config()

    @router.get("/v1/gpus")
    @router.get("/node/devices")
    def _gpus() -> dict[str, Any]:
        return handlers.get_gpus()

    @router.put("/v1/config")
    @router.put("/node/config")
    def _put_config(body: ConfigBody) -> dict[str, Any]:
        return handlers.save_config(body)

    @router.get("/v1/home")
    @router.get("/node/home")
    def _home() -> dict[str, Any]:
        return handlers.home()

    return router


create_router = create_settings_router