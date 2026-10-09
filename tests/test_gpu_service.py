from __future__ import annotations

import pytest

from kllm.agent.services.gpus import GpuSelectionService
from kllm.exceptions import ResourceUnavailableError, ValidationError


def make_service(payload: dict, configured: list[int] | None = None) -> GpuSelectionService:
    return GpuSelectionService(
        list_gpus=lambda: payload,
        configured_gpu_ids=lambda: configured,
        install_recovery_command=lambda: "kllm install --native",
    )


def test_membership_uses_configured_selection_when_inventory_is_available() -> None:
    service = make_service({"gpus": [{"index": 0}]}, configured=[0])

    assert service.membership_gpu_ids(None, body_sent=False) == [0]
    assert service.membership_gpu_ids([], body_sent=True) == []


def test_membership_ignores_stale_config_when_inventory_is_empty() -> None:
    service = make_service({"gpus": []}, configured=[3])

    assert service.membership_gpu_ids(None, body_sent=False) is None
    assert service.membership_gpu_ids([3], body_sent=True) == [3]


def test_resolve_returns_selection_with_matching_device_records() -> None:
    service = make_service({"backend": "cuda", "gpus": [{"index": 2, "vram_bytes": 1024}]})

    result = service.resolve(None)

    assert result.selection.gpu_ids == (2,)
    assert result.selection.backend == "cuda"
    assert result.selection.vram_bytes == 1024
    assert tuple(device.to_mapping() for device in result.devices) == (
        {"index": 2, "vram_bytes": 1024},
    )
    assert result.devices[0].index == 2


def test_resolve_empty_inventory_reports_install_recovery_command() -> None:
    service = make_service({"gpus": [], "error": "native probe failed"})

    with pytest.raises(ResourceUnavailableError, match="kllm install --native"):
        service.resolve([0])


def test_resolve_probe_timeout_hints_at_a_manual_first_launch() -> None:
    service = make_service(
        {
            "gpus": [],
            "error": "Command '['llama-server', '--list-devices']' timed out after 15 seconds",
        }
    )

    with pytest.raises(ResourceUnavailableError, match="once manually") as caught:
        service.resolve([0])

    assert "kllm install --native" not in str(caught.value)
    assert "timed out after 15 seconds" in str(caught.value)


def test_resolve_rejects_unknown_ids_as_domain_validation_error() -> None:
    service = make_service({"gpus": [{"index": 0}]})

    with pytest.raises(ValidationError, match="unknown gpu_ids"):
        service.resolve([1])


def test_inventory_rejects_duplicate_device_indices() -> None:
    service = make_service({"gpus": [{"index": 0}, {"index": 0}]})

    with pytest.raises(ValidationError, match="duplicate index 0"):
        service.inventory()


def test_list_payload_normalizes_backend_metadata_and_supplies_ui_options() -> None:
    service = make_service(
        {
            "backend": " CUDA ",
            "probed_backend": " CUDA ",
            "gpus": [{"index": 0}],
            "binary_version": "v0.5.0",
        }
    )

    payload = service.list_payload()

    assert payload["backend"] == "cuda"
    assert payload["probed_backend"] == "cuda"
    assert payload["allowed_backends"] == ["metal", "cuda", "rocm", "vulkan"]
    assert payload["binary_version"] == "v0.5.0"
    assert payload["install_hint"] == "kllm install --native"


def test_list_payload_turns_inventory_failure_into_cpu_fallback() -> None:
    service = GpuSelectionService(
        list_gpus=lambda: (_ for _ in ()).throw(OSError("probe unavailable")),
        configured_gpu_ids=lambda: None,
        install_recovery_command=lambda: "kllm install --native",
    )

    assert service.list_payload() == {
        "backend": "cpu",
        "probed_backend": "cpu",
        "allowed_backends": [],
        "gpus": [],
        "error": "probe unavailable",
        "install_hint": "kllm install --native",
    }
