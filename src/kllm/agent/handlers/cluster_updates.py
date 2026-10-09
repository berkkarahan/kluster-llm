"""HTTP boundary handlers for cluster update operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from kllm.agent.schemas import UpdateProgressBody
from kllm.agent.services.cluster_updates import ClusterUpdateService
from kllm.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    ValidationError,
)
from kllm.state import ClusterState


class ClusterUpdateHandlers:
    """Translate update-domain results and errors into HTTP responses."""

    def __init__(
        self,
        service: ClusterUpdateService,
        state: Callable[[], ClusterState],
    ) -> None:
        self._service = service
        self._state = state

    def request(self, supplied_key: str | None) -> dict[str, Any]:
        try:
            return self._service.request_update(self._state(), supplied_key)
        except AuthenticationError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ExternalServiceError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    def progress(self, body: UpdateProgressBody) -> dict[str, Any]:
        try:
            return self._service.report_progress(
                self._state(),
                node_id=body.node_id,
                host=body.host,
                phase=body.phase,
                error=body.error,
                git_head=body.git_head,
                llama_release=body.llama_release,
                rebuild=body.rebuild,
            )
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
