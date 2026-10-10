"""Model catalog and download workflows, independent of FastAPI."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from pathlib import Path
from typing import Any, Protocol

from kllm.shared.exceptions import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from kllm.modules.models import DownloadCancelledError, DownloadInProgressError
from kllm.modules.models.entities import DownloadAccepted, ModelCatalog, ModelCard, ModelDeletion
from kllm.shared.state import ClusterState


class ModelGateway(Protocol):
    """Operations supplied by the local model store and Hugging Face adapter."""

    def list_local_models(self) -> list[dict[str, Any]]: ...

    def sum_vram_bytes(self, members: Any) -> int | None: ...

    def search_hub_ggufs(
        self, query: str, *, total_vram_bytes: int | None = None
    ) -> dict[str, Any]: ...

    def resolve_hf_filename(self, repo_id: str, suffix: str, *, token: str | None) -> str: ...

    def resolve_hf_token(self) -> str | None: ...

    def begin_download(self, source: str, dest_name: str | None) -> dict[str, Any]: ...

    def get_download_progress(self) -> dict[str, Any] | None: ...

    def download_model(self, source: str, *, dest_name: str | None) -> Path: ...

    def set_download_terminal(
        self,
        filename: str,
        total: int | None,
        phase: str,
        error: str | None = None,
        operation_id: str | None = None,
    ) -> None: ...

    def download_job_snapshot(self) -> dict[str, Any] | None: ...

    def cancel_download(self) -> bool: ...

    def resolve_local_model(self, name: str) -> Path: ...

    def delete_local_model(self, name: str) -> Path: ...


class DeviceInventory(Protocol):
    """Read the local device inventory without binding the service to native code."""

    def list_gpus(self) -> dict[str, Any]: ...


class ModelService:
    """Coordinate model-store workflows through injected adapters and executor."""

    def __init__(
        self,
        gateway: ModelGateway,
        devices: DeviceInventory,
        executor: Executor,
    ) -> None:
        self._gateway = gateway
        self._devices = devices
        self._executor = executor

    def catalog(self, state: ClusterState) -> ModelCatalog:
        """Return an OpenAI-shaped local catalog with the loaded model marked."""
        data = [dict(model) for model in self._gateway.list_local_models()]
        loaded = state.model_name
        if loaded and state.process_pid and state.process_role == "master":
            current = next((model for model in data if model.get("id") == loaded), None)
            if current is None:
                data.insert(
                    0,
                    {
                        "id": loaded,
                        "object": "model",
                        "owned_by": "kllm",
                        "filename": loaded,
                        "loaded": True,
                    },
                )
            else:
                current["loaded"] = True
        return ModelCatalog(data=[ModelCard.model_validate(model) for model in data], loaded=loaded)

    def search(self, query: str, state: ClusterState) -> dict[str, Any]:
        """Search model repositories using cluster or local VRAM as a fit hint."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValidationError("query is required")
        try:
            total_vram = self._total_vram_bytes(state)
            return self._gateway.search_hub_ggufs(normalized_query, total_vram_bytes=total_vram)
        except Exception as exc:
            raise ExternalServiceError(str(exc)) from exc

    def start_download(
        self,
        source: str,
        name: str | None,
        node_id: str,
        *,
        runner: Callable[[str, str | None], None] | None = None,
    ) -> DownloadAccepted:
        """Validate and queue one download, returning its accepted operation."""
        try:
            if ":" in source and not source.startswith(("http://", "https://")):
                repo_id, suffix = source.split(":", 1)
                self._gateway.resolve_hf_filename(
                    repo_id, suffix, token=self._gateway.resolve_hf_token()
                )
        except Exception as exc:
            raise ValidationError(str(exc)) from exc
        try:
            operation = self._gateway.begin_download(source, name)
        except DownloadInProgressError as exc:
            raise ConflictError(str(exc)) from exc
        self._executor.submit(runner or self.run_download_job, source, name)
        return DownloadAccepted(
            source=source,
            node_id=node_id,
            **{
                key: operation[key]
                for key in ("operation_id", "started_at", "updated_at", "finished_at")
            },
        )

    def progress(self) -> dict[str, Any]:
        """Return the current or most recent terminal download snapshot."""
        snapshot = self._gateway.download_job_snapshot()
        return snapshot if snapshot is not None else {"state": "idle"}

    def cancel_download(self) -> dict[str, bool]:
        """Cancel the active download or raise a typed conflict."""
        if not self._gateway.cancel_download():
            raise ConflictError("no download in progress")
        return {"ok": True}

    def delete(self, name: str, state: ClusterState) -> ModelDeletion:
        """Delete a local model while protecting the currently loaded file."""
        try:
            path = self._gateway.resolve_local_model(name)
        except FileNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        if state.model_name and state.process_pid and state.process_role == "master":
            if path.name == state.model_name:
                raise ConflictError(f"model is currently loaded: {state.model_name}")
        try:
            deleted = self._gateway.delete_local_model(path.name)
        except FileNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return ModelDeletion(name=deleted.name, path=str(deleted))

    def _total_vram_bytes(self, state: ClusterState) -> int | None:
        if state.members:
            return self._gateway.sum_vram_bytes(state.members.values())
        try:
            inventory = self._devices.list_gpus()
        except Exception:
            return None
        return self._gateway.sum_vram_bytes(inventory.get("gpus") or [])

    def run_download_job(self, source: str, name: str | None) -> None:
        """Run a queued download and publish its terminal state."""
        operation_id = (self._gateway.get_download_progress() or {}).get("operation_id")
        try:
            path = self._gateway.download_model(source, dest_name=name)
        except DownloadCancelledError:
            return
        except Exception as exc:
            snapshot = self._gateway.get_download_progress()
            filename = (snapshot or {}).get("filename") or (name or Path(source).name)
            try:
                self._gateway.set_download_terminal(filename, None, "error", str(exc), operation_id)
            except Exception:
                pass
            return
        try:
            self._gateway.set_download_terminal(
                path.name, path.stat().st_size, "complete", operation_id=operation_id
            )
        except OSError:
            pass
