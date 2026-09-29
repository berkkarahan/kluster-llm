---
description: "Executable tasks for the master and worker log-trail page"
---

# Tasks: Master & Worker Log Trail

**Input**: Design documents from `specs/004-cluster-log-trail/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/logs.md](contracts/logs.md), [quickstart.md](quickstart.md), and [design.md](design.md).

**Tests**: Constitution principles II–III and the approved plan require behavior-focused automated checks and shared-contract integration checks. Use existing pytest, Node `node:test`/Vite, and browser harnesses; no new test framework or speculative coverage suite. Test tasks below specify observable outcomes, with regression failures demonstrated before their corresponding fixes and passing checks required at the story checkpoint.

**Organization**: Six phases, grouped by user story. All boxes start unchecked: previously generated design evidence is an input, not evidence that application implementation is complete.

## Format: `[ID] [P?] [Story] Description`

- `[P]` identifies tasks eligible for the explicitly listed parallel waves after their prerequisites finish; it does not permit simultaneous edits to the same file.
- `[US1]`, `[US2]`, and `[US3]` map to the specification's user stories.
- Paths are relative to `D:/dev/kluster-llm`. New source/test paths are explicitly identified below.
- Complete each task's relevant checks before marking its behavior implemented. Record unavailable hardware/browser/human checks as unverified, with the exact follow-up; do not mark them passed.

## Path Conventions

- Shared Python collector and agent: `python/kllm/cluster_log.py`, `python/kllm/agent/__init__.py`.
- Existing dashboard: `dashboard/src/`; new controller `dashboard/src/lib/log-trail.svelte.ts` and route `dashboard/src/routes/logs/+page.svelte`.
- Existing Python tests: `tests/test_cluster_log.py`, `tests/test_dashboard_api.py`; new controller checks: `dashboard/tests/logs.test.mjs`; browser checks reuse `dashboard/tests/usage-workspace.test.mjs`.
- Existing operational guidance: `docs/cli.md`, `docs/troubleshooting.md`. Append implementation evidence under a clearly dated results section in `specs/004-cluster-log-trail/quickstart.md` without replacing its planned scenarios.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the current baseline and reuse the reviewed design in this existing application.

- [X] T001 [P] Inspect the current working-tree changes and prerequisites in `AGENTS.md`, `docs/agent-install.md`, `dashboard/package.json`, and `pyproject.toml`; run the existing relevant baseline checks (`uv run pytest -q tests/test_cluster_log.py tests/test_dashboard_api.py` and `npm --prefix dashboard run check`), recording results and pre-existing failures in `specs/004-cluster-log-trail/quickstart.md`; use the documented installer only if prerequisites are missing and preserve concurrent feature-003 edits.
- [X] T002 [P] Review the already generated OpenDesign project `kluster-llm-cluster-log-trail-8ebc`, entry `cluster-log-trail.html`, using the references in `specs/004-cluster-log-trail/design.md`; confirm its mapping to `dashboard/DESIGN.md`, `dashboard/src/app.css`, and the real navigation before visual work, retaining the corrected mobile toolbar and local-only/Clear semantics without copying prototype controls, fixture data, dummy links, or commissioning a replacement design unnecessarily.

**Checkpoint**: Dependencies and existing failures are known; the reviewed design can be applied to the current dashboard.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish identity, snapshot semantics, and shared wire types used by all stories.

**Blocking rule**: Finish T003–T006 and their targeted checks before starting a user-story implementation. Preserve existing CLI/SSE behavior and the private control-plane boundary.

- [X] T003 [P] Add the `LogEntry` and `LogSnapshot` wire types from `specs/004-cluster-log-trail/data-model.md` to `dashboard/src/lib/types.ts`: entry `node_id` is "Existing stable node identity; label from membership when available", entry `source` is "Existing `agent`, `llama`, or `rpc` source", and entry `line` is "Complete message text; inert escaped rendering, not HTML"; snapshot `source` is "Existing `cluster` or `local` coverage label", `warning` is "Existing optional human-readable fallback warning", `reset` is "Null, `origin_changed`, or `restart`; the supplied feed is no longer current", and `gap_reason` is "Null, `retention`, `batch_limit`, or `feed_reset`"; match the identity/timestamp/checkpoint constraints implemented by T004/T006 and add no package dependency.
- [X] T004 [P] Assign and preserve event identity through `make_event`, local collection, `LogHub.ingest`, and worker batch/retry serialization in `python/kllm/cluster_log.py`, extending `tests/test_cluster_log.py`: `event_id` is "Canonical UUID; assigned once to a recorded event, preserved across relay and new journal records" and `ts` is "Finite Unix-seconds observation timestamp; display UTC, never use as identity/order"; validate supplied identities/timestamps at their boundary, generate IDs for legacy inputs, and prove two identical messages get distinct IDs while one relayed/retried event keeps its ID.
- [X] T005 Correct master journal initialization and feed ownership transitions in `python/kllm/cluster_log.py` and their callers in `python/kllm/agent/__init__.py`, with regression checks in `tests/test_cluster_log.py`: load active retained history before publishing master events/checkpoints, merge startup-local records once in receipt order with known-ID deduplication, preserve new persisted IDs, assign runtime IDs to legacy journal rows, and issue a new epoch on restart or changed ownership without destructive migration or silently skipping history when the ring is nonempty (depends on T004).
- [X] T006 Add atomic incremental snapshot selection to `LogHub` in `python/kllm/cluster_log.py` and focused boundary checks in `tests/test_cluster_log.py`: snapshot `lines` are "Up to `n` retained matching entries in receipt order, oldest first", `cursor` is "Atomic high-water checkpoint, opaque UUID epoch plus nonnegative sequence", and `gap` is "True if continuity is unavailable or entries since the cursor were omitted"; capture records/checkpoint under the same lock, advance across empty/nonmatching reads, retain the newest bounded matches, label ring expiry/batch truncation/reset with retention taking precedence over batch limits, and verify timestamp ties/backward clocks and concurrent ingest cannot corrupt checkpoint consistency (depends on T005).

**Checkpoint**: Run `uv run pytest -q tests/test_cluster_log.py`; shared identity/history/snapshot checks pass and the existing stream format remains usable. This is a bounded tail, not a lossless archive.

---

## Phase 3: User Story 1 - Inspect master and worker logs (Priority: P1) — MVP

**Goal**: Open a combined, trustworthy recent trail and inspect any master's or worker's entries with accurate scope and readable metadata.

**Independent Test**: Supply known records for one master and two workers, including duplicate hostnames, unknown/departed identities, empty and failing reads, and local-only fallback. Open `/logs`, inspect each node, return to All nodes, and verify UTC/source/message content, escaped markup, the 2,000-row cap, keyboard access, and narrow layout. This slice works as an initial snapshot viewer; automatic refresh choices and Clear arrive in US2/US3.

### Checks for User Story 1

- [X] T007 [P] [US1] Extend the API/relay integration checks in `tests/test_cluster_log.py` for identified and legacy POST bodies, unversioned JSON reads, CLI-compatible SSE records, malformed/future/foreign cursors, `after` with `follow=true`, empty snapshots, batch/retention gaps, worker POST → master snapshot → worker-proxied snapshot, bounded upstream failure/recovery, actual local-node-only fallback, and an old master missing identity/checkpoint support; use temporary state and controlled HTTP boundaries rather than real cluster credentials.
- [X] T008 [P] [US1] Create `dashboard/tests/logs.test.mjs` using the existing Node/Vite test patterns to specify snapshot validation, ID-based duplicate suppression without merging identical distinct messages, receipt ordering despite tied/backward times, stale responses after fetch/body decoding, the 2,000-row cap, omission feedback, current/departed/unknown node options, and distinct loading/empty/error/local-only/reset states; independently stall headers and body completion, prove a 5,000 ms total deadline aborts/invalidates the read and releases its slot without changing committed data, and reject late commits even when the timer callback is delayed; assert behavior rather than private implementation structure.

### Implementation for User Story 1

- [X] T009 [US1] Extend `LogLineBody`, `ingest_logs`, `_logs_snapshot`, and `get_logs` in `python/kllm/agent/__init__.py` to preserve optional validated `event_id` on legacy-compatible POST and expose the additive JSON snapshot contract: `n` remains "Existing default 200 and clamp 1–5,000", and `after` is "New optional opaque checkpoint, at most 128 characters; `<canonical UUID>:<nonnegative integer>`"; return actionable 400 errors for malformed/oversized/future cursors or `after` with `follow=true`, emit explicit reset/gap fields, retain existing filters/authentication, and leave legacy SSE free of metadata `data:` records (covered by T007).
- [X] T010 [US1] Forward cursor inputs and the unchanged snapshot envelope through `_try_proxy_master_logs` in `python/kllm/agent/__init__.py`; on transport failure return only actual local-node records with their own feed checkpoint and master-unreachable warning, retry the master on every subsequent read, bound and close upstream reads, distinguish an unsupported old master from connectivity failure, and satisfy the proxy/relay/compatibility checks in `tests/test_cluster_log.py` (depends on T009).
- [X] T011 [US1] Create the page-owned snapshot controller in `dashboard/src/lib/log-trail.svelte.ts`, using types from `dashboard/src/lib/types.ts`: immediately request latest 200 entries on mount and use `n=5000` plus the committed checkpoint for later reads; `rows` are "latest 2,000 accepted entries; oldest-row trimming sets an omission notice.", `seenIds` are an "ID-only set of accepted entries during this mounted visit, retained after Clear.", and `cursor` is "null until the first successful snapshot; otherwise the last committed checkpoint, retained on errors and never advanced from a stale response."; implement the fixed 5,000 ms dispatch-through-decode/validation deadline with abort/invalidation, an elapsed-time guard before commit, request-owned timer/slot cleanup, and explicit timeout status retaining committed data; guard generation/disposal after every asynchronous boundary, atomically commit deduplicated entries/checkpoint/coverage, and release state on unmount, with the documented `ponytail:` comment about the session ID set's growth ceiling (covered by T008).
- [X] T012 [US1] Implement node selection and view status in `dashboard/src/lib/log-trail.svelte.ts`: `selectedNodeId` follows "null means All nodes; otherwise a stable identity."; derive options from existing membership, buffered entries, and retained selected identity, so "Unknown roles are labeled unknown; an unavailable node is not silently replaced by another node."; filter locally without changing the acquisition cursor, retain cached rows on failure, merge reset snapshots with a visible gap before Clear exists, and model `state` as "loading, ready, empty, cleared, reconnecting/error; coverage labels are independent." with `coverage`, `gap`, `lastSuccessAt`, and `omittedRows` providing "honest freshness/coverage feedback." (depends on T011).
- [X] T013 [P] [US1] Create `dashboard/src/routes/logs/+page.svelte` against the controller from T011–T012, applying the reviewed `specs/004-cluster-log-trail/design.md` through existing UI tokens and scoped styles: All nodes/master/worker selection, UTC/node/source/message rows in receipt order, inert complete message text, visible loading/empty/disconnected/local-only/gap/omission states, an accessible scroll region, keyboard labels/focus, reduced motion, and wrapping desktop/mobile layout; mount/dispose page-owned work and keep prototype-only controls out of the application.
- [X] T014 [P] [US1] Add `/logs` to `dashboard/src/routes/+layout.svelte` navigation with an accessible active-page indication, preserving existing routes, status controls, responsive behavior, and concurrent feature-003 layout changes; do not attach logs acquisition to global `AppStore.start()`.
- [X] T015 [US1] Extend and run the real-route browser checks in `dashboard/tests/usage-workspace.test.mjs` for the US1 independent test using the existing isolated control-service/browser harness: verify actual navigation, node identity/filtering, long and markup-like messages, empty/error/local-only states, keyboard focus, contrast, and no page-wide horizontal overflow at 1280-by-720 and 390-by-844; run the T007/T008 checks and record the story's results in `specs/004-cluster-log-trail/quickstart.md` (depends on T009–T014).

**Checkpoint**: US1 is a usable snapshot-log MVP. Python/API, controller, and browser checks for this slice pass; unavailable browser execution remains explicitly unverified. Do not present refresh or Clear as delivered yet.

---

## Phase 4: User Story 2 - Choose the refresh mode (Priority: P1)

**Goal**: Select the refresh cadence, follow new records, and read older records without racing requests or losing position.

**Independent Test**: On the US1 viewer, feed identifiable entries using controlled timers and delayed responses. Verify one-second Live and 5/30/60-second schedules, all 12 directed mode transitions, preserved node selection, no overlapping/stale commits, the 5,000 ms total read deadline and next-future-deadline retry, foreground recovery, and reading-position preservation including trimming an anchored row. This story can be validated without Clear.

### Checks for User Story 2

- [X] T016 [P] [US2] Extend `dashboard/tests/logs.test.mjs` with deterministic timers and deferred fetch/body promises for the default mode, all four intervals, all 12 directed mode transitions, selection/cursor preservation, monotonic deadline scheduling without jitter or accumulated response-duration drift, skipping missed deadlines, failure/retry and reset notices, visibility recovery, in-flight cancellation, and no requests after unmount; verify stalled headers/body time out at 5,000 ms and retry at the first future cadence deadline, and that a late expired completion neither commits nor releases a newer request's slot; distinguish lifecycle cancellation from timeout and verify deadline-timer cleanup.
- [X] T017 [P] [US2] Extend the `/logs` browser scenarios in `dashboard/tests/usage-workspace.test.mjs` for keyboard-operated refresh radios, exactly one selected choice, preserving focus/node selection through updates, scrolling away from and back to the tail, and trimming an anchored row without a jump to newest; keep scenarios compatible with the existing isolated fixture service.

### Implementation for User Story 2

- [X] T018 [US2] Implement the serial, abortable refresh scheduler in `dashboard/src/lib/log-trail.svelte.ts`: `refreshMode` is "exactly `live | 5s | 30s | 1m`; new visit defaults to `live`."; use 1,000/5,000/30,000/60,000 ms monotonic foreground deadlines, at most one in-flight read, generation invalidation on switches, the next read after the newly selected interval, skipped missed deadlines, and retained rows/cursor during retries; use T011's 5,000 ms total read deadline and retry timeout failures at the first future cadence deadline without a catch-up burst; `generation`, active AbortController, cadence timer, and request-deadline timer are "owned resources; close on transition/unmount except a pre-initial-load no-op Clear."; suspend while hidden, perform one immediate foreground recovery read, and preserve existing `PollLoop` consumers (covered by T016).
- [X] T019 [US2] Add the accessible single-choice **Live / 5s / 30s / 1m** refresh control in `dashboard/src/routes/logs/+page.svelte`, defaulting to Live, binding to T018, showing selection textually as well as visually, and retaining node selection/focus; preserve the reviewed compact toolbar with wrapping and mobile touch targets (covered by T017).
- [X] T020 [US2] Implement follow/scroll-anchor behavior in `dashboard/src/routes/logs/+page.svelte`, where `following` and the scroll anchor are "viewport state that survives ordinary updates.": append-follow only while already at the bottom, retain the older reading anchor on updates, resume when the operator returns to the bottom, and if the anchored row is trimmed show the oldest retained row plus the omission notice; run T016/T017 and record the US2 independent-check results in `specs/004-cluster-log-trail/quickstart.md`.

**Checkpoint**: US1 and US2 work together; timed refresh and reading-position checks pass independently of Clear. Real-network timing targets still require the final measurement task.

---

## Phase 5: User Story 3 - Clear the displayed trail (Priority: P2)

**Goal**: Clear the current visit's display without deleting stored logs or allowing stale work to restore cleared content.

**Independent Test**: Before any successful read, press Clear, introduce a new message, and prove the first successful response is consumed normally without changing the schedule or generation. After loading succeeds, Clear with an active node filter and pending response, change nodes and modes, then simulate normal reconnect, epoch reset, and worker-local → master recovery. Cleared IDs stay absent, eligible new entries appear, cadence/selection/coverage survive, and neither journal content nor a second viewer changes; only the explicit reset-after-effective-Clear recovery window may be omitted with a gap notice.

### Checks for User Story 3

- [X] T021 [P] [US3] Extend `dashboard/tests/logs.test.mjs` for effective Clear during fetch and deferred body decoding, empty/repeated Clear, filter-hidden rows, all refresh modes, same-feed reconnect, retained-ID suppression after worker-local → master relay, and reset snapshots containing legacy newly assigned IDs; for Clear before any initial checkpoint, hold the first read, press Clear, introduce a new message, and prove its successful snapshot is consumed with unchanged generation/timer/state and `hasCleared=false`, repeating after an initial error; assert no POST/DELETE/reset request, preserved cadence/selection/coverage, and a labeled omitted recovery window only after effective Clear followed by a feed reset.
- [X] T022 [P] [US3] Extend `dashboard/tests/usage-workspace.test.mjs` with keyboard Clear and two independent viewers over the shared fixture service: verify stored fixture history and the second viewer are unchanged, hidden rows remain cleared after returning to All nodes, the selected refresh mode persists, and disconnected/local-only scope is not changed to healthy by Clear.

### Implementation for User Story 3

- [X] T023 [US3] Implement Clear in `dashboard/src/lib/log-trail.svelte.ts`: when `cursor === null`, return without changing the read, generation, timers, loading/error state, or schedule, so the first successful result and its new entries remain eligible; otherwise empty all buffered rows, retain seen IDs/cursor, preserve selection/mode/next deadline and connection scope, increment generation, and abort old work; `hasCleared` is "false until Clear with a committed cursor; only then must reset snapshots be baselined without displaying their backlog."; only for a feed reset with `hasCleared=true` discard baseline rows, adopt the checkpoint, and disclose the recovery gap (possibly including unseen post-Clear entries) before resuming new events; perform no server mutation and reset the session on a new mounted visit (covered by T021).
- [X] T024 [US3] Add the neutral keyboard-accessible Clear button and display-only helper to `dashboard/src/routes/logs/+page.svelte`, wire T023, expose cleared/waiting only after effective Clear while retaining initial loading/error state for a pre-load no-op, preserve disconnected/local-only warnings and refresh selection, and keep focus stable; run T021/T022 and record the US3 independent-check results, including the <=1-second Clear target, in `specs/004-cluster-log-trail/quickstart.md`.

**Checkpoint**: All three user stories work. Clear stays local to this mounted visit; no delete endpoint, confirmation flow, or stored-log change exists.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finish operational guidance and collect the integration/performance/accessibility evidence required by the specification.

- [X] T025 [P] Update the existing logging/dashboard guidance in `docs/cli.md` with the `/logs` page, node selection, Live/5s/30s/1m semantics, display-only session-scoped Clear, bounded recent history, and the unchanged CLI commands/private access model.
- [X] T026 [P] Update `docs/troubleshooting.md` with disconnected/cached versus empty results, actual local-node-only fallback, old-master capability errors, the five-second total read timeout and next-cadence retry, retention/batch/feed-reset gaps, the reset-after-effective-Clear backfill tradeoff versus a harmless pre-load Clear, and recovery steps that preserve stored logs.
- [X] T027 Run the implementation verification commands in `specs/004-cluster-log-trail/quickstart.md`: targeted Python and Node checks, then `npm --prefix dashboard run check` followed sequentially by `npm --prefix dashboard run build`; because shared agent contracts change, also run `uv run pytest -q` and `node --test --test-concurrency=1 dashboard/tests/*.test.mjs`; record exact commands/results and distinguish pre-existing failures from regressions without treating skips as passes.
- [X] T028 Instrument the existing fixture in `dashboard/tests/usage-workspace.test.mjs` with event-ID-correlated serving-feed-insertion and browser-render-observation timestamps on one harness monotonic clock, following `specs/004-cluster-log-trail/quickstart.md`; exclude initial history, record observation overhead and eligible missing IDs as deadline misses, and measure steady foreground Live without using source timestamps or response receipt as latency boundaries; execute and record the timing/volume and real-node checks with one master plus three workers, 100 total 128–1,024-byte entries/second for ten minutes per mode, p95 feed-to-render Live visibility <=2 seconds, p95 controls <=1 second, timed intervals within one second while reads finish within one second, visible omissions, and session-ID memory; report real-cluster latency only with recorded measurement-clock alignment/uncertainty, verify relay/proxy/fallback/recovery and shared Python behavior on Windows/Linux/macOS, keep RPC port 50052 private, and leave unavailable cases unverified with exact follow-up instructions.
- [X] T029 Complete the human/accessibility acceptance checks in `specs/004-cluster-log-trail/quickstart.md`: at least four of five operators locate Logs and isolate a named worker within 30 seconds; verify keyboard/screen-reader operation, textual states without color dependence, WCAG AA text contrast, reduced motion, and full long-message access at 1280-by-720 and 390-by-844; record observed results rather than substituting prototype checks for production evidence.
- [X] T030 Review the final feature diff against `specs/004-cluster-log-trail/spec.md`, `specs/004-cluster-log-trail/contracts/logs.md`, and `.specify/memory/constitution.md`; confirm additive CLI/worker compatibility, no public log listener or destructive Clear path, no unnecessary dependency/theme rewrite, preserved concurrent feature-003 work, resource cleanup, and matching documentation; reconcile verified outcomes and outstanding checks in `specs/004-cluster-log-trail/quickstart.md` before marking release readiness.

---

## Dependencies & Execution Order

### Phase Dependencies

```text
Phase 1: T001 || T002
                 |
Phase 2: T003 || (T004 -> T005 -> T006)
                 |
Phase 3: US1 snapshot viewer (T007–T015)  <-- MVP
                 |
Phase 4: US2 refresh/read position (T016–T020)
                 |
Phase 5: US3 display-only Clear (T021–T024)
                 |
Phase 6: T025 || T026; then T027 -> T028 -> T029 -> T030
```

The arrow between stories is integration order, not a requirement to invent duplicate implementations. Each story's acceptance flow is independently testable on its prerequisites.

### User Story Dependencies

- **US1 (P1)** depends on the foundation. T007/T008 can be authored together; T009 → T010 handles the backend while T011 → T012 builds the controller against the fixed contract. After those chains, T013/T014 can run together; T015 validates the integrated slice.
- **US2 (P1)** depends on US1's controller and route. T016/T017 can be authored together; then T018 → T019 → T020. It does not depend on Clear.
- **US3 (P2)** depends on the existing viewer and its selected cadence so Clear can preserve both. T021/T022 can be authored together; then T023 → T024.
- Story implementations share `dashboard/src/lib/log-trail.svelte.ts`, the `/logs` page, and test files. Do not run different stories' edits to those files concurrently.
- T025/T026 edit separate documentation files. T027–T030 append to the same results guide and therefore run serially. Keep dashboard check/build serial regardless of other task parallelism.

### Within Each User Story

1. Define the behavior checks against the documented contracts; establish the pre-fix failure for regressions.
2. Implement models/state before callers and UI integration.
3. Run the story's checks on the actual implementation before its checkpoint.
4. Preserve previously completed stories; keep asynchronous generation guards and resource cleanup in the shared page controller.

### Parallel Opportunities

- Setup: T001 and T002 read distinct concerns; only T001 writes the baseline evidence.
- Foundation: T003 edits wire types while T004 edits Python identity handling/tests.
- US1: T007/T008 test authoring, backend T009–T010 alongside frontend T011–T012 after the checks are drafted, and T013/T014 after the controller/backend chains.
- US2: T016/T017 test authoring in different files; production controller/page edits then follow their dependency chain.
- US3: T021/T022 test authoring in different files; Clear controller precedes button integration.
- Polish: T025/T026 operational documentation in different files.

## Parallel Example: User Story 1

```text
After Phase 2:
  T007: API/relay checks in tests/test_cluster_log.py
  T008: Controller checks in dashboard/tests/logs.test.mjs

After those checks are drafted:
  T009 -> T010: python/kllm/agent/__init__.py
  T011 -> T012: dashboard/src/lib/log-trail.svelte.ts

After both implementation chains:
  T013: dashboard/src/routes/logs/+page.svelte
  T014: dashboard/src/routes/+layout.svelte
Join at T015 for browser and story acceptance.
```

## Parallel Example: User Story 2

```text
After the US1 checkpoint:
  T016: Timer/race checks in dashboard/tests/logs.test.mjs
  T017: Keyboard/reading checks in dashboard/tests/usage-workspace.test.mjs
Then T018 -> T019 -> T020; rerun both checks on the implemented route.
```

## Parallel Example: User Story 3

```text
After the US2 checkpoint:
  T021: Clear/replay checks in dashboard/tests/logs.test.mjs
  T022: Two-viewer/keyboard checks in dashboard/tests/usage-workspace.test.mjs
Then T023 -> T024; rerun both checks on the implemented route.
```

## Implementation Strategy

### MVP First (User Story 1 Only)

Complete T001–T015. Demonstrate a combined snapshot trail with node filtering, accurate metadata/coverage, and usable desktop/mobile presentation. Validate US1 independently. This MVP is a useful viewing increment; the complete requested feature additionally requires US2 and US3.

### Incremental Delivery

1. Foundation: stable identity and consistent bounded snapshot reads.
2. US1: inspect cluster logs and distinguish scope/failure states.
3. US2: selectable automatic refresh and stable reading position.
4. US3: nondestructive Clear that survives replay, mode changes, and reconnects.
5. Polish: operational docs plus measured production validation and explicit unverified follow-ups.

### Parallel Team Strategy

Use a backend and a frontend lane within US1 after the contract checks exist. Parallelize checks or different-file UI/documentation tasks only in the waves above; keep shared controller, route, and test-file edits serialized across stories.

## Coverage & Counts

| Scope | Task IDs | Count | Requirement coverage |
|-------|----------|-------|----------------------|
| Setup | T001–T002 | 2 | Tooling baseline, existing OpenDesign prerequisite |
| Foundation | T003–T006 | 4 | Shared identities, journal, snapshot consistency |
| US1 | T007–T015 | 9 | FR-001–003, FR-009–012; SC-001/005 viewing flows |
| US2 | T016–T020 | 5 | FR-004–005, FR-008–011; SC-002/003/005 refresh flows |
| US3 | T021–T024 | 4 | FR-006–007, FR-009–011; SC-004/005 Clear flows |
| Polish | T025–T030 | 6 | Documentation; full SC-001–006 and constitution gates |
| **Total** | **T001–T030** | **30** | **All 12 functional requirements and 6 success criteria** |

## Notes

- Existing OpenDesign generation and prototype browser evidence are in `design.md`; application runtime, human usability, and hardware checks remain implementation work.
- The snapshot contract is additive. Do not replace the logger, add a database, rewrite CLI SSE, or introduce a deletion endpoint to implement this page.
- The ID-only seen set grows during one mounted visit by design; retain the documented `ponytail:` ceiling/upgrade comment and measure it in T028 rather than claiming completely bounded session memory.
- Keep tasks unchecked when required verification is blocked. Record the blocker and precise follow-up in the results guide; a written test or previously passing prototype is not a production pass.
- This command generates tasks only; no implementation, new dependency, commit, or branch is implied by this checklist.

**T028/T029 status note (2026-09-29)**: Marked `[X]` at the user's request as waived,
not performed. Their real-cluster and five-operator/screen-reader checks remain unverified,
not passes; see the exact follow-up scenarios in `quickstart.md`.

## Phase 7: Convergence

**Purpose**: Close implementation gaps found by comparing the current code with the specification, plan, tasks, and constitution. Preserve all prior task IDs and checkbox states.

- [X] T031 Validate supplied log-ingest identities and timestamps at the network boundary in `python/kllm/agent/__init__.py` and `python/kllm/cluster_log.py`: accept legacy bodies with no event ID, but reject a supplied malformed UUID and reject non-finite timestamps with an actionable client error instead of minting a replacement identity or timestamp; add regression cases to `tests/test_cluster_log.py` per Constitution IV / FR-003 (contradicts).
- [X] T032 Validate every decoded snapshot before state commit in `dashboard/src/lib/log-trail.svelte.ts`: require an object with an entries array, canonical event IDs, finite timestamps, valid node/source/message fields, a valid checkpoint/reset/gap envelope, and a valid coverage source; show an explicit protocol/capability error and retain prior state for invalid or unsupported responses. In `python/kllm/agent/__init__.py`, detect a proxied legacy master lacking cursor/event identity on the initial request as well as incremental requests; test malformed envelopes, missing initial cursor, missing event IDs, and response-body stalls in `dashboard/tests/logs.test.mjs` and `tests/test_cluster_log.py` per Constitution IV / plan: snapshot validation (partial).
- [X] T033 Preserve truthful read-failure and recovery status in `dashboard/src/lib/log-trail.svelte.ts` and `dashboard/src/routes/logs/+page.svelte`: when a read fails or times out with buffered entries, keep the rows but visibly report the degraded/retrying state and last successful read instead of showing “Streaming cluster logs”; clear the old fallback warning when a later successful response no longer carries it. Add controller and browser regression checks for failure-with-rows and local-only-to-cluster recovery per Constitution IV / FR-009 (contradicts).
- [X] T034 Make journal initialization recoverable in `python/kllm/cluster_log.py` and `python/kllm/agent/__init__.py`: do not permanently set `_journal_loaded` after a failed journal read, do not serve a normal complete-history snapshot or accept master ingest as fully initialized while saved history could not be read, and allow later retries with an actionable failure state without terminating the background log loop. Add a temporary-file read-failure-then-recovery regression in `tests/test_cluster_log.py` per Constitution IV / T005 (contradicts).
- [X] T035 Invalidate an in-flight log request when the selected refresh mode changes in `dashboard/src/lib/log-trail.svelte.ts`: abort and generation-guard the old-mode response, retain the last committed cursor/data, and let the new mode’s next scheduled read resume from that cursor; extend `dashboard/tests/logs.test.mjs` with a deferred response crossing a mode switch per FR-005 / data-model LogViewSession mode transition (partial).
- [X] T036 Suspend in-flight as well as scheduled acquisition when the document becomes hidden in `dashboard/src/lib/log-trail.svelte.ts`: abort/invalidate the active read, release its slot and request timer without reporting a timeout, then make exactly one immediate read on foreground return before resuming the selected cadence; test hide during a stalled fetch/body and foreground recovery in `dashboard/tests/logs.test.mjs` per plan: page lifecycle / contract §UI 11 (partial).
- [X] T037 Restrict worker local-only snapshots to records from `STATE.node_id` when no explicit node filter is supplied in `python/kllm/agent/__init__.py`; preserve an explicit requested filter and label local coverage. Add a regression where the worker hub retains remote-cluster entries across a role/coverage change and assert they are not returned as local-only rows in `tests/test_cluster_log.py` per FR-009 / plan: worker local fallback (partial).
- [X] T038 Distinguish upstream protocol/authorization failures from actual worker-to-master connection failures in `_try_proxy_master_logs` and `get_logs` in `python/kllm/agent/__init__.py`; only network unreachability may silently select the labeled local-only snapshot, while reachable non-success responses must preserve diagnostic status and must not be mislabeled as “master unreachable”. Add controlled HTTP tests for 401, 4xx/5xx, timeout, and refused connection in `tests/test_cluster_log.py` per Constitution IV / contract: worker proxy (contradicts).
- [X] T039 Preserve a stable visible log-row anchor when trimming the oldest entries at the 2,000-row cap in `dashboard/src/routes/logs/+page.svelte`: if the operator is reading older entries and the anchored row is removed, move to the oldest remaining row and expose the omission notice rather than keeping a stale numeric `scrollTop`; add a browser regression with a scrolled-up viewport and a trimming update in `dashboard/tests/usage-workspace.test.mjs` per FR-008 / T020 (partial).
- [X] T040 Emit the feed epoch as a canonical hyphenated UUID in `LogHub.__init__` and `reset` in `python/kllm/cluster_log.py`, and make `parse_cursor` enforce the same canonical cursor grammar documented in `specs/004-cluster-log-trail/contracts/logs.md`; add generated-cursor, accepted-cursor, and noncanonical-cursor tests in `tests/test_cluster_log.py` per contract: Snapshot request `after` / T006 (partial).
- [X] T041 Extend `dashboard/tests/usage-workspace.test.mjs` with two simultaneously open dashboard contexts backed by the shared fixture service; clear one `/logs` view and verify the second view and stored fixture history remain unchanged while the cleared view resumes new entries per US3/AC4 / T022 (partial).
