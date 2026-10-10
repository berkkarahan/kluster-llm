"""Unit tests for role-aware cluster update orchestration."""

from __future__ import annotations

from typing import Any

import pytest

from kllm.modules.updates.interactors.cluster_update_service import ClusterUpdateService
from kllm.modules.updates.interactors.cluster_update import UpdateInProgressError
from kllm.shared.exceptions import AuthenticationError, ConflictError, ExternalServiceError, ValidationError
from kllm.shared.state import ClusterState


class FakeGateway:
    def __init__(self) -> None:
        self.workers: list[dict[str, Any]] = []
        self.accepted: dict[str, Any] | None = None
        self.progress: dict[str, Any] | None = None
        self.busy = False

    def fanout_worker_updates(
        self, workers: list[dict[str, Any]], join_key: str, *, default_port: int
    ) -> dict[str, Any]:
        self.workers = workers
        if self.busy:
            raise UpdateInProgressError("update is already running")
        return {"ok": True, "workers": workers, "join_key": join_key, "port": default_port}

    def accept_local_update(self, **params: Any) -> dict[str, Any]:
        self.accepted = params
        return {"ok": True, "accepted": True}

    def apply_worker_progress(self, **params: Any) -> dict[str, Any] | None:
        self.progress = params
        return {"phase": params["phase"]}

    def get_update_progress(self) -> dict[str, Any] | None:
        return None


def test_master_update_fanout_filters_nonworkers_and_requires_key() -> None:
    gateway = FakeGateway()
    service = ClusterUpdateService(gateway, lambda: "http://master:18765")
    state = ClusterState(
        role="master",
        join_key="secret",
        control_port=18000,
        members={
            "worker": {"node_id": "worker", "role": "worker"},
            "master": {"node_id": "master", "role": "master"},
        },
    )

    result = service.request_update(state, None)

    assert [worker["node_id"] for worker in gateway.workers] == ["worker"]
    assert result["port"] == 18000
    with pytest.raises(AuthenticationError):
        service.request_update(state, "wrong")


def test_worker_update_receives_connection_settings_from_state() -> None:
    gateway = FakeGateway()
    service = ClusterUpdateService(gateway, lambda: "http://master:18765")
    state = ClusterState(
        role="worker",
        node_id="worker",
        join_key="secret",
        master_host="master",
        master_port=18765,
        rpc_port=50052,
        rpc_bind="10.0.0.2",
        gpu_ids=[0],
    )

    result = service.request_update(state, "secret")

    assert result["accepted"] is True
    assert gateway.accepted is not None
    assert gateway.accepted["master_base"] == "http://master:18765"
    assert gateway.accepted["gpu_ids"] == [0]


def test_update_conflicts_and_invalid_roles_are_domain_errors() -> None:
    gateway = FakeGateway()
    service = ClusterUpdateService(gateway, lambda: "http://master")
    gateway.busy = True
    master = ClusterState(role="master", join_key="secret")
    with pytest.raises(ConflictError):
        service.request_update(master, None)
    with pytest.raises(ExternalServiceError):
        service.request_update(ClusterState(role="master"), None)
    with pytest.raises(ValidationError):
        service.request_update(ClusterState(role="idle"), None)


def test_progress_reporting_is_master_only_and_typed() -> None:
    gateway = FakeGateway()
    service = ClusterUpdateService(gateway, lambda: "http://master")

    result = service.report_progress(
        ClusterState(role="master"),
        node_id="worker",
        host="10.0.0.2",
        phase="rebuilding",
        error=None,
        git_head="abc",
        llama_release="v1",
        rebuild="build",
    )

    assert result == {"ok": True, "update": {"phase": "rebuilding"}}
    assert gateway.progress is not None
    assert gateway.progress["node_id"] == "worker"
    with pytest.raises(ValidationError):
        service.report_progress(
            ClusterState(role="worker"),
            node_id="worker",
            host=None,
            phase="done",
            error=None,
            git_head=None,
            llama_release=None,
            rebuild=None,
        )
