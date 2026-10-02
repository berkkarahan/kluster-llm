# Quickstart: MoE Expert Offload Validation Guide

Deterministic local checks that prove the feature without a live cluster; hardware validation is a separate, explicitly-deferred step.

## Prerequisites

- Repository bootstrap per `docs/agent-install.md` (`python scripts/install.py`).
- `uv` available; native extension built (if Rust changed, `uv run python scripts/build.py`).

## Scenario A — MoE expert cost model (deterministic)

1. Craft a synthetic MoE GGUF fixture (as in `tests/test_memory_estimation.py`) with `general.architecture = qwen35moe` and expert tensors present.
2. Assert the layout reader splits per-layer bytes into shared and expert portions, and `expert_cost > 0`.
3. Run: `uv run pytest -q tests/test_memory_estimation.py`.

Expected: expert-aware cost splits; `weights_bytes` still equals total tensor bytes.

## Scenario B — Expert placement across nodes (deterministic)

1. Build a multi-node cluster fixture where the master GPU cannot hold the model but remote nodes (GPU, then RAM) can hold the expert portion.
2. Run `plan_devices` with `moe_offload="auto"`.
3. Assert `expert_offloaded is True`, `expert_strategy in ("experts","mix")`, and expert bytes land on remote nodes (GPU first, then RAM).
4. Run: `uv run pytest -q tests/test_cluster_optimizations.py`.

Expected: plan succeeds where it previously overflowed; fit status and per-node allocations include expert bytes.

## Scenario C — Offload option resolution and launch args (deterministic)

1. Submit a `RunBody` with `moe_offload="auto"` and one with `moe_offload=3`.
2. Assert `resolve_launch_settings` accepts both, and `build_master_launch` emits `-ot` patterns (and `--n-cpu-moe` when host spill applies) — and that `--n-cpu-moe`/`-ot` remain rejected as user `extra_args`.
3. Run: `uv run pytest -q tests/test_plan_run_params.py`.

Expected: planner-owned flags present; user override still blocked.

## Scenario D — Batch / micro-batch ceilings (deterministic)

1. Resolve `batch_size=2048`, `micro_batch=512` at a small context → both accepted.
2. Resolve `batch_size=1024`, `micro_batch=2048` → micro-batch capped to the resolved batch (512 or batch, whichever applies).
3. Omit both → defaults 512 / 128.
4. Run: `uv run pytest -q tests/test_memory_estimation.py tests/test_plan_run_params.py`.

Expected: ceilings honored, defaults unchanged, no silent clamping.

## Scenario E — Dashboard check

1. Run `npm --prefix dashboard run check`.
2. Confirm the Run panel exposes the offload control and the batch/micro-batch `max` attributes match 2048 / 512.

Expected: no type/check errors; UI limits match backend.

## Hardware acceptance (deferred, must not be reported as passed without evidence)

- On a real multi-node cluster with a pinned `v0.5.0` build, load an MoE model that overflows a single node's GPU, run with `moe_offload="auto"`, and verify: remote nodes receive expert tensors (`-ot RPC[...]` accepted on the pinned build), inference completes, and plan/launch estimates match. Record model, backend, and hardware, per Constitution III.
