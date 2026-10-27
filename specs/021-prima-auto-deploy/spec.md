# Feature Specification: Prima Auto-Planning Delegation and One-Click Deploy Flow

**Feature Branch**: `021-prima-auto-deploy`

**Created**: 2026-10-27

**Status**: Draft

**Input**: User description from `ORIGINAL_REQUEST.md` (section `## 2026-10-27T10:00:56Z`): "Use the Spec Kit workflow to specify, clarify, plan, and implement (both frontend and backend) a new feature-flagged flow in `kluster-llm` that delegates resource and topology planning to `prima.cpp` so end users only select a model and click deploy. First iteration's goal is a one-click deploy achieving the best performance available across the given cluster nodes while keeping existing deployment flows completely intact when the feature flag is off."

---

## Clarifications

### Session 2026-10-27

- **Q: What is the exact feature flag name and where is it registered?**  
  **A**: The flag is named `"prima-auto-deploy"`. It is registered in the immutable `FEATURE_FLAGS` tuple in `src/kllm/shared/config.py`. It is managed via `kllm flags prima-auto-deploy [--enable/--disable]`, persisted in `~/.kllm/config.toml`, and can be overridden per invocation using `kllm run <model> --prima-auto-deploy`.

- **Q: What is the default state of the feature flag and what is the backward compatibility contract?**  
  **A**: The flag defaults to disabled (`False`). When disabled, 100% of existing CLI commands, backend endpoints, deployment options, manual layer/device fraction controls, and Svelte 5 dashboard views (including the 2-tab configuration interface and the 5-stage Prepare & Profile stepper) behave identically to baseline without regression.

- **Q: How does the backend propagate the feature flag state to the dashboard frontend?**  
  **A**: `DashboardStatusService` in `src/kllm/modules/cluster/presenters/dashboard_status.py` queries `kllm_config.feature_flags()` and includes `feature_flags: dict[str, bool]` in the response payload for `/v1/status`, `/v2/cluster/status`, and live WebSocket event snapshots. The dashboard receives this payload and binds it reactively to `app.cluster.status.feature_flags`.

- **Q: How does auto-planning allocate layers, GPU offloads, and devices without manual user input?**  
  **A**: When the flag is active, the backend delegates optimization to `prima.cpp`'s native HiGHS Mixed-Integer Linear Programming (MILP) scheduler (`common/common.cpp:862` / `src/kllm/modules/primacpp/interactors/highs_scheduler.py`). The solver discovers all online, healthy cluster participants, analyzes available VRAM and RAM budgets, and computes the mathematically optimal layer allocation window ($w_m$), GPU offload count ($n_m$), and pipeline ring cycle parameter ($k$) that minimizes token generation step latency, bypassing manual `core_placement.py` layer constraints.

- **Q: How does the dashboard UI represent the one-click deploy experience in Iteration 1?**  
  **A**: In `dashboard/src/lib/modules/run/view/PrimaRunPage.svelte`, when `cluster.status?.feature_flags?.['prima-auto-deploy']` is true, the UI renders `PrimaAutoDeployView.svelte` instead of the multi-tab parameter accordion. The view presents a clean model selector dropdown, a cluster readiness summary badge (nodes, GPUs, available memory), and a single primary "Deploy Model" button. Clicking the button initiates background asset verification/sync and launches the model without requiring manual stage progression.

- **Q: How are unsupported model families handled during one-click deployment?**  
  **A**: If the operator selects a model whose architecture is not supported by Prima (e.g., `kolibri1`), the backend immediately rejects the request with `PRIMA_MODEL_UNSUPPORTED`. The dashboard surfaces this diagnostic directly in the UI and disables the "Deploy Model" action before any native rank process is created.

- **Q: How does auto-deploy execute when only a single node is present in the cluster?**  
  **A**: When HiGHS produces a manifest with `len(manifest.ranks) == 1`, auto-deploy routes execution directly to the single-node Prima runtime coordinator (`src/kllm/modules/primacpp/interactors/runtime.py` / `kllm/agent/runtime.py`), bypassing private network ID checks, peer handshakes, and multi-node ring data sockets. When `len(manifest.ranks) >= 2`, execution routes through `distributed_runs.py` across the active cluster network transport.

- **Q: How does auto-deploy handle corrupted, 0-byte, or non-GGUF model files?**  
  **A**: Before invoking HiGHS scheduling, the backend validates that `read_model_layout(model)` is not `None` and `model.stat().st_size > 0`. If the file is 0-byte, corrupt, or lacks a valid GGUF header, the API immediately returns HTTP 400 with detail code `PRIMA_MODEL_CORRUPT`, preventing fallback to fictitious layer counts (e.g., 32 layers).

- **Q: What error is returned and how does the UI react when cluster capacity is insufficient to host a model?**  
  **A**: If total available cluster RAM and VRAM cannot accommodate the model weights and minimal context, HiGHS returns `feasible = False`. The backend returns HTTP 422 Unprocessable Entity with `detail.code = "PRIMA_INSUFFICIENT_CAPACITY"` and a human-readable explanation of required vs available memory. The dashboard surfaces an error chip ("Insufficient cluster capacity") and disables the "Deploy Model" button.

- **Q: How does automated staging handle worker disconnects or network transfer timeouts?**  
  **A**: Automated worker staging enforces a 60-second connection and transfer timeout per chunk. On network dropout or worker failure, the run status transitions to `failed`, temporary staging artifacts on workers and master are cleaned up, and the backend returns `PRIMA_STAGING_FAILED` with the failing `node_id`.

- **Q: What happens if an operator toggles the feature flag while an auto-deployed inference run is active?**  
  **A**: Toggling `prima-auto-deploy` while an inference run is active MUST NOT terminate or interrupt the running workload. The active deployment remains fully operational, and `PrimaDeploymentShell` (legacy manual view) MUST display the running model status and allow stopping it even when the flag is disabled.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — One-Click Deployment with Native Auto-Planning (Priority: P1)

An operator wants to deploy a model across the cluster without configuring manual layer counts, device fractions, context splits, or stepping through a 5-stage preparation wizard. With the `prima-auto-deploy` feature flag enabled, the operator opens the dashboard Run page, selects an available local GGUF model, and clicks "Deploy Model". Kluster automatically determines the optimal layer placement and GPU offload across all active nodes using the HiGHS solver, synchronizes necessary assets, and launches the distributed inference run directly into serving readiness.

**Why this priority**: Delivering an autonomous, zero-configuration deployment experience is the primary goal of the feature. Today, operators must possess deep knowledge of GPU VRAM capacity, layer sizes, and network topology to deploy models across nodes without triggering capacity or placement errors.

**Independent Test**: Enable the feature flag, select an available GGUF model in the dashboard or CLI, execute one-click deploy, and verify that the run reaches `inference_up = true` with optimal layer distribution across online nodes without manual parameter input.

**Acceptance Scenarios**:

1. **Given** `prima-auto-deploy` is enabled and an online cluster has 1 master and 1 worker, **When** the operator selects a model and clicks "Deploy Model", **Then** the backend bypasses manual placement constraints, solves optimal layer and GPU allocations via HiGHS, automatically verifies and stages model assets, launches native rank processes, and reaches active serving status.
2. **Given** a single-node cluster with 1 Vulkan GPU, **When** the operator initiates auto-deploy, **Then** HiGHS allocates the maximum feasible layers to the GPU, places remaining layers on CPU, assigns rank 0 as standalone coordinator, and routes execution to the single-node runtime coordinator without requiring private network IDs or multi-node ring sockets.
3. **Given** an active cluster serving inference, **When** the operator views the auto-deploy interface, **Then** the "Deploy Model" action is disabled and displays a clear message stating that the active deployment must be stopped before launching a new model.

---

### User Story 2 — CLI Feature Flag Management and Status Propagation (Priority: P1)

A cluster administrator wants to control the availability of the auto-deployment flow dynamically and inspect its state across the CLI and dashboard. The administrator enables or disables the flag using `kllm flags prima-auto-deploy [--enable/--disable]`, verifies the persistent state in `~/.kllm/config.toml`, and observes immediate live status synchronization in the dashboard without restarting the backend daemon.

**Why this priority**: Clean, non-destructive gating is mandatory to prevent disruption of existing production workflows and to guarantee seamless rollback.

**Independent Test**: Execute `kllm flags prima-auto-deploy --enable`, verify output and configuration file persistence, query `/v1/status` and `/v2/cluster/status` to confirm `feature_flags['prima-auto-deploy'] === true`, then disable and confirm immediate reversion.

**Acceptance Scenarios**:

1. **Given** a default installation, **When** the administrator runs `kllm flags`, **Then** `"prima-auto-deploy = disabled"` is printed to console and `get_feature_flag("prima-auto-deploy")` evaluates to `False`.
2. **Given** an operator running `kllm flags prima-auto-deploy --enable`, **When** the command completes, **Then** `~/.kllm/config.toml` records the updated flag, and `/v2/cluster/status` immediately reports `"feature_flags": {"prima-auto-deploy": true}`.
3. **Given** the flag is disabled globally in config, **When** the operator executes `kllm run <model> --prima-auto-deploy`, **Then** the request payload carries `prima_auto_deploy = true`, triggering auto-planning for that specific invocation without mutating global configuration.
4. **Given** an active auto-deployed inference run is serving requests (`status.inference_up == true`), **When** the operator disables the feature flag via `kllm flags prima-auto-deploy --disable`, **Then** the active deployment remains fully operational without interruption, and the dashboard legacy manual view (`PrimaDeploymentShell`) displays the running model status and allows the operator to stop the workload.

---

### User Story 3 — Non-Destructive Legacy Deployment Coexistence (Priority: P2)

An advanced operator or automated test suite requires manual control over layer boundaries, context length, KV cache types, and the step-by-step 5-stage Prepare & Profile stepper. When `prima-auto-deploy` is disabled (the default setting), the operator navigates to the Run view and finds the complete, unaltered multi-tab configuration interface with all sliders, LoRA options, and stage retry buttons operating identically to baseline.

**Why this priority**: Preserving existing capabilities and preventing regressions in manual deployment workflows, expert tuning, and existing regression tests is a mandatory project invariant.

**Independent Test**: With the flag disabled, execute the full baseline deployment and profiling test suites (`tests/test_prima_deployment_review.py`, `dashboard/tests/run-planning.test.mjs`), confirming 100% test pass rate and identical UI rendering.

**Acceptance Scenarios**:

1. **Given** `prima-auto-deploy` is disabled, **When** the operator accesses the dashboard Run page, **Then** `PrimaRunPage.svelte` renders the full 2-tab view ("Deployment settings" and "Next deployment") with all parameter accordions and stepper controls visible.
2. **Given** `prima-auto-deploy` is disabled, **When** a client sends a standard `POST /v2/inference/runs` request without the auto-deploy flag, **Then** the backend executes standard manual plan validation (`core_placement.py`), requiring pre-approved preparation manifests.
3. **Given** any existing automated pytest or dashboard test suite, **When** executed against the default flag-off configuration, **Then** all tests pass with zero assertion failures or interface breakage.

---

## Edge Cases

- **Unsupported Model Architecture**: If the operator selects a GGUF whose architecture is not supported by `prima.cpp` (e.g. `kolibri1`), the auto-deploy planning request immediately fails with HTTP 400 and detail code `PRIMA_MODEL_UNSUPPORTED`. The UI displays a clear error chip and blocks deployment without spawning rank processes.
- **Corrupt, 0-Byte, or Non-GGUF Model Files**: Before invoking HiGHS planning, the engine validates that `read_model_layout(model)` is not `None` and `model.stat().st_size > 0`. If the file is 0-byte, corrupt, or lacks a valid GGUF header, the request immediately rejects with HTTP 400 and detail code `PRIMA_MODEL_CORRUPT`, preventing fallback to default layer heuristics (e.g. 32 layers) and blocking rank process creation.
- **Insufficient Cluster Capacity**: When total available cluster RAM and VRAM cannot accommodate the model weights and minimal context (HiGHS returns `feasible = False`), the backend returns HTTP 422 Unprocessable Entity with `detail.code = "PRIMA_INSUFFICIENT_CAPACITY"` and a human-readable explanation of required vs available memory. The UI displays an error chip ("Insufficient cluster capacity") and disables the "Deploy Model" button.
- **Single-Node vs Multi-Node Execution Routing**: When `len(manifest.ranks) == 1`, auto-deploy routes execution directly to the single-node Prima runtime coordinator (`src/kllm/modules/primacpp/interactors/runtime.py` / `kllm/agent/runtime.py`) without requiring private network IDs, peer handshakes, or ring data sockets. When `len(manifest.ranks) >= 2`, execution routes through `distributed_runs.py` over the active network transport.
- **Heterogeneous Hardware and Weak Devices**: When nodes have disparate computing or memory capacities (e.g., one node with 24 GB VRAM and one node with 4 GB VRAM), HiGHS packs layers onto the high-capacity GPU and allocates minimal or zero layers to weak nodes, preventing slow nodes from bottlenecking the pipeline ring.
- **Automated Staging Network Failure & Timeout Recovery**: During automated staging to worker nodes, transfer enforces a 60-second connection and transfer timeout per chunk. On network dropout or worker node failure, the run status transitions to `failed`, temporary staging artifacts on workers and master are cleaned up, and the backend returns `PRIMA_STAGING_FAILED` with the failing `node_id`.
- **Runtime Feature Flag Toggle During Active Inference**: Toggling `prima-auto-deploy` while an inference run is active MUST NOT terminate or interrupt the running workload. The active deployment remains fully operational, and `PrimaDeploymentShell` (legacy manual view) MUST display the running model status and allow stopping it even when the flag is disabled.
- **Active Inference Run Conflict**: If an inference model is already running on the cluster (`status.inference_up == true`), the one-click deploy button is disabled with an explanatory tooltip ("Stop active model before deploying").
- **Invalid or Unknown CLI Flag Name**: Executing `kllm flags non-existent-flag --enable` raises a clean `ValueError` with a list of valid flags, exiting non-zero without modifying configuration.
- **Install Boundary Guard**: Code execution during auto-planning or one-click deployment must never invoke external package managers or dynamic compiler toolchains, strictly obeying `tests/test_install_boundary.py`.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST register `"prima-auto-deploy"` as a valid feature flag in the `FEATURE_FLAGS` tuple in `src/kllm/shared/config.py`.
- **FR-002**: The CLI command `kllm flags prima-auto-deploy [--enable/--disable]` MUST persist the flag's boolean state in `~/.kllm/config.toml` and report the updated status.
- **FR-003**: The CLI command `kllm run` MUST accept an optional `--prima-auto-deploy / --no-prima-auto-deploy` option that overrides the persistent configuration for the duration of that command invocation.
- **FR-004**: The backend `DashboardStatusService` MUST include `feature_flags: dict[str, bool]` in `/v1/status`, `/v2/cluster/status`, and live WebSocket event snapshots, reflecting the active state of `"prima-auto-deploy"`.
- **FR-005**: When `prima-auto-deploy` is enabled, the backend deployment planning engine MUST delegate layer distribution, GPU offloading counts (`ngl`), and pipeline ring cycle parameters to the native HiGHS MILP scheduler (`solve_highs_halda_schedule`). If HiGHS determines the plan is infeasible due to insufficient memory, the system MUST return HTTP 422 Unprocessable Entity with `detail.code = "PRIMA_INSUFFICIENT_CAPACITY"` and memory shortfall breakdown.
- **FR-006**: The auto-planning engine MUST discover all online, healthy cluster participants and compute an optimal multi-node or single-node placement plan without requiring manual `LayerRange`, `vram_percent`, or `ram_percent` inputs from the user.
- **FR-007**: Auto-planning MUST bypass manual `core_placement.py` capacity reservation limits that would otherwise block viable deployments, allowing the HiGHS mathematical model to find the globally optimal feasible layer allocation.
- **FR-008**: The auto-deployment execution pipeline MUST automate asset verification, manifest materialization, and worker synchronization. If `len(manifest.ranks) == 1`, execution MUST route to the single-node runtime coordinator (`runtime.py`); if `len(manifest.ranks) >= 2`, execution MUST route to `distributed_runs.py`. Worker staging MUST enforce a 60-second connection and transfer timeout per chunk, transition to `failed`, clean up temporary staging artifacts, and return `PRIMA_STAGING_FAILED` with the failing `node_id` on network dropout.
- **FR-009**: When `prima-auto-deploy` is enabled, the Svelte 5 dashboard Run view MUST conditionally render a streamlined one-click deploy component (`PrimaAutoDeployView.svelte`) displaying a model selector dropdown and a single primary "Deploy Model" button. The view MUST render error chips and disable the deploy button for active runs in flight, unsupported architectures (`PRIMA_MODEL_UNSUPPORTED`), corrupt models (`PRIMA_MODEL_CORRUPT`), and insufficient cluster capacity (`PRIMA_INSUFFICIENT_CAPACITY`).
- **FR-010**: The one-click deploy view MUST render a cluster auto-planning readiness badge showing participating node count, available GPUs, and total aggregated memory.
- **FR-011**: When `prima-auto-deploy` is disabled, the dashboard MUST render the complete baseline multi-step deployment interface (`PrimaDeploymentShell`), preserving all accordions (Overview, Computers, Memory, Features, Expert), LoRA settings, and the 5-stage Prepare & Profile stepper. Toggling the flag from enabled to disabled while an inference run is active MUST NOT interrupt the running workload, and `PrimaDeploymentShell` MUST display the running model status and provide a working stop action.
- **FR-012**: When `prima-auto-deploy` is disabled, all existing CLI commands, backend endpoints, and deployment options MUST behave identically to baseline without regression.
- **FR-013**: Attempting to auto-deploy an unsupported model architecture MUST immediately return `PRIMA_MODEL_UNSUPPORTED` and block deployment before any native process is spawned.
- **FR-014**: All implementation code across Python backend and Svelte 5 dashboard MUST strictly comply with the module shape and repository boundaries defined in `.specify/memory/architecture_constitution.md`.
- **FR-015**: The implementation MUST NOT perform dynamic package or binary installations at runtime, ensuring `tests/test_install_boundary.py` passes continuously.
- **FR-016**: Before invoking HiGHS planning, the auto-planning engine MUST validate that `read_model_layout(model)` is not `None` and `model.stat().st_size > 0`. If the file is 0-byte, corrupt, or not a valid GGUF, it MUST immediately return HTTP 400 with detail code `PRIMA_MODEL_CORRUPT` without invoking the solver or spawning native processes.

### Key Entities

- **`PrimaAutoDeployFlag`**: Boolean feature flag stored in `~/.kllm/config.toml` gating the automated deployment pathway.
- **`AutoPlacementPlan`**: HiGHS-computed layer window assignments ($w_m$), GPU layer offload counts ($n_m$), and cycle count ($k$) across online cluster nodes.
- **`OneClickDeployRequest`**: Streamlined deployment payload specifying the model ID and auto-deploy directive (`prima_auto_deploy: true`).
- **`ClusterReadinessBadge`**: Svelte UI component displaying cluster node count, GPU hardware availability, and auto-planning status.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Running `kllm flags prima-auto-deploy --enable` persists the flag to `~/.kllm/config.toml`, and `kllm flags` displays `prima-auto-deploy = enabled`.
- **SC-002**: Backend endpoints `/v1/status` and `/v2/cluster/status` return `"feature_flags": {"prima-auto-deploy": true}` when the flag is enabled, and `false` when disabled.
- **SC-003**: In one-click deploy mode (flag enabled), selecting a model and clicking "Deploy Model" results in an active inference deployment (`inference_up = true`) with HiGHS-optimized layer allocation without manual slider adjustments, correctly routed to single-node coordinator if `len(manifest.ranks) == 1` or distributed runner if $\ge 2$.
- **SC-004**: In legacy mode (flag disabled), 100% of existing manual configuration parameters, tabs, accordions, and stepper controls function with zero regressions.
- **SC-005**: Selecting an unsupported model architecture returns `PRIMA_MODEL_UNSUPPORTED`, selecting a corrupt or 0-byte model returns HTTP 400 `PRIMA_MODEL_CORRUPT`, and insufficient memory returns HTTP 422 `PRIMA_INSUFFICIENT_CAPACITY`, with zero native processes spawned and clear error chips in the dashboard UI.
- **SC-006**: Automated unit and integration test suites in `tests/test_cli_flags.py`, `tests/test_prima_auto_deploy.py`, and `dashboard/tests/prima-auto-deploy.test.mjs` achieve 100% pass rate.
- **SC-007**: Spec Kit consistency verification via `speckit-analyze` completes with zero critical or high-severity issues across `spec.md`, `plan.md`, and `tasks.md`.
- **SC-008**: `uv run python -m pytest -q tests/test_install_boundary.py` passes cleanly, and zero unauthorized modifications are made to `.specify/memory/` or `docs/memory/`.

---

## Assumptions

- The cluster has at least one functional compute node (master) and may optionally have one or more connected worker nodes over private LAN or WireGuard.
- The GGUF model files to be deployed are stored locally or accessible via Kluster's model inventory.
- Hardware profiling data is either cached in SQLite from previous runs or estimated via standard hardware probes for the HiGHS solver.
- Svelte 5 runes (`$state`, `$derived`, `$effect`) are standard across all new dashboard components.

---

## Out of Scope

- Introducing multi-node Tensor Parallelism (row-split TP), which is unsupported by the underlying `prima.cpp` engine.
- Fine-grained per-layer manual adjustment controls within the streamlined one-click deploy view (retained exclusively in manual mode).
- Modifying package installation scripts in `scripts/install.py` or altering external system dependencies.
- Changes to the project constitution or durable memory files in `docs/memory/`.
