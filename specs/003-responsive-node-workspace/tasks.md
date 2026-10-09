# Tasks: Responsive Node Workspace

**Input**: Design documents in `specs/003-responsive-node-workspace/`.
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [UI contract](contracts/workspace-ui.md),
[quickstart.md](quickstart.md), and the [saved implementation sequence](../../plans/responsive-node-workspace.md).
**Created**: 2026-09-29

**Tests**: Extend the existing behavioral/browser checks for the specification's explicit
known-reading, viewport, and keyboard acceptance scenarios and Constitution II regression
requirements. Write each story's new assertions first and confirm the relevant assertions
fail for the current behavior. Reuse the existing Node/Svelte/CDP harness.

**Organization**: Tasks are grouped by user story. All are initially unchecked; generating
this file does not establish implementation or validation results.

## Implementation status (2026-09-29)

US1, US2 and US3 are implemented and verified by the existing dashboard checks:

- `npm --prefix dashboard run check` → 0 errors, 0 warnings.
- `node --test --test-concurrency=1 dashboard/tests/*.test.mjs` → 12 passed, 0 failed, 0 skipped
  (the Chrome/Edge CDP browser test executed).
- `npm --prefix dashboard run build` → completed.

**32 of 32 tasks closed.** T024 and T025 were closed at the user's direction with explicit
waivers; their human-only checks were not performed and are not reported as passes. The
200%-zoom, short-landscape and reduced-motion automated checks are covered by T032. OpenDesign
registration for T002 is complete. See
[`docs/node-usage-monitor.md`](../../docs/node-usage-monitor.md#responsive-workspace-refactor-feature-003-2026-09-29)
for implementation and verification evidence.

## Format and path conventions

- Task lines use checkbox, sequential ID, optional `[P]`, story label for story phases, and
  an exact repository-relative file path. Indented quotes belong to the preceding task and
  preserve data-model constraints verbatim.
- `[P]` permits parallel work only within the dependency-ready, disjoint-file groups below.
  It does not authorize starting before prerequisites or concurrently running shared Vite/
  SvelteKit checks.
- Primary source is under `dashboard/src/`; checks are under `dashboard/tests/`; design and
  verification evidence goes in existing `docs/node-usage-monitor.md`.
- Explicitly target `SPECIFY_FEATURE_DIRECTORY=specs/003-responsive-node-workspace` for
  Spec Kit commands. Use `SPECIFY_FEATURE_NO_PERSIST=1` when sharing the checkout with other
  feature sessions; the shared active-feature pointer is not this feature's identity.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the current working-tree baseline using the existing project setup.

- [X] T001 Verify the installed dashboard/browser prerequisites against `docs/agent-install.md` and `dashboard/package.json`, inspect the current feature-002 edits before modifying them, run `npm --prefix dashboard run check` followed by `node --test --test-concurrency=1 dashboard/tests/*.test.mjs`, and record commands, actual results, browser availability, and pre-existing failures in `docs/node-usage-monitor.md`; use the documented installer only if setup is missing.

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the required OpenDesign revision and reusable measured-node fixtures.
Both tasks must finish before any user-story implementation starts. Existing types, store,
private interfaces, and history storage are reused; no new infrastructure layer is needed.

- [X] T002 [P] Generate and review feature-003 variants in OpenDesign project `kluster-llm-node-usage-monitor-307e` using the exact brief in `specs/003-responsive-node-workspace/contracts/workspace-ui.md`: 1280x720/1920x1080 desktop and 390x844 mobile, full-area graph/list, node-local CPU/RAM/multi-GPU rows, contextual inspection, and unavailable/stale/error states; record the new artifact/preview references and reviewed decisions in `docs/node-usage-monitor.md` before visual implementation. An unavailable generation workflow leaves this task blocked, not complete.

  > **Status 2026-09-29 — COMPLETE.** The feature-003 design reference at
  > `dashboard/design/003-responsive-node-workspace.html` passed
  > `od lint dashboard/design/003-responsive-node-workspace.html --fail-on p1` with 0 P0/P1/P2
  > and is now registered in the OpenDesign project `Kluster LLM Responsive Node Workspace`
  > (id `kluster-llm-responsive-node-workspace-d8d4`) as artifact
  > `003-responsive-node-workspace.html` (version `cf84d5b5-c511-4464-8a23-f3fd7ffc324a`),
  > created through the OpenDesign MCP `create_project`/`create_artifact` tools. The stored
  > copy's SHA256 matches the repository file. Project/artifact references and the
  > `od artifacts create` diagnosis are recorded in
  > [`docs/node-usage-monitor.md`](../../docs/node-usage-monitor.md#design-reference). Human
  > visual sign-off against the artifact remains part of the open `T024` walkthrough.
- [X] T003 [P] Extend the existing isolated fake-API/CDP fixture setup in `dashboard/tests/usage-workspace.test.mjs` to supply 1/4/16 keyed nodes with complete live readings, multiple GPUs, duplicate/long names, node-specific lifetimes, controllable delayed/failed history responses, and per-endpoint request counters; retain existing test coverage and cleanup without creating a second test harness.

**Checkpoint**: Revised design references and deterministic fixtures are ready. Source
implementation can now begin; no physical GPU or production cluster is needed for fixtures.

## Phase 3: User Story 1 - Open a screen-filling cluster workspace (Priority: P1)

**Goal**: Home occupies the real area beneath navigation immediately, with automatic initial
framing and a usable narrow-screen list.

**Independent Test**: Reload with 1/4/16 fixture nodes at 1280x720, 1440x900, and 1920x1080;
verify workspace/header/viewport bounds within 2px, readable initial one-/four-node views,
and a navigable sixteen-node overview without first clicking Fit. Resize through 768x1024,
700/701px, and 390x844; check scrolling, no horizontal page overflow, and stable manual view.

### Tests for User Story 1

- [X] T004 [US1] Add failing real-browser regressions in `dashboard/tests/usage-workspace.test.mjs` for the independent-test matrix: initial full-area sizing without manual Fit, no permanent side columns, actual enlarged-card containment/non-overlap, wrapped header, automatic versus manual resize behavior, zoom-out after a small Fit scale, empty/loading/offline layouts, and other routes remaining scrollable; update existing assertions that assume always-visible inspector content to open it explicitly.

### Implementation for User Story 1

- [X] T005 [P] [US1] Update the shell/header/main structure in `dashboard/src/routes/+layout.svelte` so the header sizes intrinsically and main receives the remaining dynamic viewport height, preserving `appStore` lifecycle and scroll access on Models, Plan & Run, Connect, and Settings; coordinate the existing element selectors with T006 without fixed 40px subtraction.
- [X] T006 [P] [US1] Implement the shell-to-workspace flex/grid sizing chain in `dashboard/src/app.css` with `100dvh` and `100vh` fallback, shrinkable children, a remaining-space graph/list instead of the 420px/52vh cap, bounded disclosure/inspector scrolling, and the existing graph-to-list switch at 700px; anchor Chat to the actual workspace rather than a fixed header offset.
- [X] T007 [P] [US1] Restructure `dashboard/src/routes/+page.svelte` into a compact toolbar, collapsed native cluster-controls disclosure, and full-area topology; move the existing inspector content into one closed-by-default native dialog with Details/History triggers and working close/focus behavior, preserving its current data flow for now, existing action availability, Chat, and visible recording/operational status. This minimum relocation keeps the layout increment functional before US3 refines history requests and contextual state.
- [X] T008 [P] [US1] Replace viewport-percentage/fixed-row geometry in `dashboard/src/lib/components/NodeMap.svelte` with a deterministic master-first world-space grid using readable widths and measured row/card bounds, retain only supplied membership edges, fit both dimensions with padding, and observe graph/card dimensions with cleanup; implement automatic/manual navigation transitions while retaining the equivalent narrow list and existing keyboard pan/zoom controls.

  > "Owned locally by `NodeMap.svelte`; not persisted and not transmitted."
  > "Initially true; manual navigation disables it; explicit Fit enables it."
  > "First nodes plus usable dimensions -> automatic fit, with single-node scale at most 1."
  > "Resize or geometry change -> refit only while automatic; manual transform remains intact."
  > "Routine readings with unchanged geometry -> update text only; no selection/focus reset."
  > "Pan/zoom -> manual mode; zoom-out always reduces scale, including after a small Fit scale."
  > "Graph hidden for narrow list -> ignore zero-sized measurements, preserve graph state."
  > "Return to graph -> fit if automatic; retain manual state otherwise. Fit stays reachable."
  > "Unmount -> disconnect resize observation and release any active pointer gesture."

- [X] T009 [US1] Run `node --test dashboard/tests/usage-workspace.test.mjs` with the browser test actually executing, verify the US1 matrix in `specs/003-responsive-node-workspace/quickstart.md` section A, resolve feature-introduced failures in the owning UI files, and record the viewport/browser results in `docs/node-usage-monitor.md`.

**Checkpoint**: US1 is an independently usable layout increment with existing readings and
inspection still reachable. US2 adds the requested node-local resource summaries.

## Phase 4: User Story 2 - Read resource usage directly on each node (Priority: P1)

**Goal**: Compare host CPU, host RAM, and every reported GPU directly on graph cards and
list entries without selection, hover, or an inspector.

**Independent Test**: With all contextual views closed, render the known readings from
quickstart section B in both presentations: Host CPU 25%, RAM 8.0/16.0 GiB and 50%, GPU 0
75% and 6.0/8.0 GiB, GPU 1 0% and 0.0/4.0 GiB. Check all availability, scope, device-union,
and shared-memory cases plus accessible metric text and enlarged-card bounds.

### Tests for User Story 2

- [X] T010 [P] [US2] Extend `dashboard/tests/usage-live.test.mjs` with failing assertions scoped to individual graph/list node summaries for host versus process values, binary GiB/percentage, duplicate identities, GPU device-ID union, zero/null/invalid/initializing/stale states, inventory-only capacity, CPU/unknown backend with no devices, shared memory, and partial sensor failures; preserve the existing scoped `NodeUsageDetails` checks.
- [X] T011 [P] [US2] Extend `dashboard/tests/usage-workspace.test.mjs` with failing checks that unselected desktop cards and mobile entries expose the same metric text/accessibility descriptions with no inspector opened, and that multi-GPU/long-content summaries remain unclipped and non-overlapping while initial Fit, polling, and manual navigation stay stable.

### Implementation for User Story 2

- [X] T012 [US2] Share one noninteractive summary snippet between graph and list buttons in `dashboard/src/lib/components/NodeMap.svelte`, retaining node identity/role/assignment and displaying `reading.host.cpu_percent`, host used/total RAM, and percentage via the existing `dashboard/src/lib/format-memory.ts:formatGib`; use sample bytes rather than configuration budgets or process measurements and apply the following constraints.

  > "Identity is `node_id`, not hostname, array position, or current selection."
  > "Preserve stable keyed identity and distinguish duplicate hostnames."
  > "Whole-node percent; zero is measured, null/invalid is unavailable."
  > "One-decimal GiB; percentage derived only from valid sample bytes and positive total."
  > "Label as reported capacity when sample totals are unavailable; do not derive a live percentage from fallback metadata."
  > "Percent values must be finite and in 0-100; memory values must be finite and nonnegative."
  > "Compute RAM percentage from unrounded bytes; an inconsistent used/total pair does not become a fabricated valid percentage."
  > "Not evidence of measured local work."

- [X] T013 [US2] Extend the shared summary in `dashboard/src/lib/components/NodeMap.svelte` with one row per inventory/reading device, matching `String(index)` to `device_id`, showing device-wide utilization and used/total GiB independently, qualifying inventory-only capacity, and using "CPU backend · no GPUs reported" versus "GPU information unavailable" for empty-device cases; do not read remote telemetry from `appStore.gpus` or infer GPU use from host memory.

  > "Union by `String(index)`/`device_id`; retain reading-only and inventory-only devices."
  > "Device-wide values; each field can be independently missing."
  > "One physical pool; no summed host-plus-device usage."
  > "A generic device reason does not suppress independently valid fields."
  > "No managed process can legitimately mean no host or device sample."

- [X] T014 [US2] Complete the shared summary's connection/freshness/measurement-age and per-field availability text in `dashboard/src/lib/components/NodeMap.svelte`; preserve supplied ten-second stale semantics and last-known disconnected values, expose all metric text through native button naming/description with `aria-pressed`, remove the identity-only accessible-name override, and avoid nested controls or announcing every poll.

  > "Show independently; cached values retain stale/disconnected labels."
  > "Availability `initializing`, `unsupported`, and `error` is kept as explanatory text."

- [X] T015 [US2] Adjust summary typography, wrapping, focus, and card/list sizing in `dashboard/src/app.css` for all GPU rows and long identities, with resource values never ellipsized, shared tabular GiB text, and no meaningful-looking zero meter for missing RAM; keep the measured geometry from T008 responsive to changed card bounds.

  > "Decorative RAM fill exists only for a valid percentage."

- [X] T016 [US2] Run `node --test --test-concurrency=1 dashboard/tests/usage-live.test.mjs dashboard/tests/usage-workspace.test.mjs`, verify the field/state matrix in `specs/003-responsive-node-workspace/quickstart.md` section B and retained process scope in Details, resolve feature-introduced failures, and record actual results in `docs/node-usage-monitor.md`.

**Checkpoint**: The two P1 stories now deliver both original requests: a screen-filling
workspace and directly readable CPU/RAM/GPU values. US3 completes contextual request/state behavior.

## Phase 5: User Story 3 - Access history and controls without a permanent inspector (Priority: P2)

**Goal**: Open Details/History only when requested, retain trustworthy history context and
keyboard focus, and keep cluster actions and Chat working from the compact workspace.

**Independent Test**: Assert zero history requests during initial polling and node selection
with History closed; open History, change lifetime/window, inspect UTC readings and More,
close/reopen, switch nodes under delayed/failed responses, and test keyboard focus/Escape
with Chat open. Exercise the existing idle/master/worker/busy action matrix.

### Tests for User Story 3

- [X] T017 [US3] Extend `dashboard/tests/usage-workspace.test.mjs` with failing browser/interaction checks for deferred history endpoint counts, node-bound responses under delay/failure/retry, same-node lifetime/window retention, selected-node removal/rejoin, UTC point inspection/pagination, modal focus/return and Escape isolation from Chat, hidden-content inertness, preserved control/streaming rules, and visible cached-recording failure status; retain `dashboard/tests/usage-history.test.mjs` as existing zero/gap/filter-ordering regression coverage.

### Implementation for User Story 3

- [X] T018 [P] [US3] Refine inspection state and the history-loading effect in `dashboard/src/routes/+page.svelte` so selection with History closed performs no history fetch, Details reuses scoped `NodeUsageDetails`, first/new-node History uses `selectUsageNode`, same-node reopen preserves valid lifetime/window while refreshing or retrying, and subsequent filters/pagination use the existing store callbacks; retain keyed History content and response-sequence protection from `dashboard/src/lib/store.svelte.ts`.

  > "Accessible through Details with explicit process scope."
  > "Select a card with History closed -> update selection only; no history request."
  > "Open History for a new node -> `selectUsageNode`; clear old content before displaying it."
  > "Only render historical content when its loaded node identity matches the current context."
  > "The UI must not defeat `historySeq` ordering or use one node's cached lifetimes for another."

- [X] T019 [P] [US3] Finish contextual-dialog and Chat presentation in `dashboard/src/app.css` against the reviewed design: desktop/mobile bounds with reachable close controls, scrolling history and long diagnostics, visible focus, WCAG AA text contrast, and reduced-motion behavior; use the native-dialog markup established by T007 rather than introducing a custom modal framework.
- [X] T020 [US3] Complete dialog/Chat lifecycle and status handling in `dashboard/src/routes/+page.svelte`: focus the active dialog, restore its trigger or stable toolbar fallback on dismissal, let Escape close only the active context, keep hidden content unmounted/hidden/inert, follow valid-node fallback when selection disappears, and keep operational errors/last successful save visible while controls are collapsed; preserve existing start/stop/join/leave/update, Plan & Run, sampling, and Chat streaming rules.

  > "Close dialog -> restore trigger focus, retain selection, keep collection/streaming running."
  > "A live request failure qualifies cached recorder health as last known, not confirmed current health."

- [X] T021 [US3] Run `node --test --test-concurrency=1 dashboard/tests/usage-workspace.test.mjs dashboard/tests/usage-history.test.mjs`, complete the contextual keyboard/request/state checks in `specs/003-responsive-node-workspace/quickstart.md` section C, resolve feature-introduced failures, and record actual outcomes in `docs/node-usage-monitor.md`.

**Checkpoint**: All stories have independently verifiable outcomes. History, Details, and
Chat operate without permanent side columns or fabricated measurements.

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finish operator documentation and collect the complete acceptance evidence.

- [X] T022 Update `dashboard/DESIGN.md` and `docs/node-usage-monitor.md` for the implemented full-area layout, shared node summaries, compact controls, native contextual inspection, qualified unavailable/GPU states, and revised OpenDesign artifact references; preserve prior historical verification records and keep these living docs aligned with actual behavior.
- [X] T023 Run `npm --prefix dashboard run check`, then `node --test --test-concurrency=1 dashboard/tests/*.test.mjs`, then `npm --prefix dashboard run build`, waiting for each to finish; resolve feature-introduced failures in the owning files and append exact commands/results and any remaining pre-existing failures to `docs/node-usage-monitor.md`.
- [X] T024 Execute the complete viewport, 200% zoom, short-landscape, other-route scrolling, keyboard, screen-reader, contrast, reduced-motion, and available live-node walkthrough in `specs/003-responsive-node-workspace/quickstart.md`; compare against the revised OpenDesign variants and record browser/OS/backend, screenshots or measured bounds, sensor availability, and actual results in `docs/node-usage-monitor.md`, keeping synthetic UI evidence distinct from physical sensor accuracy.

  > **Status 2026-09-29 — WAIVED/CLOSED at user's direction; human checks NOT RUN.** Automated subset captured in
  > `dashboard/tests/usage-workspace.test.mjs` and recorded in `docs/node-usage-monitor.md`:
  > full-area bounds at 1280x720, 16-node Fit containment, full-width graph, pan/zoom stability,
  > 390x844 stacked scrolling list, 768x1024 no overflow, keyboard selection/focus/dialog
  > dismissal, and `/models` still scrolling. **Not** executed: real screen-reader inspection,
  > and a live node's real GPU/sensor values (all metric fixtures are synthetic). These remain
  > unverified; this checkbox records user-directed closure only, not validation. T032 later
  > added the automated 200%-zoom-equivalent, short-landscape and reduced-motion checks.
- [X] T025 Conduct SC-005 with five operators viewing the same four-node fixture: each identifies both the highest-CPU and highest-RAM node without opening details, with at least four succeeding within 15 seconds; record individual completion times/errors and the result in `docs/node-usage-monitor.md`. Do not substitute synthetic test results for real participants.

  > **Status 2026-09-29 — WAIVED/CLOSED at user's direction; NOT RUN.** Requires five real
  > operators; automated browser checks are not a substitute. This checkbox records
  > user-directed closure only, not a passing usability result.

## Dependencies & Execution Order

### Phase and task dependencies

```text
T001
  -> T002 + T003                         Foundation (both required)
  -> T004 -> (T005 + T006)
           -> (T007 + T008) -> T009      US1 layout increment
  -> (T010 + T011) -> T012 -> T013
                   -> T014 -> T015 -> T016   US2 resource summaries
  -> T017 -> (T018 + T019) -> T020 -> T021   US3 contextual behavior
  -> T022 -> T023 -> T024 -> T025        Documentation and complete evidence
```

- T005/T006 start only after T004's regressions exist and their original failures are observed;
  T007/T008 start after both shell/style tasks. T009 verifies the integrated US1 result.
- T012 starts after both US2 test tasks; T013/T014 edit the same NodeMap file and run serially.
  T015 follows the settled summary markup. T016 verifies the integrated US2 result.
- T018/T019 start after T017's new regressions exist; T020 follows both and shares Home with
  T018, so those Home edits are serialized. T021 verifies the integrated US3 result.
- New browser assertions need to fail for the relevant original behavior before implementation;
  existing passing checks remain regressions rather than being weakened to accommodate changes.

### User story dependencies

```text
Setup + Foundation -> US1 -> US2
                         -> US3
US1 + US2 + US3 -> final validation
```

US1 needs no later story to demonstrate its layout goal: its minimal inspector relocation
preserves functionality. US2 uses US1's growing-card geometry. US3 uses US1's contextual shell
but does not depend on US2's metrics logic. Each story's observable outcome has its own check.
The default execution order is US1, then US2, then US3: they share Home/CSS/browser-test files,
so story-level parallel editing would create conflicts despite functional independence.

### Parallel opportunities and examples per story

Each pair below operates on different files after its stated prerequisites; all other listed
task chains are sequential. Execute test/build commands serially even when authoring is parallel.

| Phase/story | Ready after | Parallel example |
| --- | --- | --- |
| Foundation | T001 | T002 OpenDesign/evidence in `docs/node-usage-monitor.md` alongside T003 fixtures in `dashboard/tests/usage-workspace.test.mjs`. |
| US1 | T004 | T005 shell markup in `dashboard/src/routes/+layout.svelte` alongside T006 sizing styles in `dashboard/src/app.css`. |
| US1 | T005 and T006 | T007 Home/control/dialog markup in `dashboard/src/routes/+page.svelte` alongside T008 geometry in `dashboard/src/lib/components/NodeMap.svelte`. |
| US2 | T009 | T010 rendering assertions in `dashboard/tests/usage-live.test.mjs` alongside T011 browser assertions in `dashboard/tests/usage-workspace.test.mjs`. |
| US3 | T017 | T018 history context in `dashboard/src/routes/+page.svelte` alongside T019 dialog styling in `dashboard/src/app.css`. |

## Coverage Map

| Requirement/outcome | Implementation and evidence tasks |
| --- | --- |
| FR-001, FR-010; SC-001, SC-002 | T004-T009, T011, T015, T024 |
| FR-002 | T007, T017, T020 |
| FR-003, FR-004; SC-003 | T010-T016 |
| FR-005, FR-006; SC-004 | T010, T012-T014, T016, T018, T020 |
| FR-007 | T007, T017-T021 |
| FR-008; SC-007 | T004, T008-T009, T011, T017, T020-T021 |
| FR-009 | T007, T017, T020-T021 |
| FR-011; SC-006 | T011, T014-T015, T017, T019-T021, T024 |
| SC-005 | T025 |
| OpenDesign and maintainable documentation | T002, T022, T024 |

## Implementation Strategy

### MVP first

1. Complete T001-T003, then deliver and independently validate US1 through T009.
2. US1 is the smallest demonstrable layout MVP. The recommended P1 release scope is
   **US1 + US2**, through T016, covering both explicit original UI requests.
3. Complete US3 before claiming the complete specification is implemented; its deferred
   requests and context/focus behavior are part of the saved plan.

### Incremental delivery

Implement tests, source, then verification within each story. Reuse current models,
components, callbacks, and formatter; do not create empty model/service/API layers just to
match a generic task template. Keep recording, retention, private interfaces, and collection
coverage governed by feature 002.

### Completion accounting

There are **25 tasks**: Setup **1**, Foundational **2**, US1 **6**, US2 **7**, US3 **5**, and
Polish **4**. Ten tasks are marked `[P]`, forming five disjoint-file pairs. Mark a task complete
only after its required work and verification finish; unavailable OpenDesign, browser, or
human checks remain explicit pending work, not successful results.

---

## Phase 7: Convergence

**Source**: `converge` assessment on 2026-09-29 against [spec.md](spec.md), [plan.md](plan.md),
[data-model.md](data-model.md), [contracts/workspace-ui.md](contracts/workspace-ui.md) and
`.specify/memory/constitution.md`, after `/speckit.implement` completed Phase 1–6.

**Scope**: closes gaps left by implementation. It does not restate, renumber or alter Phase 1–6
tasks. `T002`, `T024` and `T025` remain open (they need an OpenDesign project, a screen reader,
and human participants respectively) and are intentionally not duplicated.

- [X] T026 [HIGH] Extend the real-browser full-area assertions in `dashboard/tests/usage-workspace.test.mjs` to run at 1440x900 and 1920x1080 in addition to 1280x720, verifying per [SC-001](spec.md) that the workspace top equals the header bottom and its bottom equals the viewport (within 2px), the graph has no permanent side rails and no 420px height cap, and `documentElement.scrollWidth <= innerWidth`; resolve any failure in `dashboard/src/app.css` or `dashboard/src/routes/+layout.svelte` (missing).

- [X] T027 [HIGH] Add keyboard-only assertions at 390x844 to `dashboard/tests/usage-workspace.test.mjs` that Tab-reach a `.node-map-list` entry, Enter-select it, open History from `.usage-toolbar-actions`, inspect an exact UTC reading, dismiss the dialog (focus returns to the trigger) and reach Chat, with visible focus and zero page-wide horizontal overflow, per [SC-006](spec.md) and `quickstart.md` section C (missing).

- [X] T028 [MEDIUM] Make long device/host labels coexist with resource values in `dashboard/src/app.css` — allow `.usage-workspace .node-metric .k` to shrink and wrap (`min-width: 0`, `overflow-wrap: anywhere`) and confirm `.usage-workspace .node-metric .v` keeps its full text inside `.node-map-card` — and add a browser regression in `dashboard/tests/usage-workspace.test.mjs` using an overlong `GPU <id> · <name>` fixture that asserts every card's value text stays within the card's bounds, per [FR-004](spec.md), [FR-010](spec.md) and the "resource values are not ellipsized" rule in [contracts/workspace-ui.md](contracts/workspace-ui.md) (partial).

- [X] T029 [MEDIUM] Surface operational errors other than usage/apply/recording in the compact toolbar while the cluster-controls disclosure is closed: render `appStore.status.update.workers[].error`, `appStore.status.netsec_warnings` and a non-default `appStore.status.overlay_status` in `.usage-toolbar-main` in `dashboard/src/routes/+page.svelte`, keeping them also in the disclosure, per [FR-009](spec.md) and "Operational errors ... remain evident in compact status even with controls closed" in [contracts/workspace-ui.md](contracts/workspace-ui.md) (partial).

- [X] T030 [MEDIUM] Assert selection stability in `dashboard/tests/usage-workspace.test.mjs`: after selecting a node, verify the same node keeps `aria-pressed="true"` (and `appStore.selectedNodeId` is unchanged) through at least two routine poll intervals and after pan/zoom and explicit Fit, per [FR-008](spec.md) and [SC-007](spec.md) (partial).

**Checkpoint (2026-09-29)**: Phase 7 is complete. T026–T030 were implemented and verified —
`npm --prefix dashboard run check` (0 errors/warnings), `node --test --test-concurrency=1
dashboard/tests/*.test.mjs` (8 passed, 0 failed, 0 skipped), `npm --prefix dashboard run build`
(completed). The T028 regression was confirmed to fail against the previous CSS before the fix.
At that checkpoint, T002/T024/T025 were open; T002 was subsequently resolved by registering
the OpenDesign artifact. T024/T025 were later closed by user-directed waiver; their checks
remain unperformed as recorded on those tasks.

---

## Phase 8: Convergence

**Source**: `converge` assessment on 2026-09-29 (second pass) against [spec.md](spec.md),
[plan.md](plan.md), [data-model.md](data-model.md),
[contracts/workspace-ui.md](contracts/workspace-ui.md) and `.specify/memory/constitution.md`,
after `/speckit.implement` completed Phase 7.

**Scope**: closes the two gaps found in this pass. It does not restate, renumber or alter any
Phase 1–7 task. At convergence time, `T024` (screen-reader + live-hardware walkthrough) and
`T025` (SC-005 five-operator study) were already tracked and were not duplicated.

- [X] T031 [MEDIUM] Stop presenting `appStore.status.overlay_status` as a warning in the compact toolbar of `dashboard/src/routes/+page.svelte`: the agent sets it to healthy or neutral values (`up <addr> (listen <port>)`, `disabled` — see `python/kllm/agent/__init__.py:1425,1480`), so render it as informational/muted text (or omit it when healthy) and keep the warning colour reserved for genuine problems; add a browser assertion in `dashboard/tests/usage-workspace.test.mjs` that a healthy `overlay_status` is not given the warning colour while `netsec_warnings` and update errors still are, and align the wording in `docs/node-usage-monitor.md`, per [FR-011](spec.md) (contradicts).

- [X] T032 [MEDIUM] Add the outstanding responsive edge-case assertions to `dashboard/tests/usage-workspace.test.mjs`: emulate `prefers-reduced-motion: reduce` via `Emulation.setEmulatedMedia` and assert the nonessential workspace transitions are suppressed, emulate 200% browser zoom (a ~640px-wide CSS viewport) and a short-landscape window and assert no page-wide horizontal overflow with all controls and node summaries still reachable; in short landscape verify Fit contains all cards and zoom-out reduces the fitted scale (remove any minimum-scale clamp that violates Fit), per [FR-011](spec.md), the spec Edge Cases ("Browser zoom, narrow landscape windows … reduced-motion preferences") and [T024](#phase-6-polish--cross-cutting-concerns) (missing).

**Checkpoint (2026-09-29)**: Phase 8 is complete. T031–T032 pass `npm --prefix dashboard run
check` (0 errors, 0 warnings), `node --test --test-concurrency=1 dashboard/tests/*.test.mjs`
(12 passed, 0 failed, 0 skipped; browser test executed) and `npm --prefix dashboard run build`.
T032 exposed and fixed the short-landscape Fit/zoom floor issue. T024 and T025 were later
closed at the user's direction with explicit waivers; neither human-only check was performed.


