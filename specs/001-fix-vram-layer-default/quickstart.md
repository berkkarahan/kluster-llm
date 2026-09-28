# Validation guide: memory display and automatic Layer

Run from the repository root after implementing [plan.md](plan.md). Read [contracts/system-settings.md](contracts/system-settings.md) for byte-field semantics and [data-model.md](data-model.md) for shared-memory handling.

## Prerequisites

- Bootstrap using `python scripts/install.py` if the existing uv Python environment, native runtime, or dashboard dependencies are missing; follow `docs/agent-install.md` for recovery. For UI viewing, start a master agent and open its local dashboard according to `docs/agent-install.md`.
- Automated tests use controlled inventory/plan fixtures and need no physical GPU; the dashboard tests use Node 22's built-in runner. Live validation needs a probed backend and a known 8/16/24 GiB GPU; an RPC smoke test additionally needs a private LAN/WireGuard worker and an available GGUF model. Never expose RPC port 50052 publicly.

## Automated checks

```sh
uv run pytest -q tests/test_gpus.py tests/test_config.py tests/test_plan_run_params.py tests/test_cluster_optimizations.py
node --experimental-strip-types --test dashboard/tests/format-memory.test.mjs
node --test dashboard/tests/store.test.mjs
npm --prefix dashboard run check
npm --prefix dashboard run build
```

The Node checks cover the display path (16 GiB → 16.0 GiB; 8 + 16 → 24.0 GiB; 15.2 GiB plan budget; invalid measurements → unavailable) and effective Layer through dashboard estimate, plan, run, save, and reload with legacy config. Record command results and any pre-existing failures in the change description.

## Browser and API scenarios

1. Open System Settings and Plan & Run with known local GPU inventory: a measured 16 GiB device reads **16.0 GiB** capacity, a measured 8 GiB device reads **8.0 GiB**, and with both selected the discrete total reads **24.0 GiB**. Deselect one and verify the total changes; an unknown capacity or reduced reported capacity is shown truthfully, without a guessed full total.
2. With the 16 GiB device fully available and a 95% budget, request a plan with a fitting model: its applicable device budget reads **15.2 GiB**, clearly separate from capacity and consumption. Limit available memory and re-plan: the budget falls accordingly. Verify the displayed plan budget reflects the devices actually used, rather than an unrelated full inventory sum.
3. Plan a CPU-spill workload and a CPU-only workload: GPU-only estimated consumption excludes the CPU allocation, the combined number says **total memory**, and CPU-only figures say RAM/memory. On a unified-memory backend, shared capacity is identified and counted only once.
4. Verify neither settings page has a split-mode selector or Row/Tensor selection instructions. With fresh config and then a saved `split_mode = "row"` or `"tensor"` (using the existing local-only `kllm config` interface), capture dashboard requests to `/v1/plan` (live estimate and Plan) and `/v1/run`: each sends `split_mode: "layer"` and returns Layer. Save hardware settings, Reset model settings, then reload; repeat. Confirm dashboard hardware saves persist Layer, while explicit local-only CLI/API split options continue to follow existing validation.

Record actual OS/backend, model, measured capacity, and pass/fail results. If a real device or cluster is unavailable, mark those hardware scenarios unverified and retain the exact follow-up steps above.
