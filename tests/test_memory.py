"""Canonical RAM/VRAM snapshot classification and effective selection."""

from __future__ import annotations

from types import SimpleNamespace

from kllm.core.gpu_selection import GpuDeviceInput, default_gpu_ids, select_gpu_devices
from kllm.core.memory import build_snapshot, normalize_devices, sum_vram_bytes

GIB = 1024**3
DISCRETE = {
    "index": 0,
    "name": "AMD Radeon RX 9070 XT",
    "backend": "vulkan",
    "vram_bytes": 16 * GIB,
}
IGPU = {
    "index": 1,
    "name": "AMD Radeon(TM) Graphics",
    "backend": "vulkan",
    "vram_bytes": 48 * GIB,
}


def test_build_snapshot_keeps_discrete_vram_and_lists_integrated_separately():
    snapshot = build_snapshot(
        total_ram_bytes=64 * GIB,
        available_ram_bytes=32 * GIB,
        devices=[DISCRETE, IGPU],
        backend="vulkan",
        hostname="box",
    )

    assert snapshot.gpu_ids == (0,)
    assert snapshot.vram_bytes == 16 * GIB
    assert snapshot.vram_physical_bytes == 64 * GIB
    assert [device.integrated for device in snapshot.physical_devices] == [False, True]
    assert snapshot.unified_memory is False
    assert snapshot.has_devices is True


def test_build_snapshot_integrated_only_node_stays_unified():
    snapshot = build_snapshot(
        total_ram_bytes=64 * GIB,
        available_ram_bytes=32 * GIB,
        devices=[{**IGPU, "index": 0}],
        backend="vulkan",
    )

    assert snapshot.gpu_ids == (0,)
    assert snapshot.vram_bytes == 48 * GIB
    assert snapshot.unified_memory is True


def test_build_snapshot_keeps_registry_sum_when_no_device_can_be_named():
    snapshot = build_snapshot(
        total_ram_bytes=64 * GIB,
        available_ram_bytes=32 * GIB,
        devices=[],
        backend="vulkan",
        registry_vram_bytes=39 * GIB,
    )

    assert snapshot.vram_bytes == 39 * GIB
    assert snapshot.vram_physical_bytes is None
    assert snapshot.gpu_ids == ()


def test_build_snapshot_reports_unknown_when_a_successful_inventory_is_empty():
    snapshot = build_snapshot(
        total_ram_bytes=64 * GIB,
        available_ram_bytes=32 * GIB,
        devices=[],
        backend="vulkan",
    )

    assert snapshot.vram_bytes is None


def test_normalize_devices_accepts_native_records_and_classifies_names():
    devices = normalize_devices(
        [
            SimpleNamespace(index=0, name="RTX 4090", vram_bytes=24 * GIB),
            SimpleNamespace(index=1, name="Intel(R) UHD Graphics", vram_bytes=8 * GIB),
        ]
    )

    assert devices[0].integrated is False
    assert devices[1].integrated is True


def test_normalize_devices_positions_partial_entries():
    devices = normalize_devices([{"vram_bytes": 8 * GIB}])

    assert devices[0].index == 0
    assert devices[0].vram_bytes == 8 * GIB


def test_default_gpu_ids_prefers_discrete_and_falls_back_to_all():
    devices = [GpuDeviceInput(index=0), GpuDeviceInput(index=1, integrated=True)]

    assert default_gpu_ids(devices) == (0,)
    assert default_gpu_ids([GpuDeviceInput(index=2, integrated=True)]) == (2,)
    assert default_gpu_ids([]) == ()


def test_select_gpu_devices_auto_excludes_integrated_and_sums_discrete():
    selection = select_gpu_devices(
        None,
        [
            GpuDeviceInput(index=0, vram_bytes=16 * GIB),
            GpuDeviceInput(index=1, vram_bytes=48 * GIB, integrated=True),
        ],
        backend="vulkan",
    )

    assert selection.gpu_ids == (0,)
    assert selection.vram_bytes == 16 * GIB


def test_select_gpu_devices_honors_explicit_integrated_selection():
    selection = select_gpu_devices(
        [1],
        [
            GpuDeviceInput(index=0, vram_bytes=16 * GIB),
            GpuDeviceInput(index=1, vram_bytes=48 * GIB, integrated=True),
        ],
        backend="vulkan",
    )

    assert selection.gpu_ids == (1,)
    assert selection.vram_bytes == 48 * GIB


def test_gpu_device_input_derives_integrated_from_name():
    integrated = GpuDeviceInput.from_mapping(IGPU)
    assert integrated.integrated is True
    assert integrated.to_mapping()["integrated"] is True

    discrete = GpuDeviceInput.from_mapping(DISCRETE)
    assert discrete.integrated is False
    assert "integrated" not in discrete.to_mapping()


def test_sum_vram_bytes_is_none_without_reported_totals():
    assert sum_vram_bytes([GpuDeviceInput(index=0)]) is None
    assert sum_vram_bytes([]) is None
