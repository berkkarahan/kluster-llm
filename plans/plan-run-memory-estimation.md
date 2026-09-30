# PLAN & RUN memory estimation plan

> **Created:** 2026-09-30
> **Status:** Planned
> **Repo / scope:** kluster-llm — PLAN & RUN inference-memory estimation

## Conventions captured at creation

- `python/kllm/placement.py:memory_costs` is the active Python estimator used when resolved
  launch settings are supplied. It accounts for GGUF tensor spans, KV cache, recurrent state,
  checkpoints, and a fixed execution-overhead approximation.
- `python/kllm/run_planning.py:resolve_launch_settings` resolves automatic context, parallel,
  batch, microbatch, checkpoint, cache, and compatible extra-argument settings before
  `execute_plan` invokes placement.
- `dashboard/src/lib/store.svelte.ts:planPayload` sends the PLAN & RUN settings to
  `/v1/plan?preview=true`; its debounced preview path suppresses stale responses.
- `dashboard/src/routes/run/+page.svelte` displays aggregate estimated, GPU, and CPU memory
  through `formatGib`, whose one-decimal GiB presentation can hide smaller estimate changes.
- The public planning response and persisted cluster-state shapes are compatibility boundaries.
  Existing response fields remain valid; new estimate details are additive.
- Python is managed with `uv`. Keep changes scoped under the existing Python and dashboard
  modules, retain facade patch surfaces, and do not expose the unauthenticated RPC port 50052.
- Research was performed against kluster-llm commit `a940b54`, Unsloth commit
  `0dba81767825a5b18c594b7b12e77f0e36a117a9`, and the repository-pinned llama.cpp v0.5.0
  behavior. Unsloth Studio is an AGPL reference only; its implementation must not be copied.

## Summary

Make the Python planner used by `/v1/plan` the canonical inference-memory estimator. Every
relevant PLAN & RUN edit triggers a fresh estimate, including logical batch-size edits, while
the resulting byte count may legitimately remain unchanged when the effective physical
allocation is unchanged.

Implement the formulas independently against pinned llama.cpp v0.5.0 semantics. Use Unsloth
only as a behavioral reference because its Studio implementation is AGPL-licensed. Unsloth's
estimator accounts for resolved context, cache types, slots, physical microbatch, flash
attention, SWA, checkpoints, draft models, projector runtime, and split mode; its tests also
distinguish total context from per-slot compute growth:

- [Estimator inputs](https://github.com/unslothai/unsloth/blob/0dba81767825a5b18c594b7b12e77f0e36a117a9/studio/backend/models/inference.py#L869-L1077)
- [Runtime calculation](https://github.com/unslothai/unsloth/blob/0dba81767825a5b18c594b7b12e77f0e36a117a9/studio/backend/routes/inference.py#L11822-L12139)
- [llama.cpp formulas](https://github.com/unslothai/unsloth/blob/0dba81767825a5b18c594b7b12e77f0e36a117a9/studio/backend/core/inference/llama_cpp.py#L16573-L16730)

## Implementation changes

### Resolve effective launch settings once

- Cap effective batch by context and effective microbatch by effective batch.
- Treat context as the total KV-cache context, not a value automatically multiplied by
  parallel slots.
- Treat K and V cache types independently, including block-quantized storage widths.
- Resolve flash attention, unified KV, SWA mode, checkpoint count, speculative settings, and
  allowed equivalent extra arguments through one precedence path shared by estimation and
  launch generation.

### Add an architecture-aware memory breakdown

- Use exact GGUF tensor spans for resident model weights.
- Cover standard GQA/MLA and existing recurrent/hybrid KV layouts, cache padding, and available
  sliding-window metadata.
- Calculate compute, output, attention-mask, cache-dequantization, and split-mode scratch from
  physical microbatch, context, flash attention, slots, and device count.
- Scale recurrent state and checkpoint host memory by architecture and slots.
- Include draft-model weights, KV, compute and rollback buffers, plus vision-projector weights
  and a runtime reserve.
- Include the file sizes of supplied local LoRA and control-vector assets. Mark an estimate
  incomplete when a memory-affecting argument or asset cannot be priced instead of silently
  returning a confident total.
- Reject incompatible settings, including explicitly disabled flash attention with a quantized
  V cache, before launch.

### Extend the planning contract additively

Keep all existing response fields and add:

- `weights_estimate_bytes`
- `auxiliary_weights_bytes`
- `compute_estimate_bytes`
- `auxiliary_runtime_bytes`
- `estimate_complete: bool`
- `estimate_warnings: string[]`

Retain the existing KV, recurrent-state, checkpoint, VRAM, CPU, and total fields. Ensure
`estimated_bytes` equals both the component total and the CPU/VRAM allocation total without
double counting.

### Expose estimate behavior in PLAN & RUN

- Display the component breakdown and resolved context, batch, microbatch, and slot count.
- Retain the aggregate one-decimal GiB display, but render small components in MiB so changes
  below 0.1 GiB remain visible.
- When logical batch changes without changing effective microbatch, show that the preview was
  recalculated and explain why its total remained constant.
- Document which controls affect total memory versus placement only:
  - Total/runtime memory: context, cache types, physical microbatch, slots through compute or
    recurrent buffers, flash/SWA, checkpoints, draft/vision assets, adapters, split mode, and
    model architecture.
  - Placement only: node shares, GPU selection, device mode, headroom, and placement weights.
  - No reserved-memory effect: sampling, template/thinking controls, and threads.
  - `cache_ram` is a server cache limit; mmap/mlock primarily change host residency rather than
    model VRAM.

## Test plan

- Add estimator matrix tests proving:
  - Larger context and wider cache types increase raw bytes.
  - Standard-transformer KV does not multiply by aligned slot count, while compute/output
    buffers do.
  - Hybrid recurrent state and checkpoint memory scale with slots.
  - Logical batch always causes a new preview request but changes memory only when it changes
    effective microbatch.
  - Flash/SWA, checkpoint count, draft models, projectors, and supported extra arguments affect
    the correct components.
  - CPU, GPU, unified-memory, and RPC allocations conserve the component total.
- Add API tests for automatic context resolution, additive response fields, incomplete-estimate
  warnings, stale-preview suppression, and invalid flash/cache combinations.
- Add dashboard tests for recalculation from context, requests, batch, microbatch, context
  quantization, and the other memory-affecting controls. Verify sub-0.1 GiB changes remain
  visible.
- Use synthetic GGUF fixtures in CI for standard, MLA, and Qwen hybrid architectures. Add an
  optional regression check against the locally available Qwen3.5 122B GGUF without making
  that file a CI dependency.

## Assumptions and defaults

- "Recalculate the estimate" means issue a fresh calculation after every relevant setting
  change; it does not force reported memory to change when llama.cpp would allocate the same
  buffers.
- The Python PLAN & RUN path remains authoritative. Any legacy bare native estimator is marked
  approximate and covered by parity tests for its supported inputs.
- Existing request and response fields remain backward compatible; breakdown and confidence
  fields are additive.
- Unsloth source is research input only. No AGPL implementation is copied into this MIT
  repository.

This plan is a point-in-time implementation handoff. After it is realized, superseded, or
abandoned, freeze the body and update only its status or replacement pointer.
