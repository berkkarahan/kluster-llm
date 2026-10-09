# Implementation Plan: MoE Expert Offload to Scattered Nodes

**Branch**: `013-moe-expert-offload` | **Date**: 2026-10-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-moe-expert-offload/spec.md`

## Summary

Add a first-class option to offload a MoE model's expert weights (then whole layers when still over capacity) to remote cluster nodes when the model does not fit in GPU memory. The strategy is heuristic-determined by default ("automatic") with an optional explicit count override, and applies to automatic placement, manual node-share, and partial GPU-offload paths. Raise the configurable batch-size ceiling to 2048 and micro-batch ceiling to 512 while keeping defaults at 512 / 128.

## Technical Context

**Language/Version**: Python 3.11+ (managed with uv), Rust for `crates/kllm-native`

**Primary Dependencies**: FastAPI (agent), Pydantic (run/placement models), llama.cpp pinned tag `v0.5.0`, Svelte (dashboard)

**Storage**: JSON config (`~/.kllm/config.toml`), persisted cluster state JSON; no new storage

**Testing**: `uv run pytest -q` (Python), `cargo test --manifest-path crates/kllm-native/Cargo.toml` (Rust), `npm --prefix dashboard run check` (dashboard)

**Target Platform**: Windows, Linux, macOS (multi-node RPC cluster)

**Project Type**: CLI + REST agent + dashboard, distributed inference (llama.cpp RPC)

**Performance Goals**: No regression to non-MoE placement; MoE offload must fit a model that overflows a single node's GPU without degrading placement accuracy (plan must match launch)

**Constraints**: Port 50052 private; llama.cpp pinned tag unchanged; extra-arg sanitization preserved; no backward-incompatible change to batch/micro-batch defaults

**Scale/Scope**: Multi-node clusters (up to ~16 nodes per existing dashboard assumptions); MoE models in the tens-of-Billions parameter range

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|------------|
| I. Simple, consistent code | PASS — reuse existing placement/cost-model modules; no new abstraction unless a second caller needs it |
| II. Behavior-focused regression tests | PASS — deterministic local checks for cost model, resolution, and launch-arg emission; hardware check deferred and labeled |
| III. Verified integration/portability | PASS with note — `-ot` RPC semantics on pinned v0.5.0 must be verified on hardware; fallback documented |
| IV. Explicit contracts/failures | PASS — new option is a validated first-class field, not an unblocked extra-arg; overflow still errors explicitly |
| V. Maintainable/reproducible | PASS — docs (`docs/cli.md`) and dashboard wording updated in the same change |

No violations requiring `Complexity Tracking` entries.

## Project Structure

### Documentation (this feature)

```text
specs/013-moe-expert-offload/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── moe-offload.md
│   └── batch-micro-batch.md
└── tasks.md              # created by /speckit.tasks (not this command)
```

### Source Code (repository root)

```text
src/kllm/
├── core/
│   ├── placement.py            # expert-aware allocation + RPC tensor-override emission
│   ├── memory_estimation.py    # expert weight cost modeling
│   └── placement_models.py     # new run-setting / plan-result fields
├── run_settings_resolution.py  # batch/micro-batch ceilings + offload option resolution
├── run_launch.py               # emit --n-cpu-moe / -ot / --override-tensor
├── run_settings.py             # RunBody field for the offload option
├── gguf_meta.py                # MoE arch + expert tensor size detection
└── extra_args.py               # keep managed flags blocked; offload emitted natively
crates/kllm-native/
└── src/ (planner.rs / process.rs)  # only if expert cost modeling moves native
dashboard/src/routes/run/+page.svelte  # offload control + batch/micro-batch max attrs

tests/
├── test_memory_estimation.py   # expert cost + batch ceiling checks
├── test_cluster_optimizations.py  # MoE placement across nodes
└── test_plan_run_params.py     # launch arg + offload option contract
```

**Structure Decision**: Single project (default). All Python application code lives in `src/kllm/`; native code stays in `crates/kllm-native/`; UI in `dashboard/`. No new module boundaries.

## Complexity Tracking

None — no constitution violations.
