# Memory Synthesis

## Current Scope

- **Feature**: `019-highs-scheduler-planning` — Implement and integrate HiGHS (Halda) MILP scheduler from `prima.cpp` into Kluster LLM's planning and dashboard.
- **Modules in Scope**:
  - `src/kllm/modules/primacpp/` (`entities.py`, `interactors/highs_scheduler.py`, `interactors/planner.py`, `interactors/placement.py`, `interactors/distributed_planning.py`, `interactors/measured_planning.py`, `interactors/segment_costs.py`, `interactors/control.py`)
  - `src/kllm/modules/inference/` (`router_http.py`, `router_cli.py`)
  - `src/kllm/shared/schemas.py`
  - `dashboard/src/lib/modules/run/` (`entities.ts`, `interactor.svelte.ts`, `view/RunPage.svelte`, `view/DeploymentPlan.svelte`)
  - `dashboard/src/lib/modules/runtimes/` (`entities.ts`, `interactor.svelte.ts`, `view/PrimaPreparation.svelte`)
  - `tests/` (`test_prima_highs_scheduler.py`, `test_prima_windows.py`, `dashboard/src/lib/modules/run/`)

## Relevant Decisions

- `[D1]` Patch surfaces live on the owning module (`Status: active`, `Source: docs/memory/decisions/2026-10-09-viper-owning-module-patch-surface.md`, `Reason Included: All new HiGHS scheduler helpers and patch targets must live on their owning interactors in src/kllm/modules/primacpp/interactors/`).
- `[D2]` Scope HiGHS scheduling to Prima.cpp planning and preparation while keeping standard llama.cpp RPC on existing capacity-first/priority planning (`Status: active`, `Source: specs/019-highs-scheduler-planning/spec.md#clarifications`, `Reason Included: Defines engine boundary for FR-001`).
- `[D3]` Allow unrestricted Halda memory-pressure sets ($M_1, M_2, M_3$) traded off against resident memory ($M_4$) whenever `mmap` is enabled (`Status: active`, `Source: specs/019-highs-scheduler-planning/spec.md#clarifications`, `Reason Included: Governs MILP constraint matrix rows for memory sets in FR-003`).
- `[D4]` Placement mode `auto` uses HiGHS optimization when complete profiles exist (with automatic fallback to capacity-first when unmeasured), alongside explicit `capacity_first` and `priority` modes (`Status: active`, `Source: specs/019-highs-scheduler-planning/spec.md#clarifications`, `Reason Included: Governs planner dispatch and dashboard selector in FR-011`).

## Active Architecture Constraints

- `[A1]` Structural coding standards and VIPER module layout (`Source: .specify/memory/architecture_constitution.md`, `Reason Included: Python feature code lives in src/kllm/modules/<feature>/ and Svelte 5 dashboard code in dashboard/src/lib/modules/<feature>/`).
- `[A2]` Canonical digest stability on `ExecutionManifest` and `RankAllocation` (`Source: specs/018-cross-platform-planning/memory.md`, `Reason Included: RankAllocation uses extra="forbid" on worker control channels; scheduler metadata must be returned on the plan preview/response and not injected as unknown fields into RankAllocation`).

## Accepted Deviations

- [none]

## Relevant Security Constraints

- `[C1]` Signed participant evidence (`ParticipantEvidence`), storage identity (`StorageEvidence`), and private link receipts (`StreamScope`) must be verified before measured distributed profiles can drive HiGHS scheduling (`Source: .specify/memory/constitution.md`, `Reason Included: Prevents unverified or stale measurements from altering placement`).
- `[C2]` Plan previews must remain strictly read-only and never initiate network profiling, stage model layouts, or write profile caches (`Source: specs/019-highs-scheduler-planning/spec.md`, `Reason Included: Enforces FR-012 preview safety`).

## Related Historical Lessons

- `[B1]` Platform metadata and diagnostic fields must not be added to `RankAllocation` unless omitted when unset, because `control.py` sends `rank.model_dump()` to workers with `extra="forbid"` (`Reason Included: Keep HiGHS schedule diagnostics at the plan/preview level`).
- `[B2]` Segmented placement ($k > 1$ or non-canonical layer windows) requires `segmented_placement` in runtime capabilities plus exact `prima_graphs: True` and `prima_graph_counts` reservations matching `graph_visits(topology)` (`Reason Included: Multi-cycle HiGHS solutions k > 1 must populate exact segments and graph visit reservations`).

## Conflict Warnings

- [none]

## Retrieval Notes

- Reviewed `docs/memory/INDEX.md`, `.specify/memory/constitution.md`, `.specify/memory/architecture_constitution.md`, `.specify/memory/workflow.md`, `specs/018-cross-platform-planning/memory.md`, and `specs/019-highs-scheduler-planning/spec.md`.
- Synthesis word count: ~460 words (well within `max_synthesis_words: 900`).
