"""Canonical RAM and VRAM facts for capacity reporting and planning.

Hardware probing stays at its I/O boundaries — the Rust extension for host RAM
and OS adapters, ``llama.cpp --list-devices`` for engine-visible devices. This
module is the single owner of how those observations are classified and
combined:

* which accelerators are discrete VRAM and which are integrated adapters
  sharing host RAM (see :func:`kllm.accel.is_integrated_gpu`);
* the *effective* device set a node reports (configured ids win; auto mode
  keeps discrete accelerators and drops integrated ones);
* the physical and effective VRAM sums, host RAM totals, and the node
  unified-memory classification.

Capacity (this module) is deliberately separate from the live usage probe
(``kllm.usage_probe``): capacity describes what a node can be asked to run,
usage describes what is running right now.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from kllm.shared.accel import is_integrated_gpu, is_unified_memory
from kllm.modules.inference.interactors.gpu_selection import GpuDeviceInput, default_gpu_ids


@dataclass(frozen=True)
class MemorySnapshot:
    """One node's canonical memory facts.

    ``devices``/``vram_bytes`` are the *effective* set the runtime will use.
    ``physical_devices``/``vram_physical_bytes`` keep every engine-visible
    device for informational surfaces (settings, live usage).
    """

    total_ram_bytes: int
    available_ram_bytes: int
    devices: tuple[GpuDeviceInput, ...]
    physical_devices: tuple[GpuDeviceInput, ...]
    gpu_ids: tuple[int, ...]
    vram_bytes: int | None
    vram_physical_bytes: int | None
    backend: str
    probed_backend: str
    requested_backend: str | None
    unified_memory: bool
    hostname: str

    @property
    def has_devices(self) -> bool:
        return bool(self.devices)


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def normalize_devices(raw: Iterable[Any] | None) -> tuple[GpuDeviceInput, ...]:
    """Coerce native pyclasses, mappings, or inputs into device records.

    Entries without an index (a partial capability payload) are positioned in
    discovery order rather than rejected; the probe only sums what it can name.
    """
    devices: list[GpuDeviceInput] = []
    for position, item in enumerate(raw or ()):
        if isinstance(item, GpuDeviceInput):
            devices.append(item)
            continue
        if isinstance(item, Mapping):
            if item.get("index") is None:
                item = {**item, "index": position}
            devices.append(GpuDeviceInput.from_mapping(item))
            continue
        index = getattr(item, "index", None)
        if index is None:
            continue
        name = getattr(item, "name", None)
        name_text = str(name) if name is not None else None
        devices.append(
            GpuDeviceInput(
                index=int(index),
                name=name_text,
                vram_bytes=_optional_int(getattr(item, "vram_bytes", None)),
                integrated=is_integrated_gpu(name_text),
            )
        )
    return tuple(devices)


def sum_vram_bytes(devices: Iterable[GpuDeviceInput]) -> int | None:
    """Sum reported VRAM. ``None`` when no device reports a usable total."""
    total: int | None = None
    for device in devices:
        value = device.vram_bytes
        if value is not None and value >= 0:
            total = (total or 0) + value
    return total


def build_snapshot(
    *,
    total_ram_bytes: int,
    available_ram_bytes: int,
    devices: Iterable[Any] | None,
    backend: str = "cpu",
    probed_backend: str | None = None,
    requested_backend: str | None = None,
    unified_memory: bool | None = None,
    hostname: str = "",
    registry_vram_bytes: int | None = None,
) -> MemorySnapshot:
    """Build one canonical snapshot from a single probe observation.

    ``backend`` is the hardware-classification backend (the Rust probe), which
    may differ from the engine backend when the capability probe failed.
    ``registry_vram_bytes`` is the OS registry/SMI sum kept only when the
    engine probe failed before naming any device.
    """
    physical = normalize_devices(devices)
    effective_ids = default_gpu_ids(physical)
    by_index = {device.index: device for device in physical}
    effective = tuple(by_index[index] for index in effective_ids if index in by_index)
    backend_name = str(backend or "cpu").strip().lower()
    if unified_memory is None:
        unified_memory = is_unified_memory(backend_name, physical)
    vram = sum_vram_bytes(effective)
    if vram is None and not physical and registry_vram_bytes is not None:
        vram = registry_vram_bytes
    return MemorySnapshot(
        total_ram_bytes=int(total_ram_bytes),
        available_ram_bytes=int(available_ram_bytes),
        devices=effective,
        physical_devices=physical,
        gpu_ids=effective_ids,
        vram_bytes=vram,
        vram_physical_bytes=sum_vram_bytes(physical),
        backend=backend_name,
        probed_backend=str(probed_backend or backend_name).strip().lower(),
        requested_backend=requested_backend,
        unified_memory=unified_memory,
        hostname=hostname,
    )
