---
description: "Executable story-based tasks for dashboard live updates"
---

# Tasks: Dashboard Live Updates

**Input**: Design documents from `specs/005-dashboard-live-updates/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/dashboard-live.md](contracts/dashboard-live.md), [quickstart.md](quickstart.md)
**Tests**: Include the observable acceptance/regression checks specified by the feature's User Scenarios & Testing and success criteria, the plan/quickstart, and constitution II–III. Reuse pytest, Node's built-in runner, and the existing CDP browser harness. No new test framework.
**Organization**: Shared transport foundation, then four independently verifiable user-story increments. All four stories are P1; follow their specification order. Full automatic recovery across every area is completed in US4.
**Evidence**: Implementation creates `specs/005-dashboard-live-updates/validation.md` for actual commands, results, measurements, and unavailable prerequisites. Do not rewrite the planned quickstart into a results claim.

## Format: `[ID] [P?] [Story] Description`

- All task checkboxes start unchecked; completion means implementation and the task's required verification actually succeeded.
- `[P]` means eligible for the explicitly identified parallel window once its prerequisites are complete, not permission to ignore dependency or file conflicts.
- `[US1]`–`[US4]` map directly to the specification's stories. Setup, foundation, and final tasks have no story label.
- Literal field constraints from the data model are quoted in the implementing task descriptions below. The linked contract remains authoritative for message examples and error codes.

## Path Conventions

- All paths are relative to the repository root: Python under `python/kllm/`, UI under `dashboard/`, Python checks under `tests/`.
- New production modules are `python/kllm/agent/live.py` and `dashboard/src/lib/live-updates.ts`; use the existing domain modules for operation and log semantics.
- New test files are `tests/test_dashboard_live.py` and `dashboard/tests/live-updates.test.mjs`. Other named source/test files already exist.
- No dependency, database migration, native ABI, llama tag, or public RPC-port change is planned.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing environment and capture the required before-change evidence before replacing timers.

- [X] T001 Verify the uv-managed Python, Node 22+, dashboard dependencies, and Chrome/Edge prerequisites using `docs/agent-install.md` and `dashboard/package.json`; use `python scripts/install.py` only if setup is missing, and record tool/browser availability and pre-existing failures in `specs/005-dashboard-live-updates/validation.md`.
- [X] T002 Capture the existing ten-minute idle application-state-request baseline for one and five visible dashboard windows, with Logs in Live mode and no actions/recovery, using the measurement procedure in `specs/005-dashboard-live-updates/quickstart.md`; record baseline commit `a0a4f5259c7dfd5c204deb5ff0972f27b8e87c69`, source/render clock method, workload, browser, OS, hardware, node/viewer counts, and raw counts in `specs/005-dashboard-live-updates/validation.md` before changing polling.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the common bounded GET/WebSocket contract and browser acceptance boundary before attaching real story-specific sources.

**Critical**: Complete T003–T011 before story implementation. Use controlled source callbacks in these checks; do not invent sample production telemetry or a general event framework.

### Contract checks first

- [X] T003 [P] Add controlled-source contract checks in `tests/test_dashboard_live.py` for real-Uvicorn GET/WS admission, strict query/control validation, one complete initial frame, shared revisions/acquisition, ACK limits, heartbeat independence, malformed/oversized input, partial area failure, sanitized errors, and shutdown/repeated lifespan cleanup; use an ephemeral port and temporary state, and establish failures for missing behavior rather than environment/import errors.
- [X] T004 [P] Add Node controller checks in `dashboard/tests/live-updates.test.mjs` for whole-frame validation before mutation, stale/equal revisions, superseded connection/subscription callbacks, initial-frame completeness, ACK-after-commit, and late cleanup not releasing a newer request; use controlled clocks and deferred bodies, with no network/GPU dependencies.

### Shared implementation

- [X] T005 Define the common source/envelope validation in `python/kllm/agent/live.py` and matching types in `dashboard/src/lib/types.ts`, following `specs/005-dashboard-live-updates/data-model.md`; validate finite numeric ranges, UTC timestamps, safe integers, required fields, and additive-field compatibility before accepting data.
  Enforce the literal field rules: `version`: “Protocol integer, initially `1`.”; `epoch`: “Canonical UUID, new on lifespan start or role/master identity change.”; `areas`: “Requested subset of `status`, `usage`, `load`, `download`.”; `revision`: “Nonnegative safe integer, monotonically increasing within the epoch on semantic data/availability change.”; `sampled_at`: “UTC timestamp of the latest successful source read, or null before any success; not a new hardware observation time.”; `availability`: “`loading`, `available`, `unavailable`, or `denied`.”; `coverage`: “`local`, `cluster`, or `local_with_cluster_view`; not a connectivity flag.”; `upstream_last_success_at`: “Nullable UTC time for worker-mirrored/proxied information; expose old upstream information as stale/unavailable.”; `data`: “Valid topic payload when available; null for errors/loading. Clients retain their last accepted good value separately.”; `error`: “Null or bounded `{code, message}`; no raw exception, credentials, URL, or payload dump.”
- [X] T006 Implement lifespan-owned source caches, demand leases, and single-in-flight acquisition in `python/kllm/agent/live.py`, integrating ownership in `python/kllm/agent/__init__.py`; share acquisition across viewers/GETs, copy observations atomically per area, reject obsolete role/master/process generations, expire HTTP demand after five seconds, and stop viewer scheduling without stopping inference/recording/collection. Use at-most-once-per-second status/activity/load/usage and 500 ms active download/log acquisition, isolate blocking work from the event loop, and do not release a still-running thread acquisition merely because its await was canceled.
- [X] T007 Implement the GET `/v1/dashboard/live` route and admission in `python/kllm/agent/__init__.py` with bounded serialization in `python/kllm/agent/live.py`: validate unique `topics`, `logs=0|1`, cursor/continuity only with Logs, and `page_origin` as “Optional valid HTTP(S) origin only, maximum 512 characters; no path, userinfo, query, or fragment.”; require at least one topic/Logs, enforce actual/diagnostic Origin independently, allow only the two port-5173 loopback development exceptions, set `Cache-Control: no-store`, and return every requested area after at most two seconds of shared cold-source waiting. Apply the contract's HTTP error classifications and 256-character safe error messages; enforce 1 MiB per state record, 256 KiB complete log result, 64 KiB outer metadata and 5 MiB total, with consistently revisioned `payload_too_large` records rather than truncated state.
- [X] T008 Implement the WebSocket route in `python/kllm/agent/__init__.py` and bounded per-viewer sender in `python/kllm/agent/live.py`, including hello, full replacement subscribe, one `initial:true` frame within three seconds, changed records, ACK, two-second ping/pong with serialization-time `server_time`, four-second silence detection, and five-second send/ACK limits. Enforce subscription “Increasing safe integer within a connection; full replacement of desired topics/log cursor.” and outstanding batch “At most one bounded data frame, identified by subscription generation and batch number.”; require positive safe batch IDs, reject future/invalid ACKs and binary/unknown controls, cap controls at 4 KiB and sustained 20 messages/second, cap data at 5 MiB, keep heartbeat independent of the ACK gate, and use the contract's close codes. Area errors must be revisioned data; standalone errors are connection-level only. No source lock may span a send and no per-viewer log queue may accumulate.
  Preserve the time-field rule: “HTTP responses and socket hello/data/time-probe replies carry `server_time` (UTC at serialization).”
- [X] T009 [P] After T004–T005, implement the plain-TypeScript connection/observation gate in `dashboard/src/lib/live-updates.ts`: idempotent explicit start/stop, no import-time I/O, derived ws/wss serving URL, replaceable interests, complete-frame validation, staged initial commit then ACK, bounded reads, and cancellation-safe cleanup. Enforce connection generation “Browser-local monotonic counter; invalidates old sockets, HTTP completions, timers and cleanup callbacks.” and accepted epoch/revisions “Latest accepted epoch plus per-area revisions. A new epoch is admitted only at the active initialization/recovery boundary.”; accept valid stale/equal records as no-ops without withholding ACK, and never let transport controls invoke commands. This task supplies the shared primitives; US4 adds retry/fallback/visibility policy.
- [X] T010 [P] Enable `ws: true` for the existing `/v1` development proxy in `dashboard/vite.config.ts`, retaining its target and `/health` routing and preserving the browser Origin; do not enable Origin rewriting or add a second public service.
- [X] T011 Run the foundational real-Uvicorn checks in `tests/test_dashboard_live.py` and Node checks in `dashboard/tests/live-updates.test.mjs`, confirming five near-limit requested results fit one initial frame and slow/invalid peers cannot block other consumers; record commands/results in `specs/005-dashboard-live-updates/validation.md` and resolve failures before attaching real sources.

**Checkpoint**: A shared, validated, resource-bounded transport exists and is tested with controlled sources. No user story relies on an untested wire protocol.

---

## Phase 3: User Story 1 — See cluster changes as they become available (Priority: P1) — MVP

**Goal**: Deliver real cluster/status and usage changes to master- and worker-served dashboards without recurring healthy status/usage reads or disrupted operator state.

**Independent Test**: Open master and worker views with known membership and usage; change membership, activity, warnings and readings. Current state appears initially, subsequent changes arrive live, coverage is truthful, and node selection, map position, drafts, and valid plans survive. Block maintenance and age a reading to verify the 25-second membership and ten-second stale limits.

### Checks first

- [X] T012 [P] [US1] Extend `tests/test_dashboard_live.py`, `tests/test_dashboard_api.py`, and `tests/test_membership.py` with shared acquisition counts for one/five viewers, status/usage GET-versus-push races, cached age, worker scope and upstream denial, and a second member expiring while persistence/replanning/probes are blocked; assert no duplicated usage sampler and prompt status/usage disconnection publication.
- [X] T013 [P] [US1] Extend `dashboard/tests/live-updates.test.mjs` and `dashboard/tests/store.test.mjs` with initial-state and explicit-action races, duplicate warnings, selected/departed node retention, unchanged plan fingerprints/drafts, source freshness distinct from delivery health, cached first reads, clock skew/jumps and replayed observations that cannot become younger.

### Implementation

- [X] T014 [US1] Refactor fast status/activity observation in `python/kllm/agent/__init__.py` and connect its shared source to `python/kllm/agent/live.py`; preserve the existing Status payload, separate slow capacity/software/native work into completed caches, share inference-activity acquisition with heartbeat callers, and never run the expensive status route once per viewer or substitute fabricated zero/loading metadata.
- [X] T015 [US1] Split fast membership removal/publication from serialized slow maintenance in `python/kllm/agent/__init__.py` and `python/kllm/membership.py`; retain the 20-second expiry/two-second sweep, publish status and cached usage connectivity immediately after `sweep_liveness`, and coalesce persistence/lifetime closure/replanning/probes behind one in-flight maintenance worker plus pending removals. Guard role/membership generations and rejoined identities, reject older in-flight topology results, and satisfy disconnection within 25 seconds without depending on browser reads.
- [X] T016 [US1] Add shared UsageStore-backed and worker-proxied usage observations in `python/kllm/agent/live.py` and `python/kllm/agent/__init__.py`, reusing `python/kllm/http_clients.py:control_client` and `_master_control_base`; preserve node/process lifetime identity, null-versus-zero, recording health, last saved data and local/mirrored scope. Preserve upstream 401/403 as denied rather than 503, label unavailable upstream coverage, track upstream success, and advance cached age by monotonic residence before serialization.
- [X] T017 [P] [US1] After T013 and the foundation, implement observation-age tracking in `dashboard/src/lib/live-updates.ts` using `server_time`, HTTP/hello/ping round-trip bounds and local monotonic time; “Use the upper age bound, not a synchronized-clock assumption.” and “Equal observation replay never moves its existing aging anchor later.” Make ten-second-old or uncertain readings stale without network polling; heartbeat must not refresh observation age and clock jumps must trigger conservative resynchronization.
- [X] T018 [US1] Integrate validated status/usage apply methods and versioned explicit-action refreshes in `dashboard/src/lib/store.svelte.ts`, root ownership in `dashboard/src/routes/+layout.svelte`, and Home-only usage interest in `dashboard/src/routes/+page.svelte`; remove the status/usage intervals, retain `memberFingerprint`/`fp`, avoid reconnect configuration hydration/remounts, preserve selected node/history even during coverage loss, and route `saveConfig`, `post`, and `updateWorkers` follow-up reads through revision checks without replaying commands.
- [X] T019 [US1] Present live/stale/unavailable source values and local-versus-mirrored coverage using `dashboard/src/lib/components/NodeMap.svelte`, `dashboard/src/lib/components/NodeUsageDetails.svelte`, and `dashboard/src/routes/+layout.svelte`; retain null/zero distinctions, user map position, text status, keyboard focus and existing actions, and do not present stale upstream membership or recording failure as healthy telemetry.
- [X] T020 [US1] Run the US1 Python and Node checks from T012–T013 plus a real-route master/worker snapshot/push smoke, confirming initial state within three seconds, at least 95% of controlled status/usage changes within two seconds, zero healthy recurring reads for those two areas, and preserved operator state; record results and acquisition/clock measurements in `specs/005-dashboard-live-updates/validation.md`.

**Checkpoint / suggested MVP**: Status and usage are a complete live increment. This checkpoint does not claim the feature-wide five-area request reduction or full automatic recovery acceptance, which are validated after the remaining stories.

---

## Phase 4: User Story 2 — Follow model operations to a trustworthy outcome (Priority: P1)

**Goal**: Deliver identified load/download progress and recoverable terminal outcomes, with once-only completion effects and truthful cancellation.

**Independent Test**: Start, finish, cancel and fail representative operations; repeat the same filename/model; open a view mid-operation and disconnect/reconnect it around completion. Verify stable IDs, correct final or unknown outcome, updated model inventory and no repeated auto-selection/notification. Use a controlled reconnect driver here; US4 validates automatic retry policy.

### Checks first

- [X] T021 [P] [US2] Extend `tests/test_model_management_api.py` with accepted-command→executor→progress→terminal checks for distinct consecutive same-name operations, admission before executor work, failure, blocked-transfer cancellation, retained terminal state, restart/supersession and explicit/membership-driven load start/stop; assert terminal identity cannot be overwritten by a late result and ready does not retroactively become load failure.
- [X] T022 [P] [US2] Extend `dashboard/tests/store.test.mjs` with terminal-before-running, initial historical terminal, locally accepted operation ID, duplicate terminal, newer current plus prior terminal, delayed inventory/selection and cancellation races; assert model inventory reconciles once per successful terminal ID while auto-selection/notification remain visit-eligible and unknown outcomes are not fabricated success.

### Implementation

- [X] T023 [P] [US2] After T021–T022, implement download source-owned operation records and atomic admission/terminal transitions in `python/kllm/models.py`, with matching operation types in `dashboard/src/lib/types.ts`; reserve identity before work, permit a new job after terminal state, retain only active/latest-terminal records, use finite transfer waits, and report cancellation only after the work stops.
  Enforce the operation model's literal rules: `operation_id`: “UUID assigned and reserved before accepting work; never derived from filename, model name, or PID.”; `kind`: “`load` or `download`.”; `node_id`: “Serving node identity.”; `state`: “`running`, `complete`, `error`, or `cancelled`.”; `started_at`/`updated_at`/`finished_at`: “UTC timestamps; finish is null while running.”; subject: “Existing model/filename; load also retains the managed process identity when established.”; progress: “Existing bytes/fraction/percentage/rate/ETA/phase fields, with their existing units and null semantics.”; error: “Bounded, sanitized message when failed.”
- [X] T024 [P] [US2] After T021–T022, implement the same operation constraints from T023 for source-owned load state in `python/kllm/load_progress.py`; reserve a new UUID per actual load, guard samples by operation/process identity, latch ready/error/cancelled terminal state, retain active/latest-terminal independently of viewers, and keep post-ready process death separate from completed load outcome.
- [X] T025 [US2] Wire identified admission, success/failure/cancel and accepted response fields through `python/kllm/agent/__init__.py` and every applicable explicit/membership spawn/stop caller in `python/kllm/membership.py`; remove the download admission race/terminal restart blockage and the canceled `None`→`path.stat()` path, keep response changes additive, and preserve existing command behavior.
- [X] T026 [US2] Register load/download sources with `python/kllm/agent/live.py` and expose `{current, last_terminal}` through the new feed using source accessors in `python/kllm/agent/__init__.py`; enforce “`current` is null or the active observation; `last_terminal` is null or the latest completed observation of that kind.” and atomically move current to terminal. Project current, else terminal, else existing idle shape for old progress endpoints, and do not infer success when both retained records are absent.
- [X] T027 [US2] Replace load/download polling with validated operation application in `dashboard/src/lib/store.svelte.ts`; inspect `last_terminal` even while a newer job runs, record handled IDs before inventory reconciliation, restrict auto-selection/notification to observed-running or locally accepted IDs, guard later async selection against user changes, and reconcile cancellation rather than synthesizing success. Show missing tracked outcomes as unknown. Remove `dashboard/src/lib/poll-loop.ts` only after searching all its callers and moving its two consumers.
- [X] T028 [US2] Update `dashboard/src/lib/components/LoadProgressPanel.svelte`, `dashboard/src/lib/components/DownloadPanel.svelte`, and `dashboard/src/routes/+layout.svelte` for current/terminal/unknown outcomes, operation-keyed ready dismissal, and dedicated-download precedence over stale status summaries; preserve keyboard controls and existing load/cancel actions.
- [X] T029 [US2] Run the US2 API and store regressions plus real command/progress feed scenarios, including completion while disconnected, terminal-without-running observation and a second same-name job, and record once-only effects and truthful outcome evidence in `specs/005-dashboard-live-updates/validation.md`.

**Checkpoint**: Load/download behavior is independently verifiable through the shared transport, including retained outcomes and controlled reconnection.

---

## Phase 5: User Story 3 — Read live logs at the chosen pace (Priority: P1)

**Goal**: Push checkpointed Live logs while preserving timed modes, filtering, display-only Clear, bounded rows, coverage and reading position.

**Independent Test**: Deliver the same identified master/worker batches through Live and timed GET; exercise all modes, empty results, repeated text with distinct IDs, Clear before/after first load, delayed bodies, restart/coverage reset and a slow viewer. Validate deadlines, checkpoint ownership, no restored cleared entries, readable retained rows and explicit gaps.

### Checks first

- [X] T030 [P] [US3] Extend `tests/test_cluster_log.py` and `tests/test_dashboard_live.py` with unfiltered snapshot/WS parity, byte/count suffix bounds, future/malformed cursors, empty highwater advancement, master-ID-preserving worker mirror acquisition shared across viewers, atomic reset plus suffix, denied/malformed upstream errors, missed coverage round trips and held-ACK retention loss.
- [X] T031 [P] [US3] Extend `dashboard/tests/logs.test.mjs` with deferred live/GET decode, all refresh deadlines, a stalled state read spanning a log deadline, Clear no-op before success, Clear during active work, baseline-only reset after Clear, namespace handoff, duplicate IDs versus identical text, 2,000-row trimming and late cleanup/generation isolation; use listener sets so hidden/route leaks are observable.

### Implementation

- [X] T032 [US3] Implement the bounded common log snapshot result in `python/kllm/agent/live.py`, reusing `python/kllm/cluster_log.py:LogHub.snapshot` and adding only a scoped helper there if necessary; acquire unfiltered receiver order, preserve IDs/highwater, reject future same-epoch cursors, and emit reset/gap rather than invented continuity. For entries enforce “`event_id` is a canonical UUID; time is finite Unix seconds; `source` is `agent`, `llama`, or `rpc`.”, existing nonempty node/string-message validation and inert text. Retain the newest suffix fitting the complete 256 KiB log-result bound and report omitted rows explicitly; an oversized newest entry may leave an empty suffix with an advanced highwater/gap, never a truncated apparently complete message.
  Enforce literal snapshot rules: `lines`: “Bounded unfiltered suffix, oldest first. First load requests up to 200; incremental delivery caps at 500 and 256 KiB.”; `cursor`: “Opaque canonical `UUID:sequence`, at most 128 characters; highwater through which entries were delivered or explicitly omitted.”; `reset`: “Null, `origin_changed`, or `restart`.”; `gap`/`gap_reason`: “Existing boolean and null / `retention` / `batch_limit` / `feed_reset`.”; snapshot `source`: “`cluster` or `local`.”; `warning`: “Optional safe coverage explanation.”; `continuity_id`: “UUID identifying the current coverage/continuity interval. Changes on upstream reset/gap or source change, including coverage round trips.”; `discontinuity`: “Null or `{id, reason, detected_at}`; sticky for the interval, reasons `upstream_reset`, `upstream_retention`, `upstream_batch_limit`, `coverage_change`.”
- [X] T033 [US3] Implement the shared worker log mirror/source selection in `python/kllm/agent/live.py` and its private upstream accessor in `python/kllm/agent/__init__.py`; use a separate nonpersistent `LogHub` with `ingest(..., persist=False)`/`snapshot()` only, retain upstream cursor privately, atomically reset/ingest/update sticky continuity on upstream gaps, and distinguish local transport-failure fallback from denial/invalid data. Never reset the real local HUB or use its journal/tailing/old follow subscriber machinery for the mirror, and keep the existing CLI stream contract.
- [X] T034 [P] [US3] After T031 and the foundation, add log-result/continuity types in `dashboard/src/lib/types.ts` and validate new GET/WS log results in `dashboard/src/lib/log-trail.svelte.ts` against the exact field rules quoted in T032; retain existing event-ID validation, accept empty successful checkpoints, reject malformed results before mutation, and preserve unknown additive fields without inventing missing required metadata.
- [X] T035 [US3] Integrate LogTrail's single checkpoint owner with `dashboard/src/lib/live-updates.ts` in `dashboard/src/lib/log-trail.svelte.ts`; enforce accepted checkpoint “Advanced only after complete validation and commit; supplied on recovery/replace.” Keep visit-wide seen IDs and one independent log-read lane; Live uses replacement subscriptions, timed modes use 5/30/60-second monotonic GET deadlines without jitter/catch-up, and every discrete read has a five-second dispatch-through-decode deadline. Effective Clear keeps IDs/cursor/filter/mode/deadline, invalidates old work, and resubscribes only in Live; reset after Clear is baseline-only. Preserve 2,000 rendered rows, sticky gap notices, and cleanup ownership checks.
- [X] T036 [US3] Wire mounted Logs interests and update `dashboard/src/routes/logs/+page.svelte` to show content state, delivery health and local/cluster coverage independently, retain selected/departed node options after Clear, remove the misleading Live-one-second label, and preserve newest-first/top-following anchors, complete inert messages, keyboard focus and non-announced routine arrivals.
- [X] T037 [US3] Run the US3 Python, controller and real-transport log scenarios, verifying all four modes, identical-text/distinct-ID handling, Clear isolation from stored logs and a second viewer, stalled-state-read deadline independence, coverage round trips and slow-viewer omission notices; record outcomes in `specs/005-dashboard-live-updates/validation.md`.

**Checkpoint**: Live and timed Logs share tested identities/continuity while retaining visit-local controls. US2 and US3 have no semantic dependency on one another.

---

## Phase 6: User Story 4 — Recover without reloading or repeating work (Priority: P1)

**Goal**: Complete automatic recovery and fallback across all areas, truthful health/access presentation and leak-free visibility/navigation behavior.

**Independent Test**: Blackhole or block only the socket while reads work, deny one area, return incompatible legacy payloads, restart the agent with upgrades still blocked, and repeat in-app navigation/hide/show/reconnect at least 20 times. Validate ten-second failure/fallback/read recovery, 60-second live restoration, retained state, no repeated command/effect, and released viewer work within ten seconds of closure.

### Checks first

- [X] T038 [P] [US4] Extend `tests/test_dashboard_live.py` and `tests/test_cp_proxy.py` with full-source silent/one-way failure, blocked source/send/ACK, source epoch replacement, repeated lifespan, safe denial/malformed upstream behavior, five-viewer isolation and private worker-proxy checks; reuse a controllable real-Uvicorn fixture within the test module for browser-driven runs, with finite startup/shutdown and no additional server framework.
- [X] T039 [P] [US4] Extend `dashboard/tests/live-updates.test.mjs` with two-second HTTP-only recovery, denied-area retry suppression, atomic staged live cutover, complete initial-frame ACK, obsolete area errors, HTTP epoch changes while upgrades stay blocked, parallel legacy diagnostic deadlines, missing operation identity, log cursor namespace reset and no command replay; assert source freshness remains independent of delivery traffic.
- [X] T040 [US4] After T038, extend `dashboard/tests/usage-workspace.test.mjs` to use the controllable real transport fixture, actual in-app navigation and visibility transitions, selective WS failure, five simultaneously visible windows plus chat streams, and response/frame/request counters; add the 20-cycle cleanup, preserved drafts/map/focus, second-viewer Clear isolation, and 1280×720/390×844 assertions, and close all streams/processes during teardown. Treat a missing browser as a recorded skip, not a pass.

### Implementation

- [X] T041 [US4] Complete automatic live/fallback/unavailable recovery in `dashboard/src/lib/live-updates.ts` using four-second foreground silence detection, three-second connection/initialization deadlines, immediate permitted fallback, two-second refresh cadence, five-second dispatch-through-decode limits and a 30-second ±20% single live probe. Enforce HTTP ownership “One non-log aggregate read plus one independently owned log read may be in flight; legacy fallback serializes each old area read.”; continue HTTP retries while unavailable, admit a new epoch through a current-generation GET recovery boundary, stage one complete initial frame before state cutover, and pause Logs before resubscribing from its latest accepted cursor. Never use an action/probe to force early timed-log rows.
- [X] T042 [US4] Implement the contract's legacy compatibility adapters in `dashboard/src/lib/live-updates.ts`, `dashboard/src/lib/store.svelte.ts`, and `dashboard/src/lib/log-trail.svelte.ts`: validate old status/usage/progress/log payloads, serialize versionless area reads, label unidentifiable prior outcomes unknown, suppress unprovable recovery auto-selection, and report missing event-ID/cursor support as unsupported. Treat new/mirror↔legacy log handoff as explicit continuity reset with preserved seen IDs and baseline-only recovery after Clear; run needed legacy candidate reads alongside diagnostics within the ten-second budget, discard them on confirmed denial, and never infer unsupported transport from 1006/HTML/404 alone.
- [X] T043 [US4] Complete visibility/route/teardown ownership in `dashboard/src/lib/live-updates.ts`, `dashboard/src/lib/store.svelte.ts`, `dashboard/src/lib/log-trail.svelte.ts`, and `dashboard/src/routes/+layout.svelte`: one coordinator visibility listener, hidden socket/read/timer cancellation, retained domain state, one foreground recovery, page-interest release and idempotent root disposal. Preserve generation-checked cleanup so aborted old work cannot release newer work; leave recording, collection and workloads independent of viewers.
- [X] T044 [US4] Present per-area delivery/access/recovery state in `dashboard/src/lib/store.svelte.ts`, `dashboard/src/routes/+layout.svelte`, and `dashboard/src/routes/logs/+page.svelte`; enforce delivery health “`loading`, `live`, `reconnecting`, `periodic-refresh`, `unavailable`; paused while hidden.” and per-area health “Availability, last successful update, source freshness/coverage, and safe error separate from delivery health.” Retain last good values/time or never-successful text, show denial without retries until explicit retry/access change, and announce meaningful state changes only with text, preserved focus and narrow-screen readability.
- [X] T045 [US4] Run all US4 failure, legacy, browser and private-proxy checks, including HTTP-only restart, source denial, one slow viewer, 20 lifecycle cycles and commands executed exactly once; verify the ten-second/60-second recovery budgets and closed-view cleanup, then record commands, results and any unmet environment prerequisites in `specs/005-dashboard-live-updates/validation.md`.

**Checkpoint**: Every implemented area has one delivery owner and verified automatic recovery; the remaining phase establishes full regression and measured acceptance evidence.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Finish operational documentation and verify the complete feature against the specification without substituting synthetic checks for hardware/usability evidence.

- [X] T046 [P] Update `docs/cli.md` and `docs/troubleshooting.md` with the actual live/fallback/timed-Logs behavior, delivery-versus-source freshness, unknown operation outcomes, explicit denial, worker coverage and development upgrade diagnostics; keep examples consistent with `specs/005-dashboard-live-updates/contracts/dashboard-live.md` and existing private-cluster commands.
- [X] T047 Run the complete quality-gate command sequence below, sequentially, and record each actual result in `specs/005-dashboard-live-updates/validation.md`; review changes to `python/kllm/agent/live.py`, `dashboard/src/lib/live-updates.ts`, and existing callers for duplicate scheduling, leftover healthy polls, validation omissions and secret-bearing diagnostics. Any discovered packaging/dependency change must follow the supported uv build/import checks rather than an editable-pip shortcut.
- [X] T048 Execute the before/after performance and physical-platform matrix in `specs/005-dashboard-live-updates/quickstart.md`, recording results in `specs/005-dashboard-live-updates/validation.md`: one/five visible viewers, 1/4/16 nodes, five chat streams, 100 aggregate log entries/second for ten minutes with one slow viewer, ≥90% fewer idle state-check requests, 95% live source-to-render ≤2 seconds, initial display ≤3 seconds and controls ≤1 second. Include acquisition/queueing, clock uncertainty and unrendered-event misses; record Windows/Linux/macOS, actual model/backend/hardware and any unverified rows with exact follow-up, keeping incomplete required acceptance open.
- [X] T049 Perform the SC-007 five-operator usability check and keyboard/viewport checks at 1280×720 and 390×844 using the procedure in `specs/005-dashboard-live-updates/quickstart.md`; record whether at least four operators distinguish live/fallback/unavailable/stale within 30 seconds, along with focus, announcements, contrast and overflow results in `specs/005-dashboard-live-updates/validation.md`, or identify the unverified participant checks without claiming a pass.
- [X] T050 Review `specs/005-dashboard-live-updates/tasks.md` and `specs/005-dashboard-live-updates/validation.md` against every FR/SC mapping below and the constitution; confirm task statuses match evidence, CLI/HTTP/chat/history contracts remain compatible, no public RPC exposure or unjustified dependency was introduced, and all required unmet checks have explicit outstanding follow-up before reporting full acceptance.

### Quality-gate commands (T047)

Run from repository root in this order; do not run Svelte check/tests/build concurrently:

```text
uv run pytest -q tests/test_dashboard_live.py tests/test_cluster_log.py tests/test_dashboard_api.py tests/test_model_management_api.py tests/test_membership.py tests/test_cp_proxy.py
npm --prefix dashboard run check
node --experimental-strip-types --test --test-concurrency=1 dashboard/tests/*.test.mjs
npm --prefix dashboard run build
uv run pytest -q
```

---

## Dependencies & Execution Order

### Phase dependencies and story completion graph

```text
Setup T001–T002
       |
Foundation T003–T011
       |
US1 T012–T020  (first useful live increment)
       |
       +-------------------+
       |                   |
US2 T021–T029        US3 T030–T037
       |                   |
       +-------------------+
                 |
         US4 T038–T045
                 |
       Final T046–T050
```

- Foundation blocks story implementation. US1 attaches actual status/usage and the root owner; US2/US3 reuse that working increment and are independently testable siblings.
- US4's cross-area completion gate depends on US2 and US3. Its isolated controller test design can be prepared earlier, but do not claim complete recovery until all five areas are exercised.
- Default execution is numeric order. A `[P]` task may move into a window below only after its listed prerequisites and phase gate are complete.
- US2 and US3 both edit `python/kllm/agent/live.py`, `python/kllm/agent/__init__.py`, `dashboard/src/lib/types.ts`, and shared tests. Parallelize disjoint work, not entire story branches without coordinating these files.
- Test additions precede the matching behavior and must fail for the intended absence/defect; environment/import setup failures are not regression evidence. Each story's final verification must pass before its checkpoint is marked complete.

### Safe parallel windows

| Window | Prerequisites | Tasks / distinct write sets |
|---|---|---|
| Foundation contracts | T002 | T003 Python checks and T004 Node checks. |
| Foundation transport | T003–T005 | T006–T008 server work can overlap T009 browser gate and T010 Vite proxy; T011 joins all. |
| US1 tests | T011 | T012 Python/API/membership checks and T013 Node/store checks. |
| US1 source/age | T012–T013 | T014–T016 backend work can overlap T017 frontend age logic; T018 joins them. |
| US2 tests | T020 | T021 Python operation checks and T022 Node/store checks. |
| US2 operation sources | T021–T022 | T023 download module/types and T024 load module; T025 joins both. |
| US3 tests | T020 and no concurrent edits of shared test files | T030 Python log checks and T031 Node log checks. |
| US3 source/validation | T030–T031 | T032–T033 Python log source/mirror can overlap T034 TS/log validation; T035 joins them. |
| US4 tests | T029 and T037 | T038 Python/private-proxy checks and T039 Node recovery checks; T040 follows T038's fixture. |
| Final docs/checks | T045 | T046 operational docs and T047 sequential quality commands; their output files differ. |

### Parallel Example: User Story 1

After T011, write T012 (`tests/test_dashboard_live.py`, `tests/test_dashboard_api.py`,
`tests/test_membership.py`) alongside T013 (`dashboard/tests/live-updates.test.mjs`,
`dashboard/tests/store.test.mjs`). After both, T017 can proceed while backend T014–T016
run; integrate in T018 only after both sides are ready.

### Parallel Example: User Story 2

After T020, write T021 Python operation checks alongside T022 store checks. After those
checks, T023 (`python/kllm/models.py`, `dashboard/src/lib/types.ts`) and T024
(`python/kllm/load_progress.py`) are disjoint; T025 wires their finished lifecycles.

### Parallel Example: User Story 3

After US1 and with shared Python tests free, write T030 alongside T031. Then T034
(`dashboard/src/lib/types.ts`, `dashboard/src/lib/log-trail.svelte.ts`) can proceed while
T032–T033 implement Python snapshots/mirroring. Do not overlap T034 with US2's type edits.

### Parallel Example: User Story 4

After US2 and US3, write T038 (`tests/test_dashboard_live.py`, `tests/test_cp_proxy.py`)
alongside T039 (`dashboard/tests/live-updates.test.mjs`). T040 consumes the completed
T038 fixture; the implementation tasks then share coordinator/store files and run serially.

---

## Requirement & Contract Coverage

| Requirement / criterion | Owning tasks |
|---|---|
| FR-001, all five areas | T014–T018, T025–T028, T032–T036 |
| FR-002, current state and race safety | T003–T009, T012–T013, T018, T038–T041 |
| FR-003, replace healthy recurring checks | T018, T027, T035, T047–T048 |
| FR-004, preserve selections/drafts/plans | T013, T018–T020, T027, T036, T040 |
| FR-005, truthful identified outcomes | T021–T029, T039, T042 |
| FR-006, refresh modes and deadlines | T031, T035–T037, T039–T041 |
| FR-007, Clear/dedup/bounds/gaps | T030–T037, T040, T042 |
| FR-008, explicit delivery state/time | T007–T009, T039–T045 |
| FR-009, age/membership/coverage | T012–T019, T033, T039, T044 |
| FR-010, recovery without replay | T009, T027, T035, T038–T045 |
| FR-011, labeled fallback and restoration | T038–T045 |
| FR-012, ownership/cleanup/independent collection | T003, T006, T009, T018, T035, T038–T045 |
| FR-013, input/ordering/slow-viewer protection | T003–T009, T012–T013, T030–T035, T038–T042 |
| FR-014, private access and scope | T007–T008, T016, T033, T038, T042, T044, T046 |
| FR-015, existing commands/consumers | T018, T025–T029, T038–T040, T045–T047, T050 |
| FR-016, accessible status/focus | T019, T028, T036, T040, T044, T049 |
| SC-001 and SC-002, latency/request reduction | T002, T020, T029, T037, T048 |
| SC-003 and SC-004, recovery/duplicates/cleanup | T011, T029, T037–T045 |
| SC-005, timed Logs/Clear/gaps | T031, T035–T037, T040, T048 |
| SC-006, scale/high-volume/slow viewer | T038, T040, T045, T048 |
| SC-007, usability/accessibility | T040, T044, T049 |
| Shared GET/WS query/control/size/admission contract | T003–T011; domain sources joined in T014–T016, T026, T032–T033 |
| Worker mirror and cursor/continuity contract | T030–T037, T038, T042 |
| Legacy and HTTP-only epoch recovery contract | T038–T045 |

## Implementation Strategy

### MVP First (User Story 1)

1. Complete environment/baseline and the shared contract foundation.
2. Finish US1 and pass T020 to demonstrate useful live status/usage with safe ordering,
   source freshness, private scope and preserved workspace state.
3. Use the first checkpoint as an incremental demo, not evidence that all five-area
   performance or recovery criteria have passed.

### Incremental Delivery

1. Add US2's operation identity/progress lifecycle and verify its independent scenario.
2. Add US3's checkpointed Logs while retaining all timed/Clear/scroll semantics.
3. Complete US4 automatic recovery/legacy/visibility behavior across those actual sources.
4. Run the final shared regression, real cluster, load and usability acceptance matrix.

### Parallel Team Strategy

Use the disjoint windows above: Python versus Node checks, backend versus frontend work,
and download versus load source modules. Coordinate all shared-module edits explicitly;
the `[P]` marker does not authorize simultaneous edits of another story's shared files.
Check/build commands remain sequential even when document/source work is parallel.

## Notes

- **50 tasks total**: Setup 2, foundation 9, US1 9, US2 9, US3 8, US4 8, final 5.
- **17 `[P]` tasks** identify safe conditional parallel opportunities; not all run at once.
- Existing production modules remain the source of truth; deliberate long-session seen-ID
  growth is retained to uphold Clear rather than silently weakening identity semantics.
- Record missing hardware/platform/browser/participant evidence as unverified. Do not
  mark a required acceptance task complete merely because its prerequisites are unavailable.
- Finish task generation with this file; implementation and execution of these tasks are
  the next phase.

## Phase 8: Convergence

- [X] T051 Update `docs/cli.md` and `docs/troubleshooting.md` with implemented live delivery, periodic fallback, denial, source freshness/coverage, unknown operation outcomes, and recovery diagnostics per Constitution V and FR-008–FR-011 (missing).
- [X] T052 Add `dashboard/tests/live-updates.test.mjs` deterministic checks for whole-frame validation, revision/epoch ordering, initial commit before ACK, reconnect/subscription races, fallback deadlines, and cleanup; verify the checks fail for the current controller gaps per Constitution II, FR-002, FR-010, and FR-013 (partial).
- [X] T053 Replace demand-triggered source reads in `python/kllm/agent/live.py` with lifespan-owned shared cadence/acquisition and role/process-generation guards; split fast status/activity from slow status work and track truthful source age, worker coverage, and upstream success per FR-001, FR-003, FR-009 and plan: shared agent runtime (partial).
- [X] T054 Complete server and browser protocol validation in `python/kllm/agent/live.py`, `python/kllm/agent/__init__.py`, and `dashboard/src/lib/live-updates.ts`: validate all required fields/ranges, stage complete frames before mutation, ACK valid stale/equal batches, enforce aggregate/control limits, and isolate slow or invalid peers per FR-002, FR-013 and plan: bounded GET/WebSocket contract (partial).
- [X] T055 Implement foreground silence/initialization deadlines, recurring HTTP fallback, live restoration probes, conservative legacy compatibility, and per-page area interests across `dashboard/src/lib/live-updates.ts`, `dashboard/src/lib/store.svelte.ts`, and `dashboard/src/routes/+layout.svelte` per FR-008, FR-010–FR-012 (partial).
- [X] T056 Preserve active and latest-terminal load/download records concurrently and reconcile terminal operation IDs once in `python/kllm/models.py`, `python/kllm/load_progress.py`, and `dashboard/src/lib/store.svelte.ts`; cover disconnect completion, same-name restart, cancellation, and user-selection races per FR-005 and US2/AC3–AC4 (partial).
- [X] T057 Implement shared worker log mirroring, continuity/gap transitions, complete log-result validation, and cursor-based change detection in `python/kllm/agent/live.py`, `python/kllm/agent/__init__.py`, and `dashboard/src/lib/log-trail.svelte.ts`; verify timed modes, Clear, coverage round trips, and slow-viewer omission per FR-006, FR-007, and FR-014 (partial).
- [X] T058 Retain and present per-area last-good values, last-success time, availability/access, source freshness, and local/cluster coverage in `dashboard/src/lib/store.svelte.ts`, `dashboard/src/routes/+layout.svelte`, and `dashboard/src/routes/logs/+page.svelte`; preserve accessible text and focus per FR-008, FR-009, and FR-016 (partial).
- [X] T059 Execute and record the outstanding SC-001–SC-007 acceptance matrix in `specs/005-dashboard-live-updates/validation.md`, including request baseline, latency/scale/slow-viewer tests, 20 lifecycle cycles, supported-platform follow-up, and five-operator usability; leave unavailable measurements explicitly unverified per plan: validation (missing).

## Phase 9: Convergence

- [X] T060 Add lifespan-owned shared source scheduling and expiring HTTP demand leases to `python/kllm/agent/live.py` and `python/kllm/agent/__init__.py`; stop source work when dashboard demand expires without affecting recording, collection, or inference per FR-001, FR-003, FR-012 and plan: shared agent runtime (partial).
- [X] T061 Split fast membership expiry/publication from persistence, replanning, lifetime closure, and sequential worker probes in `python/kllm/agent/__init__.py` and `python/kllm/membership.py`; test a blocked maintenance path and establish the 25-second disconnection limit per FR-009, US1/AC2 and plan: membership publication (partial).
- [X] T062 Complete browser recovery in `dashboard/src/lib/live-updates.ts` and `dashboard/src/lib/store.svelte.ts`: validate compatible legacy reads, suppress retries for denied areas, admit HTTP-only epoch recovery through a staged current-generation boundary, and bound/jitter live probes per FR-008, FR-010–FR-011 and plan: delivery recovery (partial).
- [X] T063 Advance cached usage age at serialization and track conservative browser/server time bounds through HTTP and heartbeat probes in `python/kllm/agent/live.py`, `python/kllm/agent/__init__.py`, and `dashboard/src/lib/live-updates.ts`; verify skew, jumps, replay, and the ten-second stale threshold per FR-009, US4/AC4 and plan: observation age (partial).
- [X] T064 Bound download transfer waits and guard load samples/terminal transitions by operation and managed-process identity in `python/kllm/models.py`, `python/kllm/load_progress.py`, and applicable callers; test cancellation, supersession, process replacement, and disconnect recovery per FR-005 and US2/AC2–AC4 (partial).
- [X] T065 Complete real-transport log parity and continuity checks across `python/kllm/agent/live.py`, `python/kllm/agent/__init__.py`, and `dashboard/src/lib/log-trail.svelte.ts`, including empty checkpoints, missed coverage round trips, held ACK/retention gaps, and second-viewer Clear isolation per FR-006–FR-007 and US3/AC1–AC5 (partial).
- [X] T066 Route visibility and page-interest changes through the root coordinator in `dashboard/src/lib/live-updates.ts`, `dashboard/src/lib/log-trail.svelte.ts`, and `dashboard/src/routes/+layout.svelte`; verify 20 in-app navigation/hide/show cycles leave one owner and release viewer work within ten seconds per FR-010 and FR-012 (partial).
- [X] T067 Run and record matched baseline, physical scale/performance, supported-platform, and operator usability acceptance in `specs/005-dashboard-live-updates/validation.md`; use exact follow-ups for unavailable platforms or participants and do not claim unmeasured criteria as passes per SC-001–SC-007 and Constitution III (missing).

## Phase 10: Convergence

- [X] T068 Restore the existing master `join_key` and actionable `apply_error` in authorized live status data while keeping secret-bearing values out of feed errors and diagnostics; add master GET/WS-to-Home join-key and warning regressions in `tests/test_dashboard_live.py` and `dashboard/tests/store.test.mjs` per Constitution IV and FR-001/FR-015 (contradicts).
- [X] T069 Accept valid `loading`, `unavailable`, and `denied` load/download area records with `data:null` during whole-frame validation, while continuing to validate complete operations for available records; prove a mixed-success/error initial GET/WS frame commits unaffected areas and is ACKed per Constitution IV and FR-002/FR-013 (contradicts).
- [X] T070 Preserve upstream 401/403 status as a safe `denied` usage record at the shared worker proxy boundary in `python/kllm/agent/__init__.py`; do not relay raw upstream response bodies in errors, and test master/worker denial alongside permitted local areas per Constitution IV and FR-014 (contradicts).
- [X] T071 Make the coalesced membership maintenance worker release its in-flight ownership on exception and discard work for changed role or rejoined identity; regress an exceptional save/replan followed by another expiry and verify storage/replan maintenance resumes per Constitution IV and FR-009/FR-015 (contradicts).
- [X] T072 Complete lifespan-owned shared source cadence, per-source single-flight acquisition, role/process-generation rejection, and expiring HTTP demand leases with bounded cold reads in `python/kllm/agent/live.py`; verify source work stops after the last viewer/lease without stopping collection per FR-003/FR-012 and plan: shared agent runtime (partial).
- [X] T073 Complete denial-aware legacy read adapters, bounded HTTP-only recovery, staged live cutover, jittered probes, and one visibility/log checkpoint owner in `dashboard/src/lib/live-updates.ts` and `dashboard/src/lib/log-trail.svelte.ts`; verify restart and repeated in-app navigation against the required deadlines per FR-008/FR-010–FR-012 (partial).
- [X] T074 Use bounded HTTP and client-ping round trips with `server_time` to age cached usage conservatively across transit, equal-revision replay, clock skew, and jumps; add deterministic stale-at-ten-seconds checks without healthy-state HTTP polling per FR-009 and US4/AC4 (partial).
- [X] T075 Verify and fix retained load/download terminal handling across same-name restarts, explicit and membership-driven process replacement, cancellation, and disconnect recovery, including once-only inventory/selection effects and unknown outcomes, using actual API/feed boundaries per FR-005 and US2/AC2–AC4 (partial).
- [X] T076 Verify new GET/WebSocket Logs parity, empty checkpoint advancement, master-ID-preserving worker mirror, coverage round trips, held-ACK retention loss, and second-viewer Clear isolation; correct cursor/gap behavior where checks fail per FR-006/FR-007 and US3/AC1–AC5 (partial).
- [X] T077 Capture matched before/after request counts and the 1/4/16-node, five-viewer/chat, log-load, failure-recovery, twenty-cycle, keyboard/viewport, and five-operator acceptance results in `specs/005-dashboard-live-updates/validation.md`; record exact unavailable-environment follow-ups and do not claim unmeasured targets per SC-001–SC-007 and Constitution III (missing).
