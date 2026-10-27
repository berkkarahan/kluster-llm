# Feature-local context: `021-prima-auto-deploy`

Scratch context for Feature 021 only. Durable project knowledge belongs in `docs/memory/` with an `INDEX.md` entry, and only with explicit user authorization.

## Locked decisions (2026-10-27)

- **Feature Directory & Slug**: `specs/021-prima-auto-deploy/` sequentially follows `specs/020-prima-qwen35-model-support/`.
- **Feature Flag Key**: `"prima-auto-deploy"` registered in the `FEATURE_FLAGS` tuple in `src/kllm/shared/config.py`.
- **Default State**: Disabled (`False`) by default.
- **CLI Commands**:
  - `kllm flags prima-auto-deploy --enable` (persists `True` in `~/.kllm/config.toml`).
  - `kllm flags prima-auto-deploy --disable` (persists `False`).
  - `kllm flags` lists all flags with status.
  - `kllm run <model> --prima-auto-deploy` / `--no-prima-auto-deploy` provides per-invocation command-line override.
- **Backend Status Propagation**:
  - `DashboardStatusService` injects `feature_flags: self._feature_flags()` into `/v1/status`, `/v2/cluster/status`, and live WebSocket event snapshots.
- **Auto-Planning Engine**:
  - Delegates layer distribution, GPU offloading counts (`ngl`), and pipeline ring cycle parameters ($k$) to `prima.cpp`'s native HiGHS Mixed-Integer Linear Programming (MILP) solver (`common/common.cpp:862-1528` and `src/kllm/modules/primacpp/interactors/highs_scheduler.py`).
- **Placement Bypass**:
  - When `prima-auto-deploy` is enabled, manual layer boundaries, device filters, and headroom/share limits in `core_placement.py` are bypassed in favor of mathematical optimization over detected online cluster nodes.
- **Automated Staging & Dispatch**:
  - Materialization, asset verification/push to worker nodes, and process dispatch run automatically during the deployment action without requiring manual 5-stage stepper intervention.
- **Dashboard UI**:
  - In `dashboard/src/lib/modules/run/view/PrimaRunPage.svelte`, when `cluster.status?.feature_flags?.['prima-auto-deploy']` is true, conditionally renders the streamlined one-click deploy view (`PrimaAutoDeployView.svelte`).
  - Allows the operator to select a model and click a single primary "Deploy Model" button.
- **100% Backward Compatibility**:
  - When `prima-auto-deploy` is disabled, the dashboard renders the baseline multi-step deployment interface with all tabs, accordions, sliders, LoRA options, and the 5-stage Prepare & Profile stepper completely unchanged.
- **Error Handling**:
  - If an unsupported model family is selected, the system returns `PRIMA_MODEL_UNSUPPORTED` immediately without spawning native rank processes.
- **Architecture & Install Governance**:
  - Strict compliance with `.specify/memory/architecture_constitution.md`.
  - Zero edits to `.specify/memory/` governance files or `docs/memory/` durable store.
  - Install boundary guarded by `tests/test_install_boundary.py` (no runtime package or binary installations).

## Codebase Anchor Points

| Area | File Path | Line Anchors | Notes |
|---|---|---|---|
| Feature Flag Registry | `src/kllm/shared/config.py` | `L28, L113-133, L278-298` | Tuple `FEATURE_FLAGS`, `get_feature_flag`, `set_feature_flag` |
| CLI Flags Router | `src/kllm/modules/settings/flags_cli.py` | `L17-40` | `kllm flags [name] [--enable/--disable]` |
| CLI Inference Router | `src/kllm/modules/inference/router_cli.py` | `L510-591` | `kllm run` option parsing & request construction |
| Status Presenter | `src/kllm/modules/cluster/presenters/dashboard_status.py` | `L49, L86, L140` | Emits `feature_flags` dict in status payload |
| Agent Runtime Composition | `src/kllm/agent/runtime.py` | `L1107, L1485-1535` | Injects config flags into `DashboardStatusService` and inference deps |
| Inference HTTP Router | `src/kllm/modules/inference/router_http.py` | `L130-145, L207-230` | Auto-planning branch bypassing manual constraints |
| Run Planning Interactor | `src/kllm/modules/inference/interactors/run_planning.py` | `L265-310` | Auto-plan execution coordinator |
| Prima Planner | `src/kllm/modules/primacpp/interactors/planner.py` | `L208-306` | Calls HiGHS scheduler across cluster members |
| HiGHS Scheduler | `src/kllm/modules/primacpp/interactors/highs_scheduler.py` | `L122-180, L553-620`| Solves optimal layer window $w_m$ and GPU offload $n_m$ |
| Distributed Runs Launcher | `src/kllm/modules/primacpp/interactors/distributed_runs.py` | `L38-51, L384-415` | Automated staging and manifest dispatch |
| Svelte Cluster Entities | `dashboard/src/lib/modules/cluster/entities.ts` | `L79` | `Status.feature_flags?: Record<string, boolean>` |
| Svelte Run View Shell | `dashboard/src/lib/modules/run/view/PrimaRunPage.svelte` | `L1-60, L50-100` | Conditional rendering of one-click deploy view |
| Svelte Auto-Deploy View | `dashboard/src/lib/modules/run/view/PrimaAutoDeployView.svelte` | New component | Streamlined model selection & single Deploy button |

## Non-Goals

- Replacing or deprecating the manual deployment workflow (retained fully when flag is off).
- Introducing multi-node Tensor Parallelism (row-split TP is rejected by `prima.cpp` design).
- Modifying `scripts/install.py` or altering package/binary prerequisites at runtime.
- Modifying files in `.specify/memory/` or `docs/memory/`.
