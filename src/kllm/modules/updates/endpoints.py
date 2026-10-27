"""Updates HTTP endpoints for the agent composition root."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from fastapi import (
    APIRouter,
    Depends,
    Header,
)
from kllm.shared.schemas import UpdateProgressBody

from kllm.modules.http_deps import require_key

_cluster_update_handlers_provider: Callable[[], Any] | None = None


def set_cluster_update_handlers_provider(provider: Callable[[], Any]) -> None:
    global _cluster_update_handlers_provider
    _cluster_update_handlers_provider = provider


def _get_handlers() -> Any:
    if _cluster_update_handlers_provider is not None:
        return _cluster_update_handlers_provider()
    raise RuntimeError("cluster update handlers provider is not configured")


def cluster_update_endpoint(
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Master: fan out update to workers. Worker: accept and spawn detached updater."""
    return cast(dict[str, Any], _get_handlers().request(x_kllm_key))


def cluster_update_progress(
    body: UpdateProgressBody,
    _: None = Depends(require_key),
) -> dict[str, Any]:
    """Worker updater → master: report pull/rebuild/rejoin phase."""
    return cast(dict[str, Any], _get_handlers().progress(body))


def create_updates_router(handlers: Any) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/cluster/update")
    @router.post("/cluster/update")
    def _update(x_kllm_key: str | None = Header(default=None)) -> dict[str, Any]:
        return handlers.request(x_kllm_key)

    @router.post("/v1/cluster/update/progress")
    @router.post("/cluster/update-progress")
    def _progress(body: UpdateProgressBody, _: None = Depends(require_key)) -> dict[str, Any]:
        return handlers.progress(body)

    return router


create_router = create_updates_router