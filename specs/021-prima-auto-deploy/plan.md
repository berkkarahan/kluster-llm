# Implementation Plan: Prima Auto-Planning Delegation and One-Click Deploy Flow

**Branch**: `021-prima-auto-deploy` | **Date**: 2026-10-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/021-prima-auto-deploy/spec.md`

---

## Summary

`kluster-llm` currently relies on a manual deployment model where operators must configure context size, split modes, GPU/CPU memory percentages, device filters, and priority nodes, followed by executing a 5-stage preparation and profiling stepper. However, `prima.cpp` natively possesses an internal auto-scheduling engine using the HiGHS Mixed-Integer Linear Programming (MILP) solver (`common/common.cpp:862-1528`), which is also mirrored in Python (`highs_scheduler.py`).

This plan delivers a **feature-flagged one-click deployment flow** (`prima-auto-deploy`):
1. **CLI & Feature Flag Subsystem**: Register `"prima-auto-deploy"` in `src/kllm/shared/config.py`, support `kllm flags prima-auto-deploy [--enable/--disable]`, `kllm run --prima-auto-deploy`, and propagate flag status through `DashboardStatusService` to `/v1/status`, `/v2/cluster/status`, and WebSocket streams. Toggling the flag while an inference workload is active leaves the workload running uninterrupted.
2. **Backend Auto-Planning Delegation & Execution Routing**: When the flag is enabled, perform fast-refusal validation against corrupt/0-byte models (HTTP 400 `PRIMA_MODEL_CORRUPT`), bypass manual `core_placement.py` layer/device fraction constraints, and delegate optimization to the HiGHS scheduler. If memory is insufficient, return HTTP 422 `PRIMA_INSUFFICIENT_CAPACITY`. Check `len(manifest.ranks)`: route single-node deployments (`len(manifest.ranks) == 1`) directly to the single-node runtime coordinator (`runtime.py`) without network/ring overhead, and multi-node deployments (`len(manifest.ranks) >= 2`) to `distributed_runs.py` with 60s automated staging timeouts and artifact cleanup on failure.
3. **Svelte 5 Dashboard One-Click UI**: When the flag is active, conditionally render `PrimaAutoDeployView.svelte` in `PrimaRunPage.svelte`, presenting a clean model selector, cluster readiness badge, error chips (`PRIMA_INSUFFICIENT_CAPACITY`, `PRIMA_MODEL_CORRUPT`), and a single "Deploy Model" button, while preserving 100% of existing manual configuration views, accordions, and 5-stage stepper controls (`PrimaDeploymentShell`) when the flag is disabled, including full stop control over running models.
4. **Verification & Governance**: Ensure strict compliance with `.specify/memory/architecture_constitution.md`, zero edits to `.specify/memory/` or `docs/memory/`, and continuous validation against `tests/test_install_boundary.py`.

---

## Technical Context

- **Language/Version**: Python 3.12 (`src/kllm/`), TypeScript / Svelte 5 with runes (`dashboard/src/lib/`), Rust (`crates/kllm-native/`).
- **Primary Dependencies**: Typer CLI, FastAPI / Starlette, Pydantic, HiGHS solver (`scipy.optimize.linprog` / `highs_scheduler.py`), SvelteKit 2, Vite, `uv`-managed Python environment.
- **Storage**: `~/.kllm/config.toml` (persistent feature flags), SQLite (`profiling.db` for cached profiles), GGUF model files on local storage.
- **Testing**: `uv run python -m pytest -q` (Python unit/integration tests), `npm --prefix dashboard test` (Node test runner for dashboard), `npm --prefix dashboard run check` (SvelteKit type check), `speckit-analyze` (Spec Kit consistency analysis).
- **Target Platform**: Windows (primary development & acceptance host), Linux/macOS compatible.
- **Project Type**: Cross-layer application feature (CLI + Backend Services + Svelte 5 Frontend).
- **Performance Goals**: Zero-overhead delegation; solves optimal layer allocation in $< 100$ ms via HiGHS; maintains maximum possible GPU VRAM utilization across online cluster nodes.
- **Constraints**: 100% backward compatibility when flag is disabled; zero runtime package/binary installations (guarded by `tests/test_install_boundary.py`); module boundaries strictly preserved per architecture constitution.
- **Scale/Scope**: Affects 3 Python backend modules (`shared/config.py`, `settings/`, `inference/`, `cluster/`), 1 dashboard module (`run/`), and test suites.

---

## Constitution Check

*GATE: Must pass before implementation. Re-check after design.*

1. **Owning-Module Patch Surfaces**:
   - Feature flag registry lives in `src/kllm/shared/config.py`.
   - CLI flags commands live in `src/kllm/modules/settings/flags_cli.py`.
   - Status propagation lives in `src/kllm/modules/cluster/presenters/dashboard_status.py`.
   - Auto-planning logic lives in `src/kllm/modules/inference/interactors/run_planning.py` and `src/kllm/modules/primacpp/interactors/planner.py`.
   - Dashboard UI lives in `dashboard/src/lib/modules/run/view/`.
   - No cross-module re-exports or facades added. **PASS**.
2. **Cross-Module Gateways & Constructor Injection**:
   - Dependencies injected into interactors via constructors (`DashboardStatusService`, `InferenceHandlers`, `PrimaDeployment`).
   - No direct imports of `kllm.agent` from inside feature interactors. **PASS**.
3. **Tests Assert Observable Behavior**:
   - New unit tests assert CLI flag persistence, HTTP status responses, auto-planning execution payload, and UI conditional rendering.
   - Negative assertions cover unsupported models and missing flags. **PASS**.
4. **Deterministic & Isolated Tests**:
   - Tests use temporary directories for configuration, synthetic cluster member states, and mocked HiGHS/run fixtures; no live GPUs or remote network connections required for automated regression suites. **PASS**.
5. **Native Trust Boundaries**:
   - No new arbitrary subprocess execution or privilege escalation; native process spawning remains securely managed by `crates/kllm-native/src/process.rs`. **PASS**.
6. **No Runtime Package Installation**:
   - Neither CLI nor agent runs pip, npm install, or downloads unmanaged compilers at runtime. `tests/test_install_boundary.py` remains inviolate. **PASS**.
7. **Governance & Memory Boundary**:
   - No edits made to `.specify/memory/` governance files or durable project memory in `docs/memory/`. Feature state stays strictly within `specs/021-prima-auto-deploy/`. **PASS**.

---

## Project Structure

### Documentation & Specifications (this feature)

```text
specs/021-prima-auto-deploy/
├── memory.md        # Feature scratchpad, locked decisions, code anchors
├── spec.md          # User scenarios, functional requirements (FR-001..FR-015), success criteria (SC-001..SC-008)
├── plan.md          # This implementation plan
└── tasks.md         # Dependency-ordered implementation checklist
```

### Source Code (affected repositories and files)

```text
src/kllm/
├── shared/
│   └── config.py                                      # Register "prima-auto-deploy" in FEATURE_FLAGS
├── modules/
│   ├── settings/
│   │   └── flags_cli.py                              # CLI kllm flags commands (persists to config.toml)
│   ├── cluster/
│   │   └── presenters/dashboard_status.py            # Injects feature_flags into /v1/status and WebSocket
│   ├── inference/
│   │   ├── router_cli.py                             # Adds --prima-auto-deploy to kllm run
│   │   ├── router_http.py                            # Auto-planning route handling and error mapping
│   │   └── interactors/run_planning.py               # Auto-planning pipeline bypass
│   └── primacpp/
│       ├── interactors/planner.py                    # HiGHS auto-planning invocation
│       ├── interactors/highs_scheduler.py            # Mathematical MILP solver
│       └── interactors/distributed_runs.py           # Automated staging and launch dispatch

dashboard/src/lib/
├── modules/
│   ├── cluster/
│   │   └── entities.ts                               # Status.feature_flags interface
│   └── run/
│       ├── interactor.svelte.ts                      # RunInteractor auto-deploy action triggers
│       └── view/
│           ├── PrimaRunPage.svelte                   # Conditional toggle between One-Click and Manual views
│           └── PrimaAutoDeployView.svelte            # NEW: Streamlined model selector & one-click deploy view

tests/
├── test_cli_flags.py                                 # Feature flag CLI unit tests
├── test_prima_auto_deploy.py                         # NEW: Backend auto-planning delegation tests
└── test_install_boundary.py                          # Guard against runtime package installation

dashboard/tests/
└── prima-auto-deploy.test.mjs                        # NEW: Dashboard one-click deploy UI tests
```

---

## Milestones & Execution Phases

### Milestone 0: Spec Kit Specification & Consistency Analysis
- **Goal**: Author complete Spec Kit artifacts in `specs/021-prima-auto-deploy/` and verify cross-artifact consistency.
- **Deliverables**: `memory.md`, `spec.md`, `plan.md`, `tasks.md`.
- **Exit Criteria**: `speckit-analyze` validates 0 critical/high issues, 0 `[NEEDS CLARIFICATION]` markers, 100% requirement-to-task coverage.

### Milestone 1: CLI Feature Flag & Status Propagation
- **Goal**: Register `"prima-auto-deploy"` and wire flag management from CLI through configuration to backend status feeds.
- **Tasks**:
  - Add `"prima-auto-deploy"` to `FEATURE_FLAGS` in `src/kllm/shared/config.py`.
  - Validate flag behavior in `src/kllm/modules/settings/flags_cli.py`.
  - Add `--prima-auto-deploy` option to `kllm run` in `src/kllm/modules/inference/router_cli.py`.
  - Ensure `DashboardStatusService` propagates `feature_flags` dict in `/v1/status`, `/v2/cluster/status`, and live status feeds.
  - Guarantee runtime feature flag toggle lifecycle: toggling `prima-auto-deploy` while an inference run is active does not interrupt workload, and legacy view reconciles and displays active run with working stop control.
  - Add automated test cases in `tests/test_cli_flags.py`.
- **Exit Criteria**: `kllm flags prima-auto-deploy --enable` persists `True`, `/v1/status` reflects `"prima-auto-deploy": true`, runtime toggle preserves active workloads, and pytest passes.

### Milestone 2: Backend Auto-Planning Delegation Engine & Execution Routing
- **Goal**: Enable the backend to delegate layer and device allocation directly to HiGHS when `prima-auto-deploy` is active, bypassing manual constraints, with fast refusal, capacity error contracts, single-node routing, and staging timeouts.
- **Tasks**:
  - Update `src/kllm/modules/inference/interactors/run_planning.py` to recognize `prima_auto_deploy` in execution requests.
  - Add fast-refusal validation in `src/kllm/modules/primacpp/interactors/planner.py` before HiGHS invocation: verify `read_model_layout(model)` is not `None` and `model.stat().st_size > 0`, returning HTTP 400 with detail code `PRIMA_MODEL_CORRUPT` for 0-byte, corrupt, or non-GGUF files (FR-016).
  - Implement the auto-planning pathway in `src/kllm/modules/primacpp/interactors/planner.py`, invoking `solve_highs_halda_schedule` across online cluster members without manual `core_placement.py` layer limits.
  - Handle solver infeasibility: when HiGHS returns `feasible = False` due to memory exhaustion, return HTTP 422 Unprocessable Entity with `detail.code = "PRIMA_INSUFFICIENT_CAPACITY"` and human-readable explanation of required vs available memory.
  - Implement single-node vs distributed execution routing based on `len(manifest.ranks)`:
    - If `len(manifest.ranks) == 1`: Route execution to the single-node Prima runtime coordinator (`src/kllm/modules/primacpp/interactors/runtime.py` / `kllm/agent/runtime.py`) without requiring private network IDs or multi-node ring sockets.
    - If `len(manifest.ranks) >= 2`: Route execution through `distributed_runs.py`.
  - Wire automated staging with network dropout resilience in `src/kllm/modules/primacpp/interactors/distributed_runs.py`: enforce a 60-second connection and transfer timeout per chunk; on network failure or worker disconnect, transition run status to `failed`, clean up temporary staging artifacts, and return `PRIMA_STAGING_FAILED` with the failing `node_id`.
  - Add validation for unsupported model families returning `PRIMA_MODEL_UNSUPPORTED`.
  - Add integration tests in `tests/test_prima_auto_deploy.py` covering single-node coordinator dispatch, multi-node staging, corrupt model fast refusal, and capacity exhaustion error responses.
- **Exit Criteria**: Auto-planning generates valid `PlacementPlan` with HiGHS-optimized layers across online nodes; single-node deployments dispatch without network IDs; unsupported and corrupt models fail immediately without process spawn; staging network failures clean up cleanly; tests pass.

### Milestone 3: Svelte 5 Dashboard One-Click Deploy UI
- **Goal**: Create the streamlined one-click deploy user experience in the Svelte 5 dashboard.
- **Tasks**:
  - Implement `dashboard/src/lib/modules/run/view/PrimaAutoDeployView.svelte` featuring model selection, cluster readiness badge, error chips (`PRIMA_INSUFFICIENT_CAPACITY`, `PRIMA_MODEL_CORRUPT`, `PRIMA_MODEL_UNSUPPORTED`), and primary "Deploy Model" button.
  - In `dashboard/src/lib/modules/run/view/PrimaRunPage.svelte`, conditionally render `PrimaAutoDeployView.svelte` when `cluster.status?.feature_flags?.['prima-auto-deploy']` is true.
  - Preserve the complete baseline 2-tab view and 5-stage stepper (`PrimaDeploymentShell`) when the flag is false, ensuring active runs remain visible with functioning stop control even if the flag is disabled while running.
  - Wire deploy action to `RunInteractor` / `PrimaDeployment` to trigger backend auto-deploy.
  - Add unit and component tests in `dashboard/tests/prima-auto-deploy.test.mjs`.
- **Exit Criteria**: Dashboard check (`npm run check`) reports 0 errors; tests pass; UI switches cleanly based on flag state; error chips render for capacity and corruption rejections.

### Milestone 4: Comprehensive Test Verification & Boundary Hardening
- **Goal**: Execute full regression suites across Python, Rust, and dashboard surfaces.
- **Tasks**:
  - Run `uv run python -m pytest -q tests/test_install_boundary.py`.
  - Run affected Python test suites (`tests/test_cli_flags.py`, `tests/test_prima_auto_deploy.py`, `tests/test_prima_integration.py`).
  - Run dashboard test suite `npm --prefix dashboard test` and type check `npm --prefix dashboard run check`.
  - Verify flag-off backward compatibility ensures 100% baseline test stability.
  - Verify edge cases: corrupt model fast refusal, capacity exhaustion HTTP 422, single-node coordinator dispatch, staging dropout timeout cleanup, and active run flag toggle preservation.
- **Exit Criteria**: All test suites pass with 0 failures; boundary checks pass cleanly.

### Milestone 5: Converge, Documentation & Forensic Audit
- **Goal**: Conduct consistency assessment, update documentation, and prepare audit receipts.
- **Tasks**:
  - Run `speckit-converge` verification confirming 100% completed tasks in `tasks.md`.
  - Update `docs/cli.md` and `docs/control-api-v2.md` to document the new feature flag.
  - Compile audit receipts verifying zero hardcoding, zero facade shortcuts, and genuine implementation logic.
- **Exit Criteria**: Hand-off approved by orchestrator and auditor.

---

## Complexity Tracking

| Architectural Choice | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Registering flag in `FEATURE_FLAGS` tuple in `src/kllm/shared/config.py` | Provides unified persistence in `config.toml`, CLI management, and automated status propagation to dashboard | Hardcoding an environment variable or ad-hoc query parameter would fail to synchronize state with the UI or persist across restarts |
| Delegating to Python HiGHS solver (`highs_scheduler.py`) | Directly produces the exact mathematical optimal layer allocation ($w_m$, $n_m$, $k$) identical to `prima.cpp` native solver while generating a compliant `ExecutionManifest` | Launching `prima.cpp` without any manifest requires rewriting process supervision, port readiness checks, and job tracking |
| Single-node execution routing (`len(manifest.ranks) == 1`) to coordinator (`runtime.py`) | Single-node clusters lack private network IDs and multi-node ring sockets; routing through `distributed_runs.py` triggers invalid manifest assertions | Forcing single-node clusters to configure private networks and ring sockets introduces unnecessary latency and configuration overhead |
| Fast-refusal validation before solver invocation | Validating `read_model_layout` and file size $> 0$ prevents corrupt/0-byte files from falling back to fictitious layer counts (e.g. 32 layers) | Permitting corrupt files to reach the solver causes downstream unhandled exceptions during native process launch or staging |
| Explicit HTTP 422 `PRIMA_INSUFFICIENT_CAPACITY` error contract | Allows the frontend to display dedicated error chips and disable deployment when hardware cannot accommodate the model | Generic 500 internal server errors or uncaught solver exceptions provide poor user experience and leave UI in an indeterminate state |
| Separate component `PrimaAutoDeployView.svelte` with conditional rendering in `PrimaRunPage.svelte` | Clean separation of concerns; isolates new one-click UI logic while leaving legacy multi-tab and stepper components 100% untouched | Interspersing conditional `{#if}` blocks across 600+ lines of existing parameter accordions would create high regression risk |
