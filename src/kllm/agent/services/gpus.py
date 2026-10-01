"""GPU inventory and selection policy at the hardware boundary."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from kllm.accel import allowed_accel_backends
from kllm.core.gpu_selection import GpuDeviceInput, GpuSelection, select_gpu_devices
from kllm.exceptions import ValidationError


@dataclass(frozen=True)
class GpuInventory:
    """One hardware inventory snapshot indexed by physical device number."""

    payload: Mapping[str, Any]
    devices: Mapping[int, GpuDeviceInput]


@dataclass(frozen=True)
class ResolvedGpuSelection:
    """Selection policy result paired with its hardware detail records."""

    selection: GpuSelection
    devices: tuple[GpuDeviceInput, ...]

    @property
    def gpu_ids(self) -> tuple[int, ...]:
        return self.selection.gpu_ids

    @property
    def backend(self) -> str:
        return self.selection.backend

    @property
    def vram_bytes(self) -> int | None:
        return self.selection.vram_bytes

    @property
    def has_devices(self) -> bool:
        return bool(self.devices)

    def device_payloads(self) -> list[dict[str, Any]]:
        return [device.to_mapping() for device in self.devices]

    def to_payload(self) -> dict[str, Any]:
        """Serialize the selected hardware for API and cluster wire payloads."""
        return {
            "gpu_ids": list(self.gpu_ids),
            "backend": self.backend,
            "gpus": self.device_payloads(),
            "vram_bytes": self.vram_bytes,
        }


class GpuSelectionService:
    """Apply GPU selection and membership defaults using injected host adapters."""

    def __init__(
        self,
        list_gpus: Callable[[], Mapping[str, Any]],
        configured_gpu_ids: Callable[[], list[int] | None],
        install_recovery_command: Callable[[], str],
        allowed_backends: Callable[[str, Any], list[str]] = allowed_accel_backends,
    ) -> None:
        self._list_gpus = list_gpus
        self._configured_gpu_ids = configured_gpu_ids
        self._install_recovery_command = install_recovery_command
        self._allowed_backends = allowed_backends

    def list_payload(self) -> dict[str, Any]:
        """Return the normalized GPU inventory consumed by the settings UI."""
        try:
            listed = self._list_gpus()
        except Exception as exc:
            return {
                "backend": "cpu",
                "probed_backend": "cpu",
                "allowed_backends": [],
                "gpus": [],
                "error": str(exc),
                "install_hint": self._install_recovery_command(),
            }
        gpus = listed.get("gpus") or []
        probed = str(listed.get("probed_backend") or listed.get("backend") or "cpu").strip().lower()
        backend = str(listed.get("backend") or probed).strip().lower()
        payload: dict[str, Any] = {
            "backend": backend,
            "probed_backend": probed,
            "allowed_backends": listed.get(
                "allowed_backends", self._allowed_backends(probed, gpus)
            ),
            "requested_backend": listed.get("requested_backend"),
            "actual_backend": listed.get("actual_backend", backend),
            "binary_version": listed.get("binary_version"),
            "unified_memory": listed.get("unified_memory", False),
            "backend_warning": listed.get("backend_warning"),
            "installed_backends": listed.get("installed_backends", []),
            "gpus": gpus,
            "install_hint": self._install_recovery_command(),
        }
        if listed.get("error"):
            payload["error"] = listed["error"]
        return payload

    def inventory(self, listed: Mapping[str, Any] | None = None) -> GpuInventory:
        payload = listed if listed is not None else self._list_gpus()
        raw_devices = payload.get("gpus") or []
        if not isinstance(raw_devices, (list, tuple)):
            raise ValidationError("GPU inventory must be a list")
        devices: dict[int, GpuDeviceInput] = {}
        for raw_device in raw_devices:
            if not isinstance(raw_device, Mapping):
                raise ValidationError("GPU inventory contains an invalid device entry")
            device = GpuDeviceInput.from_mapping(raw_device)
            if device.index in devices:
                raise ValidationError(f"GPU inventory contains duplicate index {device.index}")
            devices[device.index] = device
        return GpuInventory(payload=payload, devices=devices)

    def membership_gpu_ids(
        self,
        body_gpu_ids: list[int] | None,
        *,
        body_sent: bool,
        listed: Mapping[str, Any] | None = None,
    ) -> list[int] | None:
        inventory = self.inventory(listed)
        if inventory.devices:
            if body_sent or body_gpu_ids is not None:
                return body_gpu_ids
            return self._configured_gpu_ids()
        if body_sent:
            return body_gpu_ids
        return None

    def resolve(
        self,
        gpu_ids: list[int] | None,
        *,
        listed: Mapping[str, Any] | None = None,
    ) -> ResolvedGpuSelection:
        inventory = self.inventory(listed)
        unavailable_detail = None
        if gpu_ids and not inventory.devices:
            detail = f"no GPUs available; cannot select {gpu_ids}"
            probe_error = inventory.payload.get("error")
            if probe_error:
                detail += f" ({probe_error})"
            unavailable_detail = (
                f"{detail}. On this machine run: {self._install_recovery_command()}"
            )
        selection = select_gpu_devices(
            gpu_ids,
            inventory.devices.values(),
            backend=inventory.payload.get("backend"),
            unavailable_detail=unavailable_detail,
        )
        return ResolvedGpuSelection(
            selection=selection,
            devices=tuple(inventory.devices[index] for index in selection.gpu_ids),
        )
