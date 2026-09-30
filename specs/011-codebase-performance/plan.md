# Implementation Plan: Measured Codebase and Test Performance

**Branch**: `main` (setup utility fallback label: `011-codebase-performance`) | **Date**: 2026-09-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification at `specs/011-codebase-performance/spec.md`

## Summary

Reduce measured avoidable test and log-preparation work while preserving existing test
coverage and behavior. First select localized deterministic unit dependencies/timing and
fix oversized-log suffix selection using the existing byte serializer. Separately deliver
an additive, negotiated event-position contract with explicit retirement/backpressure so
the dashboard can bound exact deduplication state without changing legacy behavior. Keep
caches, fan-out, persistence tuning, UI virtualization and parallel tests conditional on
their own measured benefit and correctness evidence. Full repeated suite measurements decide
whether the specified 30% Python/20% dashboard and 50% log targets have actually passed.

## Technical Context

**Language/Version**: Python 3.10+ (reference runtime 3.12.13), Rust edition 2021, TypeScript/JavaScript on Node 22.23.2.

**Primary Dependencies**: Existing stdlib, pytest/pytest-asyncio, FastAPI/httpx, Svelte 5,
Vite 6 and Node `node:test`; do not add a runtime dependency. A pytest worker plugin is not
selected. Browser validation uses existing Chrome/Edge harness.

**Storage**: Existing bounded JSONL cluster log journal and in-memory `LogHub`; SQLite usage
history remains unchanged. Negotiated producer identity/frontier state is persisted with the
existing log journal/owned state only if atomic restart correctness can be demonstrated.

**Testing**: `uv run pytest -q`, targeted pytest unit/integration markers, sequential
`node --experimental-strip-types --test --test-concurrency=1 "dashboard/tests/*.test.mjs"`,
`npm --prefix dashboard run check/build`; `cargo test --manifest-path crates/kllm-native/Cargo.toml`
is a protected measured baseline, not an optimization target absent a future profile.

**Target Platform**: Windows reference machine; preserve and check affected Windows, Linux,
macOS behavior. Inference hardware claims require matched real hardware/model/backend evidence.

**Project Type**: Python cluster agent/CLI and native extension with Svelte dashboard and
developer test/build workflows.

**Performance Goals**: Complete pytest median -30%, complete dashboard validation median
-20%, and specified uncached 500-entry log-envelope preparation median -50%, each against
five matched uninstrumented baseline runs; zero dropped cases/assertions or new unexplained
skips. Proposed percentages remain unverified until those runs pass.

**Constraints**: No inference-settings changes; public CLI/API/native and persisted legacy
log compatibility; encoded snapshot/result budget 256 KiB and 500-row live cap; bounded
queues and finite deadlines; preserve all log IDs, order, highwater, gaps, Clear, and
visit-wide exact suppression in negotiated mode. Legacy servers continue UUID behavior and
do not claim bounded identity memory. Do not reduce durability/retention or expose RPC 50052.

**Scale/Scope**: Existing test case set (baseline 516 passed + 5 skipped, 47 Node tests,
34 Rust tests); synthetic large logs of 500 × 1,024-character messages; runtime matrix
1/4/16 nodes and 1/5 viewers; existing physical journal/history limits. No new inference
service or broad engine rewrite.

## Research Decisions

1. Replace repeated log pop/re-encode only in `LiveFeed._log_result` with binary search over
   omitted-prefix lengths, using `_encoded` for each exact candidate. This makes sizing
   `O(B log n)` in serialized bytes while preserving serializer rules; one-pass byte accounting
   is deferred because it duplicates canonical JSON-size rules.
2. Size against the complete live `logs` result (the `LiveArea`/snapshot/error wrapper)
   capped at 256 KiB, matching `dashboard/src/lib/live-updates.ts::validSnapshot` and
   `specs/005-dashboard-live-updates/contracts/dashboard-live.md`. This may shorten suffixes
   versus the Python-only snapshot baseline; treat as an intentional alignment and verify
   that the existing near-limit aggregate frame still fits. Impossible metadata is an explicit
   oversized error.
3. Preserve a complete-candidate validation before trimming so invalid data cannot be hidden
   by omission. For the byte-trim search, freeze the `batch_limit` gap metadata in every
   candidate; choose the smallest prefix whose complete result fits (maximal newest suffix).
   Retain count-cap, retention precedence, cursors and source copies.
4. For timed Python unit checks, use controlled external boundaries and a per-feed wait-budget
   seam whose production default remains 2 seconds. Keep real HTTP/WS, process, helper timeout,
   and default production deadline integration cases. Do not globally mock clocks/sleep.
5. In dashboard tests, reuse existing Node mock-timer pattern after SSR modules load; test
   toast timer tails, replace blind waits with completion evidence only, and use lightweight
   SSR Vite configuration only if full compiler behavior and measured gains are retained.
   Browser tests keep the full SvelteKit/Tailwind setup.
6. Exact UUID dedup with unbounded arbitrary replay cannot be bounded safely from current
   receiver cursors. User selected an additive negotiated stable producer position plus
   contiguous retirement frontier. Keep legacy behavior; upon downgrade within a visit,
   preserve exact UUID state and explicitly mark the visit legacy/unbounded rather than
   silently evicting identities. Negotiated mode must fail/backpressure when its bounded
   reorder window cannot advance.
7. No pytest-xdist/Node parallelism, broad cache, new DB/index, serializer dependency, native
   planner port or inference tune unless profiling shows localized work misses acceptance.

Detailed options and provisional evidence: [research.md](research.md). Wire-level extension:
[contracts/log-identity.md](contracts/log-identity.md). This protocol decision is the
largest risk and requires strict end-to-end validation before it can be called bounded/exact.

## Constitution Check (before research)

- **I. Simple/consistent**: PASS with a localized log algorithm and reuse of current modules;
  do not broaden into speculative caches/rewrites. The negotiated identity protocol is an
  additive complexity justified only by the user's bounded-exact requirement and is gated by
  explicit capacity/backpressure, no service/dependency.
- **II. Behavior tests**: PASS; byte/gap/replay boundary regressions, deterministic failure
  tests and retained actual transport/browser/process checks are specified.
- **III. Integration/portability**: PASS with follow-up gates: additivity and worker forwarding
  need real HTTP/WebSocket checks; platform-specific tests remain on affected OS. No measured
  speedup until repeated reference evidence; no inference claim without hardware.
- **IV. Contracts/failures**: PASS; protocol is negotiated; validation before commit; explicit
  unsupported/overflow states; bounded replay window must not silently drop/admit; old readers
  remain unchanged. Secret boundaries/private RPC unchanged.
- **V. Reproducibility**: PASS; matched commands/data, no dependency/lock changes selected;
  benchmark/validation procedures and outcomes documented.
- **Architecture constitution**: PASS conditionally on facade patch surfaces (`kllm.agent`,
  loaded `scripts/install.py`), top-level import/reload constraints and real cfg branches.

**Gate result**: PASS for Phase 0/1 design. Risk condition: the negotiated position/frontier
protocol does not ship as “bounded exact” until restart durability, hole handling, authority
handoff and overflow behavior pass the contract tests. Otherwise it must return explicit
unsupported/unavailable, never quietly evict IDs.

## Project Structure

### Documentation (this feature)

```text
specs/011-codebase-performance/
├── spec.md
├── memory.md
├── memory-synthesis.md
├── doc-synthesis.md
├── research.md
├── plan.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── log-identity.md
│   └── performance-validation.md
└── checklists/requirements.md
```

### Source code (repository root)

```text
python/kllm/agent/live.py             # exact bounded live-envelope suffix selection; wait budget
python/kllm/cluster_log.py            # event positions, bounded replay frontier and persistence
python/kllm/agent/__init__.py         # existing log endpoints, request negotiation and facade
python/kllm/agent/live_ws.py          # worker mirror position/frontier preservation
tests/test_dashboard_live.py          # live byte/deadline/API integration regressions
tests/test_cluster_log.py             # producer position, retirement, restart, cursor contracts
tests/test_agent_auth.py              # authenticated ingest capability boundary
tests/test_cp_proxy.py                # proxy/socket behavior (keep true integration subset)
tests/                                 # controlled unit boundaries and explicitly marked integrations
pyproject.toml                         # pytest category markers only if useful and complete
dashboard/src/lib/types.ts            # optional negotiated position/frontier types
dashboard/src/lib/live-updates.ts     # validate complete result before forwarding/ACK
dashboard/src/lib/log-trail.svelte.ts # exact bounded negotiated identity state + legacy mode
dashboard/tests/logs.test.mjs         # replay, Clear, retirement and transitions
dashboard/tests/*.test.mjs            # SSR fixture timing/cleanup; browser tests retained
```

No schema migration, external service, production new dependency or model/inference setting
is planned. If position journal state cannot be persisted and validated additively, halt that
protocol implementation for redesign; do not create a second storage system as a fallback.

**Structure Decision**: Extend existing `LogHub`/dashboard live log path and current tests;
keep all imported/monkeypatched facade names in their established modules. Test categories
are markers/selections, not file moves or default coverage reduction. Only localized test
fixture sharing that is demonstrably immutable is permitted.

## Phase 0: Design Gates and Baseline

1. Preserve `bbde77432c0ca0a87c5f56e796afcb10caae7964` as the recorded one-run baseline; first
   recapture five complete uninstrumented baseline runs on the same Windows setup, case lists,
   loaded `.pyd`, browser and cache state. Record setup/test/teardown. Baseline was 516 passed,
   5 skipped / 80.53 s pytest, 47 Node passed / 46.689 s, 34 Rust passed / 10.418 s command.
2. Profile only repeatable slow paths. Preserve opt-in hardware skips and explain their existing
   prerequisites. Record browser teardown/process exit and pytest setup/call/teardown separately.
3. Resolve the live-snapshot byte-unit discrepancy per decision 2 and add a golden envelope
   comparison before optimizing; retain current endpoint compatibility in legacy mode.
4. Before implementation, prove a negotiated identity model end-to-end on paper and in an
   executable test oracle: authoritative fixed slot assignment, durable generations/sequences,
   idempotent retries, contiguous frontier across every allocated position, fixed slot capacity,
   behavior on holes/retired generations/authority change/journal loss. Do not claim an exact
   bounded protocol when any event can be re-admitted under a new position after retirement.
5. Preserve the user decision: legacy transition keeps exact seen UUIDs and marks legacy
   bookkeeping unbounded until visit end; a new visit resets visit-owned state. No silent
   transition to a capped UUID set.

## Phase 1: Implementation Strategy (for `/speckit.tasks`)

### Checkpoint A — bounded live log sizing

- Add a direct reproduction benchmark/report with warmup, 5×20 uninstrumented samples and
  correctness assertions for exact newest suffix, IDs, order, complete-result bytes, highwater,
  gap reason, reset/discontinuity and no source mutation.
- Make full-candidate validation happen before trimming; exact `_encoded` binary search chooses
  smallest fitting omitted prefix with fixed byte-limit metadata.
- Add boundary regressions: fit by one byte / overflow by one; 500/501 lines; Unicode/quotes/
  escapes; a single oversized newest row; oversized metadata; invalid omitted candidate;
  existing retention/count gap precedence; HTTP and WS final envelope; cancellation/scope.
- Target ≥50% median preparation reduction vs repeated baseline. If exact serializer-based
  binary search misses it, profile and evaluate exact one-pass accounting as a subsequent option;
  do not change serializer or tolerance.

### Checkpoint B — safe test feedback improvements

- Introduce `unit`/`integration` markers or equivalent in-place selections. Complete default
  commands still collect all existing non-opt-in checks; browser/hardware prerequisites remain
  explicit. No case/assertion deletion.
- Mock external socket/HTTP/discovery/GPU dependencies only for unit outcomes, not the route or
  behavior under test. Preserve representative actual transport, proxy, subprocess and timeout
  integration checks at production defaults.
- Add only a `LiveFeed` test wait-budget argument/default (2 seconds unchanged); shorten the
  two tests that need pending-work behavior but do not assert the deadline. Keep cold-snapshot
  and slow-log production-budget tests as real checks.
- Close every owned feed/server/socket/client/process/toast timer. Stabilize a teardown test
  proving no child/thread/server survives. Isolate fixed-port/network-dependent test setup.
- Node mock timers apply after module load. Browser polls are replaced with explicit refresh
  completion barriers; retain same HTTP/WebSocket/browser assertions and all 2,200-log outcomes.
- Lean SSR setup is conditional: keep only if it still compiles/routes the same components and
  improves matched measurements. Never share mutable SSR store singletons across cases.
- xdist and concurrent Node file execution are not in MVP. Consider only after test isolation,
  sequential improvements, repeated runs, and measured total-wall gain.

### Checkpoint C — negotiated exact bounded replay protocol

- Implement contract first as additive optional capability. Legacy request/body/response and
  UUID behavior remain intact. Unknown/unsupported capability is explicitly surfaced.
- Position fields carry producer slot, durable generation and monotonic sequence; stable
  position is bound to the existing event UUID and preserved in journal, relay and mirror.
- Fixed maximum 64 negotiated producer slots, each retaining only current generation and
  contiguous retirement frontier in durable state. Never evict/reassign an occupied slot unless
  retired generation and next generation are durably committed. When capacity/admission fails,
  return explicit retryable unsupported/full response; do not mint fallback positions.
- Hub dedup is idempotent by position. It advances a per-slot frontier only across contiguous
  accepted or explicitly retired positions. Bound out-of-order pending set to 4,096 positions
  per active slot. At capacity/hole exhaustion, backpressure/fail negotiated ingest and expose
  `identity_window_exhausted`; no auto-skip, ID eviction or partial commit.
- Persist allocation, rows and frontier safely across journal restart/rotation. If atomic recovery
  cannot prove no sequence reuse or replay after retirement, negotiated feature is unavailable.
  Legacy journal rows stay legacy; never fabricate durable producer positions by replay order.
- Worker mirrors preserve producer position/domain and upstream frontier; their receiver cursor
  stays a separate transport checkpoint. Local fallback/old master/role/master replacement
  without provable domain continuity explicitly makes that visit legacy/unbounded, preserving
  the exact UUID set; no watermark reset that re-admits previously seen IDs.
- Dashboard validates the entire frame/result before mutating cursor or identity state. Track
  watermarks plus at most 4,096 out-of-order positions per at most 64 negotiated slots. Advance
  only to advertised retirement frontier, never maximum observed row. Keep exact UUID set across
  legacy downgrade for rest of visit, mark it unbounded; Clear retains identities/frontiers;
  errors, denials, stale ACK/generation and aborts cannot advance state. Unmount frees all state.
- Add boundary tests for duplicate/out-of-order retry, missing holes, batch/page overlap, journal
  restart/rotation, worker forwarding/coverage transition, old-server fallback, slot exhaustion,
  Clear/reset, malformed frontier atomicity, exact-limit error and strictly bounded memory.

**Implementation stop gate**: if slot authority, durable reservation, contiguous-hole disposition
or compatible domain handoff cannot be established using existing ownership, report the protocol
as unsupported and propose a revised narrower requirement; do not ship a claimed bounded/exact
client using cache eviction. This gate does not block independent Checkpoints A/B.

### Checkpoint D — conditional candidates and measured acceptance

- Capture per-operation counts/profiles for capacity inventory, GGUF metadata, history query/
  retention, client construction, state saves, UI paint and startup/build cold/warm. Implement
  only candidates whose cost is measured and which have localized semantics/tests.
- Run full five-run comparisons and protected matrix after all selected work. Each proposed
  acceptance target is independent; one miss does not permit changing workloads or counting
  eliminated coverage as speed.

## Phase 1 Design Summary

- Feature data entities: [data-model.md](data-model.md).
- Public/additive compatibility contracts: [contracts/log-identity.md](contracts/log-identity.md)
  and [contracts/performance-validation.md](contracts/performance-validation.md).
- Runnable commands and measured acceptance procedure: [quickstart.md](quickstart.md).
- No durable project memory change. Feature-local notes document the choice/conflict.

## Constitution Check (post-design)

- **I**: PASS with constrained check-pointed work; the event-position protocol has significant
  complexity but addresses the explicit bounded-exact invariant; fixed bounds and fail-closed
  admission avoid future abstraction/services. The exact UUID-suppression conflict is resolved
  by stable identity/retirement and an explicit legacy mode, not silent semantic relaxation.
- **II**: PASS; tests assert observable byte-equivalent suffix, user-visible cursor/gap/dedup
  behavior, failure/overflow and cleanup. Unit categories do not remove cases.
- **III**: CONDITIONAL PASS; HTTP/WS/worker/mirror/restart boundaries and affected OS tests are
  mandatory. Hardware acceptance is not claimed for a codebase timing change absent inference
  workload; Mac/Linux branches changed by code still require target checks.
- **IV**: CONDITIONAL PASS; new fields optional/negotiated, legacy flows preserved, malformed or
  unsupported negotiated state fails explicitly, resources released; proof of durable generation
  and no replay aliases is required before enabling negotiated exact mode.
- **V**: PASS; no new dependency/lockfile and existing build commands retained. Update docs only
  if an actual public validation selection or negotiated interface changes.
- **Architecture constitution**: PASS; facade/patch names remain at source of truth, imports
  follow existing reload constraints, platform code retained.

**Post-design gate**: PASS to `/speckit.tasks`, with Checkpoint C explicitly blocked from release
until its finite-slot, restart, hole, retirement and fallback test matrix passes. No NEEDS
CLARIFICATION remains after user's exact-replay selection. No Complexity Tracking violations.

## Complexity Tracking

No new service, dependency, public CLI option, database or serving process is justified.
Negotiated position protocol's fixed 64-slot registry and 4,096-position reorder cap are the
only bounded state extension; these are explicit limits with failure/backpressure rather than
silent eviction. Reconsider slot/buffer sizes from real cluster measurements before release.
