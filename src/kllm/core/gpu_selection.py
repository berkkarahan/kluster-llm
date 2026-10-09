"""GPU selection rules independent of CLI and HTTP frameworks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from kllm.accel import is_integrated_gpu
from kllm.exceptions import ResourceUnavailableError, ValidationError


@dataclass(frozen=True)
class GpuSelection:
    """Validated selection metadata; hardware detail remains at the adapter edge."""

    gpu_ids: tuple[int, ...]
    backend: str
    vram_bytes: int | None


@dataclass(frozen=True)
class GpuDeviceInput:
    """Immutable hardware facts for one discovered accelerator."""

    index: int
    device: str | None = None
    name: str | None = None
    backend: str | None = None
    vram_bytes: int | None = None
    free_bytes: int | None = None
    reclaimable_bytes: int = 0
    # Shares host RAM (iGPU / APU graphics). Integrated adapters never inflate
    # the default VRAM total while a discrete card is present.
    integrated: bool = False
    additional: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> GpuDeviceInput:
        raw_index = value.get("index")
        if raw_index is None:
            raise ValidationError("GPU inventory entry is missing index")
        raw_vram = value.get("vram_bytes")
        raw_free = value.get("free_bytes")
        name = str(value["name"]) if value.get("name") is not None else None
        backend = str(value["backend"]) if value.get("backend") is not None else None
        raw_integrated = value.get("integrated")
        integrated = (
            raw_integrated
            if isinstance(raw_integrated, bool)
            else is_integrated_gpu(name, backend)
        )
        try:
            return cls(
                index=int(raw_index),
                device=str(value["device"]) if value.get("device") is not None else None,
                name=name,
                backend=backend,
                vram_bytes=int(raw_vram) if raw_vram is not None else None,
                free_bytes=int(raw_free) if raw_free is not None else None,
                reclaimable_bytes=int(value.get("reclaimable_bytes") or 0),
                integrated=integrated,
                additional=MappingProxyType(
                    {
                        key: item
                        for key, item in value.items()
                        if key
                        not in {
                            "index",
                            "device",
                            "name",
                            "backend",
                            "vram_bytes",
                            "free_bytes",
                            "reclaimable_bytes",
                            "integrated",
                        }
                    }
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError("GPU inventory entry contains invalid hardware data") from exc

    def to_mapping(self) -> dict[str, Any]:
        """Serialize the hardware row at a CLI or API boundary."""
        value = dict(self.additional)
        value["index"] = self.index
        for key in ("device", "name", "backend", "vram_bytes", "free_bytes"):
            item = getattr(self, key)
            if item is not None:
                value[key] = item
        if self.reclaimable_bytes:
            value["reclaimable_bytes"] = self.reclaimable_bytes
        if self.integrated:
            value["integrated"] = True
        return value


def parse_gpu_ids(value: str | None) -> list[int] | None:
    """Parse comma-separated GPU indices; ``None`` means use the default set."""
    if value is None:
        return None
    if not value.strip():
        raise ValidationError("expected comma-separated GPU indices, e.g. 0,1")

    selected: list[int] = []
    seen: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            index = int(part)
        except ValueError as exc:
            raise ValidationError(f"invalid GPU index {part!r}") from exc
        if index < 0:
            raise ValidationError(f"GPU index must be >= 0, got {index}")
        if index not in seen:
            selected.append(index)
            seen.add(index)

    if not selected:
        raise ValidationError("expected comma-separated GPU indices, e.g. 0,1")
    return selected


def default_gpu_ids(devices: Iterable[GpuDeviceInput]) -> tuple[int, ...]:
    """Auto selection: discrete accelerators, falling back to every device.

    An integrated adapter shares host RAM; it must not inflate the node's
    advertised VRAM when the machine also has a discrete card. A machine whose
    only accelerator is integrated keeps it.
    """
    ordered = list(devices)
    all_ids = tuple(sorted(device.index for device in ordered))
    discrete = tuple(sorted(device.index for device in ordered if not device.integrated))
    return discrete or all_ids


def select_gpu_devices(
    requested_ids: Sequence[int] | None,
    devices: Iterable[GpuDeviceInput],
    *,
    backend: str | None,
    unavailable_detail: str | None = None,
) -> GpuSelection:
    """Validate device indices and compute selected VRAM without side effects."""
    by_index = {device.index: device for device in devices}
    normalized_backend = backend or "cpu"

    if not by_index:
        if requested_ids:
            detail = unavailable_detail or "no GPUs are available for the requested selection"
            raise ResourceUnavailableError(detail)
        return GpuSelection(gpu_ids=(), backend=normalized_backend, vram_bytes=None)

    if requested_ids is None:
        selected_ids = default_gpu_ids(by_index.values())
    else:
        if not requested_ids:
            raise ValidationError("gpu_ids must be non-empty when provided")
        unknown = [index for index in requested_ids if index not in by_index]
        if unknown:
            raise ValidationError(f"unknown gpu_ids {unknown}; available={sorted(by_index)}")
        selected_ids = tuple(dict.fromkeys(requested_ids))

    total_vram = 0
    has_vram = False
    for index in selected_ids:
        value = by_index[index].vram_bytes
        if value is None:
            continue
        total_vram += int(value)
        has_vram = True
    return GpuSelection(
        gpu_ids=selected_ids,
        backend=normalized_backend,
        vram_bytes=total_vram if has_vram else None,
    )
