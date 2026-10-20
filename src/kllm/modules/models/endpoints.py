"""Models HTTP endpoints for the agent composition root."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from fastapi import APIRouter
from kllm.shared.schemas import (
    DeleteBody,
    DownloadBody,
    SearchBody,
)
from kllm.modules.models.entities import (
    DownloadAccepted,
    ModelCatalog,
    ModelDeletion,
)

_model_handlers_provider: Callable[[], Any] | None = None


def set_model_handlers_provider(provider: Callable[[], Any]) -> None:
    global _model_handlers_provider
    _model_handlers_provider = provider


def _get_model_handlers() -> Any:
    if _model_handlers_provider is not None:
        return _model_handlers_provider()
    raise RuntimeError("model handlers provider is not configured")


def list_models() -> ModelCatalog:
    return cast(ModelCatalog, _get_model_handlers().catalog())


def search_models(body: SearchBody) -> dict[str, Any]:
    return cast(dict[str, Any], _get_model_handlers().search(body))


def download_model(body: DownloadBody) -> DownloadAccepted:
    return cast(DownloadAccepted, _get_model_handlers().download(body))


def download_progress() -> dict[str, Any]:
    return cast(dict[str, Any], _get_model_handlers().progress())


def cancel_download() -> dict[str, Any]:
    return cast(dict[str, Any], _get_model_handlers().cancel())


def delete_model(body: DeleteBody) -> ModelDeletion:
    return cast(ModelDeletion, _get_model_handlers().delete(body))


def create_models_router(handlers: Any = None) -> APIRouter:
    router = APIRouter()

    def get_h() -> Any:
        if handlers is not None:
            return handlers() if callable(handlers) else handlers
        return _get_model_handlers()

    @router.get("/v1/models")
    @router.get("/models")
    def _list() -> ModelCatalog:
        return cast(ModelCatalog, get_h().catalog())

    @router.post("/v1/models/search")
    @router.post("/models/search")
    def _search(body: SearchBody) -> dict[str, Any]:
        return cast(dict[str, Any], get_h().search(body))

    @router.post("/v1/models/download")
    @router.post("/models/download")
    def _download(body: DownloadBody) -> DownloadAccepted:
        return cast(DownloadAccepted, get_h().download(body))

    @router.get("/v1/models/download/progress")
    @router.get("/models/download/progress")
    def _progress() -> dict[str, Any]:
        return cast(dict[str, Any], get_h().progress())

    @router.post("/v1/models/download/cancel")
    @router.post("/models/download/cancel")
    def _cancel() -> dict[str, Any]:
        return cast(dict[str, Any], get_h().cancel())

    @router.post("/v1/models/delete")
    @router.delete("/models")
    def _delete(body: DeleteBody) -> ModelDeletion:
        return cast(ModelDeletion, get_h().delete(body))

    return router


create_router = create_models_router