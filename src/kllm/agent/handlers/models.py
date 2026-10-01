"""FastAPI-facing model handlers and domain-error translation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from kllm.agent.schemas import DeleteBody, DownloadBody, SearchBody
from kllm.agent.services.models import ModelService
from kllm.exceptions import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from kllm.models.schemas import DownloadAccepted, ModelCatalog, ModelDeletion
from kllm.state import ClusterState


class ModelHandlers:
    """Translate model service results and errors at the HTTP boundary."""

    def __init__(
        self,
        service: ModelService,
        state: Callable[[], ClusterState],
        download_runner: Callable[[str, str | None], None],
    ) -> None:
        self._service = service
        self._state = state
        self._download_runner = download_runner

    def catalog(self) -> ModelCatalog:
        return self._service.catalog(self._state())

    def search(self, body: SearchBody) -> dict[str, Any]:
        try:
            return self._service.search(body.query, self._state())
        except (ValidationError, ExternalServiceError) as exc:
            raise HTTPException(400, str(exc)) from exc

    def download(self, body: DownloadBody) -> DownloadAccepted:
        try:
            return self._service.start_download(
                body.source,
                body.name,
                self._state().node_id,
                runner=self._download_runner,
            )
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    def progress(self) -> dict[str, Any]:
        return self._service.progress()

    def cancel(self) -> dict[str, bool]:
        try:
            return self._service.cancel_download()
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    def delete(self, body: DeleteBody) -> ModelDeletion:
        try:
            return self._service.delete(body.name, self._state())
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
