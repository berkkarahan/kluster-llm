"""Deterministic dense-Llama tensor shards matching llama.cpp b11026.

The split axes, layer rotation, and rounding rules mirror
``llama_meta_device_get_split_state`` at the pinned engine revision. The
manifest describes strided axis-0 slices as tensor geometry instead of claiming
they are one contiguous file range. It is the shared input for admission,
memory planning, loading, and engine verification.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from kllm.gguf_meta import read_model_layout

MANIFEST_VERSION = "kllm-llama-b11026-tp-v1"


class MpiManifestError(ValueError):
    """The GGUF cannot be represented by the first supported shard format."""


# dtype id -> (name, elements per storage block, bytes per storage block)
_GGML_TYPES: dict[int, tuple[str, int, int]] = {
    0: ("f32", 1, 4), 1: ("f16", 1, 2),
    2: ("q4_0", 32, 18), 3: ("q4_1", 32, 20),
    6: ("q5_0", 32, 22), 7: ("q5_1", 32, 24),
    8: ("q8_0", 32, 34), 9: ("q8_1", 32, 36),
    10: ("q2_k", 256, 84), 11: ("q3_k", 256, 110),
    12: ("q4_k", 256, 144), 13: ("q5_k", 256, 176),
    14: ("q6_k", 256, 210), 15: ("q8_k", 256, 292),
    24: ("i8", 1, 1), 25: ("i16", 1, 2), 26: ("i32", 1, 4),
    27: ("i64", 1, 8), 28: ("f64", 1, 8), 30: ("bf16", 1, 2),
}

_Q_WEIGHT = re.compile(r"blk\.(\d+)\.attn_q\.weight$")
_KV_WEIGHT = re.compile(r"blk\.(\d+)\.attn_[kv]\.weight$")
_ATTN_OUT = re.compile(r"blk\.(\d+)\.attn_output\.weight$")
_FFN_UP_GATE = re.compile(r"blk\.(\d+)\.ffn_(?:up|gate)\.weight$")
_FFN_DOWN = re.compile(r"blk\.(\d+)\.ffn_down\.weight$")
_Q_BIAS = re.compile(r"blk\.(\d+)\.attn_q\.bias$")
_KV_BIAS = re.compile(r"blk\.(\d+)\.attn_[kv]\.bias$")


def _scalar(meta: dict[str, Any], key: str, layers: int) -> list[int]:
    value = meta.get(key)
    if isinstance(value, list):
        if len(value) != layers:
            raise MpiManifestError(f"{key} has {len(value)} entries for {layers} layers")
        result = [int(item) for item in value]
    elif value is not None:
        result = [int(value)] * layers
    else:
        raise MpiManifestError(f"GGUF metadata is missing {key}")
    if any(item <= 0 for item in result):
        raise MpiManifestError(f"GGUF metadata {key} must be positive")
    return result


def _dtype(tensor: dict[str, Any]) -> tuple[str, int, int]:
    dtype = int(tensor["dtype"])
    try:
        return _GGML_TYPES[dtype]
    except KeyError as exc:
        raise MpiManifestError(
            f"tensor {tensor['name']} uses unsupported GGML dtype {dtype}"
        ) from exc


def _tensor_bytes(dimensions: list[int], block: int, type_size: int) -> int:
    if not dimensions or dimensions[0] % block:
        raise MpiManifestError("tensor row is not aligned to its quantization block")
    return dimensions[0] // block * type_size * math.prod(dimensions[1:])


def _reference(tensors: dict[str, dict[str, Any]], layer: int, suffix: str) -> dict[str, Any]:
    name = f"blk.{layer}.{suffix}"
    try:
        return tensors[name]
    except KeyError as exc:
        raise MpiManifestError(f"dense Llama tensor {name} is missing") from exc


def _layer_index(match: re.Match[str], layers: int, tensor_name: str) -> int:
    layer = int(match.group(1))
    if layer >= layers:
        raise MpiManifestError(
            f"tensor {tensor_name} refers to layer {layer}, but the model has {layers} layers"
        )
    return layer


def _attention_granularities(
    layer: int,
    world_size: int,
    head_count: list[int],
    head_count_kv: list[int],
    embedding: int,
    tensors: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    heads = head_count[layer]
    kv_heads = head_count_kv[layer]
    if embedding % heads or heads % kv_heads:
        raise MpiManifestError("Llama attention head metadata is not divisible")
    head_dim = embedding // heads
    n_gqa = heads // kv_heads
    n_embd_q = n_gqa * head_dim
    _, block, _ = _dtype(_reference(tensors, layer, "attn_output.weight"))
    performance_block = block
    while performance_block < 128 and performance_block * world_size < n_embd_q:
        performance_block *= 2
    gran_q = math.lcm(n_embd_q, performance_block)
    return gran_q, gran_q // n_gqa


def _split_counts(size: int, world_size: int, granularity: int, rotation: int) -> list[int]:
    counts = [0] * world_size
    low = 0
    for logical in range(world_size - 1):
        high = size * (logical + 1) // world_size
        high -= high % granularity
        counts[(logical + rotation) % world_size] = high - low
        low = high
    counts[(world_size - 1 + rotation) % world_size] = size - low
    if sum(counts) != size or any(value < 0 for value in counts):
        raise MpiManifestError("invalid shard boundaries")
    return counts


def _split_starts(counts: list[int], rotation: int) -> list[int]:
    """Return each physical rank's axis offset in logical shard order."""
    starts = [0] * len(counts)
    cursor = 0
    for logical in range(len(counts)):
        rank = (logical + rotation) % len(counts)
        starts[rank] = cursor
        cursor += counts[rank]
    return starts


def build_manifest_from_layout(
    layout: dict[str, Any], model_sha256: str, world_size: int, context: int = 4096
) -> dict[str, Any]:
    if world_size < 1 or world_size > 128:
        raise MpiManifestError("world size must be between 1 and 128")
    if context < 1:
        raise MpiManifestError("context must be positive")
    if layout.get("architecture") != "llama" or layout.get("confidence") != "metadata":
        raise MpiManifestError("MPI requires a complete dense Llama GGUF layout")
    raw_tensors = layout.get("tensors")
    meta = layout.get("metadata")
    if not isinstance(raw_tensors, list) or not isinstance(meta, dict):
        raise MpiManifestError("GGUF tensor metadata is incomplete")
    layers = int(meta.get("llama.block_count") or len(layout.get("layers") or []))
    if layers <= 0:
        raise MpiManifestError("GGUF has no Llama layers")
    embedding = int(meta.get("llama.embedding_length") or 0)
    if embedding <= 0:
        raise MpiManifestError("GGUF metadata is missing llama.embedding_length")
    head_count = _scalar(meta, "llama.attention.head_count", layers)
    head_count_kv = _scalar(meta, "llama.attention.head_count_kv", layers)
    tensors = {str(tensor["name"]): tensor for tensor in raw_tensors}
    if len(tensors) != len(raw_tensors):
        raise MpiManifestError("GGUF has duplicate tensor names")

    entries: list[dict[str, Any]] = []
    rank_bytes = [0] * world_size
    for raw in sorted(raw_tensors, key=lambda tensor: str(tensor["name"])):
        name = str(raw["name"])
        dimensions = [int(value) for value in raw["dimensions"]]
        dtype_name, block, type_size = _dtype(raw)
        full_bytes = _tensor_bytes(dimensions, block, type_size)
        axis: int | None = None
        granularity = 1
        layer: int | None = None

        match = _Q_WEIGHT.fullmatch(name)
        if match:
            layer = _layer_index(match, layers, name)
            axis = 1
            granularity, _ = _attention_granularities(
                layer, world_size, head_count, head_count_kv, embedding, tensors
            )
        else:
            match = _KV_WEIGHT.fullmatch(name)
            if match:
                layer = _layer_index(match, layers, name)
                axis = 1
                _, granularity = _attention_granularities(
                    layer, world_size, head_count, head_count_kv, embedding, tensors
                )
            else:
                match = _ATTN_OUT.fullmatch(name)
                if match:
                    layer = _layer_index(match, layers, name)
                    axis = 0
                    granularity, _ = _attention_granularities(
                        layer, world_size, head_count, head_count_kv, embedding, tensors
                    )
                else:
                    match = _FFN_UP_GATE.fullmatch(name)
                    if match:
                        layer = _layer_index(match, layers, name)
                        axis = 1
                        _, ref_block, _ = _dtype(_reference(tensors, layer, "ffn_down.weight"))
                        granularity = math.lcm(ref_block, 128)
                    else:
                        match = _FFN_DOWN.fullmatch(name)
                        if match:
                            layer = _layer_index(match, layers, name)
                            axis = 0
                            granularity = math.lcm(block, 128)
                        elif name == "output.weight":
                            axis = 1
                        elif name == "output.bias":
                            axis = 0
                        else:
                            match = _Q_BIAS.fullmatch(name)
                            if match:
                                layer = _layer_index(match, layers, name)
                                axis = 0
                                granularity, _ = _attention_granularities(
                                    layer, world_size, head_count, head_count_kv, embedding, tensors
                                )
                            else:
                                match = _KV_BIAS.fullmatch(name)
                                if match:
                                    layer = _layer_index(match, layers, name)
                                    axis = 0
                                    _, granularity = _attention_granularities(
                                        layer, world_size, head_count, head_count_kv, embedding, tensors
                                    )

        if axis is None:
            counts = None
            rotation = None
            shards = [{
                "rank": rank,
                "elements": None,
                "axis_start": None,
                "axis_end": None,
                "bytes": full_bytes,
            } for rank in range(world_size)]
            for rank in range(world_size):
                rank_bytes[rank] += full_bytes
            placement = "mirrored"
        else:
            if axis >= len(dimensions):
                raise MpiManifestError(f"tensor {name} cannot split on axis {axis}")
            rotation = (layer if layer is not None else layers) % world_size
            counts = _split_counts(dimensions[axis], world_size, granularity, rotation)
            starts = _split_starts(counts, rotation)
            shards = []
            for rank, count in enumerate(counts):
                shard_dims = list(dimensions)
                shard_dims[axis] = count
                size_bytes = _tensor_bytes(shard_dims, block, type_size) if count else 0
                rank_bytes[rank] += size_bytes
                shards.append({
                    "rank": rank,
                    "elements": count,
                    "axis_start": starts[rank],
                    "axis_end": starts[rank] + count,
                    "bytes": size_bytes,
                })
            placement = "sharded"
        entries.append({
            "name": name,
            "dtype": dtype_name,
            "dtype_id": int(raw["dtype"]),
            "dimensions": dimensions,
            "file_offset": int(layout.get("data_offset") or 0) + int(raw["offset"]),
            "gguf_data_offset": int(raw["offset"]),
            "tensor_bytes": full_bytes,
            "placement": placement,
            "axis": axis,
            "rotation": rotation,
            "granularity": granularity if axis is not None else None,
            "counts": counts,
            "shards": shards,
        })

    kv_layers = []
    rank_kv_bytes = [0] * world_size
    for layer in range(layers):
        if embedding % head_count[layer]:
            raise MpiManifestError("Llama embedding is not divisible by attention heads")
        head_dim = embedding // head_count[layer]
        kv_width = head_dim * head_count_kv[layer]
        _, kv_granularity = _attention_granularities(
            layer, world_size, head_count, head_count_kv, embedding, tensors
        )
        counts = _split_counts(
            kv_width, world_size, kv_granularity, layer % world_size
        )
        starts = _split_starts(counts, layer % world_size)
        bytes_by_rank = [count * context * 2 * 2 for count in counts]
        rank_kv_bytes = [
            current + addition
            for current, addition in zip(rank_kv_bytes, bytes_by_rank)
        ]
        kv_layers.append({
            "layer": layer,
            "axis": 0,
            "width": kv_width,
            "counts": counts,
            "starts": starts,
            "rotation": layer % world_size,
            "granularity": kv_granularity,
            "bytes_by_rank": bytes_by_rank,
        })

    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "engine_revision": "b11026",
        "architecture": "llama",
        "model_sha256": model_sha256,
        "world_size": world_size,
        "rank_weight_bytes": rank_bytes,
        "kv": {"dtype": "f16", "context": context, "layers": kv_layers},
        "rank_kv_bytes": rank_kv_bytes,
        "rank_model_bytes": [
            weights + kv for weights, kv in zip(rank_bytes, rank_kv_bytes)
        ],
        "tensors": entries,
    }
    manifest["identity"] = "sha256:" + hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


@lru_cache(maxsize=8)
def _file_sha256(path: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    del size, mtime_ns, ctime_ns
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def model_sha256(model_path: Path) -> str:
    path = model_path.resolve()
    stat = path.stat()
    return _file_sha256(
        str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    )


def build_shard_manifest(model_path: Path, world_size: int, context: int) -> dict[str, Any]:
    path = model_path.resolve()
    layout = read_model_layout(path)
    if layout is None:
        raise MpiManifestError("GGUF tensor layout cannot be read")
    return build_manifest_from_layout(
        layout,
        model_sha256(path),
        world_size,
        context,
    )
