---
description: "Executable tasks for the interactive node and process usage monitor"
---

# Tasks: Interactive Node and Process Usage Monitor

**Input**: `specs/002-node-usage-monitor/{plan.md,spec.md,research.md,data-model.md,quickstart.md,contracts/usage-monitor.md}` and `.specify/memory/constitution.md`

**Tests**: Required by the feature's validation quickstart and the constitution for new non-trivial logic. Add behavioral checks before each implementation increment; use isolated `KLLM_HOME`, fake clocks/processes/network/sensors and existing pytest/Node test runners.

**Organization**: Shared collection and private transport first; then independently verifiable live inspection, recorded history, and the exo-inspired workspace. Paths are repository-relative. Do not add a new dependency or publicly expose RPC port 50052.

## Format: `[ID] [P?] [Story] Description`

- `[P]` means the task can be done alongside other currently ready `[P]` tasks in different files.
- `[US1]`, `[US2]`, `[US3]` correspond to the three stories in `spec.md`.
- Tests precede implementation within each phase; verify the new checks fail before satisfying them.

## Phase 1: Setup (Existing Project)

**Purpose**: Establish the existing paths and implementation constraints; Python uses uv and the dashboard uses its current npm/Svelte setup.

- [X] T001 Trace tracked-PID mutations and `/v1/status`, heartbeat, worker→master proxy and SPA mounting in `python/kllm/agent/__init__.py`, `python/kllm/membership.py` and `python/kllm/state.py`; trace existing home controls/chat and shared-memory presentation in `dashboard/src/routes/+page.svelte`, `dashboard/src/lib/format-memory.ts` and `specs/001-fix-vram-layer-default/data-model.md` before editing these files.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Run the private two-second collector without a browser and durably accept only trusted managed-process readings; both P1 stories need this foundation.

- [X] T002 [P] Add deterministic behavior checks in `tests/test_usage.py` for one saturated core on eight logical CPUs = 12.5% machine CPU, 2 GiB RSS, initial CPU `null`/`initializing`, supported host measurements, missing sensors, PID reuse, agent restart continuing the same actual-start lifetime, duplicates/invalid/late data, gaps, recording failure and crash-safe oldest-first 24-hour/256 MiB physical retention.
- [X] T003 [P] Add private boundary and liveness checks in `tests/test_dashboard_api.py` and `tests/test_membership.py` for authenticated registered-worker ingest, mismatched identity/oversize/invalid payload rejection, no liveness update on ingest, worker forwarding, 20-second membership sweep, and history surviving disconnect/rejoin.
- [X] T004 Implement the trusted local two-second sampler in `python/kllm/usage.py`: sample only `ClusterState.process_pid`/`process_role`, verify `psutil.Process.create_time()` every time, normalize delta CPU by `psutil.cpu_count(logical=True)`, prime the first CPU sample as unavailable, read process RSS and host CPU/RAM, and probe identified devices only where supported with bounded work. Preserve `data-model.md` constraints: "PID must be tracked by the local agent; never enumerate unrelated processes." "Agent restart reads actual start identity and continues the same lifetime." "Unsupported metrics are `null` with availability reason." Label process/host/device scope, UTC `Z` times, units and measurement errors; never collect command lines, prompts, credentials or other processes.
- [X] T005 Implement master-owned transactional `home_dir()/usage.sqlite3` storage in `python/kllm/usage.py` with indexed retained node/lifetime/reading/coverage metadata, safe concurrent access and clean close. Follow `data-model.md`: "All persisted timestamps are UTC instants; wire times are ISO 8601 UTC with `Z`. Raw memory is bytes, CPU/device utilization is a percentage of the stated scope (0–100), temperature is °C, power is W." Enforce these `data-model.md` constraints: "Unique `(lifetime_id, observed_at)` for an accepted process sample; scopes are `process`, `host`, `device`." "Non-finite/negative/out-of-range values and mismatched node/PID/creation identity are invalid, not zero." "Late valid data can appear in history without replacing the latest observation." "No prompts, responses, credentials, command lines, or unrelated processes." Store lifetime state `running`/`ended`/`unobserved`, time-bounded known model/assignment associations, and coverage `recording`/`interrupted`/`failed`, `last_successful_save`, gaps and `storage_truncated`. Preserve: "Exit or PID reuse closes the old observed lifetime; no subsequent samples attach to it. Worker lifetime need not change when its model association changes." "Empty history before monitoring began is `unrecorded`. Missing intervals remain gaps; gaps are not readings with zero values. Entire cluster has a rolling 24-hour window and one 256 MiB physical budget including SQLite sidecars." Commit healthy samples within ten seconds; prune oldest readings beyond either bound and reclaim pages without destroying newer data; surface read/write errors without stopping inference.
- [X] T006 Propagate the local tracked process's actual start identity in `python/kllm/agent/__init__.py` and `python/kllm/membership.py` via existing member/heartbeat snapshots; retain the `node_id` stable key, distinguish duplicate display hosts, validate a worker's asserted PID/start identity against its registered snapshot, and preserve existing 20-second sweep/25-second disconnected-state behavior. Follow `data-model.md`: "`node_id` is saved in `ClusterState`. Display host is not a key. Membership comes from master status; departed node metadata persists only while associated retained history is needed. A rejoin with the same ID updates a single live card." Never make usage ingest act as a heartbeat.
- [X] T007 Start/stop a separate collector loop in `python/kllm/agent/__init__.py` from `lifespan` (independent of `_tick_membership`, dashboard tabs and slow capacity probes), forward worker batches with `_master_control_base()`/`http_clients.control_client()` and `X-Kllm-Key`, and add master-only `POST /v1/usage/ingest` before `_mount_dashboard`. Use the same validation/store for master-local readings; reject unregistered/role-mismatched senders, unexpected fields, >8 points or >64 KiB, >30-second future or >24-hour-old timestamps, bad scopes/PID/start identity, non-finite/out-of-range percentages/bytes/watts/temperature; accept duplicate points idempotently, return accepted/duplicates/last successful durable save, sanitized 4xx or 503 errors, and never interrupt inference on collection/transport/storage failure.
- [X] T008 Run `uv run pytest -q tests/test_usage.py tests/test_dashboard_api.py tests/test_membership.py` after the shared sampler/ingest work, fixing any regression in `python/kllm/usage.py`, `python/kllm/agent/__init__.py` or `python/kllm/membership.py` before beginning either story.

**Checkpoint**: An agent with no dashboard open collects trusted local readings; the master commits its own and authenticated worker samples, rejects malformed data and retains prior history on errors.

---

## Phase 3: User Story 1 — Explore the Live Cluster and Inspect a Process (Priority: P1) 🎯 MVP

**Goal**: See actual master/worker relationships and scoped, timestamped process/host/device measurements; inspect a node without changing workloads.

**Independent Test**: With a master and three registered workers (including duplicate hostnames), fetch live readings and select each node; confirm one core of eight = 12.5%, RSS 2.0 GiB, 10-second stale text, unsupported sensors unavailable, no worker↔worker edges or invented per-node compute.

### Tests for User Story 1

- [X] T009 [P] [US1] Add `GET /v1/usage/live` HTTP checks in `tests/test_dashboard_api.py` for master and worker-served same-origin reads, stable node IDs, role/known relationships, measured vs unavailable and fresh vs 10-second stale values, disconnected-but-retained selection, no managed process, and master-unreachable errors without a browser join key.
- [X] T010 [P] [US1] Add a focused Svelte/Node behavior check in `dashboard/tests/usage-live.test.mjs` for node selection surviving refresh, exact scoped/aged GiB text, only master→worker relationships, measured zero vs missing data, and no workload-mutating request from map interaction.

### Implementation for User Story 1

- [X] T011 [US1] Implement `GET /v1/usage/live` in `python/kllm/agent/__init__.py` from master membership plus retained `python/kllm/usage.py` metadata: stable IDs, master→worker links only, latest accepted observation (not latest receipt), measured process/host/device values and availability reasons, age/freshness threshold 10 seconds, node disconnected state, explicit no-managed-process state and recorder status/error/last save. Proxy worker-served reads server-side via the existing private master route; do not leak a join key or substitute worker-only history.
- [X] T012 [US1] Add exact `/v1/usage/live` Node, relationship, managed lifetime, scoped nullable measurements and recorder wire types in `dashboard/src/lib/types.ts`, including `running`/`ended`/`unobserved`, `fresh`/`stale`/`unavailable`, and UTC timestamps; keep `Status` and existing control contract compatible.
- [X] T013 [US1] Fetch live measurements independently from `/v1/status` in `dashboard/src/lib/store.svelte.ts` while the workspace is open, preserving existing `viewState` loading/idle/offline distinction, selection and last readings on transient errors; never treat disconnected or >10-second-old readings as healthy or trigger collection from the browser.
- [X] T014 [P] [US1] Create selectable `dashboard/src/lib/components/NodeMap.svelte` with stable-ID-keyed buttons/cards, known master→worker decorative SVG edges plus textual equivalents, pan/zoom/Fit controls, selection and viewport retained on updates, duplicate-host disambiguation, accessible labels and no network/membership mutations. Respect the data-model rule "Only registered master→worker links. Lines express known cluster membership, never measured traffic, physical adjacency or a worker→worker link."
- [X] T015 [P] [US1] Create `dashboard/src/lib/components/NodeUsageDetails.svelte` showing PID/role/actual start/state/observation time, process CPU as % of total logical machine and process RSS separately from host CPU/RAM and device-wide used/total RAM, utilization/°C/W. Use `formatGib` from `dashboard/src/lib/format-memory.ts` for one-decimal binary GiB; distinguish capacity vs measurement vs `last_plan` estimate, mark unified RAM `shared` and count it once, label incomplete totals `partial`, report unsupported/failed/stale/disconnected readings as text rather than zero, and label assignment and cluster activity separately from measured local work.
- [X] T016 [US1] Replace the topology table with `NodeMap` and `NodeUsageDetails` in `dashboard/src/routes/+page.svelte`, preserving current Start/Stop/Join/Leave/Update disable rules, `/run` navigation and chat; show genuine loading, empty, idle and offline states rather than demo nodes. Run `node --test dashboard/tests/*.test.mjs` and `npm --prefix dashboard run check` after integration.

**Checkpoint**: Story 1 is inspectable on its own through `/v1/usage/live` and the home map, without history filters.

---

## Phase 4: User Story 2 — Review Recorded Process Usage (Priority: P1)

**Goal**: Inspect saved usage by node/lifetime/window after closing the dashboard, stopping a process, disconnecting or restarting an agent; see gaps and retention/failure coverage.

**Independent Test**: Collect changing samples for five minutes with all browser tabs closed, stop/restart the child (including simulated PID reuse), restart monitoring and query a departed node directly via history endpoints; verify distinct lifetimes, UTC exact points, gaps, truncation and retained history without using the map.

### Tests for User Story 2

- [X] T017 [P] [US2] Extend `tests/test_usage.py` with isolated multi-lifetime history, bounded 15m/1h/24h aggregation (measured zero count >0 vs missing count 0), exact seek pagination, cursor/filter mismatch, stopped/departed-node metadata, storage-limited earliest coverage, interrupted recording, partial readable history after failure and workload associations that do not force a worker restart.
- [X] T018 [P] [US2] Add `GET /v1/usage/lifetimes`, `/series` and `/readings` HTTP contract tests in `tests/test_dashboard_api.py`: unknown node 404; bad window/cursor/limit 400/422 with actionable detail; worker server-side proxy and master unavailable error; <=720 plotted buckets, `limit` 1–1000, opaque filter-bound cursor, UTC and error/coverage fields without credentials.
- [X] T019 [P] [US2] Add `dashboard/tests/usage-history.test.mjs` behavior checks for node→lifetime→15m/1h/24h selection, bounded accessible timestamped point navigation, gaps vs zero, stopped-process inspection, recording-failed message and no automatic repeated full-history fetch on status polls.

### Implementation for User Story 2

- [X] T020 [US2] Implement indexed filtered lifetime listing, <=720 time buckets with process CPU/RSS min/max and scoped optional values, explicit empty buckets/gaps, and exact `(observed_at, id)` seek pagination in `python/kllm/usage.py`; accept only node/lifetime pairs and `15m`/`1h`/`24h`, bound pages to 1–1000, bind opaque cursors to filters and fixed window bounds, return earliest/latest/time-zone UTC, `storage_truncated` and recorder state; expose `unrecorded` before collection began.
- [X] T021 [US2] Implement read-only `GET /v1/usage/lifetimes`, `GET /v1/usage/series` and `GET /v1/usage/readings` in `python/kllm/agent/__init__.py`, validating the contract's node/lifetime/window/limit/cursor input, proxying worker requests privately through `_master_control_base()` with the server-side key, mapping missing/invalid/failed/master-unreachable reads to actionable 404/4xx/503 errors without hiding previously committed readable points.
- [X] T022 [US2] Add lifetime, coverage, time-bucket, exact-reading and opaque-cursor response types from `specs/002-node-usage-monitor/contracts/usage-monitor.md` to `dashboard/src/lib/types.ts`; preserve nullable measurements, scoped units and UTC times rather than coercing gaps to zero.
- [X] T023 [US2] Add explicit node/lifetime/window-filtered history fetches and bounded exact-reading pagination in `dashboard/src/lib/store.svelte.ts`; fetch on selection/filter/page changes rather than each status tick, retain inspection across a disconnected selected node, surface `recording`/`interrupted`/`failed`, last save, read errors and shortened coverage.
- [X] T024 [US2] Create `dashboard/src/lib/components/UsageHistory.svelte` with lifetime and 15m/1h/24h controls, bounded process CPU/RSS and optional host/device visualization, disconnected gaps, exact paginated UTC timestamp/value/units selectable by keyboard, explicit `unrecorded`/storage-truncated/failed status and stopped-process end; do not connect a plot across empty buckets.
- [X] T025 [US2] Integrate `UsageHistory` with the selected node in `dashboard/src/routes/+page.svelte` without making the history API depend on the map; keep selected departed nodes inspectable and existing controls/chat intact. Run `uv run pytest -q tests/test_usage.py tests/test_dashboard_api.py tests/test_membership.py`, `node --test dashboard/tests/*.test.mjs` and `npm --prefix dashboard run check`.

**Checkpoint**: All Story 2 assertions work through the history API even without the node map; the home workspace adds optional navigation to that history.

---

## Phase 5: User Story 3 — Use an Exo-Inspired Monitoring Workspace (Priority: P2)

**Goal**: Deliver the requested visual direction while preserving Kluster LLM workflows and accessible narrow-screen/keyboard inspection.

**Independent Test**: Compare the implemented home workspace to the recorded feature-specific OpenDesign artifact, then inspect a node and exact history point with keyboard only at 1280×720 and 390×844, with reduced motion and empty/offline states; verify controls, `/run` and chat are still reachable.

### Design prerequisite

- [X] T026 [US3] Generate and review the requested feature-specific OpenDesign artifact using the supplied image/exo reference and `dashboard/DESIGN.md` as context **before visual implementation**; record its project/artifact/preview reference and reviewed decisions in `docs/node-usage-monitor.md`. Use the OpenDesign generation workflow, not an unreviewed generic theme or a claim that the artifact already exists.

### Tests for User Story 3

- [X] T027 [P] [US3] Add `dashboard/tests/usage-workspace.test.mjs` behavior checks for keyboard-reachable node/history controls, textual statuses and scoped readings in loading/empty/offline/failed states, retained cluster action disable rules, and no unsupported split-mode choices in the home workspace.
- [X] T028 [P] [US3] Extend `dashboard/tests/store.test.mjs` with a regression check that model planning/launch stays on `/run`, existing start/stop/join/leave/chat availability is unchanged, and monitoring refresh/selection sends no workload mutation requests.

### Implementation for User Story 3

- [X] T029 [US3] Apply feature-local high-contrast amber/yellow accents, visible focus and reduced-motion treatment to monitoring selectors/cards/edges in `dashboard/src/app.css` and `dashboard/src/lib/components/NodeMap.svelte`; keep the global Kluster LLM theme and existing navigation identity.
- [X] T030 [US3] Refine `dashboard/src/routes/+page.svelte` with the reviewed node-centered layout, monospaced telemetry, fill indicators, adjacent details and 390px stacked/list fallback without page-wide horizontal scroll; keep cluster controls, `/run` access and chat operable at 1280×720 and 390×844.
- [X] T031 [US3] Verify keyboard-only map selection/Fit/history point inspection, textual status without color/hover, WCAG AA text contrast and reduced motion against `specs/002-node-usage-monitor/quickstart.md`; record the two viewport results and any unavailable browser/hardware checks in `docs/node-usage-monitor.md`.

**Checkpoint**: The reviewed visual reference, 390px layout, keyboard experience and existing product workflows all remain usable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Operational documentation and reproducible validation after the selected story increments are integrated.

- [X] T032 Document privacy/private-cluster boundary, `KLLM_HOME/usage.sqlite3`, 24-hour/256 MiB cluster retention, recording failure/coverage messages, supported/unsupported sensors and operator history workflow in `docs/node-usage-monitor.md`; link it from `docs/cli.md` or `README.md` where operators already find dashboard guidance.
- [X] T033 Run the automated quickstart commands from `specs/002-node-usage-monitor/quickstart.md` (`uv run pytest -q tests/test_usage.py tests/test_dashboard_api.py tests/test_membership.py`, `node --test dashboard/tests/*.test.mjs`, `npm --prefix dashboard run check`, `npm --prefix dashboard run build`) and record commands/results, remaining portability/hardware checks, and any failures in `docs/node-usage-monitor.md`.
- [X] T034 Validate the available Windows managed-child process sampling, five-minute dashboard-closed persistence, physical SQLite + sidecar retention and privacy with isolated checks in `tests/test_usage.py` and `tests/test_dashboard_api.py`; document actual results and exact follow-up commands for unavailable Linux/macOS, 1/4/16-node, hardware sensor, matched five-before/five-after inference performance and human usability scenarios in `docs/node-usage-monitor.md` without claiming unrun scenarios passed (cluster validation deferred by user).

---

## Dependencies & Execution Order

### Phase and story dependency graph

```text
Setup T001 → Foundation T002–T008 → US1 T009–T016 (MVP)
                             └──────→ US2 T017–T024 (API/component independent of map)
US1 T016 + US2 T024 → US2 home integration T025 → US3 T026–T031 → Polish T032–T034
```

- **US1 (P1)** starts after Foundation; its `/v1/usage/live` route and components work without history UI.
- **US2 (P1)** starts after Foundation for history tests, queries and standalone component; `T025` also needs `T016` because both integrate into the existing home page. Edits to shared `python/kllm/usage.py`, `python/kllm/agent/__init__.py`, `dashboard/src/lib/types.ts`, `dashboard/src/lib/store.svelte.ts` and `tests/test_dashboard_api.py` must be sequenced even if different people build US1/US2. Its history API remains independently testable without the map.
- **US3 (P2)** requires the working live/history increments; finish `T026` and review the design reference before styling in `T029`/`T030`.
- **Polish** follows the desired stories; T033/T034 evidence can be collected separately after implementation but both append to `docs/node-usage-monitor.md` sequentially.
- Within a story, write its tests first, then implement model/store → service/API → typed client → UI → integration; run the phase checkpoint before moving on.

### Parallel examples per story

- **US1**: After Foundation, `T009` (`tests/test_dashboard_api.py`) and `T010` (`dashboard/tests/usage-live.test.mjs`) can be authored together; once `T012` is complete, `T014` (`NodeMap.svelte`) and `T015` (`NodeUsageDetails.svelte`) can be implemented together.
- **US2**: After Foundation, `T017` (`tests/test_usage.py`), `T018` (`tests/test_dashboard_api.py`) and `T019` (`dashboard/tests/usage-history.test.mjs`) can be authored together. Do not overlap US1's edit of `tests/test_dashboard_api.py` with T018.
- **US3**: After design review T026, `T027` (`dashboard/tests/usage-workspace.test.mjs`) and `T028` (`dashboard/tests/store.test.mjs`) can be authored together; run them before T029/T030. Visual changes to `NodeMap.svelte` must wait for US1's T014.

## Implementation Strategy

1. **MVP first**: T001–T016. Validate Story 1 independently with authentic, scoped live values and existing controls.
2. **Recorded increment**: T017–T025. Validate five minutes without a dashboard, stopped/departed lifetimes, gaps, retention and restart using the history API directly before relying on home navigation.
3. **Workspace increment**: T026–T031. Review the requested OpenDesign artifact, then deliver and check the responsive, accessible visual treatment.
4. **Release evidence**: T032–T034. Update operator docs, run automated checks and record real hardware/platform/performance evidence and precise unverified follow-ups.

## Phase 7: Convergence

- [X] T035 CRITICAL Expose sanitized worker collection/ingest transport errors and failed master liveness-history writes as interrupted or failed telemetry with last successful save in python/kllm/agent/__init__.py; verify independent inference and actionable status in tests/test_dashboard_api.py per Constitution IV (contradicts)
- [X] T036 CRITICAL Make multi-point /v1/usage/ingest acceptance atomic across validation, durable write and retention in python/kllm/agent/__init__.py and python/kllm/usage.py; test later-point storage failure, duplicates and no partial success in tests/test_dashboard_api.py per Constitution IV (contradicts)
- [X] T037 CRITICAL Validate opaque cursor start/end against its node, lifetime, selected 15m/1h/24h window and bounded seek position in python/kllm/usage.py; test forged cursors cannot widen filtered history in tests/test_usage.py and tests/test_dashboard_api.py per Constitution IV (contradicts)
- [X] T038 CRITICAL Return sanitized 503 rather than 400 or uncaught 500 for damaged persisted observation timestamps and coverage metadata in python/kllm/usage.py and python/kllm/agent/__init__.py; keep unaffected records readable and check the HTTP boundary in tests/test_dashboard_api.py per Constitution IV (partial)
- [X] T039 CRITICAL Add an isolated worker-to-master authenticated ingest, durable SQLite save and worker-proxied history-read round-trip check in tests/test_dashboard_api.py through the real private control API boundary in python/kllm/agent/__init__.py per Constitution III (partial)
- [X] T040 CRITICAL Add deterministic success, unsupported partial sensor, subprocess timeout and per-device scope checks for python/kllm/usage.py device probing in tests/test_usage.py per Constitution II (partial)
- [X] T041 CRITICAL Add a runnable dashboard interaction check for keyboard node and point selection, map pan/zoom/Fit, refresh stability and narrow-screen/empty/offline states in dashboard/tests/usage-workspace.test.mjs using the existing dashboard test tooling per Constitution II (partial)
- [X] T042 Detect observed child exit or PID reuse even when ClusterState.process_pid remains set, close its lifetime without fabricating zero, and report collection failure in python/kllm/agent/__init__.py and python/kllm/usage.py; check process death between samples in tests/test_usage.py per FR-003 (partial)
- [X] T043 Enforce the single physical 256 MiB database-plus-sidecar cap before acknowledging a durable sample, including failure with no reclaimable readings, in python/kllm/usage.py; check crash/restart recovery, oldest-first pruning and preserved prior rows in tests/test_usage.py per FR-010 (partial)
- [X] T044 Retain bounded backend/device/capacity and known assignment context for departed node cards in python/kllm/usage.py and python/kllm/agent/__init__.py, and show assignment on dashboard/src/lib/components/NodeMap.svelte; verify rejoin and duplicate-host identity in tests/test_dashboard_api.py and dashboard/tests/usage-live.test.mjs per FR-001 (partial)
- [X] T045 Aggregate available scoped device measurements without overwriting earlier bucket values in python/kllm/usage.py; render process RSS and available host/device readings alongside CPU with inspectable units, UTC and gaps in dashboard/src/lib/components/UsageHistory.svelte and dashboard/tests/usage-history.test.mjs per FR-011 (partial)
- [X] T046 Keep the displayed history series, exact points, selected point and pagination bound to the current node/lifetime/window while fetches fail or complete out of order in dashboard/src/lib/store.svelte.ts and dashboard/src/lib/components/UsageHistory.svelte; check filter changes and stale responses in dashboard/tests/usage-history.test.mjs per FR-011 (partial)
- [X] T047 Apply authoritative membership timeout before composing GET /v1/usage/live in python/kllm/agent/__init__.py so a worker last seen more than 25 seconds ago cannot appear connected on that endpoint alone; retain its history and test direct live reads in tests/test_dashboard_api.py per FR-006 (partial)
- [X] T048 Compute Fit from actual node bounds and visible map dimensions rather than resetting scale to 1 in dashboard/src/lib/components/NodeMap.svelte; check all nodes fit at 1 and 16 nodes without changing workloads in dashboard/tests/usage-workspace.test.mjs per FR-002 (partial)
- [X] T049 Close time-bounded model/workload associations at observed process end and surface each known association interval in python/kllm/usage.py and dashboard/src/lib/components/UsageHistory.svelte; test worker model changes without lifetime splits in tests/test_usage.py per FR-012 (partial)
- [X] T050 Reject host/device readings whose nullable values contradict their measured/error/unsupported availability, and attach known unavailable reasons for partial sensor results in python/kllm/usage.py; display field-level reasons in dashboard/src/lib/components/NodeUsageDetails.svelte and test invalid and unsupported inputs in tests/test_usage.py per FR-004 (partial)
