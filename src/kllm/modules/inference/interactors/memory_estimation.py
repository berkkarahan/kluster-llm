"""Model memory estimation independent of cluster allocation policy."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from kllm.modules.inference.interactors.placement_models import MemoryEstimate, ModelLayout, PlacementSettings
from kllm.shared.exceptions import ValidationError

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
    sliding: bool = False


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
    if not settings.kv_unified and not settings.prima_kv_sequences:
        context = _pad(context // slots) * slots
        if not context:
            raise ValidationError("context per slot must be positive")
    batch_size = min(
        context,
        settings.requested_batch_size
        if settings.requested_batch_size is not None
        else settings.batch_size,
    )
    if settings.prima_kv_sequences:
        # Prima pads the causal batch to GGML_KQ_MASK_PAD before capping ubatch.
        batch_size = max(32, batch_size)
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
    slots, ubatch = settings.prima_kv_sequences or settings.parallel_slots, settings.micro_batch
    tokens = max(ubatch, slots)
    ffn = meta.get(f"{arch}.feed_forward_length", 4 * embd)
    ffn = max(ffn) if isinstance(ffn, (list, tuple)) else int(ffn)
    vocab = int(meta.get(f"{arch}.vocab_size", 32000))
    for tensor in layout.tensors:
        if tensor.name in ("token_embd.weight", "output.weight"):
            vocab = int(tensor.dimensions[-1])
            break
    activations = 4 * tokens * (8 * embd + 3 * ffn)
    if settings.prima_kv_sequences and arch == "openelm":
        query_heads = meta.get(f"{arch}.attention.head_count", 32)
        kv_heads = meta.get(f"{arch}.attention.head_count_kv", query_heads)
        for counts in (query_heads, kv_heads):
            if isinstance(counts, (list, tuple)) and len(counts) != len(layout.layers):
                raise ValidationError("OpenELM head counts must match every layer")
        first_heads = int(_at(query_heads, 0))
        if first_heads <= 0:
            raise ValidationError("OpenELM head counts must be positive")
        head_width = int(meta.get(f"{arch}.attention.key_length", embd // first_heads))
        projection_rows = 0
        for index in range(len(layout.layers)):
            heads, kvheads = int(_at(query_heads, index)), int(_at(kv_heads, index))
            if heads <= 0 or kvheads <= 0 or heads % kvheads or head_width <= 0:
                raise ValidationError("OpenELM projection/head dimensions are invalid")
            projection_rows = max(projection_rows, (heads + 2 * kvheads) * head_width)
        if projection_rows > 2**31 - 1:
            raise ValidationError("OpenELM projection width exceeds its native bounds")
        # Fused projection output, contiguous slices and head norms can be
        # wider than the embedding. Reserve their peak independently of FFN.
        activations += 8 * tokens * max(0, projection_rows - embd)
    if settings.prima_kv_sequences and arch == "phi3":
        # The fused up projection and its contiguous gate/value halves coexist
        # with the SILU result and product before the down projection.
        activations += 12 * tokens * ffn
    direct_expert_widths = arch in ("granite", "granitemoe", "granite_swa", "olmoe") and int(meta.get(f"{arch}.expert_count", 0)) > 0
    if settings.prima_kv_sequences and (arch == "qwen2moe" or direct_expert_widths):
        experts = int(meta.get(f"{arch}.expert_count", 0))
        selected_values = meta.get(f"{arch}.expert_used_count", 0)
        if isinstance(selected_values, (list, tuple)):
            if arch != "granite_swa" or len(selected_values) != len(layout.layers):
                raise ValidationError(f"{arch} selected expert counts must match every layer")
            if any(type(value) is not int or not 0 < value <= experts for value in selected_values):
                raise ValidationError(f"{arch} selected expert counts exceed their native bounds")
            selected = max(selected_values, default=0)
        else:
            selected = int(selected_values)
        if not 0 < selected <= experts <= 2**31 - 1:
            raise ValidationError(f"{arch} expert counts exceed their native bounds")
        widths = meta.get(f"{arch}.feed_forward_length", ffn) if direct_expert_widths else meta.get(f"{arch}.expert_feed_forward_length", 0)
        if isinstance(widths, (list, tuple)) and len(widths) != len(layout.layers):
            raise ValidationError(f"{arch} expert widths must match every layer")
        widths = list(widths) if isinstance(widths, (list, tuple)) else [int(widths)]
        if any(width < 0 or width > 2**31 - 1 or (direct_expert_widths and width == 0) for width in widths):
            raise ValidationError(f"{arch} expert widths exceed their native bounds")
        expert_width = max(widths, default=0) if direct_expert_widths else max((width or ffn // selected for width in widths), default=0)
        shared_width = int(meta.get(f"{arch}.expert_shared_feed_forward_length", 0))
        if not direct_expert_widths:
            shared_width = shared_width or ffn
        if not 0 <= shared_width <= 2**31 - 1 or expert_width <= 0 or (not direct_expert_widths and not shared_width):
            raise ValidationError(f"{arch} shared/expert widths exceed their native bounds")
        # Routed and shared branches coexist until the final addition. Reserve
        # every selected expert's projections, router/top-k state, and the
        # always-active shared projections independently of the dense FFN proxy.
        activations += tokens * (
            4 * (3 * selected * expert_width + selected * embd + 3 * shared_width)
            + 12 * experts + 12 * selected + 8 * embd
        )
        if arch == "granite_swa":
            if expert_width > (2**31 - 1) // 2 or shared_width > (2**31 - 1) // 2:
                raise ValidationError("Granite SWA fused widths exceed their native bounds")
            # Fused projections coexist with contiguous gate/up halves and
            # their activation/product before the down projection.
            activations += 12 * tokens * (selected * expert_width + shared_width)
    inner = int(meta.get(f"{arch}.ssm.inner_size", 0))
    state_dim = int(meta.get(f"{arch}.ssm.state_size", 0))
    if inner and state_dim:
        # Recurrent projections and chunked scan/delta-net matrix scratch.
        activations += 4 * tokens * inner
        activations += 4 * max(slots, math.ceil(ubatch / 16)) * state_dim * inner
    # Host logits/embeddings and sequence bookkeeping grow with active outputs.
    output_rows = (
        max(slots, settings.parallel_slots * (settings.draft_max + 1))
        if settings.prima_kv_sequences and settings.draft_size_bytes
        else slots
    )
    if settings.prima_kv_sequences:
        output_rows = max(output_rows, ubatch)
    outputs = 4 * output_rows * (vocab + embd)
    if settings.prima_kv_sequences and arch == "bert":
        classifier = next((tensor for tensor in layout.tensors if tensor.name == "cls.output.weight"), None)
        if classifier is not None:
            if len(classifier.dimensions) < 2 or classifier.dimensions[0] != embd or not 0 < classifier.dimensions[1] <= 2**31 - 1:
                raise ValidationError("BERT classifier output dimensions are invalid")
            # Pooled class vectors are independent of token embedding width.
            outputs += 4 * output_rows * classifier.dimensions[1]
    if settings.prima_kv_sequences:
        projected = embd
        projection_widths = []
        for name in ("dense_2.weight", "dense_3.weight"):
            tensor = next((tensor for tensor in layout.tensors if tensor.name == name), None)
            if tensor is None:
                continue
            if (not 2 <= len(tensor.dimensions) <= 4 or any(dimension != 1 for dimension in tensor.dimensions[2:])
                    or tensor.dimensions[0] != projected
                    or not 0 < tensor.dimensions[1] <= 2**31 - 1):
                raise ValidationError("Retrieval projection chain has invalid dimensions")
            projected = tensor.dimensions[1]
            projection_widths.append(projected)
        declared = int(meta.get(f"{arch}.embedding_length_out", 0)) or projected
        if not 0 < declared <= 2**31 - 1 or (projection_widths and declared != projected):
            raise ValidationError("Output embedding width differs from retrieval projections")
        # Reserve projection intermediates and pooled input/output copies even
        # when the current request uses generation instead of embeddings.
        outputs += 4 * output_rows * max(0, declared - embd)
        activations += 4 * tokens * sum(projection_widths)
        activations += 4 * slots * tokens + 8 * slots * declared
    # BERT currently materializes F32 bidirectional scores even when FA is requested.
    supports_fa = arch.lower() not in ("bert",)
    flash = (
        settings.flash_attention == "on"
        or (settings.flash_attention == "auto" and supports_fa)
    ) and not (settings.prima_kv_sequences and arch == "bert")
    attention = 0
    mask_pools: dict[bool, int] = {}
    separate_masks = bool(settings.prima_kv_sequences and arch in ("gpt-oss", "gemma2", "gemma3", "granite_swa", "olmo2"))
    for dimension in dimensions:
        mask = (6 if separate_masks and flash else 4) * dimension.capacity * _pad(tokens, 32)
        scores = 0 if flash else 4 * dimension.heads * dimension.capacity * tokens
        dequant = (
            0
            if flash
            else 4
            * dimension.capacity
            * (
                (dimension.key_width if settings.cache_type_k not in ("f32", "f16", "bf16") else 0)
                + (
                    dimension.value_width
                    if settings.cache_type_v not in ("f32", "f16", "bf16")
                    else 0
                )
            )
        )
        if separate_masks:
            # Sliding architectures retain dense and SWA input masks at the same time,
            # including full-storage mode where both masks have equal width.
            mask_pools[dimension.sliding] = max(mask_pools.get(dimension.sliding, 0), mask)
            attention = max(attention, scores + dequant)
        else:
            attention = max(attention, mask + scores + dequant)
    attention += sum(mask_pools.values())
    if settings.prima_kv_sequences and arch == "bert":
        # The no-cache graph attends over the complete microbatch. KV storage
        # is still allocated by this native revision and remains priced below.
        heads = meta.get("bert.attention.head_count", 32)
        heads = max(heads) if isinstance(heads, (list, tuple)) else int(heads)
        attention = max(attention, 4 * tokens * (_pad(tokens, 32) + heads * tokens))
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
            prima_kv_sequences=1 if settings.prima_kv_sequences else 0,
        )
        if settings.prima_kv_sequences or settings.draft_contexts > 1:
            # Prima has an independent local draft context for each HTTP slot.
            # The baseline keeps its existing shared-context pricing.
            draft_settings = replace(draft_settings, parallel_slots=1, kv_unified=False)
        memory = memory_costs(draft, settings.draft_size_bytes, context, draft_settings)
        runtime += (memory.kv_bytes + memory.state_bytes + memory.overhead) * settings.draft_contexts
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
        "granite",
        "granitemoe",
        "granite_swa",
        "phi3",
        "olmo",
        "olmoe",
        "olmo2",
        "openelm",
        "bert",
        "gpt-oss",
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
    if settings.prima_kv_sequences and arch in ("phi3", "granite", "granitemoe", "granite_swa"):
        factor_names = {"rope_factors_long.weight", "rope_factors_short.weight"}
        if arch in ("granite", "granitemoe", "granite_swa"):
            factor_names.add("rope_freqs.weight")
        factors = sum(tensor.size_bytes for tensor in layout.tensors if tensor.name in factor_names)
        # Factor tensors can be duplicated across local layer buffer types.
        # Retain the existing input allowance and conservatively price a copy
        # with every layer, including layers assigned to remote ranks.
        layers = [weight + factors for weight in layers]
    state = []
    kv = []
    dimensions = []
    recurrent_layers = val("attention.recurrent_layers", None)
    if recurrent_layers is None and hybrid:
        interval = max(1, int(val("full_attention_interval", 4)))
        recurrent_layers = [
            arch in ("mamba", "mamba2") or (j + 1) % interval != 0 for j in range(len(layers))
        ]
    swa = int(val("attention.sliding_window", 4096 if settings.prima_kv_sequences and arch == "gemma2" else 0))
    if settings.prima_kv_sequences and arch == "phi3":
        swa = 0  # Match the pinned baseline and native Phi-3 dense attention.
    swa_layers = val("attention.sliding_window_pattern", None)
    if settings.prima_kv_sequences and arch == "granite_swa":
        if not 0 < swa <= 2**31 - 1 or not isinstance(swa_layers, (list, tuple)):
            raise ValidationError("Granite SWA requires a positive window and an explicit layer pattern")
        rope_layers = val("attention.rope_pattern", None)
        if rope_layers is not None and (not isinstance(rope_layers, (list, tuple))
                or len(rope_layers) != len(layers)
                or any(type(value) not in (int, bool) or value not in (0, 1) for value in rope_layers)):
            raise ValidationError("Granite SWA rotary pattern must match every layer")
    if arch in ("gpt-oss", "gemma2", "gemma3", "granite_swa", "olmo2"):
        if not (0 < swa <= 2**31 - 1 if arch == "gpt-oss" else 0 <= swa <= 2**31 - 1):
            raise ValidationError(f"{arch} sliding window exceeds its native bounds")
        if swa_layers is not None:
            if isinstance(swa_layers, (list, tuple)):
                if len(swa_layers) != len(layers) or any(
                        type(value) not in (int, bool) or value not in (0, 1)
                        for value in swa_layers):
                    raise ValidationError(f"{arch} sliding-window pattern must match every layer")
            elif type(swa_layers) is not int or not 0 <= swa_layers <= 2**32 - 1:
                raise ValidationError(f"{arch} sliding-window pattern must be an unsigned integer")
    if swa and swa_layers is None:
        if arch in ("gemma2", "gemma3"):
            swa_layers = [
                i % (2 if arch == "gemma2" else 6) != (1 if arch == "gemma2" else 5)
                for i in range(len(layers))
            ]
        elif arch == "olmo2":
            swa_layers = [i % 4 < 3 for i in range(len(layers))]
        elif arch == "gpt-oss":
            # llama.cpp openai-moe.cpp applies the alternating pattern for gpt-oss
            # (set_swa_pattern(2)): even layers use the sliding window, odd layers
            # keep full attention.
            swa_layers = [i % 2 == 0 for i in range(len(layers))]
        else:
            swa_layers = [True] * len(layers)
    if swa and isinstance(swa_layers, int):
        if arch in ("gpt-oss", "gemma2", "gemma3", "granite_swa", "olmo2") and swa_layers == 0:
            swa_layers = [True] * len(layers)
        else:
            swa_layers = [(i + 1) % max(1, swa_layers) != 0 for i in range(len(layers))]
    sequences = settings.prima_kv_sequences or settings.parallel_slots
    compact_capacity = 0
    flash = settings.flash_attention == "on" or (
        settings.flash_attention == "auto" and arch.lower() not in ("bert",)
    )
    default_head_width = embd // (int(_at(val("attention.head_count", 32), 0)) or 1)
    v_widths = [
        int(_at(val("attention.head_count_kv", val("attention.head_count", 32) if arch == "openelm" else 32), i))
        * int(
            _at(
                val(
                    "attention.value_length",
                    default_head_width if arch == "openelm" else embd // (int(_at(val("attention.head_count", 32), i)) or 1),
                ),
                i,
            )
        )
        for i in range(len(layers))
    ]
    for i, weight in enumerate(layers):
        heads = int(_at(val("attention.head_count", 32), i)) or 1
        kvheads = int(_at(val("attention.head_count_kv", heads), i))
        dk = int(_at(val("attention.key_length", default_head_width if arch == "openelm" else embd // heads), i))
        dv = int(_at(val("attention.value_length", default_head_width if arch == "openelm" else embd // heads), i))
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
                4 * (d * inner + (conv - 1) * (inner + 2 * groups * d)) * sequences
            )
            state.append(recurrent_bytes)
            kv.append(0)
        else:
            streams = 1 if settings.kv_unified or settings.prima_kv_sequences else settings.parallel_slots
            capacity = context // streams
            if (swa and bool(_at(swa_layers, i)) and not settings.swa_full
                    and (not settings.prima_kv_sequences or arch in ("gpt-oss", "gemma2", "gemma3", "granite_swa", "olmo2"))):
                capacity = _pad(
                    min(
                        capacity,
                        swa * (sequences if streams == 1 else 1)
                        + settings.micro_batch,
                    )
                )
                if settings.prima_kv_sequences:
                    compact_capacity = max(compact_capacity, capacity)
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
            dimensions.append(AttentionDimensions(
                heads, key, value, capacity * streams,
                bool(swa and _at(swa_layers, i)),
            ))
            state.append(0)
        layers[i] = weight + kv[-1] + state[-1]
    compute = _compute_buffers(layout, settings, dimensions)
    if settings.prima_adapter_bytes < 0 or settings.prima_adapter_scratch_per_token < 0:
        raise ValidationError("Prima adapter reservations must be nonnegative")
    compute += max(settings.micro_batch, settings.parallel_slots) * settings.prima_adapter_scratch_per_token
    overhead = 256 * MIB + compute
    if settings.prima_kv_sequences:
        # Full cell/sequence metadata and completed token/position/phase history
        # persist even when layer tensors use a compact sliding-window pool.
        overhead += 128 * context * sequences + 4096 * sequences
        if compact_capacity:
            # Live forward/reverse maps plus read-only reservation/restore copies,
            # seen bitmaps and pre-admission global-ID payloads.
            overhead += 16 * (context + compact_capacity) + 8 * settings.micro_batch
    checkpoints = settings.checkpoints
    checkpoint_count = 32 if checkpoints == "auto" else max(0, int(checkpoints))
    # Maximum checkpoint storage for recurrent state, allocated by the server on CPU.
    checkpoint_bytes = sum(state) * checkpoint_count
    if settings.prima_checkpoints and checkpoint_count:
        # Prima retains full sequence KV/state rather than the baseline's partial
        # recurrent-only checkpoints. Include tokens, cell metadata, tensor headers
        # and container overhead, and reserve this ceiling on every native rank.
        checkpoint_bytes = checkpoint_count * (
            sum(kv) + sum(state) + 32 * context
            + 4096 * sequences + 512 * len(layers) + 8 * compact_capacity
        )
    auxiliary = _auxiliary_runtime(settings, context, sum(state))
    warnings.extend(auxiliary.warnings)
    expert_costs = tuple(layout.expert_layers) + (0,)
    if len(expert_costs) != len(layers) + 1:
        expert_costs = (0,) * (len(layers) + 1)
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
        expert_costs=expert_costs,
        expert_bytes=sum(expert_costs),
    )
