"""Models HTTP endpoints for the agent composition root."""

from __future__ import annotations

from typing import Any, cast
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

def _host():
    from kllm.agent import runtime as host

    return host

def list_models() -> ModelCatalog:
    return cast(ModelCatalog, _host()._model_handlers().catalog())

def search_models(body: SearchBody) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._model_handlers().search(body))

def download_model(body: DownloadBody) -> DownloadAccepted:
    return cast(DownloadAccepted, _host()._model_handlers().download(body))

def download_progress() -> dict[str, Any]:
    return cast(dict[str, Any], _host()._model_handlers().progress())

def cancel_download() -> dict[str, Any]:
    return cast(dict[str, Any], _host()._model_handlers().cancel())

def delete_model(body: DeleteBody) -> ModelDeletion:
    return cast(ModelDeletion, _host()._model_handlers().delete(body))