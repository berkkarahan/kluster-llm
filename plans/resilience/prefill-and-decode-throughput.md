# Prefill and decode throughput

> **Created:** 2026-10-08
> **Status:** Planned
> **Repo / scope:** kluster-llm — llama.cpp launch settings that control prompt processing and token generation

## Conventions captured at creation

Python launch settings live in `src/kllm/` and the native process adapter lives in `crates/kllm-native/`. `resolve_launch_settings` in `src/kllm/run_settings_resolution.py` is the single normalization step before planning and launch. Auto values today are `parallel_slots=1` (which also sets `kv_unified`), `batch_size=512`, and `micro_batch=128`. Effective micro-batch is then capped with `min(requested, batch_size, 512)`. Quantized V cache (`q8_0` is the CLI and launch default) forces `flash_attention` from `auto` to `on` in that same function. Speculative decoding stays `auto` unless a draft model is enabled, in which case it becomes `draft-simple`.

`build_master_launch` in `src/kllm/run_launch.py` passes the resolved integers through. `push_auto_i32` in `crates/kllm-native/src/process.rs` still falls back to `--ubatch-size 128`, `--parallel 1`, and `--batch-size 512` when those keys are missing. `rpc-server` is already started with `-c` so tensors are not recopied across RPC after a reconnect.

Memory estimates in `src/kllm/core/memory_estimation.py` (`_compute_buffers`, `effective_launch_settings`) treat micro-batch as activation scratch. The estimate is documented there as conservative, not a backend allocator measurement. Spec `specs/013-moe-expert-offload` recorded the decision to keep the 512/128 defaults and to cap micro-batch at 512. Tests in `tests/test_run_planning_order.py` assert an auto launch uses `micro_batch == 128`.

The agent already keeps its localhost client to llama-server warm for 120 seconds (`INFERENCE_LIMITS` in `src/kllm/http_clients.py`, applied by `AgentLifespan` in `src/kllm/agent/application_lifecycle.py`). That removes a fresh handshake between chat turns. It does not change tokens per second once generation has started.

Public patch surfaces stay on façade modules. Do not move `kllm.agent:app` or the native exports. A behavior change here needs a regression that fails before the change, plus an explicit statement that unit fixtures are not a hardware throughput measurement.

## Intended change

Raise prompt-processing throughput when the current plan has memory headroom, without changing the stored auto default and without claiming a tokens-per-second win from a unit test.

Auto `micro_batch=128` makes llama.cpp prefill a long prompt in small chunks. The engine's own usual physical batch is 512, and this repo already caps the effective value at 512. Jumping the global default to 512 would multiply the token-linear activation term in `_compute_buffers` and can turn a plan that fits today into overflow or a smaller context. Spec 013 froze that default for that reason.

The follow-up is a planner policy, not a new default:

1. Keep `resolve_launch_settings` auto values at 512/128 so explicit user settings and the spec 013 contract stay put.
2. After a plan fits, if estimated free memory on every selected device still covers the scratch delta from the current micro-batch up to `min(batch_size, 512)`, rewrite only the launch record's `micro_batch` (and the matching estimate) upward. Leave the user's requested value unchanged when they set it.
3. Pass that integer through `build_master_launch`. Update the Rust fallback only so a missing key cannot disagree with the Python policy; the Python path already supplies an int.
4. Do not turn on speculative decoding unless `draft_enabled()` is already true. Do not raise `parallel_slots` in the same change. One slot with `kv_unified` is the current auto contract and a second slot changes KV layout.
5. Measure before and after on one identified GPU, model, context, and prompt length. Record prefill tokens/s and decode tokens/s separately. A unit test may assert the headroom rule. It must not be described as a hardware result.

## Phased steps

1. Add a pure function next to `effective_launch_settings` that, given a fit plan and a candidate micro-batch, returns the largest allowed step (128, 256, 512) that still fits. Cover: no headroom stays at 128; explicit micro-batch is untouched; the 512 cap and `micro_batch <= batch_size` still hold.
2. Call it from the planning path that already produces `resolved_launch_settings`, so Plan and Run cannot disagree.
3. Extend `tests/test_run_planning_order.py` (or the memory-estimation tests) for the headroom cases. Keep the existing assertion that an untouched auto resolution is 128.
4. Run one local prefill comparison after the rule exists. If the estimator is too conservative to ever step up, record that and stop. Do not weaken the estimate to force a faster number.

## Open questions

- How much of `_compute_buffers` is unused once flash attention is on, and would a tighter estimate free a step-up that the conservative formula currently blocks?
- Should the dashboard show the launched micro-batch next to the requested one so an automatic step-up is visible?
- Is a concurrent-slot policy worth a later plan once single-request prefill is measured, given `kv_unified` is tied to `parallel_slots == "auto"`?
