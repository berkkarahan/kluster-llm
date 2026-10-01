from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kllm.core.placement_models import ModelLayout, PlacementNodeInput


def test_placement_node_input_normalizes_nested_device_inventory():
    node = PlacementNodeInput.from_mapping(
        {
            "node_id": "master",
            "is_master": True,
            "backend": "metal",
            "total_ram_bytes": 32,
            "available_ram_bytes": 24,
            "reclaimable_ram_bytes": 2,
            "unified_memory": True,
            "rpc_endpoint": None,
            "latency_ms": 0,
            "devices": [
                {
                    "device": "Metal",
                    "backend": "metal",
                    "vram_bytes": 40,
                    "free_bytes": 30,
                    "reclaimable_bytes": 4,
                }
            ],
        }
    )

    assert node.node_id == "master"
    assert node.devices[0].vram_bytes == 40
    assert node.devices[0].free_bytes == 30
    assert node.devices[0].reclaimable_bytes == 4
    with pytest.raises(FrozenInstanceError):
        node.node_id = "changed"
    with pytest.raises(FrozenInstanceError):
        node.devices[0].vram_bytes = 0


@pytest.mark.parametrize(
    "value",
    [
        {"backend": "cpu"},
        {"node_id": "worker", "devices": [None]},
        {"node_id": "worker", "devices": {}},
    ],
)
def test_placement_node_input_rejects_malformed_inventory(value):
    with pytest.raises(ValueError):
        PlacementNodeInput.from_mapping(value)


def test_model_layout_freezes_nested_metadata_and_round_trips_reader_shape():
    source = {
        "architecture": "llama",
        "metadata": {"llama.block_count": 2, "llama.layers": [1, 2]},
        "layers": [10, 20],
        "input_bytes": 3,
        "output_bytes": 4,
        "tensors": [
            {
                "name": "blk.0.attn.weight",
                "dimensions": (2, 3),
                "dtype": 0,
                "offset": 8,
                "size_bytes": 12,
            }
        ],
        "confidence": "metadata",
    }

    layout = ModelLayout.from_mapping(source)

    assert layout is not None
    source["metadata"]["llama.layers"].append(3)
    assert layout.metadata["llama.layers"] == (1, 2)
    assert layout.layers == (10, 20)
    assert layout.tensors[0].dimensions == (2, 3)
    with pytest.raises(TypeError):
        layout.metadata["llama.block_count"] = 4
    assert layout.to_mapping()["metadata"]["llama.layers"] == [1, 2]


@pytest.mark.parametrize(
    "value",
    [
        {"architecture": "llama"},
        {"architecture": "llama", "metadata": {}, "layers": {}, "tensors": []},
        {
            "architecture": "llama",
            "metadata": {},
            "layers": [],
            "tensors": [{"name": "bad", "dimensions": []}],
        },
    ],
)
def test_model_layout_rejects_malformed_reader_payload(value):
    with pytest.raises((KeyError, ValueError)):
        ModelLayout.from_mapping(value)
