"""Model memory estimation independent of cluster allocation policy."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from kllm.core.placement_models import MemoryEstimate, ModelLayout, PlacementSettings
from kllm.exceptions import ValidationError

MIB = 1024**2
Layout = ModelLayout
Settings = PlacementSettings


@dataclass(frozen=True)
class AttentionDimensions:
    """Per-layer attention dimensions used to estimate scratch memory."""

    heads: int
    key_width: int
    value_width: int
    capacity: int


@dataclass(frozen=True)
class AuxiliaryRuntime:
    """Memory and warnings contributed by vision and speculative runtime state."""

    runtime_bytes: int
    warnings: tuple[str, ...]


MetricDimensions = list[AttentionDimensions]
WIDTHS = {
    "f32": 4,
    "f16": 2,
    "bf16": 2,
    "q8_0": 34 / 32,
    "q4_0": 18 / 32,
    "q4_1": 20 / 32,
    "q5_0": 22 / 32,
    "q5_1": 24 / 32,
    "iq4_nl": 18 / 32,
}


def _pad(value: int, alignment: int = 256) -> int:
    return (value + alignment - 1) // alignment * alignment


def effective_launch_settings(settings: PlacementSettings, context: int) -> PlacementSettings:
    """Mirror v0.5.0 context padding, per-stream padding and causal batch caps."""
    slots = settings.parallel_slots
    context = _pad(context)
    if not settings.kv_unified:
        context = _pad(context // slots) * slots
        if not context:
            raise ValidationError("context per slot must be positive")
    batch_size = min(
        context,
        settings.requested_batch_size
        if settings.requested_batch_size is not None
        else settings.batch_size,
    )
    micro_batch = min(
        batch_size,
        settings.requested_micro_batch
        if settings.requested_micro_batch is not None
        else settings.micro_batch,
    )
    return replace(settings, context=context, batch_size=batch_size, micro_batch=micro_batch)


def _at(value: Any, index: int) -> Any:
    return value[index] if isinstance(value, (list, tuple)) else value


def _cache_row_bytes(elements: int, dtype: str) -> int:
    # GGML row storage includes the scale/minimum in each 32-element block.
    block = 1 if dtype in ("f32", "f16", "bf16") else 32
    return math.ceil(elements / block) * int(WIDTHS[dtype] * block)


def _compute_buffers(layout: Layout, settings: Settings, dimensions: MetricDimensions) -> int:
    """Conservative graph reserve, not a backend allocator measurement.

    v0.5.0 reserves prefill graphs at n_ubatch and decode graphs at n_seq_max.
    Attention scores are materialized without FA; quantized cache conversion
    and row/tensor transfer buffers need their own scratch. Buffer reuse and
    backend-specific workspaces remain approximate.
    """
    arch, meta = layout.architecture, layout.metadata
    embd = int(meta.get(f"{arch}.embedding_length", 4096))
    slots, ubatch = settings.parallel_slots, settings.micro_batch
    tokens = max(ubatch, slots)
    ffn = meta.get(f"{arch}.feed_forward_length", 4 * embd)
    ffn = max(ffn) if isinstance(ffn, (list, tuple)) else int(ffn)
    vocab = int(meta.get(f"{arch}.vocab_size", 32000))
    for tensor in layout.tensors:
        if tensor.name in ("token_embd.weight", "output.weight"):
            vocab = int(tensor.dimensions[-1])
            break
    activations = 4 * tokens * (8 * embd + 3 * ffn)
    inner = int(meta.get(f"{arch}.ssm.inner_size", 0))
    state_dim = int(meta.get(f"{arch}.ssm.state_size", 0))
    if inner and state_dim:
        # Recurrent projections and chunked scan/delta-net matrix scratch.
        activations += 4 * tokens * inner
        activations += 4 * max(slots, math.ceil(ubatch / 16)) * state_dim * inner
    # Host logits/embeddings and sequence bookkeeping grow with active outputs.
    outputs = 4 * slots * (vocab + embd)
    flash = settings.flash_attention == "on"
    attention = 0
    for dimension in dimensions:
        mask = 4 * dimension.capacity * _pad(tokens, 32)
        scores = 0 if flash else 4 * dimension.heads * dimension.capacity * tokens
        dequant = (
            0
            if flash
            else 4
            * dimension.capacity
            * (
                (
                    dimension.key_width
                    if settings.cache_type_k not in ("f32", "f16", "bf16")
                    else 0
                )
                + (
                    dimension.value_width
                    if settings.cache_type_v not in ("f32", "f16", "bf16")
                    else 0
                )
            )
        )
        attention = max(attention, mask + scores + dequant)
    split = 0
    if settings.split_mode in ("row", "tensor"):
        split = 4 * tokens * embd * max(1, settings.device_count)
    return int(activations + outputs + attention + split)


def _auxiliary_runtime(settings: Settings, context: int, state_bytes: int) -> AuxiliaryRuntime:
    runtime = 512 * MIB * settings.parallel_slots if settings.vision_runtime else 0
    warnings = []
    if settings.draft_size_bytes:
        draft = settings.draft_layout
        if not draft:
            warnings.append(
                "Draft model metadata unavailable; draft KV and compute use fallback dimensions"
            )
        draft_settings = replace(
            settings,
            cache_type_k=settings.draft_cache_type_k,
            cache_type_v=settings.draft_cache_type_v,
            draft_size_bytes=0,
            draft_layout=None,
            vision_runtime=False,
            estimate_warnings=(),
            auxiliary_weights_bytes=0,
            auxiliary_bytes=0,
            checkpoints=0,
        )
        memory = memory_costs(draft, settings.draft_size_bytes, context, draft_settings)
        runtime += memory.kv_bytes + memory.state_bytes + memory.overhead
        warnings.extend(f"Draft: {warning}" for warning in memory.warnings)
    if settings.speculative not in ("none", "off"):
        if settings.draft_size_bytes or settings.speculative != "auto":
            runtime += state_bytes * (settings.draft_max + 1)
            runtime += 4 * settings.parallel_slots * settings.draft_max * 4096
    return AuxiliaryRuntime(runtime, tuple(warnings))


def memory_costs(
    layout: Layout | None, size: int, context: int, settings: PlacementSettings
) -> MemoryEstimate:
    settings = effective_launch_settings(settings, context)
    context = settings.context
    warnings = list(settings.estimate_warnings)
    if not layout or layout.confidence != "metadata":
        warnings.append(
            "Model tensor metadata unavailable or incomplete; weights and dimensions are approximate"
        )
    if not layout:
        count = 32 if size < 6000 * MIB else 48 if size < 12000 * MIB else 80
        weight = size // (count + 2)
        layout = ModelLayout(
            architecture="unknown",
            metadata={},
            layers=(weight,) * count,
            input_bytes=weight,
            output_bytes=size - weight * (count + 1),
            tensors=(),
            confidence="approximate",
        )
    arch, meta = layout.architecture, layout.metadata

    def val(key: str, default: Any = 0) -> Any:
        return meta.get(f"{arch}.{key}", default)

    hybrid = arch in ("qwen35", "qwen35moe", "qwen3next", "mamba", "mamba2", "jamba")
    mla = (
        arch in ("deepseek2", "deepseek3")
        and bool(val("attention.key_length_mla"))
        and bool(val("attention.value_length_mla"))
    )
    known = hybrid or arch in (
        "llama",
        "qwen2",
        "qwen2moe",
        "qwen3",
        "qwen3moe",
        "mistral",
        "gemma",
        "gemma2",
        "gemma3",
        "deepseek2",
        "deepseek3",
    )
    if not known:
        warnings.append(f"Architecture {arch} is not fully supported")
    required = (
        ("embedding_length",)
        if arch in ("mamba", "mamba2")
        else ("embedding_length", "attention.head_count", "attention.head_count_kv")
    )
    if any(f"{arch}.{key}" not in meta for key in required):
        warnings.append("Architecture dimensions are incomplete; fallback dimensions are used")
    if hybrid and any(
        f"{arch}.ssm.{key}" not in meta for key in ("state_size", "inner_size", "conv_kernel")
    ):
        warnings.append("Recurrent dimensions are incomplete; fallback state dimensions are used")
    if arch == "jamba" and val("attention.recurrent_layers", None) is None:
        warnings.append("Jamba recurrent layer map unavailable; recurrent layout is approximate")
    if settings.split_mode in ("row", "tensor"):
        warnings.append(
            "Row/tensor scratch is priced, but placement uses an approximate layer allocation"
        )
    layers = list(layout.layers)
    embd = int(val("embedding_length", 4096))
    state = []
    kv = []
    dimensions = []
    recurrent_layers = val("attention.recurrent_layers", None)
    if recurrent_layers is None and hybrid:
        interval = max(1, int(val("full_attention_interval", 4)))
        recurrent_layers = [
            arch in ("mamba", "mamba2") or (j + 1) % interval != 0 for j in range(len(layers))
        ]
    swa = int(val("attention.sliding_window", 0))
    swa_layers = val("attention.sliding_window_pattern", None)
    if swa and swa_layers is None:
        if arch in ("gemma2", "gemma3"):
            swa_layers = [
                i % (2 if arch == "gemma2" else 6) != (1 if arch == "gemma2" else 5)
                for i in range(len(layers))
            ]
        else:
            swa_layers = [True] * len(layers)
    if swa and isinstance(swa_layers, int):
        swa_layers = [(i + 1) % max(1, swa_layers) != 0 for i in range(len(layers))]
    flash = settings.flash_attention == "on"
    v_widths = [
        int(_at(val("attention.head_count_kv", 32), i))
        * int(
            _at(
                val(
                    "attention.value_length",
                    embd // (int(_at(val("attention.head_count", 32), i)) or 1),
                ),
                i,
            )
        )
        for i in range(len(layers))
    ]
    for i, weight in enumerate(layers):
        heads = int(_at(val("attention.head_count", 32), i)) or 1
        kvheads = int(_at(val("attention.head_count_kv", heads), i))
        dk = int(_at(val("attention.key_length", embd // heads), i))
        dv = int(_at(val("attention.value_length", embd // heads), i))
        recurrent = hybrid and bool(_at(recurrent_layers, i))
        if recurrent:
            d = int(val("ssm.state_size", 128))
            rank = int(val("ssm.time_step_rank", 32))
            groups = int(
                val(
                    "ssm.group_count",
                    0 if arch in ("mamba", "jamba") else 1 if arch == "mamba2" else 16,
                )
            )
            conv = int(val("ssm.conv_kernel", 4))
            # Float32 delta-net matrix plus convolution history per sequence.
            # Live device state is separate from server checkpoints stored on the host.
            inner = int(val("ssm.inner_size", d * rank))
            recurrent_bytes = (
                4 * (d * inner + (conv - 1) * (inner + 2 * groups * d)) * settings.parallel_slots
            )
            state.append(recurrent_bytes)
            kv.append(0)
        else:
            streams = 1 if settings.kv_unified else settings.parallel_slots
            capacity = context // streams
            if swa and bool(_at(swa_layers, i)) and not settings.swa_full:
                capacity = _pad(
                    min(
                        capacity,
                        swa * (settings.parallel_slots if streams == 1 else 1)
                        + settings.micro_batch,
                    )
                )
            key = (
                int(val("attention.kv_lora_rank")) + int(val("rope.dimension_count", dk))
                if mla
                else kvheads * dk
            )
            value = 0 if mla else kvheads * dv if flash else max(v_widths)
            kv.append(
                capacity
                * streams
                * (
                    _cache_row_bytes(key, settings.cache_type_k)
                    + _cache_row_bytes(value, settings.cache_type_v)
                )
            )
            dimensions.append(
                AttentionDimensions(heads, key, value, capacity * streams)
            )
            state.append(0)
        layers[i] = weight + kv[-1] + state[-1]
    compute = _compute_buffers(layout, settings, dimensions)
    overhead = 256 * MIB + compute
    checkpoints = settings.checkpoints
    checkpoint_count = 32 if checkpoints == "auto" else max(0, int(checkpoints))
    # Maximum checkpoint storage for recurrent state, allocated by the server on CPU.
    checkpoint_bytes = sum(state) * checkpoint_count
    auxiliary = _auxiliary_runtime(settings, context, sum(state))
    warnings.extend(auxiliary.warnings)
    return MemoryEstimate(
        costs=tuple(layers + [layout.output_bytes]),
        input_bytes=layout.input_bytes,
        kv_bytes=sum(kv),
        state_bytes=sum(state),
        checkpoint_bytes=checkpoint_bytes,
        overhead=overhead,
        weights_bytes=sum(layout.layers) + layout.input_bytes + layout.output_bytes,
        auxiliary_weights_bytes=(settings.auxiliary_weights_bytes or settings.auxiliary_bytes),
        auxiliary_runtime_bytes=auxiliary.runtime_bytes,
        compute_bytes=compute,
        confidence="approximate; incomplete"
        if warnings
        else "metadata; execution overhead approximate",
        warnings=tuple(dict.fromkeys(warnings)),
        complete=not warnings,
        architecture=arch,
    )
