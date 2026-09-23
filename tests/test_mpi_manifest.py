from __future__ import annotations

import pytest

from kllm.mpi_manifest import MpiManifestError, build_manifest_from_layout


def _layout(dtype=1):
    tensors = []

    def add(name, dims, kind=dtype):
        tensors.append({
            "name": name,
            "dimensions": tuple(dims),
            "dtype": kind,
            "offset": len(tensors) * 4096,
            "size_bytes": 4096,
        })

    add("token_embd.weight", [256, 256])
    add("blk.0.attn_q.weight", [256, 256])
    add("blk.0.attn_k.weight", [256, 64])
    add("blk.0.attn_v.weight", [256, 64])
    add("blk.0.attn_output.weight", [256, 256])
    add("blk.0.ffn_up.weight", [256, 512])
    add("blk.0.ffn_gate.weight", [256, 512])
    add("blk.0.ffn_down.weight", [512, 256])
    add("blk.0.attn_norm.weight", [256])
    add("blk.0.ffn_norm.weight", [256])
    add("output_norm.weight", [256])
    add("output.weight", [256, 128])
    return {
        "architecture": "llama",
        "confidence": "metadata",
        "layers": [1],
        "metadata": {
            "llama.block_count": 1,
            "llama.embedding_length": 256,
            "llama.attention.head_count": 8,
            "llama.attention.head_count_kv": 2,
        },
        "tensors": tensors,
    }


def test_manifest_uses_b11026_axes_rotation_and_mirrors():
    manifest = build_manifest_from_layout(_layout(), "sha256:model", 2)
    tensors = {tensor["name"]: tensor for tensor in manifest["tensors"]}
    assert tensors["blk.0.attn_q.weight"]["axis"] == 1
    assert tensors["blk.0.attn_output.weight"]["axis"] == 0
    assert tensors["blk.0.ffn_up.weight"]["axis"] == 1
    assert tensors["blk.0.ffn_down.weight"]["axis"] == 0
    assert tensors["blk.0.attn_norm.weight"]["placement"] == "mirrored"
    # Layer zero starts rounding at rank zero; model output rotates by n_layers.
    assert tensors["output.weight"]["counts"] == [64, 64]
    assert manifest["identity"].startswith("sha256:")
    assert sum(manifest["kv"]["layers"][0]["counts"]) == 64
    assert all(total == weights + kv for total, weights, kv in zip(
        manifest["rank_model_bytes"], manifest["rank_weight_bytes"], manifest["rank_kv_bytes"]
    ))


def test_quantized_shards_stay_on_storage_blocks_and_sum_exact_bytes():
    manifest = build_manifest_from_layout(_layout(dtype=12), "sha256:model", 3)
    for tensor in manifest["tensors"]:
        if tensor["placement"] == "sharded":
            axis = tensor["axis"]
            if axis == 0:
                assert all(count == 0 or count % 256 == 0 for count in tensor["counts"])
            assert sum(shard["bytes"] for shard in tensor["shards"]) == tensor["tensor_bytes"]
    assert sum(manifest["rank_weight_bytes"]) >= sum(
        tensor["tensor_bytes"] for tensor in manifest["tensors"]
    )


def test_unequal_rotation_is_deterministic():
    layout = _layout()
    # Layer 0 q rows split 256 at granularity 128 across three ranks.
    manifest = build_manifest_from_layout(layout, "sha256:model", 3)
    q = next(t for t in manifest["tensors"] if t["name"] == "blk.0.attn_q.weight")
    assert q["counts"] == [0, 128, 128]
    assert [(shard["axis_start"], shard["axis_end"]) for shard in q["shards"]] == [
        (0, 0), (0, 128), (128, 256)
    ]

    output = next(t for t in manifest["tensors"] if t["name"] == "output.weight")
    assert output["rotation"] == 1
    ordered = sorted(output["shards"], key=lambda shard: shard["axis_start"])
    assert ordered[0]["rank"] == 1
    assert ordered[0]["axis_start"] == 0
    assert ordered[-1]["axis_end"] == output["dimensions"][output["axis"]]


def test_manifest_rejects_tensor_layer_outside_metadata():
    layout = _layout()
    layout["tensors"].append({
        "name": "blk.1.attn_q.weight",
        "dimensions": (256, 256),
        "dtype": 1,
        "offset": 99 * 4096,
        "size_bytes": 4096,
    })

    with pytest.raises(MpiManifestError, match="model has 1 layers"):
        build_manifest_from_layout(layout, "sha256:model", 2)
