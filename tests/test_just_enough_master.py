"""Automatic placement can minimize the master's GPU share."""

from __future__ import annotations

from kllm.core.placement import plan_devices
from kllm.core.placement_models import PlacementNodeInput, PlacementSettings

GiB = 1024**3


def _node(node_id: str, *, vram_gib: float, latency_ms: float, master: bool = False):
    return PlacementNodeInput.from_mapping(
        {
            "node_id": node_id,
            "is_master": master,
            "backend": "vulkan",
            "total_ram_bytes": 64 * GiB,
            "available_ram_bytes": 64 * GiB,
            "reclaimable_ram_bytes": 0,
            "unified_memory": False,
            "rpc_endpoint": None if master else f"10.0.0.{node_id}:50052",
            "latency_ms": latency_ms,
            "devices": [
                {
                    "index": 0,
                    "device": "Vulkan0",
                    "backend": "vulkan",
                    "vram_bytes": int(vram_gib * GiB),
                    "free_bytes": int(vram_gib * GiB),
                    "reclaimable_bytes": 0,
                }
            ],
        }
    )


def _plan(nodes, size_gib: float, *, just_enough_master: bool):
    return plan_devices(
        nodes,
        int(size_gib * GiB),
        None,
        PlacementSettings(),
        context=4096,
        headroom=0.8,
        device="gpu",
        vram_weight=0.6,
        latency_weight=0.4,
        just_enough_master=just_enough_master,
    )


def _master_gpu_layers(plan) -> int:
    return sum(a.layers for a in plan.device_allocations if a.node_id == "master")


def _allocated_layers(plan) -> int:
    return sum(a.layers for a in plan.device_allocations)


def test_just_enough_master_empties_the_master_when_workers_fit():
    nodes = [
        _node("master", vram_gib=15.9, latency_ms=0.0, master=True),
        _node("w1", vram_gib=48.0, latency_ms=0.3),
        _node("w2", vram_gib=32.0, latency_ms=0.5),
    ]
    default = _plan(nodes, 60, just_enough_master=False)
    # The master's zero latency otherwise fills its whole budget.
    assert _master_gpu_layers(default) > 0

    result = _plan(nodes, 60, just_enough_master=True)
    assert result.fit_status != "overflow"
    assert _master_gpu_layers(result) == 0
    assert all(a.node_id != "master" for a in result.device_allocations)
    assert _allocated_layers(result) == result.ngl


def test_just_enough_master_keeps_only_the_remainder_when_workers_are_short():
    nodes = [
        _node("master", vram_gib=15.9, latency_ms=0.0, master=True),
        _node("w1", vram_gib=20.0, latency_ms=0.3),
        _node("w2", vram_gib=20.0, latency_ms=0.5),
    ]
    default = _plan(nodes, 60, just_enough_master=False)
    result = _plan(nodes, 60, just_enough_master=True)
    assert result.fit_status != "overflow"
    assert _allocated_layers(result) == result.ngl
    # The master may still hold the tail, but never more than the default plan.
    assert _master_gpu_layers(result) <= _master_gpu_layers(default)


def test_just_enough_master_is_inert_with_a_single_node():
    nodes = [_node("master", vram_gib=64.0, latency_ms=0.0, master=True)]
    result = _plan(nodes, 40, just_enough_master=True)
    assert result.fit_status != "overflow"
    assert _master_gpu_layers(result) > 0
