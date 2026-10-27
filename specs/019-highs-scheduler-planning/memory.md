# Feature Memory: 019-highs-scheduler-planning

Scratch context for this feature only; not durable project memory.

## Sources consulted

- Governance: `.specify/memory/constitution.md`, `.specify/memory/architecture_constitution.md`, `.specify/memory/workflow.md`.
- Durable memory index: `docs/memory/INDEX.md` (`A1`, `A2`, `D1`–`D3`).
- Adjacent specs:
  - `specs/017-prima-integration/spec.md` (Prima integration acceptance, exact placement, manifest mode, segmented execution, profiling, and qualification gates).
  - `specs/018-cross-platform-planning/spec.md` and `specs/018-cross-platform-planning/memory.md` (Windows/Linux/macOS platform-aware planning, ring backend compatibility, digest stability on `RankAllocation` and `ExecutionManifest`).
- Codebase & upstream fork inspection:
  - `D:/dev/prima.cpp/common/common.cpp` (`assign_layers_to_device`, `assign_layers_and_select_devices` under `USE_HIGHS`: Halda MILP formulation over cycle count `k`, per-device window `w[m]`, GPU layer count `n[m]`, memory-pressure sets `M1..M4`, weak-worker pruning, and `master_priority`).
  - `src/kllm/modules/primacpp/interactors/planner.py`, `placement.py`, `distributed_planning.py`, `measured_planning.py`, `segment_costs.py`, `control.py`.
  - `dashboard/src/lib/modules/runtimes/view/PrimaPreparation.svelte`, `dashboard/src/lib/modules/run/view/RunPage.svelte`, `dashboard/src/lib/modules/run/view/DeploymentPlan.svelte`.

## Constraints carried into the spec

- **Exact placement & manifest authority**: Kluster owns final placement and manifest generation. Manifest mode in native `prima.cpp` does not autonomously reschedule or prune ranks at launch time; instead, the HiGHS scheduler runs as part of Kluster's planning/preview and produces an exact, digest-verified placement plan (`PrimaTopology` / `ExecutionManifest`).
- **Evidence-gated optimization & fallback**: HiGHS optimization requires valid model metadata and fresh, verified device/link profiles when optimizing latency. When profile/link evidence is missing, stale, or incomplete, or when the solver is infeasible, the planner must explicitly fall back to capacity-first placement and report the exact reason without fabricating measurements.
- **Cross-platform & Vulkan support**: Unlike legacy upstream `USE_HIGHS` (which asserted `!is_windows` and only checked Metal/CUDA), the scheduler must support Windows, Linux, and macOS and account for Vulkan (including MoltenVK), Metal, CUDA, and CPU-only coordinator ranks.
- **User constraints**: Explicit user constraints (`required_nodes`, `node_priority`, node VRAM/RAM budgets, `just_enough_master`, and segmented-execution capability gates) must be honored by the scheduler; required nodes cannot be pruned as weak devices.
- **Canonical digest compatibility**: Optional scheduler telemetry/metadata must not break existing canonical digests (`ExecutionManifest.digest()`, `RankAllocation` wire format) when omitted or unset.

## Open questions (none blocking)

- None blocking specification.

## Implementation & Verification Receipts (`2026-04-14`)

- **Domain Entities (`src/kllm/modules/primacpp/entities.py`)**:
  - Defined `MemoryPressureSet`, `PlacementStrategyMode`, `SchedulerMode`, `HighsNodeAllocation`, `HighsPrunedNode`, `HighsCycleCandidate`, and `HighsScheduleReport`.
  - Extended `PrimaTopology` (and `PrimaPlan = PrimaTopology` alias) with `scheduler_report: HighsScheduleReport | None = Field(default=None, exclude=True)` so `PrimaTopology.model_dump()` and `ExecutionManifest.digest()` remain byte-for-byte identical.
- **Inference & Schema Boundaries (`src/kllm/modules/inference/entities.py`, `parameter_catalog.py`, `src/kllm/shared/schemas.py`)**:
  - Added `placement_strategy: Literal["auto", "capacity_first", "priority"] = "auto"` (and nested `parameters.placement_strategy` normalization) to `RunSettingsFields` / `InferenceParameters` and `InferencePlanBody`.
  - Registered `--placement-strategy` in `PARAMETER_SPECS` under section `"placement"` with `memory_affecting=True`.
- **Halda / HiGHS MILP Scheduler (`src/kllm/modules/primacpp/interactors/highs_scheduler.py`)**:
  - Implemented `HighsMilpModel` CSC matrix builder, `_solve_highs_milp` (`highspy` + deterministic exact MILP solver), `classify_device_set` across `Windows`, `Linux`, and `Darwin` (supporting Vulkan, Metal, CUDA, UMA/integrated GPUs, and CPU coordinator ranks), `assign_layers_to_device(state, k)`, `assign_layers_and_select_devices(state)` (with `< 500 MB/s` slow-disk $M_4$ forcing + automatic rollback on infeasibility and weak-worker $w_m \le 1, n_m = 0$ pruning preserving master rank 0 and `required_nodes`), `build_segments_for_schedule` (`reserve_graph_visits`), and `solve_highs_halda_schedule`.
- **Prima Planning & Dashboard Integration**:
  - Wired `solve_highs_halda_schedule` into `measured_planning.py`, `distributed_planning.py`, `planner.py`, and `control.py`.
  - Added `PlacementStrategyMode`, `SchedulerMode`, and `HighsScheduleReport` types and UI telemetry to `dashboard/src/lib/modules/run/entities.ts`, `dashboard/src/lib/modules/runtimes/entities.ts`, `dashboard/src/lib/modules/run/interactor.svelte.ts`, `dashboard/src/lib/modules/run/view/RunPage.svelte`, `dashboard/src/lib/modules/run/view/DeploymentPlan.svelte`, and `dashboard/src/lib/modules/runtimes/view/PrimaPreparation.svelte`.
- **Verification**:
  - Python suites (`tests/test_highs_scheduler.py`, `tests/test_prima_measured_planning.py`, `tests/test_prima_candidate_profiles.py`, `tests/test_prima_windows.py`, `tests/test_prima_cross_platform.py`, `tests/test_prima_lifecycle.py`, `tests/test_configuration.py`, `tests/test_plan_run_params.py`): all passing (`154 passed, 1 skipped` + `53 passed`).
  - Dashboard suites (`dashboard/src/lib/modules/run/__tests__/highs-scheduler.test.ts` / `dashboard/tests/highs-scheduler.test.mjs`, `configuration.test.mjs`, `memory-estimation.test.mjs`) and `svelte-check`: all passing.

