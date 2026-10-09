# Tasks: MoE Expert Offload to Scattered Nodes

**Input**: Design documents from `/specs/013-moe-expert-offload/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — the project constitution (Principle II) requires behavior-focused regression checks for non-trivial logic, and `quickstart.md` defines deterministic scenarios.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

---

## Phase 1: Setup

**Purpose**: Verify the pinned runtime supports the flags the feature will emit.

- [X] T001 Verify the pinned `llama-release` build (`v0.5.0`) accepts `--n-cpu-moe` and `--override-tensor`/`-ot` with `RPC[...]` device patterns (research §7). Record the result and, if `-ot` RPC patterns are unsupported, document that the feature falls back to whole-layer remote placement. No code change in this task.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: MoE expert cost modeling and the `moe_offload` option — required by US1 and US2.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Add MoE expert-tensor detection to `src/kllm/gguf_meta.py`: for recognized MoE architectures (`qwen2moe`, `qwen3moe`, `qwen35moe`), bucket per-layer bytes into a shared portion (attention + gate) and an expert portion (`ffn_gate`, `ffn_up`, `ffn_down`, `ffn_gate_inp` tensor bytes), and expose `expert_count` per layer, preserving the existing `weights_bytes` invariant.
- [X] T003 [P] Add `moe_offload: AutoInt` (default `"auto"`, integer must be `>= 0`) to `src/kllm/run_settings.py` `RunSettingsFields`, include it in `normalized_auto_fields`/`snapshot_for_remember`, add model validation rejecting negative/non-integer values, and expose it as `--moe-offload` on `kllm plan`/`run` in `src/kllm/commands/inference.py` (passing through `src/kllm/cli_payload.py`).
- [X] T004 [P] Extend `src/kllm/core/memory_estimation.py` to emit per-layer shared vs expert costs (non-negative, summing to existing totals) so the planner can price expert weights separately from whole layers.
- [X] T005 Wire `moe_offload` into `src/kllm/run_settings_resolution.py` `resolve_launch_settings` and add the field to `src/kllm/models/resolved_settings.py` `ResolvedLaunchSettings` (round-trip in `from_mapping`/`to_mapping`).
- [X] T006 [P] Add failing regressions in `tests/test_gguf_meta.py` and `tests/test_memory_estimation.py` for the MoE tensor split and shared/expert cost outputs (fail before implementation; assert expert bytes > 0 and `weights_bytes` unchanged, and that a non-MoE layout yields no expert split).

**Checkpoint**: Expert cost model and option plumbing ready — US1/US2 can begin.

---

## Phase 3: User Story 1 - Automatic MoE expert offload (Priority: P1) 🎯 MVP

**Goal**: When an MoE model does not fit a node's GPU memory, automatically distribute expert weights across remote nodes (GPU first, then RAM), keeping shared weights local; report the distribution and emit planner-owned flags.

**Independent Test**: `uv run pytest -q tests/test_cluster_optimizations.py` — a multi-node fixture where the master GPU cannot hold the model now succeeds with `expert_offloaded is True` and expert bytes on remote nodes.

### Tests for User Story 1

- [X] T007 [P] [US1] Add deterministic placement tests in `tests/test_cluster_optimizations.py`: expert-only placement across remote nodes (GPU before RAM), `expert_strategy` values, and overflow-with-hint when even experts do not fit (see `quickstart.md` Scenario B).

### Implementation for User Story 1

- [X] T008 [US1] Add `expert_offloaded`, `expert_strategy`, and `expert_offload_count` reporting fields (defaults `False`/`"none"`/`None`) to `src/kllm/core/placement_models.py` `DevicePlacementPlan`, keeping `estimated_bytes`/`fit_status` consistent with expert bytes folded into `node_vram_alloc_bytes`/`node_ram_alloc_bytes`.
- [X] T009 [US1] In `src/kllm/core/placement.py` `plan_devices`, implement the expert-first fallback for MoE layouts: when the existing GPU/local/single-remote/pool attempts overflow, allocate shared portions to the master GPU and expert portions to remote nodes (GPU memory first, then RAM), before whole-layer CPU spill; set the `expert_*` fields accordingly.
- [X] T010 [US1] Emit planner-owned offload flags from `src/kllm/run_launch.py` `build_master_launch`: `-ot` patterns assigning expert tensors to `RPC[...]`/CPU devices, and `--n-cpu-moe` for host-CPU expert spill; keep `--n-cpu-moe`/`--cpu-moe`/`-ot` blocked in `src/kllm/extra_args.py`.
- [X] T011 [US1] Add option-resolution + launch-arg contract tests in `tests/test_plan_run_params.py` (see `quickstart.md` Scenario C): `moe_offload="auto"` and `moe_offload=3` both resolve; planner flags present; user `extra_args` override of `-ot`/`--n-cpu-moe` still rejected; and the emitted `-ot`/`--n-cpu-moe` args match the plan's `expert_*` fields (plan==launch, FR-006/SC-002).

**Checkpoint**: US1 works independently — an MoE model that overflows a node now offloads experts automatically.

---

## Phase 4: User Story 2 - MoE offload in partial/manual configuration (Priority: P2)

**Goal**: The offload option also works under manual node shares and partial GPU offload (model partially on GPU, partially on system RAM).

**Independent Test**: `uv run pytest -q tests/test_cluster_optimizations.py tests/test_plan_run_params.py` — manual node-share fixtures with `moe_offload` distribute expert weights per the operator's shares; partial-GPU fixtures spill correctly.

### Tests for User Story 2

- [X] T012 [P] [US2] Add tests for `moe_offload` under manual node shares and partial GPU offload in `tests/test_cluster_optimizations.py` and `tests/test_plan_run_params.py`, including the "offload disabled" path that must match current behavior (spec US2 acceptance scenarios).

### Implementation for User Story 2

- [X] T013 [US2] Extend the manual node-share branch in `src/kllm/core/placement.py` `_allocate_manual`/`plan_devices` so expert portions follow the operator's share intent when `moe_offload` is set, and keep the existing no-offload path unchanged.
- [X] T014 [US2] Ensure the partial GPU-offload path (GPU + CPU spill) considers expert offload to remote nodes before whole-layer CPU spill, matching `moe-offload.md` contract ordering.

**Checkpoint**: US1 and US2 both work independently; offload disabled behaves as before.

---

## Phase 5: User Story 3 - Larger batch and micro-batch ceilings (Priority: P3)

**Goal**: Raise configurable batch (to 2048) and micro-batch (to 512) ceilings while keeping defaults at 512 / 128.

**Independent Test**: `uv run pytest -q tests/test_memory_estimation.py tests/test_plan_run_params.py` — see `quickstart.md` Scenario D.

### Tests for User Story 3

- [X] T015 [P] [US3] Add regressions for batch/micro-batch ceilings in `tests/test_memory_estimation.py` and `tests/test_plan_run_params.py`: `batch_size=2048` and `micro_batch=512` accepted at small context; `micro_batch` > 512 or > `batch_size` capped (effective equals the cap); non-positive values rejected with a clear error; omitted values resolve to 512/128.

### Implementation for User Story 3

- [X] T016 [US3] Update `src/kllm/run_settings_resolution.py` `resolve_launch_settings` to raise the batch ceiling floor from 512 to 2048 (`min(requested, max(2048, context))`) and cap micro-batch at `min(requested, batch_size, 512)`, preserving non-positive rejection.
- [X] T017 [US3] Update `dashboard/src/routes/run/+page.svelte` batch/micro-batch input `max` attributes and validation to match 2048 / 512 so the UI cannot submit values the backend rejects; run `npm --prefix dashboard run check`.

**Checkpoint**: All three user stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Docs and validation that span the feature.

- [X] T018 [P] Update `docs/cli.md` (`--moe-offload`, batch/micro-batch ceilings) and `README.md` guidance to match the implemented interface; verify referenced paths/commands.
- [X] T019 [P] Add the dashboard offload control to `dashboard/src/routes/run/+page.svelte` (auto/off/on with optional count), and run `npm --prefix dashboard run check` + `npm --prefix dashboard run build`.
- [X] T020 Run the full local suites (`uv run pytest -q`, `npm --prefix dashboard run check`) and record results; do not report hardware-dependent MoE behavior as validated — list it as pending per `quickstart.md` Scenario E (Constitution III).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup; BLOCKS US1 and US2.
- **US1 (Phase 3)**: After Foundational.
- **US2 (Phase 4)**: After Foundational; integrates with US1.
- **US3 (Phase 5)**: After Foundational (independent of US1/US2).
- **Polish (Phase 6)**: After all stories.

### User Story Dependencies

- **US1 (P1)**: after Foundational — no dependency on other stories.
- **US2 (P2)**: after Foundational — reuses US1's placement/launch machinery.
- **US3 (P3)**: after Foundational — independent (batch ceilings).

### Within Each User Story

- Tests written and failing before implementation.
- Cost model / models before planner/launch changes.
- Story complete before moving to next priority.

### Parallel Opportunities

- T002, T003, T004, T006 (Foundational) run in parallel (different files).
- US1 and US3 can be implemented in parallel after Foundational.
- T007/T011 and T012/T015 test tasks are parallelizable within their stories.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 (T001) + Phase 2 (T002–T006).
2. Phase 3 (T007–T011).
3. **STOP and VALIDATE**: run `uv run pytest -q tests/test_cluster_optimizations.py tests/test_plan_run_params.py`.
4. Demo automatic MoE expert offload.

### Incremental Delivery

1. Foundational → US1 (MVP) → US2 (manual/partial) → US3 (batch ceilings) → Polish.

---

## Notes

- [P] tasks touch different files with no dependencies.
- [Story] labels map tasks to spec user stories.
- Verify tests fail before implementing (Constitution II).
- Record verification commands and results; hardware-dependent MoE behavior stays explicitly unverified until run on the pinned build (Constitution III).
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence.
