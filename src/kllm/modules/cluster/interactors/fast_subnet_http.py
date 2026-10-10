"""HTTP boundary for fast-subnet reachability and worker reconnect."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from kllm.shared.schemas import FastSubnetReconnectBody
from kllm.modules.cluster.interactors.fast_subnet import FastSubnetService
from kllm.shared.exceptions import AuthenticationError, ConflictError, ExternalServiceError, ValidationError
from kllm.shared.state import ClusterState


class FastSubnetHandlers:
    """Translate fast-subnet results into the control API."""

    def __init__(self, service: FastSubnetService, state: Callable[[], ClusterState]) -> None:
        self._service = service
        self._state = state

    def reachability(self) -> dict[str, Any]:
        try:
            return self._service.reachability(self._state())
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    def reconnect(self, body: FastSubnetReconnectBody, supplied_key: str | None) -> dict[str, Any]:
        try:
            return self._service.reconnect(self._state(), body, supplied_key)
        except AuthenticationError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ExternalServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        except HTTPException:
            raise
