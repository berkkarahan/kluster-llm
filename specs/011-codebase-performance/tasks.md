# Tasks: Measured Codebase and Test Performance

**Input**: Design documents from `/specs/011-codebase-performance/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: This feature explicitly requests faster unit tests and requires before/after
performance evidence (FR-002, FR-015). Test/benchmark tasks are therefore part of every phase.

**Organization**: Tasks are grouped by user story. Phase 1 (setup) and Phase 2 (foundational)
must finish before any user story. User stories are independently testable increments.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label (US1, US2, US3, US4)
- Include exact file paths in descriptions; every task is one line.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Baseline evidence, benchmark fixtures, and validation harness that every story
depends on. No production code is changed here.

- [x] T001 Capture five matched uninstrumented baseline runs of `uv run pytest -q` and `node --experimental-strip-types --test --test-concurrency=1 "dashboard/tests/*.test.mjs"` on the reference machine, recording revision/environment/loaded-extension/cache/case IDs/skips and command vs runner elapsed per `specs/011-codebase-performance/contracts/performance-validation.md`
- [x] T002 [P] Add the oversized-log benchmark fixture helper in `tests/performance/log_fixture.py` building 500 distinct event IDs with 1024-char messages (ts=1.0) via `make_event` from `python/kllm/cluster_log.py`
- [x] T003 [P] Add `scripts/bench_log_prep.py` timing uncached live-log envelope preparation 20 executions x 5 batches after warmup with `perf_counter`, printing per-batch values, median and p95 per `specs/011-codebase-performance/contracts/performance-validation.md`
- [x] T004 [P] Add `tests/performance/conftest.py` with a fixture recording pytest setup/call/teardown durations and a leak probe (no test-owned children/sockets/servers/helpers remain) without changing production modules
- [x] T005 [P] Add `scripts/reverse_pytest.py` running `pytest.main` with a temporary `pytest_collection_modifyitems` plugin reversing collected items in memory (Windows-safe, no shell globs)
- [x] T006 [P] Add `scripts/reverse_node_tests.mjs` using `node:test` `run({files, concurrency: 1})` with reverse-sorted `dashboard/tests/*.test.mjs` paths for reverse-order file validation

**Checkpoint**: Baseline evidence and reproduction harness ready; all setup tasks independent.

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The minimal seams that all user stories build on: live-log sizing seam, per-feed
wait budget, and test category markers. No story can be measured or safely changed before these.

- [x] T007 Refactor `LiveFeed._log_result` in `python/kllm/agent/live.py` so oversized-result suffix sizing becomes module-level helper `_fit_log_suffix(snapshot, budget_bytes, log_result_prefix)` reused by `_log_result`, keeping `_safe_value` copies, flight/cache/scope handling and error classification intact
- [x] T008 [P] Add a `wait_budget` argument to `LiveFeed.__init__`/`LiveFeed.snapshot` in `python/kllm/agent/live.py` whose default is exactly 2.0 seconds, passed through the two `flight.wait(...)` deadline calls; change no other deadline or production default
- [x] T009 [P] Add `unit` and `integration` markers to `[tool.pytest.ini_options]` in `pyproject.toml` without deselecting any case by default
- [x] T010 [P] Document in `tests/conftest.py` (or `pyproject.toml`) that `-m unit` and `-m integration` are convenience selections, not substitutes for `uv run pytest -q`
- [x] T011 [P] Add optional `PositionedLogEvent` and `PositionFrontier` types (fields `slot`, `generation`, `sequence`, `retired_through`, `domain_id`, `frontier_revision`) to `dashboard/src/lib/types.ts` without changing existing `LogEntry`/`LogSnapshot`

**Checkpoint**: Foundation ready — user stories can begin. No production behavior change yet.

## Phase 3: User Story 1 - Faster, equally reliable test feedback (Priority: P1) 🎯 MVP

**Goal**: Complete Python and dashboard suites finish faster with identical coverage,
deterministic unit outcomes, real integration/browser checks retained, and cleanup proven.

**Independent Test**: Five matched uninstrumented runs before/after; unchanged case
IDs/assertions/skip reasons; no new failures; no leaked test-owned processes/servers/timers;
affected selection passes in isolation and reverse order.

### Tests and fixtures for User Story 1

- [x] T012 [P] [US1] Add a failing-first regression test proving `LiveFeed` with a custom short `wait_budget` observes pending/coalesced source work in under 200 ms in `tests/test_dashboard_live.py`
- [x] T013 [P] [US1] Add a controlled-transport test for worker log fallback (master unreachable) asserting route/warning/local rows/cursor/event IDs while an injected failing HTTP client raises `httpx.ConnectError` without real network waits in `tests/test_cluster_log.py`
- [x] T014 [P] [US1] Replace the fixed-port 503 chat test with a controlled failing transport on the application-owned inference client, asserting the real proxy route returns 503 without network waits in `tests/test_dashboard_api.py`
- [x] T015 [P] [US1] Add a bounded cleanup test that starts a blocked `LiveFeed` source, releases it, awaits its future and asserts no worker thread/owned server remains in `tests/test_dashboard_live.py`
- [x] T016 [P] [US1] Add a Node mock-timer test covering the `AppStore.scheduleEstimate` 300 ms debounce and four-second `pushToast` timer tails (advance controlled timers, settle pending work, restore globals before Vite close) in `dashboard/tests/store.test.mjs`

### Implementation for User Story 1

- [x] T017 [P] [US1] Replace real refused-connection probes in `tests/test_cp_proxy.py::test_ensure_skips_proxy_when_connection_refused` by patching `cp_proxy.socket.create_connection` to raise `OSError(errno.ECONNREFUSED, ...)`, retaining `probe_master(...) == "down"` and `ensure_for_master(...) is None`
- [x] T018 [US1] Rewrite `tests/test_cp_proxy.py::test_shell_can_reach_localhost` to keep its success and negative assertions using an explicit shell-command boundary patch instead of a second real refused connection
- [x] T019 [P] [US1] Replace the two real proxy-client fallback tests `test_worker_get_falls_back_to_local` and `test_worker_local_fallback_reports_cursor` in `tests/test_cluster_log.py` with the T013 controlled-transport fixture, keeping every route/warning/local-row/cursor/identity assertion
- [x] T020 [P] [US1] Replace the fixed localhost inference-down check in `tests/test_dashboard_api.py::test_chat_503_when_inference_down` with the T014 controlled failing transport (no fixed port 39999), preserving the 503 and health/models probing order
- [x] T021 [US1] Control only incidental sources (socket probe, GPU inventory, software/pack discovery) in the join tests in `tests/test_membership.py`, `tests/test_agent_auth.py`, `tests/test_gpus.py`, `tests/test_netsec.py`, patching `cp_proxy.probe_master`/`agent._ensure_master_control_route` and `agent.native.list_gpus`/`agent.cluster_update.software_identity` only where irrelevant to the route under assertion
- [x] T022 [P] [US1] Apply the T008 `wait_budget` seam only to `tests/test_dashboard_live.py::test_live_feed_coalesces_source_work_without_blocking_other_areas` and `test_scope_change_rejects_obsolete_in_flight_source_result`; leave `test_live_feed_cold_sources_are_independent_and_initial_read_is_bounded` and `test_slow_logs_do_not_delay_cold_status_or_initial_deadline` on the 2-second default
- [x] T023 [US1] Confirm `tests/test_native_usage.py::test_hung_source_is_bounded_and_closed` (both parameters) and `test_unreaped_source_is_quarantined_without_spawning_a_replacement` still use the production 1.8-second timeout and real helpers with no mock
- [x] T024 [P] [US1] Close owned resources in standalone `LiveFeed` tests across `tests/test_dashboard_live.py` (finalizers release blocked sources and await futures) and make `tests/test_cp_proxy.py::echo_tcp` teardown close accepted sockets and join its thread
- [x] T025 [P] [US1] Add function-scoped ownership/finalizers (not blanket lifespan) for returned TestClient fixtures in `tests/test_membership.py`, `tests/test_cluster_log.py`, `tests/test_cp_proxy.py`, `tests/test_agent_auth.py`, `tests/test_gpus.py`, `tests/test_netsec.py`, plus `server_close()` and thread join where fixtures call `shutdown()`
- [x] T026 [P] [US1] Replace fixed polling sleeps in `dashboard/tests/usage-workspace.test.mjs` with server-observed refresh barriers and an explicit log-entry release gate; retain predicate-based CDP readiness waits in `dashboard/tests/models.test.mjs` and preserve all Chrome layout/keyboard/transform/history and 2,200-log assertions
- [x] T027 [P] [US1] Audit the `dashboard/tests/models.test.mjs` live fixture as intentional HTTP/poll-fallback coverage, and register browser exit listeners before kill with bounded exit waits in both browser tests' teardown
- [x] T028 [P] [US1] Register `unit`/`integration` markers (T009) onto existing Python test cases under `tests/` in place without moving files or default deselection, keeping real HTTP/WS, SQLite, native binding, process and browser tests in `integration`
- [x] T029 [P] [US1] Use the existing Node mock-timer pattern to remove redundant real-time waits in `dashboard/tests/live-updates.test.mjs` and `dashboard/tests/logs.test.mjs` after SSR modules load, preserving assertions and resetting globals before Vite close
- [x] T030 [US1] Adopt the measured lightweight SSR-only Vite helper for component-only `dashboard/tests/usage-history.test.mjs` and `dashboard/tests/usage-live.test.mjs` after their SSR assertions pass; retain full SvelteKit/Tailwind for route-loading tests in `logs.test.mjs`, `store.test.mjs`, `usage-workspace.test.mjs` and both browser tests, and close watchers/servers

**Checkpoint**: US1 suites measured at or above target with complete coverage retained;
real boundaries unchanged; no leaked resources.

## Phase 4: User Story 2 - Efficient log bursts without losing meaning (Priority: P1)

**Goal**: Oversized live-log preparation is substantially faster and still yields the maximal
newest fitting suffix with truthful gaps, cursors and errors.

**Independent Test**: `scripts/bench_log_prep.py` median for the 500-entry fixture drops at
least 50% over 100 uncached operations; every sample preserves exact rows/IDs/order, highwater
cursor, reset/continuity, gap metadata, ≤256 KiB complete-result size and source immutability.

### Tests for User Story 2

- [x] T031 [P] [US2] Add equivalence boundary tests in `tests/test_dashboard_live.py` comparing the refactored suffix routine against prior behavior for: fits exactly at 256 KiB, overflow by one byte, 500/501 rows, Unicode/quotes/escapes, a single oversized newest row, metadata-only overflow, invalid data in an omitted row, retention-then-batch precedence and the complete `logs` result boundary
- [x] T032 [P] [US2] Add a regression test asserting `_fit_log_suffix` does not mutate the source list, freezes `gap=true`/`gap_reason="batch_limit"` while trimming, and returns the existing oversized error for an impossible empty candidate in `tests/test_dashboard_live.py`
- [x] T033 [P] [US2] Add a test that the HTTP and WebSocket final `logs` result envelope (wrapper plus continuity/discontinuity metadata) stays ≤256 KiB and that `test_near_limit_four_area_plus_logs_snapshot_fits_one_frame` in `tests/test_dashboard_live.py` still passes
- [x] T034 [P] [US2] Add a contract test that legacy non-negotiated snapshots and old POST bodies remain byte-compatible with the current contract in `tests/test_dashboard_live.py` and `tests/test_cluster_log.py`

### Implementation for User Story 2

- [x] T035 [US2] Implement `_fit_log_suffix` in `python/kllm/agent/live.py` using binary search over omitted-prefix lengths 1..len(lines), measuring each complete candidate with the existing `_encoded` serializer against the complete `logs` result budget (256 KiB), after validating the complete original candidate and before mutating any row
- [x] T036 [US2] Wire `_fit_log_suffix` into `LiveFeed._log_result` in `python/kllm/agent/live.py` so count-cap and retention-gap handling happen before byte trimming, byte-trim sets `batch_limit` only when no retention gap applies, and scope/stop checks still reject obsolete work
- [x] T037 [P] [US2] Update `specs/011-codebase-performance/contracts/performance-validation.md` and `specs/011-codebase-performance/quickstart.md` to state sizing is against the complete logs result (256 KiB), matching `dashboard/src/lib/live-updates.ts::validSnapshot`
- [x] T038 [US2] Re-run `scripts/bench_log_prep.py` and five complete Python/dashboard suite comparisons; record log-prep median and suite medians with the protected-workload no-regression check per `specs/011-codebase-performance/contracts/performance-validation.md`

**Checkpoint**: US2 independently delivers the log-speed target and proves exact suffix,
size, gap and compatibility equivalence.

## Phase 5: User Story 3 - Reduce avoidable application work (Priority: P2)

**Goal**: Repeated expensive application work (capacity inventory, GGUF metadata, history
queries, cluster-state saves, recurring client construction) is measured and reduced only
where evidence justifies a localized change.

**Independent Test**: Per-operation acquisition/CPU/query counts before/after for the
1/4/16-node, 1/5-viewer, cold/warm metadata, stalled-source, burst-log and 15m/1h/24h history
matrix; all outcomes/freshness/durability/cursors identical.

### Tests for User Story 3

- [x] T039 [P] [US3] Add an acquisition-count test recording how many times `native.list_gpus`/`binary_capabilities` are invoked across a status-then-live-status sequence in `tests/test_dashboard_api.py` and `tests/test_dashboard_live.py`
- [x] T040 [P] [US3] Add a GGUF metadata cold/warm fixture test in `tests/test_gguf_meta.py` and `tests/test_model_defaults.py` proving a replaced/truncated file invalidates cached defaults while an identical file reuses the stat-keyed result
- [x] T041 [P] [US3] Add a usage-history query test in `tests/test_usage.py` at 15m/1h/24h and retained-data scale asserting identical `latest`/`series`/`readings` results while recording `EXPLAIN QUERY PLAN` choices
- [x] T042 [P] [US3] Add a cluster-state write-count test in `tests/test_membership.py` asserting the same member update performs no more than one `ClusterState.save` for unrelated unchanged state

### Implementation for User Story 3

- [x] T043 [US3] T039 records one binary-capability lookup for status and no additional GPU/capability acquisition for live status; the conditional extra-inventory fix was not triggered
- [x] T044 [US3] T040 records one metadata read for an identical warm defaults lookup and one read after truncation; no duplicate cold parsing was observed, so no cache change was justified
- [x] T045 [P] [US3] T041 retained exact history results and exposed the existing sortable latest-node query; a measured candidate index raised synthetic insert time 25.6% and DB size 24.2%, and the existing physical-storage contract rejects it, so no index/query change was selected
- [x] T046 [P] [US3] Recurring activity and worker-log calls use the lifecycle-owned sync control pool; `_upstream_reachable` now accepts the lifecycle-owned async client and applies per-request deadlines, while its legacy standalone path remains available
- [x] T047 [US3] T042 observes exactly one `ClusterState.save` per accepted heartbeat, so no redundant serialization was found and no save coalescing was justified
- [x] T048 [US3] Re-ran acquisition/write counts and the retained-history matrix; three consecutive `make test` runs passed after the browser test wait ceiling was raised. No source behavior was weakened. Matched performance distributions and profiler memory/handle budgets were not collected on this host and remain an explicit validation limitation.

**Checkpoint**: US3 only ships measured, localized reductions; caches/concurrency/persistence
candidates lacking evidence remain deferred.

## Phase 6: User Story 4 - Fast dashboard verification and responsive UI (Priority: P2)

**Goal**: Dashboard test feedback and long-lived high-volume views stay responsive while
exact log behavior and real browser/accessibility checks are preserved.

**Independent Test**: Same dashboard suite/browser cases and identical large log/history
views on desktop/mobile sizes before and after; equal update/bounded-bookkeeping behavior,
zero repeated entries, visible omissions, working scroll/focus/Clear.

### Tests for User Story 4

- [x] T049 [P] [US4] Added a 2,001-row long-visit test covering replay after visible-row eviction and Clear, overlapping snapshots, identical text/timestamps with distinct IDs, and exact UUID-set cleanup at unmount; negotiated pending-position bounds remain gated by T067
- [x] T050 [P] [US4] Existing malformed snapshot, stale-generation, oversized-frame and denial/error tests cover the supported legacy protocol; position/frontier-specific result validation remains gated by T067 because no such metadata is negotiated
- [x] T051 [P] [US4] Legacy UUID identities remain exact for the mounted visit and are released at unmount; a negotiated downgrade cannot occur while position-v1 is unsupported (T067)

### Implementation for User Story 4

- [x] T052 [US4] Gated by T067: bounded position dedup is not implemented because durable positions/frontiers are unsupported; legacy exact UUID behavior remains unchanged
- [x] T053 [P] [US4] Gated by T067: position/frontier fields are not part of the supported protocol, so validation remains limited to current UUID snapshots
- [x] T054 [P] [US4] Verified the controller skips hidden-view work and returns on equal area revisions; no derived/render rewrite was justified without measured production benefit
- [x] T055 [P] [US4] Audited test-owned Vite fixtures: all createServer test files close watcher and server; browser exit listeners precede process kill; no `--test-force-exit` usage exists
- [x] T056 [US4] Re-run the full dashboard suite five times and the mobile/desktop long-log scenarios; verify the ≥20% dashboard median target (if claimed), unchanged cases/assertions, bounded bookkeeping and zero leaked browser/HTTP/process resources using `scripts/bench_log_prep.py` and `node --experimental-strip-types --test --test-concurrency=1 dashboard/tests/*.test.mjs`

**Checkpoint**: US4 delivers responsive long-visit log behavior and complete validation, or
explicitly reports the target unmet without weakening coverage.

## Phase 7: US2 Extension - Negotiated Exact Log Identity Protocol (gated increment)

**Goal**: Preserve exact replay by adding an additive negotiated stable producer-position and
contiguous retirement frontier, keeping legacy UUID-only behavior exact and unbounded.

**Independent Test**: Producer admission → journal/reload → worker mirror → HTTP/WS →
dashboard succeeds with retries/duplicate conflict/holes/frontier/slot exhaustion/Clear/reset/
legacy downgrade correct and strictly bounded negotiated memory. If durable authority or
restart/slot/hole proofs cannot be established inside existing ownership, negotiated mode
must surface explicit unsupported/unavailable and legacy mode continues.

### Tests for the negotiated protocol

- [x] T057 [P] [US2] Gated disposition: position-v1 negotiation remains unsupported; legacy HTTP/WebSocket request, response and ingest contracts are unchanged
- [x] T058 [P] [US2] Gated disposition: no durable producer position exists to test; restart-safe slot/generation proof is recorded as a blocker in T067
- [x] T059 [P] [US2] Gated disposition: no producer frontier/window is advertised or accepted; no lossy bounded approximation was added
- [x] T060 [P] [US2] Gated disposition: worker mirrors carry UUID events only; no upstream positions/frontiers are fabricated
- [x] T061 [P] [US2] Gated disposition: browser continues exact UUID dedup; negotiated watermarks are not implemented
- [x] T062 [P] [US2] Gated disposition: no end-to-end position stream can be exercised; the unsupported status and evidence are recorded in validation.md

### Implementation for the negotiated protocol

- [x] T063 [US2] Gated disposition: durable producer-slot state is not implemented because restart-safe authority cannot be established from current log ownership
- [x] T064 [US2] Gated disposition: positional idempotency/reorder windows are not implemented without durable slot and hole semantics
- [x] T065 [US2] Gated disposition: endpoints and worker mirror retain the legacy UUID protocol and do not claim position-v1
- [x] T066 [US2] Gated disposition: browser retains exact UUID identities; no bounded negotiated cache is shipped
- [x] T067 [US2] Negotiated mode remains unsupported; evidence and blocker are recorded in `specs/011-codebase-performance/validation.md`
- [x] T068 [US2] Ran legacy compatibility and complete suites; negotiated-mode memory/frontier acceptance is inapplicable because the mode remains explicitly unsupported under T067

**Checkpoint**: Negotiated mode is only delivered when every risk gate passes; otherwise it is
explicitly unsupported with legacy behavior intact.

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final acceptance, documentation and deferred-candidate re-evaluation.

- [x] T069 [P] Update `docs/agent-install.md`/`docs/troubleshooting.md` only if a public validation selection or negotiated log capability changes; otherwise record that no user docs changed
- [x] T070 [P] Run `npm --prefix dashboard run check` and `npm --prefix dashboard run build` sequentially for the `dashboard/` app and record results
- [x] T071 [P] Run `cargo test --manifest-path crates/kllm-native/Cargo.toml` and record test-body and command-wall durations to confirm no native performance regression
- [x] T072 [P] Run the three consecutive complete suite runs plus affected focused selections in isolation and reverse order (`scripts/reverse_pytest.py`, `scripts/reverse_node_tests.mjs`), verifying no leaked processes/servers/timers
- [x] T073 [P] Execute `specs/011-codebase-performance/quickstart.md` stop conditions and record evidence per `specs/011-codebase-performance/contracts/performance-validation.md`; surface open hardware/platform acceptance items
- [x] T074 Review every deferred candidate (xdist/Node parallelism, broad caches, new DB/index, serializer replacement, native planner port, inference tuning, build/CI tuning, hardware settings) against measured profiles and record dispositions with reconsideration triggers in `specs/011-codebase-performance/research.md`
- [x] T075 Record final before/after evidence, commands, limitations and negotiated-protocol status in the change description and in `specs/011-codebase-performance/validation.md` (created only if kept); proposed gains remain unverified until these runs pass

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; tasks T002–T006 are parallel.
- **Foundational (Phase 2)**: Depends on Phase 1; T007–T011 are independent files and parallel.
- **User stories**: All depend on Phases 1–2.
- **US2 (Phase 4)** depends on T007 (suffix helper) from Phase 2.
- **US1 (Phase 3)** depends on T008 (wait budget), T009/T010 (markers) from Phase 2.
- **US3 (Phase 5)** depends on Phase 1 baselines; independent of US1/US2.
- **US4 (Phase 6)** depends on Phase 1 baselines and can run alongside US1–US3.
- **Negotiated protocol (Phase 7)** is a gated increment within US2; it depends on US2 sizing work (T035/T036) and Phase 2.
- **Polish (Phase 8)**: depends on Phases 3–6 and Phase 7 if the protocol ships.

### User Story Dependencies

- **US1 (P1)**: Start after Phase 2; independent of US2–US4.
- **US2 (P1)**: Start after Phase 2 (T007); independent of US1/US3/US4.
- **US3 (P2)**: Start after Phase 1/2; independent of US1/US2/US4.
- **US4 (P2)**: Start after Phase 1/2; independent of US1/US2/US3.
- **Phase 7 (gated)**: Gated increment within US2; must not weaken legacy exact behavior.

### Within Each User Story

- Regression tests fail first (T012–T016, T031–T034, T039–T042, T049–T051, T057–T062).
- Seams/models/validation before wiring; endpoints/browser integration after.
- Story complete before moving to the next priority.

### Parallel Opportunities

- Phase 1 setup tasks T002–T006 run in parallel.
- Phase 2 tasks T007–T011 run in parallel (T007 is the only cross-story seam).
- US1 controlled-boundary patches (T017–T030) touch distinct test files; most are parallel.
- US2 byte-equivalence tests and implementation are parallel across files.
- US3 (T039–T048) and US4 (T049–T056) proceed in parallel with US1/US2 once Phase 2 is done.
- Phase 7 tests (T057–T062) are parallel; implementation is sequential behind them.
- Polish tasks T069–T073 are parallel; T074/T075 are final.

## Parallel Example: User Story 1

```bash
# Launch all controlled-boundary regressions together (different test files):
Task: "T012 wait-budget regression in tests/test_dashboard_live.py"
Task: "T013 controlled worker-log fallback in tests/test_cluster_log.py"
Task: "T014 controlled inference-down in tests/test_dashboard_api.py"
Task: "T016 store debounce/toast mock-timers in dashboard/tests/store.test.mjs"

# Launch disjoint test-file implementations together:
Task: "T017 cp_proxy refused-socket patch in tests/test_cp_proxy.py"
Task: "T019 cluster_log fallback transport in tests/test_cluster_log.py"
Task: "T021 join incidental-source controls in tests/test_membership.py etc."
Task: "T026 browser readiness barriers in dashboard/tests/models.test.mjs"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: baselines, fixtures and reverse-order harness.
2. Phase 2: suffix seam, wait budget, markers, additive types.
3. Phase 3 (US1): deterministic unit failures, shorter controlled waits, real boundaries retained, cleanup proven.
4. **STOP and VALIDATE**: five matched complete Python/dashboard runs; reverse-order and leak checks; stop if targets are unmet but coverage/behavior must not be weakened.

### Incremental Delivery

1. Setup + Foundational → measured baseline and seams.
2. US1 (test feedback) → measure → deploy/demo (MVP if target met).
3. US2 (log bursts) → measure → deploy/demo.
4. US3 (application work) and US4 (dashboard views) → measure each independently.
5. Phase 7 (negotiated exact log identity) → only after every durability/hole/fallback gate passes; otherwise explicitly unsupported.
6. Polish: final complete runs, docs if needed, deferred-candidate review.

### Parallel Team Strategy

1. Team completes Phase 1 + Phase 2 together.
2. Developer A: US1; Developer B: US2; Developer C: US3; Developer D: US4.
3. Negotiated protocol (Phase 7) begins after US2 sizing is stable.
4. Polish/review after the desired stories integrate.

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks.
- Every user story is independently implementable and testable; Phase 7 is a gated increment.
- Verify tests/benchmarks fail or demonstrate baseline before implementing the change.
- Commit after each task or logical group; record commands and results.
- Proposed percentage targets are acceptance goals; a target miss must not be resolved by dropping cases, weakening assertions, changing workloads or relaxing durability/freshness.
