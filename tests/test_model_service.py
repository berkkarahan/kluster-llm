"""Unit tests for the injected model-management service."""

from __future__ import annotations

from concurrent.futures import Executor, Future
from pathlib import Path
from typing import Any

import pytest

from kllm.modules.models.interactors.model_service import ModelService
from kllm.shared.exceptions import ConflictError, ValidationError
from kllm.shared.state import ClusterState


class FakeExecutor(Executor):
    def __init__(self) -> None:
        self.submitted: list[tuple[Any, tuple[Any, ...]]] = []

    def submit(self, fn, /, *args, **kwargs) -> Future:
        self.submitted.append((fn, args))
        return Future()


class FakeGateway:
    def __init__(self) -> None:
        self.search_vram: int | None = None
        self.begin_result = {
            "operation_id": "op-1",
            "started_at": "start",
            "updated_at": "start",
            "finished_at": None,
        }
        self.deleted: str | None = None

    def list_local_models(self) -> list[dict[str, Any]]:
        return [{"id": "loaded.gguf", "filename": "loaded.gguf", "loaded": False}]

    def sum_vram_bytes(self, members) -> int | None:
        return sum(int(member.get("vram_bytes") or 0) for member in members)

    def search_hub_ggufs(
        self, query: str, *, total_vram_bytes: int | None = None
    ) -> dict[str, Any]:
        self.search_vram = total_vram_bytes
        return {"query": query}

    def resolve_hf_filename(self, repo_id: str, suffix: str, *, token: str | None) -> str:
        return f"{repo_id}:{suffix}"

    def resolve_hf_token(self) -> str | None:
        return None

    def begin_download(self, source: str, dest_name: str | None) -> dict[str, Any]:
        return self.begin_result

    def get_download_progress(self) -> dict[str, Any] | None:
        return None

    def download_model(self, source: str, *, dest_name: str | None) -> Path:
        return Path(dest_name or "download.gguf")

    def set_download_terminal(
        self,
        filename: str,
        total: int | None,
        phase: str,
        error: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        return None

    def download_job_snapshot(self) -> dict[str, Any] | None:
        return None

    def cancel_download(self) -> bool:
        return False

    def resolve_local_model(self, name: str) -> Path:
        return Path(name)

    def delete_local_model(self, name: str) -> Path:
        self.deleted = name
        return Path(name)


class FakeDevices:
    def list_gpus(self) -> dict[str, Any]:
        return {"gpus": [{"index": 0, "vram_bytes": 4096}]}


def make_service() -> tuple[ModelService, FakeGateway, FakeExecutor]:
    gateway = FakeGateway()
    executor = FakeExecutor()
    return ModelService(gateway, FakeDevices(), executor), gateway, executor


def test_catalog_marks_loaded_model_without_mutating_repository_row() -> None:
    service, gateway, _ = make_service()
    state = ClusterState(model_name="loaded.gguf", process_pid=42, process_role="master")

    result = service.catalog(state)

    assert result.loaded == "loaded.gguf"
    assert result.data[0].loaded is True
    assert gateway.list_local_models()[0]["loaded"] is False


def test_search_uses_cluster_vram_and_rejects_empty_queries() -> None:
    service, gateway, _ = make_service()
    state = ClusterState(members={"worker": {"vram_bytes": 8192}})

    assert service.search("  Qwen  ", state) == {"query": "Qwen"}
    assert gateway.search_vram == 8192
    with pytest.raises(ValidationError, match="query is required"):
        service.search("  ", state)


def test_download_submission_uses_injected_executor_and_includes_operation_metadata() -> None:
    service, _gateway, executor = make_service()

    result = service.start_download("https://example.test/model.gguf", None, "node-1")

    assert result.model_dump() == {
        "ok": True,
        "accepted": True,
        "source": "https://example.test/model.gguf",
        "kind": "download",
        "node_id": "node-1",
        "state": "running",
        "operation_id": "op-1",
        "started_at": "start",
        "updated_at": "start",
        "finished_at": None,
    }
    assert len(executor.submitted) == 1
    assert executor.submitted[0][1] == ("https://example.test/model.gguf", None)


def test_cancel_and_loaded_model_delete_report_domain_conflicts() -> None:
    service, gateway, _ = make_service()
    state = ClusterState(model_name="loaded.gguf", process_pid=42, process_role="master")

    with pytest.raises(ConflictError, match="no download"):
        service.cancel_download()
    with pytest.raises(ConflictError, match="currently loaded"):
        service.delete("loaded.gguf", state)
    assert gateway.deleted is None
