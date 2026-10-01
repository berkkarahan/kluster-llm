"""Cluster update orchestration independent of FastAPI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from kllm.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    ValidationError,
)
from kllm.state import ClusterState


class ClusterUpdateGateway(Protocol):
    """Worker update transport and progress persistence supplied by the adapter."""

    def fanout_worker_updates(
        self, workers: list[dict[str, Any]], join_key: str, *, default_port: int
    ) -> dict[str, Any]: ...

    def accept_local_update(
        self,
        *,
        role: str,
        node_id: str,
        join_key: str | None,
        master_host: str | None,
        master_port: int,
        control_port: int,
        rpc_port: int,
        rpc_bind: str | None,
        overlay: str,
        gpu_ids: list[int] | None,
        master_base: str | None = None,
    ) -> dict[str, Any]: ...

    def apply_worker_progress(
        self,
        *,
        node_id: str | None,
        host: str | None,
        phase: str,
        error: str | None = None,
        git_head: str | None = None,
        llama_release: str | None = None,
        rebuild: str | None = None,
    ) -> dict[str, Any] | None: ...

    def get_update_progress(self) -> dict[str, Any] | None: ...


class ClusterUpdateService:
    """Apply role-aware cluster update policy through injected collaborators."""

    def __init__(self, gateway: ClusterUpdateGateway, master_base: Callable[[], str]) -> None:
        self._gateway = gateway
        self._master_base = master_base

    def request_update(self, state: ClusterState, supplied_key: str | None) -> dict[str, Any]:
        """Start a fanout from the master or accept an update on a worker."""
        if state.role == "master":
            if not state.join_key:
                raise ExternalServiceError("master has no join key")
            if supplied_key is not None and supplied_key != state.join_key:
                raise AuthenticationError("invalid join key")
            workers = [
                member for member in state.members.values() if member.get("role") == "worker"
            ]
            try:
                return self._gateway.fanout_worker_updates(
                    workers, state.join_key, default_port=state.control_port
                )
            except ConflictError as exc:
                raise ConflictError(str(exc)) from exc

        if state.role == "worker":
            if state.join_key and supplied_key != state.join_key:
                raise AuthenticationError("invalid join key")
            try:
                return self._gateway.accept_local_update(
                    role=state.role,
                    node_id=state.node_id,
                    join_key=state.join_key,
                    master_host=state.master_host,
                    master_port=state.master_port,
                    control_port=state.control_port,
                    rpc_port=state.rpc_port,
                    rpc_bind=state.rpc_bind,
                    overlay=state.overlay,
                    gpu_ids=state.gpu_ids,
                    master_base=self._master_base() if state.master_host else None,
                )
            except ValidationError as exc:
                raise ValidationError(str(exc)) from exc

        raise ValidationError("cluster update requires master or worker role")

    def report_progress(
        self,
        state: ClusterState,
        *,
        node_id: str | None,
        host: str | None,
        phase: str,
        error: str | None,
        git_head: str | None,
        llama_release: str | None,
        rebuild: str | None,
    ) -> dict[str, Any]:
        """Record a worker update phase reported to the master."""
        if state.role != "master":
            raise ValidationError("update progress requires master role")
        snapshot = self._gateway.apply_worker_progress(
            node_id=node_id,
            host=host,
            phase=phase,
            error=error,
            git_head=git_head,
            llama_release=llama_release,
            rebuild=rebuild,
        )
        return {"ok": True, "update": snapshot or self._gateway.get_update_progress()}
