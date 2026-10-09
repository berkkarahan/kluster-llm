"""HTTP-facing GPU inventory and selection adapters."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from kllm.agent.services.gpu_visibility import gpu_visibility_environment
from kllm.agent.services.gpus import GpuSelectionService, ResolvedGpuSelection
from kllm.exceptions import ResourceUnavailableError, ValidationError


class GpuApi:
    """Translate hardware service results and failures at the HTTP boundary."""

    def __init__(self, service: GpuSelectionService, *, debug_hardware: bool = False) -> None:
        self._service = service
        self._debug_hardware = debug_hardware

    def inventory(
        self, listed: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
        inventory = self._service.inventory(listed)
        return dict(inventory.payload), {
            index: device.to_mapping() for index, device in inventory.devices.items()
        }

    def membership_gpu_ids(
        self,
        body_gpu_ids: list[int] | None,
        *,
        body_sent: bool,
        listed: dict[str, Any] | None = None,
    ) -> list[int] | None:
        return self._service.membership_gpu_ids(
            body_gpu_ids,
            body_sent=body_sent,
            listed=listed,
        )

    def resolve(
        self,
        gpu_ids: list[int] | None,
        *,
        listed: dict[str, Any] | None = None,
    ) -> ResolvedGpuSelection:
        try:
            return self._service.resolve(gpu_ids, listed=listed)
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ResourceUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc

    def visibility_environment(self, backend: str, gpu_ids: list[int] | None) -> dict[str, str]:
        env = gpu_visibility_environment(backend, gpu_ids)
        if not self._debug_hardware:
            return env
        try:
            from kllm._dbg_a1ff77 import write

            write(
                "H1",
                "agent/gpu_api.py:visibility_environment",
                "gpu visibility env",
                {"backend": backend, "gpu_ids": gpu_ids, "env": env},
            )
        except Exception:
            pass
        return env

    def list_payload(self) -> dict[str, Any]:
        return self._service.list_payload()
