"""Pinned-runtime memory contracts using small, sparse synthetic GGUFs."""

from __future__ import annotations

import os
import struct
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from kllm.shared import native
from kllm.modules.models.interactors.gguf_meta import read_model_layout
from kllm.modules.inference.interactors.placement import MIB, memory_costs, plan_devices
from kllm.modules.inference.interactors.placement_models import ModelLayout, PlacementNodeInput, PlacementSettings
from kllm.modules.models.interactors.resolved_settings import ResolvedLaunchSettings
from kllm.modules.inference.interactors.run_launch import build_master_launch
from kllm.modules.inference.interactors.run_planning import estimate_auxiliary_assets, resolve_launch_settings
from kllm.modules.inference.entities import RunBody


def synthetic_gguf(path: Path, arch="llama", **metadata):
    """Write real tensor descriptors and sparse data, without CI model downloads."""
    values = {
        "general.architecture": arch,
        f"{arch}.block_count": 4,
        f"{arch}.context_length": 8192,
        f"{arch}.embedding_length": 512,
        f"{arch}.feed_forward_length": 1024,
        f"{arch}.attention.head_count": 8,
        f"{arch}.attention.head_count_kv": 2,
        f"{arch}.attention.key_length": 64,
        f"{arch}.attention.value_length": 64,
    }
    if arch == "deepseek2":
        values.update(
            {
                f"{arch}.attention.kv_lora_rank": 512,
                f"{arch}.rope.dimension_count": 64,
                f"{arch}.attention.key_length_mla": 128,
                f"{arch}.attention.value_length_mla": 128,
            }
        )
    if arch == "qwen35moe":
        values.update(
            {
                f"{arch}.ssm.state_size": 64,
                f"{arch}.ssm.inner_size": 2048,
                f"{arch}.ssm.time_step_rank": 32,
                f"{arch}.ssm.group_count": 4,
                f"{arch}.ssm.conv_kernel": 4,
                f"{arch}.full_attention_interval": 4,
            }
        )
    values.update({f"{arch}.{k}": v for k, v in metadata.items()})
    tensors = [("token_embd.weight", (512, 2048))]
    tensors += [(f"blk.{i}.attn_q.weight", (512, 512)) for i in range(4)]
    tensors += [("output.weight", (512, 2048))]
    buf = bytearray(struct.pack("<4sIQQ", b"GGUF", 3, len(tensors), len(values)))

    def string(value):
        data = value.encode()
        buf.extend(struct.pack("<Q", len(data)) + data)

    for key, value in values.items():
        string(key)
        if isinstance(value, str):
            buf.extend(struct.pack("<I", 8))
            string(value)
        elif isinstance(value, list):
            buf.extend(struct.pack("<IIQ", 9, 4, len(value)))
            buf.extend(struct.pack("<" + "I" * len(value), *value))
        else:
            buf.extend(struct.pack("<II", 4, value))
    offset = 0
    for name, dims in tensors:
        string(name)
        buf.extend(struct.pack("<IQQIQ", 2, *dims, 0, offset))
        offset += dims[0] * dims[1] * 4
    padding = (-len(buf)) % 32
    with path.open("wb") as file:
        file.write(buf + bytes(padding))
        file.truncate(len(buf) + padding + offset)
    return read_model_layout(path)


def synthetic_moe_gguf(path: Path, arch="qwen2moe", experts=4, layers=2):
    """Write an MoE GGUF with per-expert FFN tensors to exercise expert splits."""
    values = {
        "general.architecture": arch,
        f"{arch}.block_count": layers,
        f"{arch}.context_length": 8192,
        f"{arch}.embedding_length": 512,
        f"{arch}.feed_forward_length": 1024,
        f"{arch}.attention.head_count": 8,
        f"{arch}.attention.head_count_kv": 2,
        f"{arch}.attention.key_length": 64,
        f"{arch}.attention.value_length": 64,
        f"{arch}.expert_count": experts,
    }
    tensors = [("token_embd.weight", (512, 2048))]
    for i in range(layers):
        tensors.append((f"blk.{i}.attn_q.weight", (512, 512)))
        tensors.append((f"blk.{i}.ffn_gate.weight", (64, experts)))
        for e in range(experts):
            tensors.append((f"blk.{i}.ffn_up.{e}.weight", (512, 1024)))
            tensors.append((f"blk.{i}.ffn_down.{e}.weight", (1024, 512)))
    tensors.append(("output.weight", (512, 2048)))
    buf = bytearray(struct.pack("<4sIQQ", b"GGUF", 3, len(tensors), len(values)))

    def string(value):
        data = value.encode()
        buf.extend(struct.pack("<Q", len(data)) + data)

    for key, value in values.items():
        string(key)
        if isinstance(value, str):
            buf.extend(struct.pack("<I", 8))
            string(value)
        else:
            buf.extend(struct.pack("<II", 4, value))
    offset = 0
    for name, dims in tensors:
        string(name)
        buf.extend(struct.pack("<IQQIQ", 2, *dims, 0, offset))
        offset += dims[0] * dims[1] * 4
    padding = (-len(buf)) % 32
    with path.open("wb") as file:
        file.write(buf + bytes(padding))
        file.truncate(len(buf) + padding + offset)
    return read_model_layout(path)


@pytest.fixture(params=["llama", "deepseek2", "qwen35moe"])
def model_layout(request, tmp_path):
    return synthetic_gguf(tmp_path / "model.gguf", request.param)


def settings(**kwargs):
    return resolve_launch_settings(RunBody(model="fixture.gguf", **kwargs)).to_mapping()


def costs(layout, **kwargs):
    resolved = settings(**kwargs)
    return memory_costs(
        ModelLayout.from_mapping(layout),
        12 * MIB,
        resolved["context"],
        PlacementSettings.from_mapping(resolved),
    )


def test_resolved_launch_settings_are_immutable_and_round_trip_wire_values():
    body = RunBody(model="fixture.gguf", extra_args=["--temp", "0.2"])
    resolved = resolve_launch_settings(body)

    assert isinstance(resolved, ResolvedLaunchSettings)
    assert body.temperature == 0.2
    assert resolved.extra_args == ()
    assert ResolvedLaunchSettings.from_mapping(resolved.to_mapping()) == resolved
    with pytest.raises(FrozenInstanceError):
        resolved.context = 8192


def test_typed_placement_settings_are_frozen_and_preserve_memory_contract(model_layout):
    resolved = settings(context=6144, parallel_slots=2, checkpoints=3)
    typed = PlacementSettings.from_mapping(resolved)

    typed_layout = ModelLayout.from_mapping(model_layout)
    expected = memory_costs(typed_layout, 12 * MIB, resolved["context"], typed)
    actual = memory_costs(
        typed_layout,
        12 * MIB,
        typed.context,
        typed,
    )

    assert actual == expected
    with pytest.raises(FrozenInstanceError):
        typed.context = 8192


def test_tensor_spans_and_context_cache_matrix(model_layout):
    base = costs(model_layout, context=4096)
    assert base.weights_bytes == 12 * MIB
    assert base.complete and base.warnings == ()
    assert costs(model_layout, context=8192).kv_bytes > base.kv_bytes
    assert costs(model_layout, cache_type_k="f32").kv_bytes > base.kv_bytes
    wide_v = costs(model_layout, cache_type_v="f32")
    if model_layout["architecture"] == "deepseek2":
        assert wide_v.kv_bytes == base.kv_bytes, "MLA shares the compressed K cache"
    else:
        assert wide_v.kv_bytes > base.kv_bytes


def test_slot_growth_and_checkpoints(model_layout):
    one = costs(model_layout, parallel_slots=1, checkpoints=2)
    four = costs(model_layout, parallel_slots=4, checkpoints=2)
    assert four.kv_bytes == one.kv_bytes
    assert four.compute_bytes > one.compute_bytes
    assert four.state_bytes == 4 * one.state_bytes
    assert four.checkpoint_bytes == 4 * one.checkpoint_bytes
    assert costs(model_layout, checkpoints=0).checkpoint_bytes == 0


def test_physical_batch_and_context_padding(tmp_path):
    layout = synthetic_gguf(tmp_path / "model.gguf")
    assert costs(layout, batch_size=512).overhead == costs(layout, batch_size=1024).overhead
    assert costs(layout, batch_size=64).overhead < costs(layout, batch_size=512).overhead
    assert costs(layout, micro_batch=256).overhead > costs(layout, micro_batch=128).overhead
    assert costs(layout, context=513).kv_bytes == costs(layout, context=768).kv_bytes
    capped = settings(context=512, batch_size=4096, micro_batch=4096)
    assert capped["batch_size"] == 2048
    assert capped["micro_batch"] == 512


@pytest.mark.parametrize(
    "dtype,block_bytes",
    [("q4_0", 18), ("q4_1", 20), ("q5_0", 22), ("q5_1", 24), ("q8_0", 34), ("iq4_nl", 18)],
)
def test_block_quantized_cache_storage_includes_scales(tmp_path, dtype, block_bytes):
    layout = synthetic_gguf(tmp_path / "model.gguf")
    result = costs(layout, cache_type_k=dtype, cache_type_v=dtype)
    assert result.kv_bytes == 4 * 4096 * 2 * (128 // 32) * block_bytes


def test_pure_recurrent_architecture_has_no_kv_and_scales_state(tmp_path):
    layout = synthetic_gguf(
        tmp_path / "mamba.gguf",
        "mamba",
        **{"ssm.state_size": 16, "ssm.inner_size": 1024, "ssm.conv_kernel": 4},
    )
    one = costs(layout, parallel_slots=1)
    two = costs(layout, parallel_slots=2)
    assert one.kv_bytes == two.kv_bytes == 0
    assert one.state_bytes == 4 * 4 * (16 * 1024 + 3 * 1024)
    assert two.state_bytes == 2 * one.state_bytes
    assert two.checkpoint_bytes == 2 * one.checkpoint_bytes


def test_gpt_oss_alternating_swa_pattern_prices_full_attention_layers(tmp_path):
    layout = synthetic_gguf(
        tmp_path / "gpt-oss.gguf",
        "gpt-oss",
        **{
            "attention.sliding_window": 128,
            "attention.head_count": 64,
            "attention.head_count_kv": 8,
        },
    )
    result = costs(layout)
    # llama.cpp openai-moe.cpp loads the alternating pattern (set_swa_pattern(2)):
    # even layers keep the 128-token window, odd layers pay the padded full context.
    row = (512 // 32) * 34  # q8_0 K/V row for 8 KV heads × 64 elements
    assert result.complete and result.warnings == ()
    assert result.kv_bytes == (2 * 4096 + 2 * 256) * (row + row)
    # Marking every layer as sliding-window (the old fallback) would price 4 windows.
    assert result.kv_bytes > 4 * 256 * (row + row)


def test_flash_swa_and_split_scratch(tmp_path):
    layout = synthetic_gguf(tmp_path / "model.gguf", "gemma2", **{"attention.sliding_window": 256})
    small = costs(layout)
    full = costs(layout, extra_args=["--swa-full"])
    assert small.kv_bytes < full.kv_bytes
    assert small.compute_bytes <= full.compute_bytes
    on = costs(layout, flash_attention="on", cache_type_v="f16")
    off = costs(layout, flash_attention="off", cache_type_v="f16")
    assert off.compute_bytes > on.compute_bytes
    row = settings(split_mode="row")
    a = memory_costs(
        ModelLayout.from_mapping(layout),
        12 * MIB,
        4096,
        PlacementSettings.from_mapping(dict(row, device_count=1)),
    )
    b = memory_costs(
        ModelLayout.from_mapping(layout),
        12 * MIB,
        4096,
        PlacementSettings.from_mapping(dict(row, device_count=3)),
    )
    assert b.compute_bytes > a.compute_bytes


def test_auxiliary_assets_and_resolved_launch(tmp_path):
    draft = tmp_path / "draft.gguf"
    layout = synthetic_gguf(draft)
    projector, adapter, vector = [
        tmp_path / name for name in ("projector.gguf", "adapter.gguf", "vector.gguf")
    ]
    for path in (projector, adapter, vector):
        path.write_bytes(bytes(1024))
    body = RunBody(
        model="fixture.gguf",
        draft_model=str(draft),
        speculative="draft",
        vision=True,
        mmproj=str(projector),
        extra_args=[
            "--lora-scaled",
            str(adapter),
            "0.5",
            "--control-vector",
            str(vector),
            "--draft-max",
            "8",
            "--kv-unified",
            "--swa-full",
            "-ctkd",
            "q8_0",
        ],
    )
    base_settings = resolve_launch_settings(body)
    resolved = estimate_auxiliary_assets(body, base_settings)
    resolved_mapping = resolved.to_mapping()
    assert base_settings.draft_size_bytes == 0
    assert resolved.draft_size_bytes == draft.stat().st_size
    memory = memory_costs(
        ModelLayout.from_mapping(layout),
        draft.stat().st_size,
        4096,
        PlacementSettings.from_mapping(resolved_mapping),
    )
    assert memory.auxiliary_weights_bytes == 12 * MIB + 3072
    assert memory.auxiliary_runtime_bytes > 512 * MIB
    assert memory.complete
    launch = build_master_launch(
        body,
        {"ngl": 5, "tensor_split_csv": "5", "resolved_launch_settings": resolved_mapping},
        model_path=draft,
        rpc_servers=[],
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    for flag in (
        "--kv-unified",
        "--swa-full",
        "--spec-draft-n-max",
        "--cache-type-k-draft",
        "--spec-type",
    ):
        assert launch["extra_args"].count(flag) == 1
    assert "--draft-max" not in launch["extra_args"]
    assert "draft-simple" in launch["extra_args"]
    baseline = costs(layout)
    assert memory.auxiliary_runtime_bytes > baseline.auxiliary_runtime_bytes


def test_unknown_arguments_and_missing_assets_are_incomplete(tmp_path):
    layout = synthetic_gguf(tmp_path / "model.gguf")
    body = RunBody(
        model="fixture.gguf",
        draft_model=str(tmp_path / "missing.gguf"),
        vision=True,
        extra_args=[
            "--lora",
            str(tmp_path / "missing-adapter.gguf"),
            "--some-new-memory-flag",
            "1",
        ],
    )
    resolved = estimate_auxiliary_assets(body, resolve_launch_settings(body))
    memory = memory_costs(
        ModelLayout.from_mapping(layout),
        12 * MIB,
        4096,
        PlacementSettings.from_mapping(resolved.to_mapping()),
    )
    assert not memory.complete
    assert any("draft model" in w for w in memory.warnings)
    assert any("projector" in w for w in memory.warnings)
    assert any("--lora" in w for w in memory.warnings)
    assert any("--some-new-memory-flag" in w for w in memory.warnings)
    neutral = RunBody(model="fixture.gguf", extra_args=["--temp", "0.2", "--threads-batch", "2"])
    resolved = estimate_auxiliary_assets(neutral, resolve_launch_settings(neutral))
    assert memory_costs(
        ModelLayout.from_mapping(layout),
        12 * MIB,
        4096,
        PlacementSettings.from_mapping(resolved.to_mapping()),
    ).complete


COMPONENTS = (
    "weights_estimate_bytes",
    "auxiliary_weights_bytes",
    "kv_estimate_bytes",
    "recurrent_state_bytes",
    "checkpoint_estimate_bytes",
    "compute_estimate_bytes",
    "auxiliary_runtime_bytes",
    "execution_overhead_bytes",
)


def capacity(nid="master", *, master=True, unified=False, memory=8192 * MIB):
    return dict(
        node_id=nid,
        is_master=master,
        total_ram_bytes=memory,
        available_ram_bytes=memory,
        backend="metal" if unified else "vulkan",
        unified_memory=unified,
        devices=[
            dict(
                index=0,
                device="Metal" if unified else "Vulkan0",
                vram_bytes=memory,
                free_bytes=memory,
                backend="metal" if unified else "vulkan",
            )
        ],
        rpc_endpoint=f"{nid}:50052",
    )


@pytest.mark.parametrize("placement", ["cpu", "gpu", "unified", "rpc", "overflow"])
def test_allocation_conserves_components(model_layout, placement):
    master = capacity(
        unified=placement == "unified", memory=32 * MIB if placement == "overflow" else 8192 * MIB
    )
    nodes = [master]
    if placement == "rpc":
        master["devices"] = []
        master["backend"] = "cpu"
        nodes.insert(0, capacity("worker", master=False))
    typed_nodes = tuple(PlacementNodeInput.from_mapping(value) for value in nodes)
    result = plan_devices(
        typed_nodes,
        12 * MIB,
        ModelLayout.from_mapping(model_layout),
        PlacementSettings.from_mapping(settings()),
        context=4096,
        headroom=0.8,
        device="cpu" if placement == "cpu" else "gpu",
        vram_weight=0.6,
        latency_weight=0.4,
    )
    assert result.estimated_bytes == sum(getattr(result, c) for c in COMPONENTS)
    assert result.estimated_bytes == result.vram_alloc_bytes + result.cpu_alloc_bytes
    assert result.estimated_bytes == sum(result.node_alloc_bytes)
    if placement == "rpc":
        assert result.rpc_endpoints == ("worker:50052",)
    if placement == "overflow":
        assert result.fit_status == "overflow"


def test_automatic_context_reprices_batch_for_each_candidate(tmp_path):
    layout = synthetic_gguf(tmp_path / "model.gguf")
    resolved = settings(context_auto=True, context=512, batch_size=8192, micro_batch=8192)
    node = capacity(memory=400 * MIB)
    result = native.plan_split(
        [node],
        12 * MIB,
        context=512,
        context_auto=True,
        headroom=1,
        device="cpu",
        model_layout=layout,
        launch_settings=resolved,
    )
    assert result["fit_status"] != "overflow"
    assert 512 <= result["context_effective"] < 8192
    expected = native.plan_split(
        [node],
        12 * MIB,
        context=result["context_effective"],
        headroom=1,
        device="cpu",
        model_layout=layout,
        launch_settings=resolved,
    )
    assert result["estimated_bytes"] == expected["estimated_bytes"]
    assert sum(result[c] for c in COMPONENTS) == result["estimated_bytes"]


@pytest.mark.parametrize("k,v", [("f16", "f16"), ("f32", "f16"), ("bf16", "f32")])
def test_legacy_native_parity_for_supported_single_slot_cache_geometry(k, v):
    if not native.native_available():
        pytest.skip("Native extension required for legacy parity check")
    # The legacy heuristic models 128 K and V elements per token/layer and
    # 32 layers for this file-size tier. Compare its supported float widths;
    # quantized block scales and slot semantics deliberately differ.
    layout = dict(
        architecture="llama",
        layers=[MIB] * 32,
        input_bytes=0,
        output_bytes=0,
        confidence="metadata",
        metadata={
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 1,
        },
    )
    resolved = settings(cache_type_k=k, cache_type_v=v, parallel_slots=1)
    python = memory_costs(
        ModelLayout.from_mapping(layout), 1024 * MIB, 4096, PlacementSettings.from_mapping(resolved)
    )
    legacy = native.plan_split(
        [capacity()],
        1024 * MIB,
        context=4096,
        device="cpu",
        cache_type_k=k,
        cache_type_v=v,
        parallel_slots=1,
    )
    assert legacy["kv_estimate_bytes"] == python.kv_bytes
    assert legacy["estimate_complete"] is False
    assert "legacy native" in legacy["estimate_confidence"]
    assert legacy["estimate_warnings"]


def test_optional_local_qwen_regression():
    path = os.environ.get("KLLM_TEST_QWEN_GGUF")
    if not path:
        pytest.skip("Set KLLM_TEST_QWEN_GGUF to a local Qwen3.5 122B GGUF for metadata regression")
    layout = read_model_layout(path)
    assert layout and layout["architecture"] in ("qwen35", "qwen35moe")
    one = costs(layout, context=32768, checkpoints=2)
    two = costs(layout, context=32768, parallel_slots=2, checkpoints=2)
    assert one.complete
    assert one.kv_bytes == two.kv_bytes
    assert two.state_bytes == 2 * one.state_bytes
    assert one.weights_bytes >= sum(t["size_bytes"] for t in layout["tensors"])


def test_moe_expert_tensor_split(tmp_path):
    layout = synthetic_moe_gguf(tmp_path / "moe.gguf")
    assert layout["architecture"] == "qwen2moe"
    assert len(layout["expert_layers"]) == 2
    assert all(eb > 0 for eb in layout["expert_layers"])
    assert all(eb < lb for eb, lb in zip(layout["expert_layers"], layout["layers"]))
    memory = memory_costs(
        ModelLayout.from_mapping(layout), 12 * MIB, 4096, PlacementSettings.from_mapping(settings())
    )
    assert memory.expert_bytes == sum(layout["expert_layers"])
    assert memory.expert_bytes > 0


def test_non_moe_layout_has_no_expert_split(tmp_path):
    layout = synthetic_gguf(tmp_path / "plain.gguf")
    assert all(eb == 0 for eb in layout["expert_layers"])
    memory = memory_costs(
        ModelLayout.from_mapping(layout), 12 * MIB, 4096, PlacementSettings.from_mapping(settings())
    )
    assert memory.expert_bytes == 0


def test_batch_and_micro_batch_ceilings():
    capped = settings(context=512, batch_size=4096, micro_batch=4096)
    assert capped["batch_size"] == 2048
    assert capped["micro_batch"] == 512
    assert settings()["batch_size"] == 512
    assert settings()["micro_batch"] == 128
    assert settings(batch_size=2048, context=512)["batch_size"] == 2048
    assert settings(batch_size=1024, micro_batch=2048)["micro_batch"] == 512
