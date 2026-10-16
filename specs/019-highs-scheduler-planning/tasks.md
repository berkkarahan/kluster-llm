# Tasks: HiGHS Scheduler Integration (`019-highs-scheduler-planning`)

**Input**: Design documents from `/specs/019-highs-scheduler-planning/`  
**Prerequisites**: [plan.md](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/plan.md) (required), [spec.md](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/spec.md) (required for user stories), [research.md](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/research.md), [data-model.md](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/data-model.md), [contracts/highs-scheduler-planning.md](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/contracts/highs-scheduler-planning.md), [quickstart.md](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/quickstart.md)

**Tests**: Included per Constitution Principle I (Test-First & Fixture-Driven Determinism) and `SC-001`–`SC-006`.

**Organization**: Tasks are grouped by user story (`US1`, `US2`, `US3`) to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (`[US1]`, `[US2]`, `[US3]`)
- Exact file paths and verbatim `data-model.md` constraints are included in task descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify baseline Prima planning and dashboard test suites before introducing HiGHS scheduler entities and interactors.

- [X] T001 Verify baseline Prima planning and lifecycle suites pass via `uv run pytest tests/test_prima_windows.py tests/test_prima_lifecycle.py -q` in `tests/test_prima_windows.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core domain entities (`HighsNodeAllocation`, `HighsPrunedNode`, `HighsCycleCandidate`, `HighsScheduleReport`) and `InferenceParameters.placement_strategy` schema registration that MUST be complete before any user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Define `MemoryPressureSet` (`Literal["M1", "M2", "M3", "M4"]`), `PlacementStrategyMode` (`Literal["auto", "capacity_first", "priority"]`), `SchedulerMode` (`Literal["highs_halda", "capacity_first", "priority"]`), `HighsNodeAllocation` (with fields `node_id`: `str` `"non-empty"`, `rank`: `int` `">= 0"`, `w_m`: `int` `">= 0"`, `n_m`: `int` `"0 <= n_m <= w_m"`, `cpu_layers_per_cycle`: `int` `"== w_m - n_m"`, `total_layers`: `int` `"== k * w_m"`, `total_gpu_layers`: `int` `"== k * n_m"`, `total_cpu_layers`: `int` `"== k * (w_m - n_m)"`, `memory_pressure_set`: `Literal["M1", "M2", "M3", "M4"]` `"required"`, `initial_memory_pressure_set`: `Literal["M1", "M2", "M3", "M4"]` `"default \"M4\""`, `forced_m4`: `bool` `"default False"`, `overloaded_memory_bytes`: `int` `">= 0, default 0"`, `vram_used_bytes`: `int` `">= 0, default 0"`, `ram_used_bytes`: `int` `">= 0, default 0"`, `compute_ms`: `float` `">= 0.0"`, `disk_reload_ms`: `float` `">= 0.0"`, `kv_and_sync_ms`: `float` `">= 0.0"`, `network_ms`: `float` `">= 0.0"`, `total_step_ms`: `float` `">= 0.0"`, `prima_graphs`: `int` `">= 1, default 1"`), `HighsPrunedNode` (with fields `node_id`: `str` `"non-empty"`, `reason`: `Literal["weak_device_w_le_1_ngl_0", "zero_layers_assigned"]` `"required"`, `iteration`: `int` `">= 1, default 1"`, `last_w_m`: `int` `">= 0, default 0"`, `last_n_m`: `int` `">= 0, default 0"`, `detail`: `str` `"default \"\""`), `HighsCycleCandidate` (with fields `cycle_count_k`: `int` `">= 1"`, `window_size_w`: `int` `">= 1"`, `feasible`: `bool` `"required"`, `objective_step_ms`: `float | None` `">= 0.0 when feasible"`), and `HighsScheduleReport` (with fields `scheduler_mode`: `Literal["highs_halda", "capacity_first", "priority"]` `"required"`, `requested_strategy`: `Literal["auto", "capacity_first", "priority"]` `"default \"auto\""`, `solver_backend`: `Literal["highspy", "highs_exact_milp", "heuristic_fallback"]` `"default \"highs_exact_milp\""`, `total_layers`: `int` `">= 1"`, `cycle_count_k`: `int` `">= 1, default 1"`, `window_size_w`: `int` `">= 1"`, `segmented_placement_enabled`: `bool` `"default False"`, `mmap_mode`: `str` `"default \"readahead\""`, `just_enough_master`: `bool` `"default False"`, `objective_step_ms`: `float` `">= 0.0, default 0.0"`, `compute_step_ms`: `float` `">= 0.0, default 0.0"`, `disk_reload_step_ms`: `float` `">= 0.0, default 0.0"`, `network_step_ms`: `float` `">= 0.0, default 0.0"`, `fallback_reason`: `str | None` `"default None"`, `missing_profile_nodes`: `list[str]` `"default []"`, `forced_m4_rollback`: `bool` `"default False"`, `iterations`: `int` `">= 1, default 1"`, `node_allocations`: `list[HighsNodeAllocation]` `"default []"`, `pruned_nodes`: `list[HighsPrunedNode]` `"default []"`, `evaluated_cycles`: `list[HighsCycleCandidate]` `"default []"`), plus optional `scheduler_report: HighsScheduleReport | None = None` on `PrimaPlan` (excluded from `PrimaPlan.digest()`) in `src/kllm/modules/primacpp/entities.py`
- [X] T003 [P] Add `placement_strategy: Literal["auto", "capacity_first", "priority"] | None = None` to `InferenceParameters` in `src/kllm/modules/inference/entities.py` and register `placement_strategy` with `default="auto"`, `options=("auto", "capacity_first", "priority")`, and `supports_auto=True` in `src/kllm/modules/inference/interactors/parameter_catalog.py`
- [X] T004 [P] Update `InferencePlanBody` in `src/kllm/shared/schemas.py` to accept optional top-level `placement_strategy: Literal["auto", "capacity_first", "priority"] | None = None` alongside nested `parameters.placement_strategy`

**Checkpoint**: Foundation ready — all `HighsScheduleReport` domain entities, digest-preserving `PrimaPlan` extensions, and `placement_strategy` parameter specs are in place.

---

## Phase 3: User Story 1 - Latency-Optimal Layer, GPU, and Cycle Scheduling in Planning (Priority: P1) 🎯 MVP

**Goal**: Implement the Halda / HiGHS MILP layer and cycle scheduler (`assign_layers_to_device` across divisor cycle counts $k \mid L$ and memory-pressure sets $M_1 \dots M_4$), translate multi-cycle $(w_m, n_m, k)$ solutions into contiguous `Segment` ranges and `reserve_graph_visits` counts, and wire `"auto"` (with unprofiled fallback to `"capacity_first"`) into Prima local and distributed planning.

**Independent Test**: Provide a model layout and complete `DeviceProfile` and link profiles for single-node and heterogeneous multi-node clusters across `Windows`, `Linux`, and `Darwin`, request a Prima plan, and verify that the planner solves for $(w_m, n_m, k)$ minimizing predicted step latency, classifies each rank into $M_1 \dots M_4$, reserves `prima_graphs` for $k > 1$ when `"segmented_placement"` is supported, and falls back cleanly to `"capacity_first"` with an explicit `fallback_reason` when profile evidence is missing.

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T005 [P] [US1] Create unit tests in `tests/test_highs_scheduler.py` covering Halda MILP model construction and solution (`sum(w_m) == W == L / k`, `sum(k * w_m) == L`, `0 <= n_m <= w_m`), cross-platform (`Windows`, `Linux`, `Darwin`) and Vulkan/UMA/discrete GPU memory-pressure classification ($M_1, M_2, M_3, M_4$) under `mmap="readahead"` vs `mmap="none"`, multi-cycle ($k > 1$) divisor selection when `"segmented_placement"` is enabled vs $k = 1$ restriction when absent, `Segment` range expansion and `reserve_graph_visits` graph counts, and deterministic fallback to `"capacity_first"` when `DeviceProfile` or link evidence is missing or incomplete

### Implementation for User Story 1

- [X] T006 [US1] Implement `HighsMilpModel` CSC matrix builder, `_solve_highs_milp`, `assign_layers_to_device(state, k)` with cross-platform (`Windows`, `Linux`, `Darwin`) and Vulkan/UMA/CPU $M_1 \dots M_4$ memory-pressure set classification and unrestricted `mmap` paging (`"when mmap == \"none\": all active ranks must have memory_pressure_set == \"M4\" and disk_reload_ms == 0.0"`), multi-cycle ($k \mid L$) segment expansion, and `reserve_graph_visits` integration in `src/kllm/modules/primacpp/interactors/highs_scheduler.py`
- [X] T007 [US1] Integrate `solve_highs_halda_schedule` into single-node measured planning in `src/kllm/modules/primacpp/interactors/measured_planning.py` and distributed ring planning in `src/kllm/modules/primacpp/interactors/distributed_planning.py`, enforcing evidence gating (`FR-008`) and falling back to `"capacity_first"` with `fallback_reason` and `missing_profile_nodes` when `DeviceProfile` or private-link measurements are missing or incomplete
- [X] T008 [US1] Update `PrimaPlanner.preview` and `PrimaPlanner.materialize` in `src/kllm/modules/primacpp/interactors/planner.py` and `PrimaControlService.plan` in `src/kllm/modules/primacpp/interactors/control.py` to execute the HiGHS scheduler for Prima.cpp runs (preserving standard `llama.cpp` RPC planning unchanged per `FR-001`), attach `scheduler` (`HighsScheduleReport.model_dump()`) to plan preview/control responses and `scheduler_report` to `PrimaPlan`, and propagate multi-cycle `segments` and `prima_graphs` through `topology_from_plan`

**Checkpoint**: User Story 1 is complete and independently testable via `uv run pytest tests/test_highs_scheduler.py tests/test_prima_windows.py -q`.

---

## Phase 4: User Story 2 - Weak-Worker Pruning and Constraint-Aware Participant Selection (Priority: P2)

**Goal**: Implement the outer Halda iterative refinement loop (`assign_layers_and_select_devices`) that prunes optional weak workers ($w_m \le 1 \land n_m = 0$), forces slow-disk (`< min_disk_read_speed_bytes_s`) overloaded nodes into $M_4$ with infeasibility rollback, and strictly honors operator constraints (`required_nodes`, `node_priority`, `just_enough_master`, and memory safety caps).

**Independent Test**: Request a Prima plan on a cluster with a fast GPU node and an optional weak worker ($w_m \le 1, n_m = 0$); verify the weak worker is pruned, recorded in `scheduler.pruned_nodes`, and all layers are reassigned to capable participants. Repeat with the weak worker listed in `required_nodes` and verify it is retained with $w_m \ge 1$.

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T009 [P] [US2] Add unit and integration tests in `tests/test_highs_scheduler.py` and `tests/test_prima_windows.py` covering iterative weak-worker pruning (`w_m <= 1 and n_m == 0` for worker `m > 0`), preservation of master rank 0 and `required_nodes` (`"Rank 0 (master) and any node_id in required_nodes must never appear in pruned_nodes"`), `just_enough_master` coordinator/minimal master weighting, slow-disk (`disk_read_bytes_s < min_disk_read_speed_bytes_s`) forcing into $M_4$ with automatic rollback (`forced_m4_rollback == True`) when forcing $M_4$ causes MILP infeasibility, and explicit `"capacity_first"` and `"priority"` strategy overrides

### Implementation for User Story 2

- [X] T010 [US2] Implement the outer Halda iterative refinement loop (`assign_layers_and_select_devices`) in `src/kllm/modules/primacpp/interactors/highs_scheduler.py` to detect and prune optional weak workers (`w_m <= 1 and n_m == 0`), enforce `col_lower[m] >= 1` for `required_nodes`, honor `just_enough_master` and `node_priority` ordering, force slow-disk (`< 500 MB/s`) overloaded nodes into $M_4$ with backup rollback (`forced_m4_rollback = True`) on infeasibility, and record `HighsPrunedNode` diagnostics
- [X] T011 [US2] Update candidate member filtering and topology assembly in `src/kllm/modules/primacpp/interactors/distributed_planning.py` and `src/kllm/modules/primacpp/interactors/planner.py` so pruned weak workers are excluded from the active `PrimaTopology.ranks` ring while `scheduler.pruned_nodes` and `scheduler.iterations` are preserved in the returned `HighsScheduleReport`, and explicit `placement_strategy` selections (`"capacity_first"` or `"priority"`) bypass HiGHS optimization as requested

**Checkpoint**: User Stories 1 and 2 are complete and independently testable via `uv run pytest tests/test_highs_scheduler.py tests/test_prima_windows.py -q`.

---

## Phase 5: User Story 3 - Dashboard Schedule Inspection and Strategy Controls (Priority: P3)

**Goal**: Surface the placement strategy selector (`Automatic (HiGHS / Capacity fallback)`, `Capacity-first`, `Priority`) and the complete HiGHS schedule telemetry ($k, W, w_m, n_m$, $M_1 \dots M_4$ badges, latency component breakdown, pruned weak workers, and fallback diagnostics) in the Svelte 5 dashboard Deploy page (`RunPage.svelte`, `DeploymentPlan.svelte`) and Prima Preparation panel (`PrimaPreparation.svelte`).

**Independent Test**: Render `RunPage.svelte`, `DeploymentPlan.svelte`, and `PrimaPreparation.svelte` with a HiGHS-scheduled Prima plan and a capacity-first fallback plan; verify strategy selection triggers a fresh plan preview, invalidates stale preparation state, and displays cycle count $k$, window $W$, per-node $(w_m, n_m)$, $M_1 \dots M_4$ memory-pressure badges, latency breakdown, and pruned weak workers.

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T012 [P] [US3] Add dashboard unit tests in `dashboard/src/lib/modules/run/` (or existing dashboard test suite under `dashboard/src/`) verifying `HighsScheduleReport` normalization, `placementStrategy` state updates (`"auto"`, `"capacity_first"`, `"priority"`), and schedule breakdown helper formatting

### Implementation for User Story 3

- [X] T013 [P] [US3] Add `PlacementStrategyMode`, `SchedulerMode`, `MemoryPressureSet`, `HighsNodeAllocation`, `HighsPrunedNode`, and `HighsScheduleReport` TypeScript interfaces to `dashboard/src/lib/modules/run/entities.ts` and `dashboard/src/lib/modules/runtimes/entities.ts`
- [X] T014 [US3] Update `dashboard/src/lib/modules/run/interactor.svelte.ts` to manage `placementStrategy` (`"auto" | "capacity_first" | "priority"`, defaulting to `"auto"`), include `placement_strategy` in `/v2/inference/plans` preview and launch requests, and invalidate stale Prima preparation state when `placementStrategy` changes
- [X] T015 [US3] Update `dashboard/src/lib/modules/run/view/RunPage.svelte` and `dashboard/src/lib/modules/run/view/DeploymentPlan.svelte` to render the Placement Strategy selector (`Automatic (HiGHS / Capacity fallback)`, `Capacity-first`, `Priority`) and the HiGHS schedule breakdown panel (scheduler mode badge, cycle count $k$, window $W$, per-node $w_m, n_m$, GPU/CPU layers, $M_1 \dots M_4$ memory-pressure badge, compute/disk/KV/network latency breakdown, pruned weak workers, and fallback reason)
- [X] T016 [P] [US3] Update `dashboard/src/lib/modules/runtimes/view/PrimaPreparation.svelte` to display the HiGHS schedule summary (scheduler mode, $k, W$, per-rank $M_1 \dots M_4$ set, $(w_m, n_m)$, pruned workers, and predicted step latency) in the Prima runtime preparation panel

**Checkpoint**: All three user stories (`US1`, `US2`, `US3`) are complete and independently verified across backend and dashboard.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full regression validation across Python and Svelte 5 dashboard suites and feature memory synchronization.

- [X] T017 [P] Update `specs/019-highs-scheduler-planning/memory.md` with final implementation decisions and verification receipts
- [X] T018 Run full quickstart and regression validation (`uv run pytest tests/test_highs_scheduler.py tests/test_prima_windows.py tests/test_prima_lifecycle.py -q`, `npm --prefix dashboard run check`, and `npm --prefix dashboard test`) per `specs/019-highs-scheduler-planning/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately (`T001`).
- **Foundational (Phase 2)**: Depends on Setup (`T001`) — BLOCKS all user stories (`T002`–`T004`).
- **User Story 1 (Phase 3 - P1)**: Depends on Foundational (`T002`–`T004`). Delivers the core Halda/HiGHS MILP solver and planner integration (`T005`–`T008`).
- **User Story 2 (Phase 4 - P2)**: Depends on User Story 1's inner MILP solver (`T006`) to wrap it in the outer weak-worker pruning and constraint-aware participant loop (`T009`–`T011`).
- **User Story 3 (Phase 5 - P3)**: Depends on Foundational (`T002`–`T004`) for contract shapes; TypeScript entities and views (`T012`–`T016`) can be developed in parallel with backend `US1`/`US2` and verified end-to-end once `T008`/`T011` complete.
- **Polish (Phase 6)**: Depends on completion of `US1`, `US2`, and `US3` (`T005`–`T016`).

### User Story Dependency Graph

```text
Phase 1: Setup (T001)
   │
   ▼
Phase 2: Foundational (T002, T003, T004)
   ├──► Phase 3: US1 - Latency-Optimal Layer, GPU & Cycle Scheduling (T005 → T006 → T007 → T008)
   │       │
   │       ▼
   │    Phase 4: US2 - Weak-Worker Pruning & Constraints (T009 → T010 → T011)
   │
   └──► Phase 5: US3 - Dashboard Schedule Inspection & Strategy Controls (T012, T013 → T014 → T015, T016)
                                   │
                                   ▼
                    Phase 6: Polish & Validation (T017, T018)
```

---

## Parallel Opportunities

### Phase 2 (Foundational)

```bash
# Run all Foundational tasks in parallel across separate files:
Task T002: "Define HighsScheduleReport and related entities in src/kllm/modules/primacpp/entities.py"
Task T003: "Add placement_strategy to InferenceParameters in src/kllm/modules/inference/entities.py & parameter_catalog.py"
Task T004: "Update InferencePlanBody in src/kllm/shared/schemas.py"
```

### Phase 3 & Phase 5 (Backend US1 + Frontend US3 in Parallel)

```bash
# Once Phase 2 completes, backend solver tests/implementation and frontend TypeScript entities can proceed in parallel:
Task T005: "[US1] Create unit tests in tests/test_highs_scheduler.py"
Task T012: "[US3] Add dashboard unit tests for HighsScheduleReport formatting"
Task T013: "[US3] Add TypeScript interfaces to dashboard/src/lib/modules/run/entities.ts & runtimes/entities.ts"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (`T001`)
2. Complete Phase 2: Foundational (`T002`–`T004`)
3. Complete Phase 3: User Story 1 (`T005`–`T008`)
4. **STOP and VALIDATE**: Run `uv run pytest tests/test_highs_scheduler.py tests/test_prima_windows.py -q` to verify Halda/HiGHS MILP scheduling, $M_1 \dots M_4$ set classification, multi-cycle ($k > 1$) segment expansion, and unprofiled fallback.

### Incremental Delivery

1. **Foundation (`T001`–`T004`)**: Typed `HighsScheduleReport` models and `placement_strategy` parameter spec.
2. **US1 (`T005`–`T008`)**: Core Halda/HiGHS MILP solver (`assign_layers_to_device`), $M_1 \dots M_4$ sets, $k \mid L$ cycles, and planner integration.
3. **US2 (`T009`–`T011`)**: Iterative weak-worker pruning (`assign_layers_and_select_devices`), slow-disk $M_4$ forcing with rollback, and `required_nodes` / `node_priority` / `just_enough_master` enforcement.
4. **US3 (`T012`–`T016`)**: Dashboard strategy selector and HiGHS schedule breakdown in `RunPage.svelte`, `DeploymentPlan.svelte`, and `PrimaPreparation.svelte`.
5. **Polish (`T017`–`T018`)**: Full backend and dashboard regression verification and memory capture.
