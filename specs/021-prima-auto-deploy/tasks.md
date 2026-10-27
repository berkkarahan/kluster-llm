# Tasks: Prima Auto-Planning Delegation and One-Click Deploy Flow

**Input**: Design documents from `specs/021-prima-auto-deploy/`  
**Prerequisites**: [spec.md](./spec.md), [plan.md](./plan.md)  
**Governance**: `.specify/memory/constitution.md`, `.specify/memory/architecture_constitution.md`

---

## Organization & Task Format

- **Format**: `[ ] Txxx [P?] [Story] Description`
- **Markers**:
  - `[P]`: Can be executed in parallel (touches different files, no dependency on incomplete tasks).
  - `[Story]`: Maps to User Stories: `[US1]` One-Click Deploy, `[US2]` Feature Flag & Status, `[US3]` Legacy Coexistence.

---

## Phase 1: M0 — Specification and Consistency Analysis

- [x] T001 Create feature directory `specs/021-prima-auto-deploy/` and author `memory.md` recording locked decisions, codebase anchors, and non-goals.
- [x] T002 Author `specs/021-prima-auto-deploy/spec.md` with user stories, acceptance scenarios (Given/When/Then), functional requirements (FR-001..FR-016), success criteria (SC-001..SC-008), and zero `[NEEDS CLARIFICATION]` markers.
- [x] T003 Author `specs/021-prima-auto-deploy/plan.md` detailing technical context, architecture constitution check, project structure, milestone phases, and complexity tracking.
- [x] T004 Run `speckit-analyze` cross-artifact validation across `spec.md`, `plan.md`, and `tasks.md` to confirm zero critical/high consistency issues and 100% requirement coverage.

---

## Phase 2: M1 — CLI Feature Flag & Status Propagation Subsystem (US2)

- [x] T005 [US2] Register `"prima-auto-deploy"` in the `FEATURE_FLAGS` tuple in `src/kllm/shared/config.py` (FR-001).
- [x] T006 [P] [US2] Validate `src/kllm/modules/settings/flags_cli.py` to ensure `kllm flags prima-auto-deploy [--enable/--disable]` manages the new flag cleanly in `~/.kllm/config.toml` (FR-002, SC-001).
- [x] T007 [P] [US2] Add optional `--prima-auto-deploy / --no-prima-auto-deploy` option to `kllm run` in `src/kllm/modules/inference/router_cli.py` to allow per-invocation flag overrides (FR-003).
- [x] T008 [US2] Update `src/kllm/modules/cluster/presenters/dashboard_status.py` to include `feature_flags: self._feature_flags()` in `/v1/status`, `/v2/cluster/status`, and WebSocket snapshots (FR-004, SC-002).
- [x] T009 [US2] Add unit tests in `tests/test_cli_flags.py` covering `kllm flags prima-auto-deploy` enable, disable, listing, and invalid flag rejection (FR-002, SC-001, SC-006).
- [x] T010 [US2] Add test assertions in `tests/test_dashboard_api.py` validating that `/v1/status` and `/v2/cluster/status` reflect the persistent state of `prima-auto-deploy`, and that toggling the flag at runtime does not interrupt active inference runs (FR-004, FR-011, SC-002, SC-006).

---

## Phase 3: M2 — Backend Auto-Planning Delegation Engine (US1, US2, US3)

- [x] T011 [US1] Extend request payload models in `src/kllm/modules/inference/router_http.py` to accept optional `prima_auto_deploy: bool` (FR-005).
- [x] T012 [US1] Implement the auto-planning delegation branch in `src/kllm/modules/inference/interactors/run_planning.py`, routing requests with `prima_auto_deploy=True` to the automated scheduler (FR-005, FR-006).
- [x] T013 [US1] Update `src/kllm/modules/primacpp/interactors/planner.py` to implement `auto_plan()`, bypassing `core_placement.py` manual layer/device limits and invoking `solve_highs_halda_schedule` across online cluster members; return HTTP 422 with detail code `PRIMA_INSUFFICIENT_CAPACITY` when `feasible` is False (FR-005, FR-006, FR-007, SC-003, SC-005).
- [x] T014 [US1] Connect `solve_highs_halda_schedule` in `src/kllm/modules/primacpp/interactors/highs_scheduler.py` to optimize layer distribution ($w_m$), GPU offloads ($n_m$), and cycle count ($k$) for maximum throughput within memory budgets (FR-005, FR-006).
- [x] T015 [US1] Update `src/kllm/modules/primacpp/interactors/distributed_runs.py` and execution dispatch to check `len(manifest.ranks)`: if `len(manifest.ranks) == 1`, route execution to the single-node Prima runtime coordinator (`src/kllm/modules/primacpp/interactors/runtime.py` / `kllm/agent/runtime.py`) without requiring private network IDs; if `len(manifest.ranks) >= 2`, route through `distributed_runs.py`. Enforce a 60-second connection and transfer timeout per chunk during worker staging, cleaning up temporary artifacts and returning `PRIMA_STAGING_FAILED` with the failing `node_id` on network dropout (FR-008, SC-003).
- [x] T016 [US1] Wire unsupported model family checks in `src/kllm/modules/primacpp/interactors/planner.py` to immediately return `PRIMA_MODEL_UNSUPPORTED` before rank processes spawn (FR-013, SC-005).
- [x] T016b [US1] Add fast-refusal validation in `src/kllm/modules/primacpp/interactors/planner.py` before HiGHS invocation, verifying `read_model_layout(model)` is not `None` and `model.stat().st_size > 0`, returning HTTP 400 with detail code `PRIMA_MODEL_CORRUPT` for 0-byte, corrupt, or non-GGUF files (FR-016, SC-005).
- [x] T017 [US3] Verify backward compatibility in `run_planning.py` and `planner.py` ensuring that when `prima_auto_deploy` is False or omitted, the legacy manual placement path (`candidates`, `execute_plan`) executes unmodified (FR-011, FR-012, SC-004).
- [x] T018 [P] [US1] Create `tests/test_prima_auto_deploy.py` with integration tests for auto-planning execution, HiGHS layer allocation, automated staging, and explicit single-node auto-deploy routing (`len(manifest.ranks) == 1`) to coordinator vs distributed runs (`len(manifest.ranks) >= 2`) (FR-005, FR-006, FR-008, SC-003, SC-006).
- [x] T019 [P] [US1] Add test cases in `tests/test_prima_auto_deploy.py` asserting immediate refusal with `PRIMA_MODEL_UNSUPPORTED` for unsupported architectures, `PRIMA_MODEL_CORRUPT` for 0-byte/corrupt files, and HTTP 422 `PRIMA_INSUFFICIENT_CAPACITY` when cluster memory is exhausted (FR-013, FR-016, SC-005, SC-006).

---

## Phase 4: M3 — Svelte 5 Dashboard One-Click Deploy UI (US1, US3)

- [x] T020 [US1] Verify and extend `dashboard/src/lib/modules/cluster/entities.ts` to include `feature_flags?: Record<string, boolean>` on `Status` entity (FR-004).
- [x] T021 [US1] Create `dashboard/src/lib/modules/run/view/PrimaAutoDeployView.svelte` featuring a clean model selector dropdown, cluster auto-planning readiness badge, and primary "Deploy Model" button (FR-009, FR-010, SC-003).
- [x] T022 [US1] Implement cluster auto-planning readiness badge in `PrimaAutoDeployView.svelte` showing online participating nodes, detected GPUs, and aggregate VRAM (FR-010).
- [x] T023 [US1] Implement error chip rendering and disabled deploy button state in `PrimaAutoDeployView.svelte` when an active run is in flight, when the model is unsupported (`PRIMA_MODEL_UNSUPPORTED`), corrupt (`PRIMA_MODEL_CORRUPT`), or when cluster capacity is insufficient (`PRIMA_INSUFFICIENT_CAPACITY`) (FR-009, FR-013, FR-016, SC-005).
- [x] T024 [US1] Wire the "Deploy Model" action to `RunInteractor` / `PrimaDeployment` to dispatch auto-planned execution requests with `prima_auto_deploy: true` (FR-009, SC-003).
- [x] T025 [US1] [US3] In `dashboard/src/lib/modules/run/view/PrimaRunPage.svelte`, conditionally render `PrimaAutoDeployView.svelte` when `cluster.status?.feature_flags?.['prima-auto-deploy']` is true (FR-009, FR-011, SC-003, SC-004).
- [x] T026 [US3] In `dashboard/src/lib/modules/run/view/PrimaRunPage.svelte`, strictly preserve the complete baseline 2-tab view and 5-stage stepper when the flag is false (FR-011, FR-012, SC-004).
- [x] T026b [US2] [US3] Ensure `PrimaDeploymentShell.svelte` displays running model status and functioning stop button when an active auto-deployed run is in flight even after `prima-auto-deploy` is toggled off at runtime (FR-011, FR-012, SC-004).
- [x] T027 [P] [US1] [US3] Create `dashboard/tests/prima-auto-deploy.test.mjs` verifying conditional rendering of `PrimaAutoDeployView` when the flag is enabled, error chip rendering for `PRIMA_INSUFFICIENT_CAPACITY`, and preservation of running model stop control in `PrimaDeploymentShell` when disabled (FR-009, FR-011, SC-003, SC-004, SC-006).

---

## Phase 5: M4 — Comprehensive Automated Testing & Boundary Verification (US1, US2, US3)

- [x] T028 [US1] [US2] [US3] Run full Python test suites covering affected modules (`uv run python -m pytest -q tests/test_cli_flags.py tests/test_prima_auto_deploy.py tests/test_prima_integration.py`) (SC-006).
- [x] T029 [US1] [US2] [US3] Run dashboard automated tests (`npm --prefix dashboard test`) ensuring 100% test pass rate across new and existing test suites (SC-006).
- [x] T030 [US1] [US2] [US3] Run Svelte 5 type check (`npm --prefix dashboard run check`) to verify zero TypeScript errors or broken runes (SC-006).
- [x] T031 Run install boundary guard `uv run python -m pytest -q tests/test_install_boundary.py` confirming zero runtime package or binary installations (FR-015, SC-008).
- [x] T032 Verify compliance with `.specify/memory/architecture_constitution.md` via `tests/test_architecture_boundaries.py` (FR-014).
- [x] T033 Verify zero modifications to `.specify/memory/` and `docs/memory/` using `git status` (SC-008).
- [x] T034 Execute adversarial edge case verification (invalid flag names, active running model collision, corrupt model fast refusal, capacity exhaustion HTTP 422, single-node coordinator dispatch, staging dropout timeout cleanup, and active run flag toggle preservation) (FR-002, FR-005, FR-008, FR-011, FR-013, FR-016, SC-005).

---

## Requirements Traceability Matrix

| Requirement / Criterion Key | Description | Covered by Task(s) | Status |
|---|---|---|---|
| **FR-001** | Register `"prima-auto-deploy"` in `FEATURE_FLAGS` tuple | T005 | Complete |
| **FR-002** | CLI `kllm flags prima-auto-deploy [--enable/--disable]` persistence | T006, T009, T034 | Complete |
| **FR-003** | CLI `kllm run --prima-auto-deploy` invocation override | T007 | Complete |
| **FR-004** | Status propagation of `feature_flags` dict to `/v1/status` & WebSocket | T008, T010, T020 | Complete |
| **FR-005** | Backend auto-planning delegation to HiGHS solver & HTTP 422 capacity error | T011, T012, T013, T014, T018, T034 | Complete |
| **FR-006** | Online cluster participant discovery without manual layer limits | T012, T013, T014, T018 | Complete |
| **FR-007** | Bypass manual `core_placement.py` capacity reservation limits | T013 | Complete |
| **FR-008** | Automated staging & manifest dispatch (single-node coordinator or multi-node with 60s timeout) | T015, T018, T034 | Complete |
| **FR-009** | Svelte 5 dashboard one-click deploy view (`PrimaAutoDeployView`) with error chips | T021, T023, T024, T025, T027 | Complete |
| **FR-010** | Cluster auto-planning readiness badge (nodes, GPUs, VRAM) | T021, T022 | Complete |
| **FR-011** | Full preservation of baseline deployment interface & active workload toggle preservation | T010, T017, T025, T026, T026b, T027 | Complete |
| **FR-012** | Invariant legacy CLI and backend deployment behavior when flag off | T017, T026, T026b | Complete |
| **FR-013** | Immediate refusal of unsupported model families (`PRIMA_MODEL_UNSUPPORTED`) | T016, T019, T023, T034 | Complete |
| **FR-014** | Strict compliance with `.specify/memory/architecture_constitution.md` | T032 | Complete |
| **FR-015** | Strict preservation of install boundary (`tests/test_install_boundary.py`) | T031 | Complete |
| **FR-016** | Fast-refusal validation for corrupt/0-byte/non-GGUF models (`PRIMA_MODEL_CORRUPT`) | T016b, T019, T023, T034 | Complete |
| **SC-001** | CLI flag persistence verified via `kllm flags` | T006, T009 | Complete |
| **SC-002** | Backend status propagation verified via `/v1/status` and `/v2/cluster/status` | T008, T010 | Complete |
| **SC-003** | End-to-end one-click deploy reaches active serving with single/multi-node routing | T013, T015, T018, T021, T024, T025, T027 | Complete |
| **SC-004** | 100% legacy deployment parameters and stepper views preserved with 0 regressions | T017, T025, T026, T026b, T027 | Complete |
| **SC-005** | Unsupported, corrupt, or capacity-exhausted models refused with zero rank processes spawned | T013, T016, T016b, T019, T023, T034 | Complete |
| **SC-006** | 100% test pass rate across new and affected unit/integration tests | T009, T010, T018, T019, T027, T028, T029, T030 | Complete |
| **SC-007** | Spec Kit consistency verification completes with zero critical/high issues | T004 | Complete |
| **SC-008** | Install boundary test passes and zero edits to `.specify/memory/` or `docs/memory/` | T031, T033 | Complete |

---

## Dependencies & Execution Sequence

1. **Phase 1 (M0)** gates all subsequent phases.
2. **Phase 2 (M1)** provides the foundation for feature flag persistence and status propagation.
3. **Phase 3 (M2)** implements backend auto-planning delegation and can proceed once M1's flag registry is available.
4. **Phase 4 (M3)** builds the Svelte 5 one-click UI, depending on M1 status propagation and M2 backend contracts.
5. **Phase 5 (M4)** runs comprehensive verification across all modified modules.
6. **Phase 6 (M5)** closes out documentation, consistency checks, and audit receipts.
