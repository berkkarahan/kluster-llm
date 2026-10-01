"""Unit tests for VRAM-proportional tensor-split helpers."""
from __future__ import annotations

import pytest
from kllm.core.placement_models import PlacementDeviceCandidate

from kllm.tensor_split import (
    device_can_hold,
    layer_counts_from_weights,
    proportional_weights,
    select_routing_devices,
    tensor_split_proportions,
)


def candidate(
    *, local: bool, budget_bytes: int, score: float, node_id: str = "node"
) -> PlacementDeviceCandidate:
    return PlacementDeviceCandidate(
        node_id=node_id,
        device="GPU0",
        backend="cuda",
        local=local,
        budget_bytes=budget_bytes,
        latency_ms=0.0,
        rpc_endpoint=None if local else f"{node_id}:50052",
        rpc_index=0,
        rpc_device_count=1,
        unified_memory=False,
        score=score,
    )


def test_tensor_split_proportions_example():
    assert tensor_split_proportions([24, 8]) == [24.0, 8.0]


def test_tensor_split_proportions_rejects_empty():
    with pytest.raises(ValueError, match="positive"):
        tensor_split_proportions([])
    with pytest.raises(ValueError, match="positive"):
        tensor_split_proportions([0, 0])


def test_layer_counts_from_weights_3_to_1():
    assert layer_counts_from_weights([24, 8], 32) == [24, 8]
    assert sum(layer_counts_from_weights([24, 8], 33)) == 33


def test_select_routing_prefers_local_when_fit():
    devices = [
        candidate(local=False, budget_bytes=10_000, score=1.0),
        candidate(local=True, budget_bytes=5_000, score=0.5),
    ]
    selection = select_routing_devices(
        devices, model_bytes=100, overhead=10, cpu_base=1, cpu_budget=10, locals_fit=True
    )
    assert selection.mode == "local"
    assert all(d.local for d in selection.devices)


def test_select_routing_single_remote():
    devices = [
        candidate(local=True, budget_bytes=100, score=0.2),
        candidate(local=False, budget_bytes=10_000, score=0.9, node_id="big"),
        candidate(local=False, budget_bytes=500, score=0.5, node_id="small"),
    ]
    selection = select_routing_devices(
        devices, model_bytes=1_000, overhead=10, cpu_base=1, cpu_budget=10, locals_fit=False
    )
    assert selection.mode == "single_remote"
    assert selection.devices == (devices[1],)


def test_device_can_hold():
    assert device_can_hold(candidate(local=True, budget_bytes=100, score=1), model_bytes=80, overhead=20)
    assert not device_can_hold(candidate(local=True, budget_bytes=99, score=1), model_bytes=80, overhead=20)


def test_proportional_weights_mib():
    mib = 1024**2
    assert proportional_weights(
        [
            candidate(local=True, budget_bytes=24 * 1024 * mib, score=1),
            candidate(local=False, budget_bytes=8 * 1024 * mib, score=1),
        ]
    ) == [24.0, 8.0]
