---

description: "Implementation tasks for accurate settings memory and automatic Layer split"
---

# Tasks: Correct VRAM Reporting and Default to Layer

**Input**: `specs/001-fix-vram-layer-default/plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/system-settings.md`, and `quickstart.md`.

**Organization**: US1 (P1) fixes reported capacity, effective budget, and estimate labels. US2 (P2) fixes the dashboard's effective split mode. Paths are relative to the repository root. Keep the existing HTTP byte fields and local-only CLI split options compatible.

## Phase 1: Setup (Existing Project)

**Purpose**: Use the installed uv/Node toolchains without creating a new project or dependency.

- [X] T001 Check existing Python and dashboard environments with `uv run pytest -q tests/test_gpus.py` and `npm --prefix dashboard run check`; if either toolchain is missing, follow `docs/agent-install.md` and its recovery steps before continuing.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Existing `/v1/gpus`, `/v1/config`, `/v1/plan`, `/v1/run` and shared `HardwareSettings.svelte` already provide the needed foundation. No new schema, endpoint, abstraction, or foundational task is required. Both stories may start after T001.

---

## Phase 3: User Story 1 — See accurate memory capacity and estimates (Priority: P1) 🎯 MVP

**Goal**: Display reported capacity, effective planner budget, and GPU/CPU/combined estimated consumption without unit errors or double counting.

**Independent Test**: Open the two settings surfaces with controlled 8 and 16 GiB GPUs and a 95% budget: show 8.0, 16.0, selected total 24.0, and a fully available 16 GiB device budget of 15.2 GiB; reduced availability lowers the budget. A CPU-spill plan keeps GPU allocation separate from RAM and labels combined totals total memory. Invalid capacities do not yield plausible partial totals.

### Behavioral regression check

- [X] T002 [US1] Write a failing Node built-in test in `dashboard/tests/format-memory.test.mjs` for one-decimal 8/16/24 GiB formatting, selected 8+16 GiB counted once, reduced reported capacity, missing/non-finite/negative input shown unavailable, and no partial selected total; run it with `node --experimental-strip-types --test dashboard/tests/format-memory.test.mjs` before implementation.

### Implementation

- [X] T003 [US1] Implement a scoped GiB formatter and unique selected-discrete-capacity calculation in `dashboard/src/lib/format-memory.ts`, using `dashboard/src/lib/types.ts:GpuInfo` and the existing byte fields; enforce “Missing/non-finite/negative capacity is unknown, not zero or guessed size” and “Shared pool is labeled shared and not added twice to combined memory.” Leave transfer-only decimal `formatGb` unchanged.
- [X] T004 [P] [US1] Update `dashboard/src/lib/components/HardwareSettings.svelte` to show reported per-GPU capacity and selected discrete GPU capacity total in one-decimal GiB, distinguish unified/shared inventory, recompute on selection/backend changes, and show `—` if any included capacity is invalid; do not add `member.vram_bytes` to `gpus[].vram_bytes`.
- [X] T005 [P] [US1] Update `dashboard/src/routes/run/+page.svelte` to format hardware-plan bytes in one-decimal GiB, label top-level `estimated_bytes / budget_bytes` as estimated / usable **total memory**, show `vram_alloc_bytes` as GPU-only and `cpu_alloc_bytes` as RAM, and label `device_allocations[].estimated_bytes / budget_bytes` as estimated / usable per-device budget; rely on `/v1/plan` budgets (not `vram_bytes × vram_percent`) for available-memory and unified-RAM limits, avoiding `Number(null)` becoming a false 0.0 GiB.

**Checkpoint**: Run the new Node check and relevant existing `tests/test_cluster_optimizations.py` coverage, then follow US1 scenarios in `specs/001-fix-vram-layer-default/quickstart.md` without needing US2.

---

## Phase 4: User Story 2 — Run with Layer automatically (Priority: P2)

**Goal**: Remove dashboard split selection and send Layer for every dashboard estimate, plan, and launch, even after a legacy Row/Tensor preference is loaded, saved, reset, or reloaded.

**Independent Test**: With fresh settings and then saved Row and Tensor values, observe Layer in dashboard `/v1/plan` (debounced estimate and explicit Plan) and `/v1/run` requests and results; saving hardware settings normalizes the old value. Neither settings page contains a split selector or obsolete guidance. Explicit local-only CLI/API requests retain their existing semantics.

### Behavioral regression check

- [X] T006 [P] [US2] Add a runnable dashboard regression check in `dashboard/tests/store.test.mjs` using the existing Vite/Svelte module loader and controlled `/v1/config`, `/v1/plan`, `/v1/run` responses: assert `planPayload()`, `refreshEstimate()`, `plan()`, `run()`, and hardware `saveConfig()` send Layer after loading Row or Tensor, including reset/reload; make the legacy case fail before implementing T007 and run with `node --test dashboard/tests/store.test.mjs`.

### Implementation

- [X] T007 [US2] In `dashboard/src/lib/store.svelte.ts`, put `split_mode: 'layer'` in shared `planPayload()` (used by estimate, Plan, Run), make dashboard hardware `saveConfig()` persist Layer despite legacy config, and remove obsolete mutable `splitMode` state, `setSplitMode`, and split-mode parsing/partial parameters; do not rewrite config merely on load or change `python/kllm/run_planning.py:resolve_split_mode` for explicit local-only clients.
- [X] T008 [P] [US2] Remove split-mode selector and Row/Tensor mode-selection guidance from `dashboard/src/lib/components/HardwareSettings.svelte` (shared by `dashboard/src/routes/settings/+page.svelte` and `dashboard/src/routes/run/+page.svelte`); preserve US1 capacity rendering if T004 has already modified the component.
- [X] T009 [P] [US2] Replace the obsolete “system split mode” guidance and Row/Tensor-specific dashboard table presentation in `dashboard/src/routes/run/+page.svelte` with accurate automatic Layer wording while preserving US1's capacity/budget/estimate labels from T005.

**Checkpoint**: Run the dashboard regression check; exercise fresh and legacy preference flows through both settings surfaces and the existing HTTP boundary in `tests/test_plan_run_params.py`. No physical GPU is required for the automated checks.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Align existing user documentation with the changed dashboard behavior and record validation.

- [X] T010 Update `README.md` and `docs/cli.md` so dashboard memory units, total-memory versus VRAM labels, automatic Layer selection, and the remaining explicit local-only CLI/API Row/Tensor options match actual behavior; remove README guidance that suggests a dashboard split-mode selector.
- [X] T011 Run `uv run pytest -q tests/test_gpus.py tests/test_config.py tests/test_plan_run_params.py tests/test_cluster_optimizations.py`, `node --experimental-strip-types --test dashboard/tests/format-memory.test.mjs`, `node --test dashboard/tests/store.test.mjs`, `npm --prefix dashboard run check`, and `npm --prefix dashboard run build`; follow `specs/001-fix-vram-layer-default/quickstart.md`, recording OS/backend prerequisites, results and exact follow-up for any unavailable GPU/RPC hardware.

---

## Dependencies & Execution Order

- **Setup**: T001 first. Phase 2 has no new blockers; existing infrastructure is reused.
- **US1 (MVP)**: T002 (red regression) → T003 → T004 and T005 (different files, parallel after T003). US1 is independently deliverable and testable.
- **US2**: T006 (red regression) → T007 → T008 and T009. US2 behavior is independent of US1; if work is concurrent, serialize T008 after T004 and T009 after T005 because those pairs edit the same files. T006 can run alongside US1 work, and T007 touches only the store while T004/T005 touch component/page files.
- **Polish**: T010 after the corresponding story behavior is settled; T011 after both stories and documentation. Preserve existing external CLI/API compatibility throughout.

## Parallel Execution Examples

### User Story 1

After T003, T004 in `dashboard/src/lib/components/HardwareSettings.svelte` and T005 in `dashboard/src/routes/run/+page.svelte` can proceed concurrently. Both use the completed GiB helper from T003; neither edits the other's file. Before that, run T002 first to observe the failure on the original behavior.

### User Story 2

T006 in `dashboard/src/lib/store.test.mjs` can be authored while US1's formatter/component work proceeds. After its failing case is observed, T007 changes only `dashboard/src/lib/store.svelte.ts`; T008 and T009 then remove UI affordances in separate files and can proceed concurrently once the overlapping US1 edits in those files are complete.

## Implementation Strategy

1. **MVP**: T001, then T002–T005. Validate US1 independently with the controlled capacity/budget/spill scenarios before taking on split-mode changes.
2. **Increment**: T006–T009. Verify both fresh and legacy Row/Tensor configurations on estimate/Plan/Run and after hardware save, model reset and page reload; keep explicit local-only CLI/API behavior.
3. **Finish**: T010–T011. Report commands and results, and distinguish unavailable hardware smoke checks from passes.

## Validation record (2026-09-28)

- `uv run pytest -q tests/test_gpus.py tests/test_config.py tests/test_plan_run_params.py tests/test_cluster_optimizations.py`: **54 passed** (2 dependency deprecation warnings).
- `node --experimental-strip-types --test dashboard/tests/format-memory.test.mjs`: **3 passed**; `node --test dashboard/tests/store.test.mjs`: **1 passed** (existing Svelte accessibility warnings).
- `npm --prefix dashboard run build`: **passed**. `git diff --check`: **passed**.
- `npm --prefix dashboard run check`: **2 pre-existing errors** in `dashboard/src/routes/models/+page.svelte:194,198` (`appStore.searchHits` does not exist on `AppStore`) and 7 pre-existing accessibility warnings. The same 2 errors and 7 warnings appeared at T001 before this feature was edited; no new diagnostics remain. Follow-up: correct the Models page's stale `searchHits` reference in a separate change, then rerun this check.
- Windows/Vulkan master with live RPC workers is present, but its active workload reports an allocation overflow; the controlled tests do not establish a successful hardware run. Live 8/16/24 GiB display and RPC launch remain **unverified**. Follow-up: on a known-capacity test GPU, open both settings pages, select/deselect the devices, and compare GiB labels against `/v1/gpus`; with sufficient free memory plan a fitting model at 95% and confirm the 15.2 GiB budget; on a private LAN/WireGuard RPC worker, test fresh and saved Row/Tensor config by inspecting `/v1/plan` and `/v1/run` dashboard payloads and the resolved Layer launch, without interrupting an active model. See `specs/001-fix-vram-layer-default/quickstart.md`.

## Phase 6: Convergence

- [X] T012 CRITICAL: Add FastAPI TestClient regression in `tests/test_plan_run_params.py` that persists Row and Tensor via `/v1/config`, submits an explicit `split_mode: "layer"` to `/v1/plan` and `/v1/run`, and asserts the resolved plan and mocked master launch both use Layer; keep explicit local-only modes compatible, per Constitution III and FR-007 (partial).
- [X] T013 Preserve `/v1/gpus.unified_memory` in `dashboard/src/lib/store.svelte.ts` and use it in `dashboard/src/lib/components/HardwareSettings.svelte` to identify shared capacity before `/v1/status` arrives; avoid summing a shared RAM pool as discrete VRAM, and cover that loading order in `dashboard/tests/store.test.mjs`, per FR-004 (partial).
- [X] T014 Invalidate stale `planResult` when hardware config or selected GPUs change on `dashboard/src/routes/settings/+page.svelte`, and refresh the estimate on entering `dashboard/src/routes/run/+page.svelte` once model/status are ready; assert the next displayed budget follows the new selection in `dashboard/tests/store.test.mjs`, per FR-005 (partial).
- [X] T015 Reconcile `selectedGpuIds` against the current indices after each successful `/v1/gpus` refresh in `dashboard/src/lib/store.svelte.ts`, preserving valid selections and defaulting to current eligible devices when none remain; test a backend/inventory index change and its displayed selected total in `dashboard/tests/store.test.mjs`, per T004 (partial).

## Convergence verification (2026-09-28)

- `uv run pytest -q tests/test_gpus.py tests/test_config.py tests/test_plan_run_params.py tests/test_cluster_optimizations.py tests/test_dashboard_api.py`: **77 passed** (2 dependency deprecation warnings), including Row/Tensor saved-config HTTP plan/run and local-only compatibility checks.
- `node --experimental-strip-types --test dashboard/tests/format-memory.test.mjs`: **3 passed**. `node --test dashboard/tests/store.test.mjs`: **1 passed**, covering shared-memory inventory before status, refreshed budgets, and changed/empty GPU selections. The Vite test server disables HMR and cleans up; websocket port 24678 is no longer occupied after the run.
- `npm --prefix dashboard run build`: **passed**. `npm --prefix dashboard run check`: **the same 2 pre-existing `searchHits` errors** in `dashboard/src/routes/models/+page.svelte` and 7 existing accessibility warnings; no new diagnostics. Fix the Models-page references separately, then rerun the check.
- Live known-capacity GPU and private-network RPC smoke scenarios in `specs/001-fix-vram-layer-default/quickstart.md` remain unverified on hardware and must not be reported as validated.
