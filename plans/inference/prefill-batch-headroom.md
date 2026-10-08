# Prefill batch headroom

> **Created:** 2026-10-08
> **Status:** Planned
> **Repo / scope:** kluster-llm — llama-server batch and micro-batch defaults

## Conventions captured at creation

- Python launch settings live in `src/kllm/run_settings_resolution.py` `resolve_launch_settings`. `auto` batch size resolves to 512 and `auto` micro-batch to 128. Effective values are then capped with `min(batch, max(2048, context))` and `min(micro_batch, batch, 512)`.
- `src/kllm/run_launch.py` `build_master_launch` passes those integers through. `crates/kllm-native/src/process.rs` `push_auto_i32` only applies its fallbacks (batch 512, ubatch 128) when the value is missing or the string `auto`.
- `src/kllm/core/memory_estimation.py` `effective_launch_settings` and `_compute_buffers` price compute scratch from the physical micro-batch. A larger ubatch reserves more memory and can turn a fit into overflow.
- `src/kllm/core/placement_api.py` `plan_from_settings` binary-searches context when `context_auto` is set. It does not search batch size.
- Spec `specs/013-moe-expert-offload` records the product decision that batch and micro-batch ceilings are 2048 and 512 while defaults stay 512 and 128. `docs/cli.md` repeats that.
- On this machine, `vendor/bin/llama-server.exe --help` (2026-10-08) says `--batch-size` defaults to 2048 and `--ubatch-size` defaults to 512. Kluster currently launches below both.
- Generation threads stay `min(8, available CPUs)` via `DEFAULT_THREADS` in `src/kllm/run_settings.py`. `cpu_batch_threads` already raises prompt-processing threads and `rpc-server --threads` to physical cores without going below that generation cap. `vendor/bin/rpc-server.exe --help` defaults `--threads` to 8. `llama-server --threads` defaults to `-1` (engine auto). This plan does not reopen that generation cap.
- Flash attention `auto` is promoted to `on` only for quantized V cache (`resolve_launch_settings`). The memory estimator treats any other value as attention scores without Flash Attention (`_compute_buffers`).

## Intended change

When the operator leaves batch size and micro-batch on `auto`, start from today's 512 / 128 so models that barely fit still plan. After a placement fits, try larger powers of two up to the engine defaults (batch 2048, micro-batch 512) and keep the largest pair that still fits in the same device budgets. Publish the chosen values in the effective configuration the way context adjustments already are.

Do not change an explicit batch or micro-batch. Do not raise the generation thread cap in this work.

## Phases

1. Add a pure helper next to `plan_from_settings` that, given a fitting plan, evaluates candidate `(batch, micro_batch)` pairs in increasing order and returns the largest pair whose `fit_status` is not `overflow`. Keep micro-batch 128 and batch 512 as the floor so a failed upward step falls back to today's launch.
2. Call it only for `auto` fields. Thread the chosen integers into `resolved_launch_settings` so `build_master_launch` and `effective_configuration` show the same numbers, with adjustment text that says the raise used leftover budget.
3. Extend `tests/test_memory_estimation.py` and `tests/test_plan_run_params.py` with a fixture that fits at ubatch 512 and one that overflows above 128. Assert the explicit `micro_batch=128` request is unchanged.
4. Re-read `docs/cli.md` and `docs/memory-constraints.md` so `auto` is described as "128, raised while the plan still fits" rather than a fixed 128.

## Open questions

- Should the search stop at the first overflow, or skip a micro-batch that overflows and still try a larger logical batch with the previous micro-batch?
- For `flash_attention=auto` with f16 V cache, should the estimator assume Flash Attention is on so the upward search is not blocked by scratch that the server will not allocate?
