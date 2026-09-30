# Feature Memory — 011-codebase-performance

## Scope and assumptions

- Request: improve codebase performance, including unit tests, and evaluate available
  options. This turn produces a specification and option assessment; implementation follows.
- Cover Python agent/CLI, native code, dashboard, storage, network orchestration, build
  tooling, and Python/Rust/dashboard tests. Preserve existing behavior and inference settings.
- Establish evidence first; evaluate major applicable alternatives, not every possible
  third-party library. A rejected/deferred option remains documented with its trigger.

## Selected project memory

- SQLite search `installer` retrieved D1 (facade patch surface) and A1 (structural
  standards). Keep patched names on `kllm.agent` and the loaded `scripts/install.py`
  module; helpers receive patched dependencies rather than importing their own globals.
- Search `standards` confirmed A1: use existing modules and types, same-module helpers
  before abstractions, top-level imports except cycles/optional modules/reload-sensitive tests.
- Governance constitution 1.1.0 requires behavior regression checks, real boundary checks,
  truthful platform/hardware evidence, compatible contracts, cleanup and reproducible gains.
- Private LAN/WireGuard RPC and the committed stable llama.cpp pin remain requirements.

## Related feature context (transient)

- 005 already supplies shared live acquisition, per-topic caches, viewer interest,
  ordering/freshness, bounded frames and cancellation. Reuse these; compare legacy status too.
- 010 has implemented native probes and two bounded helper slots. Its plan still says
  planned; `validation.md` and current source record implementation plus Windows hardware
  evidence. Mac/Linux runtime and broader hardware acceptance remain outstanding.
- 010's Windows real-helper-PID and native-checkout-shadowing lessons are proposals in
  feature memory, not approved durable policy. Verify loaded extension identity in measurements.

## Preparation record

- Read configured roots and governance before retrieval. Memory MCP tools were unavailable;
  executed installed extension SQLite indexing, search and synthesis APIs/CLI equivalents.
- Ordinary refresh failed with `UNIQUE constraint failed: memory_entries.id`: governance
  paths overlapped memory/doc indexing. Removed only derived doc copies of two governance
  paths, refreshed memory, and refreshed docs with a temporary in-memory governance exclusion.
  No source configuration, durable memory, or extension code changed.
- Memory refresh: 6 files scanned, 4 refreshed, 2 unchanged. Docs refresh: 99 files scanned,
  5 refreshed, 94 unchanged. Generated synthesis was too narrow; targeted cache searches and
  relevant active-feature/source reads inform the compact synthesis. Automatic doc synthesis
  selected zero chunks; this is not an empty-cache claim (99 documents were indexed).
- Initial memory synthesis banner: 2,921 baseline / 198 cached / 2,723 saved estimated
  tokens (93.2%); this is the generator's estimate, not a claim for this whole assessment.

## Watchpoints and open measurements

- Unit tests currently include real HTTP/socket/subprocess/native boundaries. Preserve these
  checks while removing accidental live dependencies from ordinary behavior tests.
- Do not gain speed by skipping tests, weakening assertions, reducing freshness, suppressing
  errors, changing GPU memory budgets, or reducing storage durability/retention.
- Distinguish CPU work, OS/network waits, fixture setup, collection, and subprocess teardown.
- Parallel test workers need isolated ports, homes, module-global state, subprocess ownership,
  and generated dashboard state. Process-global mocks cannot become shared session state.
- Need repeated baseline distributions before implementation acceptance. This specification's
  measurements are preliminary, not speedup evidence; hardware tuning remains unmeasured.

## Durable capture

No durable decision is introduced by this specification. Cache repair and assessment findings
are feature-local; propose durable lessons only after implementation and explicit approval.

## Assessment checkpoint

- Baseline revision `bbde77432c0ca0a87c5f56e796afcb10caae7964`; Windows 11 build 26200,
  Python 3.12.13, 8 physical/16 logical cores, 31.56 GiB RAM, Node 22.23.2; loaded in-tree
  extension path recorded in `validation.md`.
- Five Python baseline runs: median 81.372 s; 516 passed/5 skipped each. Five dashboard
  baseline runs: median 46.892 s; 47 passed each. Five post-change runs: Python median 55.317 s
  (521 pass/5 same hardware skips), dashboard median 41.851 s (47 pass). Python target passed
  at 32.0% reduction; dashboard target remains open at 10.8% reduction.
- Complete-envelope 500×1,024-character log benchmark: before 227.692 ms median; after
  10.215 ms (95.5% lower), 228 rows and 261,358 bytes in both. Detailed evidence and boundary
  commands/results are in `validation.md`.
- Synthetic planner medians at 32/80/256 layers and eight devices: 0.679/1.792/16.314 ms.
  No planner/native rewrite justified. Rust 34 tests remain protected.
- The test improvements include controlled external failure cases, a per-feed wait-budget seam
  with the unchanged 2-second production default, opt-in unit/integration markers, owned
  TestClient/LiveFeed/socket cleanup, store timer control, poll-completion barriers, and tested
  component-only SSR setup. Legacy log behavior is unchanged.
- Exact bounded identity protocol remains unimplemented: durable authority/restart/hole/handoff
  proof is a release gate. The current live feature has no `position-v1` negotiation. Platform
  Mac/Linux and hardware performance claims, broader history/storage options, and dashboard
  target miss are explicitly open.

## Planning decisions (2026-09-30)

- User chose to preserve exact replay identity and accept the additive negotiated position /
  retirement protocol. Legacy UUID-only behavior remains supported and exact; a visit that
  downgrades preserves UUID identities and is visibly marked unbounded until unmount.
- Focused review found that receiver cursor, UUID LRU/TTL, timestamps, and resetting at source
  changes cannot prove exact dedup under arbitrary replay. Negotiated mode therefore needs
  durable producer slot/generation/sequence, idempotent retries, contiguous retirement
  frontiers, and explicit bounded backpressure on holes/window exhaustion.
- Sizing is corrected to the complete dashboard logs result (256 KiB), not only the inner
  snapshot. Use binary search with `_encoded` after complete-candidate validation; preserve
  explicit impossible-payload errors and gap/cursor behavior.
- Test plan preserves every existing case and serial full-suite commands; control external
  dependencies only for unit cases, keep actual HTTP/process/deadline/browser tests. Python
  test wait-budget seam retains the two-second production default. No new parallel runner.
- Protocol delivery stop gate: if durable authority/restart/slot transition/hole proofs do not
  fit existing ownership, keep negotiation explicitly unsupported. No silent eviction or new
  service/database. Detailed intent is in plan.md and contracts/log-identity.md.

## Capture review

- Candidate durable bug pattern: enforce encoded-size limits on the full consumer-visible
  envelope, not an inner nested payload. Evidence: Python originally sized the snapshot while
  the dashboard validator sized the complete logs wrapper; the new boundary test confirms both
  use the same budget. This may prevent a reusable class of producer/consumer cap mismatches.
- No durable write was made. Repository governance requires explicit human approval before
  changing `docs/memory`; this implementation turn contained no approval. Candidate and evidence
  remain feature-local; low-level algorithm and unimplemented position protocol are not captured.
