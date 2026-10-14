# Implementation Plan: HiGHS Scheduler Integration (`019-highs-scheduler-planning`)

**Branch**: `feat/prima-integration` | **Date**: 2026-10-14 | **Spec**: [spec.md](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/spec.md)  
**Input**: Feature specification from `/specs/019-highs-scheduler-planning/spec.md`

## Summary

Implement and integrate the Halda / HiGHS mixed-integer linear programming (MILP) layer and device scheduler from upstream [`prima.cpp/common/common.cpp`](file:///D:/dev/prima.cpp/common/common.cpp#L862-L1659) into Kluster LLM's Prima (`primacpp`) planning backend and Svelte 5 dashboard (`run` and `runtimes` modules). The scheduler evaluates candidate cycle counts $k \mid L$ ($W = L / k$), solves for per-device layer windows ($w_m$) and GPU layer offloads ($n_m$) across memory-pressure sets $M_1 \dots M_4$, iteratively prunes weak optional workers ($w_m \le 1, n_m = 0$) while honoring `required_nodes` and `node_priority`, reserves multi-cycle Vulkan compute graphs via `reserve_graph_visits`, falls back cleanly to `capacity_first` when `DeviceProfile` measurements are incomplete, and surfaces the full HiGHS schedule telemetry and strategy selector (`auto`, `capacity_first`, `priority`) in the dashboard.

## Technical Context

**Language/Version**: Python 3.11+ (managed via `uv`), TypeScript / Svelte 5 (dashboard)  
**Primary Dependencies**: Pydantic v2, FastAPI, Rich/Typer (`kllm` CLI), Svelte 5 (`dashboard`), optional `highspy` with built-in exact integer MILP solver over the canonical `HighsModel` CSC matrix  
**Storage**: In-memory and cached `DeviceProfile` JSON state in isolated runtime/cluster state; no database migrations required  
**Testing**: `uv run pytest tests/test_highs_scheduler.py tests/test_prima_windows.py tests/test_prima_lifecycle.py`, `npm --prefix dashboard run check`, `npm --prefix dashboard test`  
**Target Platform**: Cross-platform (`Windows`, `Linux`, `Darwin` / macOS) across Vulkan (`vulkan`, `metal+vulkan`), Metal, CUDA, and CPU profiles  
**Project Type**: Distributed LLM cluster manager & runtime orchestrator (Python backend/CLI + Svelte 5 dashboard)  
**Performance Goals**: `< 50ms` internal target for deterministic schedule evaluation across clusters of 1–16 nodes and models up to 128+ transformer layers (satisfying `SC-001`'s `< 1s` plan preview budget)  
**Constraints**:
- Scoped exclusively to Prima.cpp (`primacpp`) planning and preparation; standard `llama.cpp` RPC deployments retain existing capacity-first/priority planning (`FR-001`).
- Must preserve 100% wire and SHA-256 digest compatibility for `RankAllocation`, `PrimaPlan.digest()`, and `ExecutionManifest` across worker `/v2/node/runtimes/{id}/prima-prepare` requests (`extra="forbid"`).
- Must not alter or fabricate hardware qualification gates in `qualification.json`.
**Scale/Scope**: 1–16 cluster nodes, models with 1–256 layers, single-node and distributed ring topologies (`k = 1` contiguous and `k > 1` segmented multi-cycle placements).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Phase 0 Gate Evaluation

- **I. Test-First & Fixture-Driven Determinism**: **PASS**. The HiGHS MILP model construction, iterative $M_1 \dots M_4$ set refinement, weak-worker pruning, and fallback logic are pure deterministic functions over `DeviceProfile` fixtures and can be exhaustively verified in `tests/test_highs_scheduler.py` without requiring live multi-node hardware.
- **II. VIPER Module Boundaries ([`.specify/memory/architecture_constitution.md`](file:///D:/dev/kluster-llm/.specify/memory/architecture_constitution.md))**: **PASS**. Backend changes stay within `src/kllm/modules/primacpp/` (`entities.py`, `interactors/`), `src/kllm/modules/inference/` (`entities.py`, `interactors/parameter_catalog.py`), and `src/kllm/shared/schemas.py`. Frontend changes stay within `dashboard/src/lib/modules/run/` and `dashboard/src/lib/modules/runtimes/`.
- **III. Security & Explicit Trust**: **PASS**. No changes to `SignedParticipantEvidence` HMAC verification or network port exposure.
- **IV. Verification Before Claims**: **PASS**. Scheduler predictions are explicitly labeled as model-based latency estimates (`objective_step_ms`, `compute_step_ms`, `disk_reload_step_ms`, `network_step_ms`) and do not modify hardware qualification receipts.

### Post-Phase 1 Gate Re-Evaluation

- **Wire & Digest Stability Check**: **PASS**. `RankAllocation` and `ExecutionManifest` schemas are untouched; `HighsScheduleReport` is carried via `scheduler` in plan preview/control responses and an optional `scheduler_report` field on `PrimaPlan` excluded from `PrimaPlan.digest()`.
- **VIPER Surface Check**: **PASS**. Only one cohesive interactor file (`src/kllm/modules/primacpp/interactors/highs_scheduler.py`) is added inside `primacpp/interactors/`, and all entity types are placed in `src/kllm/modules/primacpp/entities.py`.

## Project Structure

### Documentation (this feature)

```text
specs/019-highs-scheduler-planning/
├── spec.md                              # Feature specification
├── plan.md                              # This implementation plan
├── research.md                          # Phase 0 research decisions & rationale
├── data-model.md                        # Phase 1 entity & validation model
├── quickstart.md                        # Phase 1 verification guide
├── memory.md                            # Feature memory log
├── memory-synthesis.md                  # Pre-plan memory synthesis
├── checklists/
│   └── requirements.md                  # Specification quality checklist
└── contracts/
    └── highs-scheduler-planning.md      # REST API, interactor, and dashboard contracts
```

### Source Code (repository root)

```text
src/kllm/
├── shared/
│   └── schemas.py                                               # Accept optional placement_strategy on plan request schemas
└── modules/
    ├── inference/
    │   ├── entities.py                                          # Add optional placement_strategy to InferenceParameters
    │   └── interactors/
    │       └── parameter_catalog.py                             # Register placement_strategy ("auto", "capacity_first", "priority")
    └── primacpp/
        ├── entities.py                                          # Add HighsNodeAllocation, HighsPrunedNode, HighsCycleCandidate, HighsScheduleReport; optional scheduler_report on PrimaPlan
        └── interactors/
            ├── highs_scheduler.py                               # NEW: Halda / HiGHS MILP model builder, solver, M1-M4 refinement, weak-worker pruning, multi-cycle segment & graph mapper
            ├── distributed_planning.py                          # Integrate solve_highs_halda_schedule into distributed ring candidate scoring & topology building
            ├── measured_planning.py                             # Integrate solve_highs_halda_schedule into single-node measured candidate evaluation
            ├── planner.py                                       # Wire placement_strategy, scheduler report, and auto/capacity_first/priority selection in preview() and materialize()
            └── control.py                                       # Expose scheduler report in PrimaControlService.plan() responses

dashboard/src/lib/modules/
├── run/
│   ├── entities.ts                                              # Add PlacementStrategyMode, HighsScheduleReport, HighsNodeAllocation, HighsPrunedNode types
│   ├── interactor.svelte.ts                                     # Manage placementStrategy state and include in /v2/inference/plans requests
│   └── view/
│       ├── RunPage.svelte                                       # Add Placement Strategy selector (Auto / Capacity-first / Priority) for Prima runs
│       └── DeploymentPlan.svelte                                # Render HiGHS scheduler badge, cycle count k, window W, per-node (w_m, n_m, M1-M4), latency breakdown, and pruned workers
└── runtimes/
    ├── entities.ts                                              # Add HighsScheduleReport types to PrimaPlanPreview
    └── view/
        └── PrimaPreparation.svelte                              # Render HiGHS schedule breakdown in runtime Prima preparation view

tests/
├── test_highs_scheduler.py                                      # NEW: Unit tests for Halda/HiGHS MILP solver, M1-M4 sets, weak-worker pruning, k>1 segments, and strategy fallback
└── test_prima_windows.py                                        # Integration tests for PrimaPlanner.preview() and materialize() with HiGHS scheduler report & strategy overrides
```

**Structure Decision**: Keep all new scheduling math and MILP solver logic encapsulated in a dedicated interactor [`src/kllm/modules/primacpp/interactors/highs_scheduler.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/highs_scheduler.py) with typed domain models in [`src/kllm/modules/primacpp/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py), wired cleanly into [`planner.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/planner.py) and surfaced in the existing `run` and `runtimes` dashboard modules.

## Implementation Phases

### Phase 1: Domain Entities & Inference Parameter Catalog (`primacpp` & `inference`)

1. **[`src/kllm/modules/primacpp/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py)**:
   - Define `MemoryPressureSet`, `PlacementStrategyMode`, `SchedulerMode`, `HighsNodeAllocation`, `HighsPrunedNode`, `HighsCycleCandidate`, and `HighsScheduleReport` as `Record` subclasses.
   - Add `scheduler_report: HighsScheduleReport | None = None` to `PrimaPlan` and ensure `PrimaPlan.digest()` excludes `scheduler_report` so existing plan digests remain identical.
2. **[`src/kllm/modules/inference/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/entities.py) & [`parameter_catalog.py`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/interactors/parameter_catalog.py)**:
   - Add `placement_strategy: Literal["auto", "capacity_first", "priority"] | None = None` to `InferenceParameters`.
   - Register `placement_strategy` in `PARAMETER_SPECS` with `default="auto"`, `options=("auto", "capacity_first", "priority")`, and `supports_auto=True`.

### Phase 2: Halda / HiGHS MILP Scheduler Interactor (`highs_scheduler.py`)

1. **[`src/kllm/modules/primacpp/interactors/highs_scheduler.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/highs_scheduler.py)**:
   - Implement `HighsMilpModel` (matching upstream `HighsModel` CSC arrays: `col_cost`, `col_lower`, `col_upper`, `row_lower`, `row_upper`, `a_start`, `a_index`, `a_value`, `integrality`).
   - Implement `_solve_highs_milp(model)` supporting `highspy` when installed and a built-in exact integer solver over the CSC constraints (`< 2ms` execution, zero external failures).
   - Implement `assign_layers_to_device(state, k)` translating `DeviceProfile` compute timings (`cpu_layer_ms`, `gpu_layer_ms`, `input_ms`, `output_cpu_ms`, `output_gpu_ms`, `gpu_kv_copy_ms`, `gpu_upload_ms`, `gpu_download_ms`), memory budgets, `mmap` mode, and disk speeds (`disk_read_bytes_s`, `disk_random_read_bytes_s`) across `Windows`, `Linux`, and `Darwin` into $M_1 \dots M_4$ constraints and objective coefficients.
   - Implement `assign_layers_and_select_devices(...)` outer loop:
     - Iterate over divisor cycle counts $k \mid L$ ($k = 1$ when `"segmented_placement"` not in capabilities; all divisors $k \mid L$ when supported).
     - Detect weak workers ($w_m \le 1 \land n_m = 0$ for $m > 0$), prune optional weak workers while preserving rank 0 and `required_nodes`, and record `HighsPrunedNode` entries.
     - Force slow-disk (`disk_read_bytes_s < min_disk_read_speed_bytes_s`) overloaded nodes into $M_4$, with automatic rollback to the best feasible schedule if forcing $M_4$ causes infeasibility.
   - Translate the optimal $(w_m^*, n_m^*, k^*)$ into per-rank contiguous or multi-cycle `Segment` lists, `device_allocations`, `ngl`, `cpu_layers`, and `prima_graphs` / `prima_graph_counts` via [`reserve_graph_visits()`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/interactors/prima_graph_reserve.py#L46-L70).

### Phase 3: Planner & Control Integration (`planner.py`, `distributed_planning.py`, `measured_planning.py`, `control.py`)

1. **[`src/kllm/modules/primacpp/interactors/distributed_planning.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/distributed_planning.py) & [`measured_planning.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/measured_planning.py)**:
   - Wire `solve_highs_halda_schedule` into Prima single-node and distributed planning whenever `requested_strategy == "auto"` and complete `DeviceProfile` measurements exist.
   - When `requested_strategy == "capacity_first"` or `"priority"`, or when `DeviceProfile` data is missing under `"auto"`, execute the deterministic capacity-first or priority allocator and populate a `HighsScheduleReport` reflecting `scheduler_mode="capacity_first"` or `"priority"` and any `fallback_reason` / `missing_profile_nodes`.
2. **[`src/kllm/modules/primacpp/interactors/planner.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/planner.py) & [`control.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/control.py)**:
   - Include `"scheduler": report.model_dump()` and `"placement_mode": report.scheduler_mode` in `PrimaPlanner.preview()` and `PrimaControlService.plan()` responses, and attach `scheduler_report=report` to materialized `PrimaPlan` instances.
   - Ensure multi-cycle ($k > 1$) segments and `prima_graphs` produced by the HiGHS scheduler flow through `topology_from_plan` into `RankAllocation.segments` and `RankAllocation.prima_graphs`.

### Phase 4: Dashboard Strategy Selector & Schedule Breakdown (`dashboard/src/lib/modules/run/` & `runtimes/`)

1. **[`dashboard/src/lib/modules/run/entities.ts`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/entities.ts) & [`interactor.svelte.ts`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/interactor.svelte.ts)**:
   - Add `PlacementStrategyMode`, `HighsScheduleReport`, `HighsNodeAllocation`, and `HighsPrunedNode` interfaces and include `placement_strategy` in plan preview and launch payloads.
2. **[`dashboard/src/lib/modules/run/view/RunPage.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/view/RunPage.svelte) & [`DeploymentPlan.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/view/DeploymentPlan.svelte)**:
   - Add the placement strategy selector (`Automatic (HiGHS / Capacity fallback)`, `Capacity-first`, `Priority`) when a Prima runtime is selected.
   - Render the HiGHS schedule breakdown in `DeploymentPlan.svelte`: active scheduler badge (`HiGHS Halda MILP`, `Capacity-first`, `Priority`), cycle count $k$, cycle window $W$, per-node $(w_m, n_m)$, GPU vs. CPU layer counts, memory-pressure set badge ($M_1 \dots M_4$), predicted step latency breakdown (compute, disk reload, network/KV), pruned weak workers with pruning reason, and fallback notice when unprofiled.
3. **[`dashboard/src/lib/modules/runtimes/entities.ts`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/runtimes/entities.ts) & [`PrimaPreparation.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/runtimes/view/PrimaPreparation.svelte)**:
   - Surface the HiGHS schedule summary ($k, W$, per-rank $M_1 \dots M_4$ set, $(w_m, n_m)$, and predicted step latency) in the runtime Prima preparation panel.

### Phase 5: Verification & Regression Testing (`tests/` & `dashboard/`)

1. **[`tests/test_highs_scheduler.py`](file:///D:/dev/kluster-llm/tests/test_highs_scheduler.py)**:
   - Test layer conservation ($\sum_m w_m = W, \sum_m k \cdot w_m = L$) and GPU bounds ($0 \le n_m \le w_m$) across single-node and 2–4 node heterogeneous clusters.
   - Test $M_1, M_2, M_3, M_4$ classification under `mmap="readahead"` vs. `mmap="none"`, including slow-disk forcing into $M_4$ and infeasibility rollback.
   - Test iterative weak-worker pruning ($w_m \le 1, n_m = 0$) and verify that `required_nodes` and rank 0 are never pruned.
   - Test multi-cycle ($k > 1$) selection when `"segmented_placement"` is enabled and verify `Segment` ranges and `reserve_graph_visits` counts.
   - Test `"auto"`, `"capacity_first"`, and `"priority"` strategy selection and fallback when `DeviceProfile` is missing.
2. **[`tests/test_prima_windows.py`](file:///D:/dev/kluster-llm/tests/test_prima_windows.py) & Dashboard Checks**:
   - Verify `PrimaPlanner.preview()` and `PrimaPlanner.materialize()` return `scheduler` reports while preserving existing canonical digests.
   - Run `npm --prefix dashboard run check` and `npm --prefix dashboard test`.

## Complexity Tracking

No constitution violations or architectural deviations required.
