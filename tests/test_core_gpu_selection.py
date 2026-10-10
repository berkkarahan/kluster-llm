"""Tests for framework-independent GPU selection parsing."""

import pytest

from kllm.modules.inference.interactors.gpu_selection import GpuDeviceInput, parse_gpu_ids, select_gpu_devices
from kllm.shared.exceptions import ResourceUnavailableError, ValidationError


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("0", [0]), (" 2, 0,2 ", [2, 0]), ("1,,3", [1, 3])],
)
def test_parse_gpu_ids(value: str | None, expected: list[int] | None) -> None:
    assert parse_gpu_ids(value) == expected


@pytest.mark.parametrize("value", ["", "  ", ",,", "one", "-1", "0,-2"])
def test_parse_gpu_ids_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        parse_gpu_ids(value)


def test_gpu_selection_defaults_to_sorted_inventory_and_sums_selected_vram() -> None:
    devices = [
        GpuDeviceInput(index=3, vram_bytes=8),
        GpuDeviceInput(index=1, vram_bytes=4),
    ]

    selection = select_gpu_devices(None, devices, backend="cuda")

    assert selection.gpu_ids == (1, 3)
    assert selection.backend == "cuda"
    assert selection.vram_bytes == 12
    with pytest.raises(AttributeError):
        selection.gpu_ids = (3, 1)


def test_gpu_selection_preserves_requested_order_and_deduplicates() -> None:
    devices = [GpuDeviceInput(index=0, vram_bytes=5), GpuDeviceInput(index=1, vram_bytes=7)]

    selection = select_gpu_devices([1, 0, 1], devices, backend=None)

    assert selection.gpu_ids == (1, 0)
    assert selection.backend == "cpu"
    assert selection.vram_bytes == 12


@pytest.mark.parametrize("requested", [[], [9]])
def test_gpu_selection_rejects_empty_or_unknown_selection(requested: list[int]) -> None:
    devices = [GpuDeviceInput(index=0)]
    with pytest.raises(ValidationError):
        select_gpu_devices(requested, devices, backend="cuda")


def test_gpu_selection_reports_unavailable_requested_hardware() -> None:
    with pytest.raises(ResourceUnavailableError, match="install GPUs"):
        select_gpu_devices(
            [0], [], backend="cpu", unavailable_detail="install GPUs before selecting one"
        )


def test_gpu_selection_accepts_no_devices_when_no_selection_was_requested() -> None:
    selection = select_gpu_devices(None, [], backend=None)

    assert selection.gpu_ids == ()
    assert selection.vram_bytes is None
