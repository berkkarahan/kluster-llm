# Validation: Codebase and Test Performance

**Date**: 2026-09-30
**Reference revision**: `bbde77432c0ca0a87c5f56e796afcb10caae7964`
**Environment**: Windows 11 build 26200 x64; 8 physical / 16 logical CPU cores; 31.56 GiB
RAM; Python 3.12.13; pytest 9.1.1; Node 22.23.2; loaded extension
`python/kllm/_native.cp312-win_amd64.pyd`; dashboard Chrome/Edge harness available.

## Five-run complete suite comparison

Uninstrumented commands ran sequentially without other build/benchmark work. Baseline and
optimized run outputs plus machine/command metadata are retained locally in ignored
`.specify/perf-baseline-*.log`, `.specify/perf-baseline.json`, `.specify/perf-after-*.log`
and `.specify/perf-after.json`, `.specify/perf-final-python-*.log` and
`.specify/perf-final-dashboard-*.log`. The Python baseline ID list is
`.specify/perf-baseline-python-case-ids.txt`; its 521 IDs match baseline outcomes of
516 passed + 5 skipped. The five added Python checks are the suffix-boundary tests,
short-budget check, and refused-socket check. Skip reasons remain unchanged and hardware-gated.

| Suite | Baseline (five elapsed seconds; median) | Post-change (five elapsed seconds; median) | Result |
|---|---|---|---|
| Python complete suite | 81.484, 80.165, 82.135, 81.372, 80.226; **81.372 s**; 516 passed/5 skipped | 56.544, 55.317, 56.309, 54.087, 54.551; **55.317 s**; 521 passed/5 skipped | **32.0% faster**; specified 30% median target achieved. |
| Dashboard complete suite | 54.997, 46.182, 47.095, 46.282, 46.892; **46.892 s**; 47 passed | 41.782, 41.895, 41.634, 42.069, 41.851; **41.851 s**; 47 passed | **10.8% faster**; specified 20% target **not achieved** (short by ~4.34 s at baseline median). |

All five post-change Python runs and all five post-change dashboard runs completed without
failure. Dashboard's first baseline cold run is retained in the median per protocol.
No test case/assertion was removed or skipped to obtain these results.

## Oversized log preparation

Command: `uv run --no-sync python scripts/bench_log_prep.py --batches 5 --per-batch 20`.
500 distinct IDs, each 1,024 ASCII message characters; one warm-up outside 100 measured
uncached preparations. Timer covers live result preparation, not feed creation/network/browser.

| Measure | Before | After binary suffix selection |
|---|---:|---:|
| Median | 0.227692 s | 0.010215 s |
| p95 | 0.255399 s | 0.015084 s |
| Returned rows | 228 | 228 |
| Complete logs result size | 261,358 bytes | 261,358 bytes |

The 256 KiB limit applies to the full availability/sample/snapshot/error result wrapper.
Observed median reduction: **95.5%**. Equivalence tests pass for maximal newest suffix,
source non-mutation, exact byte boundary and one-byte overflow, Unicode/quotes/escaping,
retention frontier, invalid omitted row and impossible metadata. Existing count-limit and
near-limit aggregate-frame tests pass. This synthetic timing is not a browser or inference
throughput claim.

## Focused correctness and test-feedback checks

- `uv run --no-sync pytest -q tests/test_dashboard_live.py -k log_suffix`: suffix tests passed;
  complete targeted group later passed **7 tests** including wait-budget/coalescing/scope cases.
- `uv run --no-sync pytest -q tests/test_dashboard_live.py::test_live_feed_cold_sources_are_independent_and_initial_read_is_bounded tests/test_dashboard_live.py::test_slow_logs_do_not_delay_cold_status_or_initial_deadline`: **2 passed** in 4.66 s with production 2-second defaults.
- `uv run --no-sync pytest -q tests/test_native_usage.py::test_hung_source_is_bounded_and_closed tests/test_native_usage.py::test_unreaped_source_is_quarantined_without_spawning_a_replacement`: **3 passed**; real helpers and production 1.8-second deadlines remain active.
- Controlled join boundary selection across membership/auth/GPU/netsec: **6 passed** in 2.56 s.
- `tests/test_cp_proxy.py` after socket isolation/teardown changes: **7 passed**; real loopback success, shell success and helper relay checks remain.
- `tests/test_dashboard_api.py::test_chat_503_when_inference_down`: controlled failure on the actual app-owned upstream client; preserves POST proxy route and 503 failure semantics.
- `uv run --no-sync pytest -q -m unit`: **18 passed**, 508 deselected; `uv run --no-sync pytest -q -m integration`: **25 passed, 5 explicit hardware skips**, 496 deselected. Default `uv run pytest -q` still collects the complete suite.
- `node --experimental-strip-types --test --test-concurrency=1 "dashboard/tests/*.test.mjs"`: **47 passed**, zero skips.
- `node --experimental-strip-types scripts/reverse_node_tests.mjs`: **47 passed**, zero skips, reverse file order.
- `uv run --no-sync python scripts/reverse_pytest.py -q`: **521 passed, 5 skipped**, reverse collected item order.
- `npm --prefix dashboard run check`: zero errors, zero warnings.
- `npm --prefix dashboard run build`: passed (Vite 6.4.3 / adapter-static).
- `cargo test --manifest-path crates/kllm-native/Cargo.toml`: **34 passed**, 0 failed (0.21 s test bodies on final validation).
- `git diff --check`: passed. No dependency manifests/lockfiles or runtime inference options changed.

## Open / unverified acceptance

1. Dashboard's 20% target is not met: 10.8% reduction, despite all cases retained. Do not mark
   SC-002 achieved. Further options (bounded equivalent SSR Vite setup in remaining safe tests,
   browser setup profiling) require their own evidence before another patch.
2. Negotiated stable producer positions/frontier and bounded exact dedup protocol are not
   implemented. No durable position authority, restart-safe slot allocation, hole retirement,
   or domain handoff was established. The existing UUID-only exact behavior is unchanged; the
   new negotiated bounded guarantee remains open and must not be advertised as available.
3. No Mac/Linux runtime benchmark, hardware profiling, cluster-scale inference measurement,
   SQLite scaling/retention-pressure benchmark, GGUF cache benchmark, GPU-discovery benchmark,
   or clean-build comparison was conducted. No gain is claimed for those paths.
4. The full specification's storage/history, broad application profiling and cross-platform
   behavior outcomes remain open. Platform-specific tests retained skip reasons; skipped hardware
   is not represented as a pass.

## Follow-up runtime/native increment (2026-09-30)

- Consolidated native RAM/GPU acquisition reduced the local `native.probe_capacity()` median
  from **389.47 ms** to **200.12 ms** over matched warm seven-call measurements. Passing that
  inventory into local selection removes a third roughly 194 ms GPU enumeration from the
  two-second capacity refresh. Blocking probe/process PyO3 calls now release the GIL.
- A bounded stat-keyed GGUF metadata cache reduced warm one-model inventory from **129.30 ms**
  to **0.50 ms**. The tokenizer-heavy cold Python scan improved from **135.43 ms** to
  **88.84 ms** after avoiding skipped-string decoding. The new bounded Rust scanner measured
  **3.36 ms median / 3.88 ms p95** over 20 cold-cache calls with identical reported context.
- Reusing the successful local placement result reduced the 512-layer/16-device synthetic
  median from **32.89 ms** to **16.33 ms**. Ordinary 256-layer placement remained sub-millisecond;
  the 1,024-layer pooled case remains about 393 ms and is not represented as optimized.
- History aggregation for 5,000 readings with eight devices improved from **167.31 ms** to
  **135.21 ms**, and decoding/aggregation now occurs outside the SQLite store lock. A proposed
  latest-reading index was removed after validation showed it violated the existing 64 KiB
  minimum physical-storage contract.
- Recurring inference activity and worker log relay now reuse the application-owned HTTP pool.
  Native tracked children are reaped after natural exit and tracked liveness avoids an external
  `tasklist`/`kill` probe.
- Follow-up validation: Python **523 passed / 5 hardware skips** in 55.04 s; dashboard **47
  passed**; Rust **37 passed** in 0.23 s test bodies; Svelte check reported zero errors/warnings;
  dashboard production build passed; `git diff --check` passed.
- The native wheel built successfully as `artifacts/kllm-0.1.0-cp312-cp312-win_amd64.whl`.
  Updating the checkout's loaded `.pyd` remains pending because the running `kllm.agent` process
  holds it open on Windows; source and isolated-wheel validation passed without stopping that
  user-owned service.

## Retained-history and state-write checks (2026-10-01)

- Added a 7,201-reading retained-data fixture that verifies latest, series and readings
  behavior at 15m/1h/24h windows, including order, pagination and aggregate endpoints. The
  latest-node `EXPLAIN QUERY PLAN` uses `readings_filter` and a temporary B-tree for ordering.
- A local 7,201-row SQLite comparison measured 1,000 latest queries at 1.473 ms median
  without a new index and 0.0056 ms with `(node_id, observed_at DESC, id DESC)`. The index
  increased bulk insert time from 16.5 ms to 20.8 ms (+25.6%) and database size from
  2,269,184 to 2,818,048 bytes (+24.2%). The candidate also violates the existing 64 KiB
  minimum physical-storage contract; it was rejected, with schema, durability and retention
  unchanged.
- A same-member accepted heartbeat invokes `ClusterState.save` exactly once. No state-write
  coalescing was warranted.
- The recurring activity sampler and worker-log relay use the lifecycle-owned sync control
  client. `_upstream_reachable` accepts the lifespan-owned async client and keeps a 1-second
  deadline per probe. Its currently unused standalone form remains for compatibility; the
  proxy path continues to use the application-scoped inference client.
- Targeted verification: retained-history and heartbeat-count tests passed; pooled-client
  reachability test passed. Three consecutive complete `make test` runs then passed: Python
  (664 passed, 6 skipped), dashboard (50 passed including browser checks), Rust (38 passed),
  and Svelte check (0 errors, 0 warnings). The usage-workspace browser readiness wait was
  extended from 12s to 30s after a full-run-only timeout; all assertions remain intact. A
  preceding full-run failure at 30s occurred once and passed on the next three sequential
  attempts, so browser flakiness is reduced but not proven absent. This host did not run matched
  before/after workload distributions or profiler memory/handle budgets; those acceptance
  measurements remain open.

## Negotiated position-v1 protocol gate (2026-10-01)

Position-v1 remains explicitly unsupported. Current log events carry UUID IDs; missing IDs are
assigned with `uuid.uuid4()` in `cluster_log.py`. Worker relay and journal rows preserve those
IDs, while receiver cursors use a journal epoch and local sequence. The inspected ownership
path has no durable producer-slot allocation, generation recovery, next-sequence authority,
explicit retirement operation or contiguous-hole ledger. A bounded UUID eviction policy would
therefore admit old retries after eviction, and deriving frontiers from maximum observed rows
would skip holes. No positional capability is advertised or accepted, and legacy exact UUID
deduplication remains unbounded for the mounted visit. T049 covers row eviction/Clear/replay and
unmount cleanup for that legacy behavior. Negotiated cache, endpoint and end-to-end tests are
gated off rather than approximated; reconsider only after durable slot ownership and recovery
semantics are designed and tested across producer restart and journal rotation.

## Final repeat and static checks (2026-10-01)

- After the final source/test changes, `uv run python scripts/reverse_pytest.py -q --tb=long -x`
  passed 664 tests with 6 hardware skips. Its first reverse-order attempt had one transient
  failure in `test_master_update_endpoint_fans_out`; the isolated test and complete reverse-order
  rerun both passed.
- `node --experimental-strip-types scripts/reverse_node_tests.mjs` passed all 50 dashboard
  tests including the browser scenario. Three consecutive complete `make test` runs passed
  after the final browser wait-ceiling change.
- `uv run ruff check .`, `uv run mypy src` (144 files), `git diff --check`, and
  `uv run python scripts/build.py --skip-ui --skip-llama --skip-wireguard --skip-toolchains`
  passed. The build produced and installed the local CPython 3.12 macOS arm64 wheel.
