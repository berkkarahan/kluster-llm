---

description: "Task list for Feature 022: Settings Feature Flags Admin View"
---

# Tasks: Settings Feature Flags Admin View

**Input**: Design documents from `/specs/022-settings-feature-flags/`

**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/settings-flags.md`, `quickstart.md`

**Tests**: Included per Kluster LLM Constitution Principle II (Behavior-Focused Regression Tests).

**Organization**: Tasks are grouped by user story (`US1`, `US2`, `US3`) to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (`[US1]`, `[US2]`, `[US3]`)
- Exact file paths are included in every task description

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Define shared request/response schemas and frontend entity descriptors required across backend and dashboard layers.

- [X] T001 [P] Add `FeatureFlagBody` Pydantic model (`name: str` member of `FEATURE_FLAGS`, `enabled: StrictBool` rejecting non-booleans, `extra="forbid"`) in `src/kllm/shared/schemas.py`
- [X] T002 [P] Add `FeatureFlagDescriptor` type (`id: string`, `label: string`, `description: string`) and `FEATURE_FLAG_CATALOG` metadata for `private-network-setup` and `prima-auto-deploy` in `dashboard/src/lib/modules/node-settings/entities.ts`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Wire the Settings HTTP gateway (`GET /v2/node/flags`, `PUT /v2/node/flags`, `/v1/flags`) and composition root before user story UI and synchronization work begins.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Extend `ConfigGateway` protocol (`feature_flags()`, `set_feature_flag(name, enabled)`), add `get_feature_flags()` and `save_feature_flag(body: FeatureFlagBody)` methods to `SettingsHandlers` (matching `save_config` and `save_hf_token`), and register `/v2/node/flags` (`CONTROL_V2_NODE`) and `/v1/flags` (`LEGACY_V1_CONFIG`) mapped to `get_feature_flags` / `put_feature_flag` in `src/kllm/modules/settings/router_http.py`
- [X] T004 Expose `get_feature_flags()` and `put_feature_flag(body: FeatureFlagBody)` functions delegating to `handlers.get_feature_flags()` and `handlers.save_feature_flag(body)` (matching `put_config` → `handlers.save_config` and `put_hf_token` → `handlers.save_hf_token`) and register `/v1/flags` and `/node/flags` routes in `create_settings_router` in `src/kllm/modules/settings/endpoints.py`
- [X] T005 Wire `get_feature_flags` and `put_feature_flag` into `_route_handlers` in `src/kllm/agent/runtime.py`

**Checkpoint**: Backend `/v2/node/flags` and `/v1/flags` endpoints are active — user story implementation can now begin.

---

## Phase 3: User Story 1 - Toggle Feature Flags On the Go from Settings (Priority: P1) 🎯 MVP

**Goal**: Provide a dedicated **Feature flags** section (`?section=flags`) in the Settings left-hand navigation where operators can view all registered feature flags and flip them on or off with immediate effect across gated dashboard views.

**Independent Test**: Open Settings → Feature flags (`/settings?section=flags`), toggle `prima-auto-deploy` or `private-network-setup`, and verify the switch state and gated dashboard views (`PrimaRunPage.svelte`) update immediately without a page reload or node restart.

### Implementation for User Story 1

- [X] T006 [US1] Add `featureFlags` derived map (falling back to default `false` per `FEATURE_FLAG_CATALOG` entry), `loadFeatureFlags()`, and `setFeatureFlag(name: string, enabled: boolean)` (`PUT /v2/node/flags`, immediate `this.cluster.status.feature_flags` update, and `await this.refresh()`) in `dashboard/src/lib/modules/node-settings/interactor.svelte.ts`
- [X] T007 [P] [US1] Create `FeatureFlags.svelte` admin panel component rendering `FEATURE_FLAG_CATALOG` rows with title, monospace flag id, description, and accessible switch controls (`role="switch"`, `aria-checked`) in `dashboard/src/lib/modules/node-settings/view/FeatureFlags.svelte`
- [X] T008 [US1] Add `{ id: 'flags', label: 'Feature flags' }` to `sections`, handle `?section=flags` query parameter in `onMount`, update header subtitle when `section === 'flags'`, and render `<FeatureFlags presenter={settings} />` in `dashboard/src/lib/modules/node-settings/view/SettingsPage.svelte`
- [X] T009 [P] [US1] Add backend API tests for `GET /v2/node/flags` and `PUT /v2/node/flags` toggling `private-network-setup` and `prima-auto-deploy` in `tests/test_dashboard_api.py`
- [X] T010 [P] [US1] Create dashboard test verifying `SettingsPage` **Feature flags** section navigation (`?section=flags`), rendering of flag descriptions/states, and live reactivity across `PrimaRunPage` when `prima-auto-deploy` is flipped on the go in `dashboard/tests/settings-feature-flags.test.mjs`

**Checkpoint**: User Story 1 is complete and independently testable as an MVP.

---

## Phase 4: User Story 2 - Persistent Synchronization Between Settings UI and Command Line (Priority: P2)

**Goal**: Ensure feature flag toggles made in Settings persist across browser reloads, `kllm stop`, and restarts, and stay synchronized with `kllm flags` CLI changes and live status broadcasts.

**Independent Test**: Toggle a flag via `PUT /v2/node/flags`, verify `kllm flags` CLI and SQLite (`managed-network.sqlite`) reflect the change across stop/restart, and verify external CLI changes received via `cluster.status.feature_flags` update `FeatureFlags.svelte`.

### Implementation for User Story 2

- [X] T011 [US2] Reconcile `NodeSettingsInteractor` flag state with incoming `cluster.status.feature_flags` snapshots in `onStatusFingerprint` and `loadFeatureFlags` in `dashboard/src/lib/modules/node-settings/interactor.svelte.ts`
- [X] T012 [P] [US2] Add backend integration test verifying `PUT /v2/node/flags` persists to `managed-network.sqlite`, matches `GET /v2/cluster/status` (`feature_flags`), and matches `kllm.shared.config.get_feature_flag()` in `tests/test_dashboard_api.py`
- [X] T013 [P] [US2] Add dashboard test verifying `FeatureFlags.svelte` reflects external CLI / live status updates to `cluster.status.feature_flags` in `dashboard/tests/settings-feature-flags.test.mjs`

**Checkpoint**: User Stories 1 and 2 work together with full persistence and CLI/UI synchronization.

---

## Phase 5: User Story 3 - Clear Feedback and Safe Recovery on Failed Toggle (Priority: P3)

**Goal**: Guard against concurrent rapid clicks while a toggle is in flight, reject invalid flag payloads at the API boundary, and revert to the last confirmed state with a clear error message if a toggle fails.

**Independent Test**: Simulate a failed `PUT /v2/node/flags` request (HTTP 400/500 or network error) or rapid clicks in `FeatureFlags.svelte` and verify that in-flight controls are disabled, the switch remains in its prior confirmed state on error, and an error banner is displayed.

### Implementation for User Story 3

- [X] T014 [US3] Add per-flag `pendingFlags = $state<Record<string, boolean>>({})` lock and `flagError = $state<string | null>(null)` state in `NodeSettingsInteractor.setFeatureFlag` so rapid clicks are ignored while in flight and failed requests preserve the confirmed state and surface the error in `dashboard/src/lib/modules/node-settings/interactor.svelte.ts`
- [X] T015 [US3] Render pending indicator (`disabled={Boolean(presenter.pendingFlags[flag.id])}`, `aria-busy`) and inline error notice (`role="alert"` when `presenter.flagError` is non-null) in `dashboard/src/lib/modules/node-settings/view/FeatureFlags.svelte`
- [X] T016 [P] [US3] Add backend validation error tests (unknown flag name -> 400, non-boolean `enabled` -> 422, extra fields -> 422) in `tests/test_dashboard_api.py` and UI failure/in-flight guard tests in `dashboard/tests/settings-feature-flags.test.mjs`

**Checkpoint**: All user stories (`US1`, `US2`, `US3`) are independently functional and verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify architecture boundaries, Svelte typecheck/build, and full quickstart validation.

- [X] T017 [P] Verify router isolation and Clean Architecture boundaries by running `tests/test_architecture_boundaries.py` and `tests/test_install_boundary.py`
- [X] T018 [P] Run `npm --prefix dashboard run check` and `npm --prefix dashboard run build` to verify zero TypeScript/Svelte diagnostics
- [X] T019 Execute all validation commands in `specs/022-settings-feature-flags/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — `T001` and `T002` can run in parallel immediately.
- **Foundational (Phase 2)**: Depends on `T001` — `T003` → `T004` → `T005` complete the backend HTTP routing and block user story API integration.
- **User Story 1 (Phase 3 - P1 MVP)**: Depends on Phase 1 (`T002`) and Phase 2 (`T003`–`T005`).
- **User Story 2 (Phase 4 - P2)**: Depends on Phase 3 (`T006`–`T008`).
- **User Story 3 (Phase 5 - P3)**: Depends on Phase 3 (`T006`–`T008`).
- **Polish (Phase 6)**: Depends on Phases 3–5 completion.

### Parallel Opportunities

- **Phase 1**: `T001` (`src/kllm/shared/schemas.py`) and `T002` (`dashboard/src/lib/modules/node-settings/entities.ts`) touch independent backend/frontend files and can run in parallel.
- **Phase 3 (US1)**: `T007` (`FeatureFlags.svelte`), `T009` (`tests/test_dashboard_api.py`), and `T010` (`dashboard/tests/settings-feature-flags.test.mjs`) can be developed in parallel once `T006` defines the interactor interface.
- **Phase 4 (US2)**: `T012` (backend persistence sync test) and `T013` (frontend live sync test) can run in parallel.
- **Phase 5 (US3)**: Backend error tests and frontend error tests in `T016` can run in parallel.

---

## Parallel Example: User Story 1

```bash
# Run Phase 1 setup in parallel:
Task T001: "Add FeatureFlagBody Pydantic model in src/kllm/shared/schemas.py"
Task T002: "Add FeatureFlagDescriptor type and FEATURE_FLAG_CATALOG in dashboard/src/lib/modules/node-settings/entities.ts"

# Run US1 component and test tasks in parallel:
Task T007: "Create FeatureFlags.svelte admin panel component in dashboard/src/lib/modules/node-settings/view/FeatureFlags.svelte"
Task T009: "Add backend API tests for GET /v2/node/flags and PUT /v2/node/flags in tests/test_dashboard_api.py"
Task T010: "Create dashboard test for SettingsPage Feature flags section in dashboard/tests/settings-feature-flags.test.mjs"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (`T001`–`T002`)
2. Complete Phase 2: Foundational (`T003`–`T005`)
3. Complete Phase 3: User Story 1 (`T006`–`T010`)
4. **STOP and VALIDATE**: Verify operators can open Settings → Feature flags and flip flags on the go with immediate UI response.

### Incremental Delivery

1. Setup + Foundational → `/v2/node/flags` API ready
2. Add User Story 1 → Settings Feature flags section & live toggle (MVP)
3. Add User Story 2 → Full CLI/status feed synchronization & persistence verification
4. Add User Story 3 → In-flight lock, strict validation, and error recovery banner
5. Polish → Full architecture boundary, `svelte-check`, and production build verification
