# Performance Assessment and Option Evaluation

**Date**: 2026-09-30
**Feature**: [spec.md](spec.md)
**Status**: Specification-stage assessment; not an implementation plan or speedup report.

## 1. Recommendation

Start with avoidable test waits and oversized-log serialization. Both have measured costs
and existing correctness tests. Next measure repeated GPU discovery, model metadata reads
and history queries before selecting changes. Keep the already fast planner and Rust test
bodies unless more representative workloads establish a need.

No runtime, test, dependency, build or engine configuration was modified here. Targets in
the specification are prospective requirements, not achieved improvements.

## 2. Baseline evidence

Revision: `bbde77432c0ca0a87c5f56e796afcb10caae7964`; clean tree before this specification.
Windows 11 build 26200, x64, 8 physical / 16 logical cores, 31.56 GiB RAM;
Python 3.12.13, pytest 9.1.1, Node 22.23.2. Python loaded
`python/kllm/_native.cp312-win_amd64.pyd`. Dependencies were already installed.
GPU/model/network throughput was not measured. Benchmark runs were sequential.

| Workload | Command/method | Result | Limitation |
|---|---|---|---|
| Complete Python suite | Five sequential `uv run --no-sync python -m pytest -q` runs | 516 passed, 5 skipped, 2 deprecation warnings each; elapsed **81.484, 80.165, 82.135, 81.372, 80.226 s**; median **81.372 s** | Same revision/native module, serial and uninstrumented; per-run logs/metadata retained in ignored `.specify/perf-baseline-*`. |
| Complete dashboard suite | Five sequential `node --experimental-strip-types --test --test-concurrency=1 "dashboard/tests/*.test.mjs"` runs | 47 passed, zero skips each; elapsed **54.997, 46.182, 47.095, 46.282, 46.892 s**; median **46.892 s** | First-run compilation outlier retained in median; real browser included; logs/metadata in ignored `.specify/perf-baseline-*`. |
| Native suite | `cargo test --manifest-path crates/kllm-native/Cargo.toml` | 34 passed; **0.23 s** test bodies; **10.418 s** command wall time | Existing build artifacts; wall time includes Cargo work/startup. Clean build unmeasured. |
| Oversized log preparation | Direct uncached `LiveFeed._log_result`, 500 identified 1,024-character messages | Five calls: 245.71, 269.10, 245.57, 210.47, 208.53 ms; **245.57 ms median** | Synthetic batch stress, not an ordinary 200-line source or end-to-end browser latency. |
| Log output baseline | Same workload | **228 retained entries**, **261,269 encoded snapshot bytes** in every call | Existing 256 KiB limit; suffix, gap and cursor semantics must remain correct. |
| Planner, 8 local devices | Five `plan_devices` calls per size at 4,096 context | 32 layers: **0.679 ms** median (tight); 80: **1.792 ms** (tight); 256: **16.314 ms** (overflow) | Synthetic; not manual shares, unified memory, auto-context or inference throughput. |

The single-run suite times would imply 56.37 s Python and 37.35 s dashboard at the proposed
30%/20% reductions. These are illustrations; acceptance uses new five-run matched medians.

### Slow Python cases

| Test | Call time | Source interpretation |
|---|---:|---|
| `test_real_uvicorn_http_snapshot_and_websocket_change` | 3.22 s | Required transport, heartbeat and failures. Retain actual boundary checks. |
| `test_ensure_skips_proxy_when_connection_refused` | 3.02 s | Two real refused-connection probes on loopback port 1; OS refusal/wait cost. |
| `test_shell_can_reach_localhost` | 2.39 s | Real shell startup/socket failure, Windows PowerShell path. |
| `test_live_feed_viewer_interest_drives_one_shared_cadence_and_releases_on_close` | 2.39 s | Actual cache cadence followed by 1.2 s sleep to assert no continued work. |
| `test_join_applies_cluster_members` | 2.30 s | Route side effects/external isolation merit tracing; duration alone does not prove the exact cause. |
| `test_chat_503_when_inference_down` | 2.05 s | Real unused port 39999; another local application can change the outcome. |
| Two worker-log fallback cases | 2.03 / 2.02 s | Failed upstream reads; check control-client isolation. |
| Four blocked/cold/scope live-feed cases | About 2.00 s each | Deliberately exhaust the two-second snapshot deadline. |
| Native hung-source cases, host/GPU | 1.81 / 1.80 s | Real helpers verify production 1.8-second timeout and cleanup. |
| Native unreaped-source quarantine | 1.83 s | Actual timeout verifies no replacement before reaping. |

Not all waits are accidental. Replace redundant *unit* waits with controlled outcomes/time,
while keeping representative real defaults and every behavioral assertion.

T001 repeat baseline (five sequential, uninstrumented full runs, same revision/extension,
Windows 11 build 26200, no simultaneous benchmarks): Python elapsed **81.484, 80.165, 82.135,
81.372, 80.226 s**, median **81.372 s**; every run 516 passed, 5 skipped and 2 warnings.
Dashboard elapsed **54.997, 46.182, 47.095, 46.282, 46.892 s**, median **46.892 s**; every
run 47 passed, zero skipped. The first Node run is a visible cold/setup outlier and stays in
the median per protocol. Full outputs, machine metadata and the reconstructed 521 baseline
Python case IDs are retained in ignored `.specify/perf-baseline-*` files; the five later-added
checks were excluded from current collection to match baseline test identity.

Dashboard baseline component costs in a separate initial diagnostic: browser workspace
**24.896 s**; SSR model search **3.152 s**; hardware-setting/store **2.637 s**; history/live/
workspace SSR tests about **1.565–1.716 s** each. Files repeatedly create Vite servers and load
modules. Controlled timer tests already exist in `live-updates.test.mjs`; reuse that pattern.

### Reproduce the log baseline

Run this with `uv run --no-sync python <temporary-script.py>` from the repository root.
It needs no cluster or GPU. Promote it into a comparison harness only during implementation.

```python
import statistics
import time
from kllm.agent.live import LiveFeed, _encoded
from kllm.cluster_log import make_event

rows = [make_event("synthetic", "agent", "x" * 1024, ts=1.0) for _ in range(500)]
durations = []
for _ in range(5):
    feed = LiveFeed(
        {},
        lambda after: {
            "lines": rows,
            "cursor": "00000000-0000-4000-8000-000000000001:500",
            "reset": None,
            "gap": False,
            "gap_reason": None,
            "source": "cluster",
        },
    )
    try:
        began = time.perf_counter()
        result = feed._log_result(None, None)
        durations.append(time.perf_counter() - began)
        snapshot = result["snapshot"]
        assert snapshot["lines"] == rows[-228:]
        assert snapshot["gap_reason"] == "batch_limit"
        assert len(_encoded(snapshot)) <= 256 * 1024
    finally:
        feed.stop()
print(durations, statistics.median(durations))
```

Acceptance uses five batches of 20 uncached calls and compares retained fields rather than
UUID-valued continuity. This baseline times `_log_result` only, excluding feed creation.
The 228-row expectation identifies this exact fixture, not a universal count.

### Reproduce the planner baseline

Call `placement.plan_devices` five times for each of 32, 80 and 256 layers, with:

- Layout: architecture `llama`, confidence `metadata`, embedding length 4096, 32 attention
  heads / 8 KV heads; each layer, input and output weigh 100 MiB.
- One master node, 1 GiB total/available RAM, eight local Vulkan devices, each 4 GiB
  total/free memory; no unified memory, no reclaimable allocation.
- Settings: one parallel slot, micro-batch 128, zero checkpoints, q8_0 K/V cache, layer split.
- Call parameters: model size `(layer_count + 2) * 100 MiB`, context 4096, headroom .8,
  device GPU, VRAM weight .6, latency weight .4.

Measure only the call with `perf_counter`, report all five values/median and fit status.
No model file, driver discovery or inference execution is involved.

## 3. Current flows and reuse opportunities

- **Capacity**: `agent.status` → `native.probe_capacity` → native GPU enumeration plus
  `native.list_gpus` → native enumeration again. `_refresh_local_capacity` additionally
  calls `_resolve_gpu_selection` → `native.list_gpus` again. Non-Mac enumeration can run
  nvidia-smi/rocm-smi/WMI. Installed-binary capability probes already have an LRU keyed by
  binary, modification time and a 30-second epoch. Measure fresh/warm work before adding caches.
- **Live status**: `_live_status` already reads completed member metadata. `LiveFeed` already
  coalesces acquisition, isolates topics, respects viewer interest, rejects obsolete scopes
  and ages cached usage. Legacy status still probes capacity/software.
- **Logs**: `_log_result` repeatedly pops the oldest line and encodes the entire snapshot.
  This fixture removes 272 of 500 entries, re-encoding hundreds of kilobytes hundreds of
  times. `LogHub.snapshot` copies/scans its bounded ring. Browser rows cap at 2,000, but
  `seenIds` grows for the whole visit.
- **Metadata**: `list_local_models` reads uncached context metadata for every file, then
  resolves defaults, parsing again on a cold miss. Layout already has a 16-entry stat-keyed
  LRU; defaults use a separate unbounded dict. Prefer bounded shared extraction over
  persistent cache infrastructure.
- **Planning**: main routes pass launch settings into `native.plan_split`, selecting the
  Python device planner. The Rust planner is a distinct compatibility path; speeding only
  Rust would miss this route. Python DP copies assignment paths and scans nested states;
  `memory_costs` evaluates a default recurrent-layer list inside each iteration. Current
  measured costs do not justify a rewrite.
- **Network**: peer latency/preflight loops are serial. Capacity/latency already have
  single-in-flight guards. `http_clients` pools control connections with 120-second keepalive;
  inference proxying uses an application-owned async client and cancellation-safe closure.
  Activity sampling, log relay and `_upstream_reachable` still construct clients.
- **History**: SQLite uses FULL synchronization, DELETE journal, FULL auto-vacuum and existing
  time/node-lifetime-time indexes. Latest-node queries may still sort because lifetime is
  not filtered. `series` decodes/aggregates payloads under the store lock. Byte-cap `_prune`
  individually deletes/commits rows. Measure query plans, lock time and physical cap behavior.
- **State**: heartbeat/maintenance paths serialize the full dataclass via `ClusterState.save`.
  Reducing writes needs recovery-safe rules; changing timestamps and role/process transitions
  cannot be ignored as if all persisted fields were static.
- **Probes**: two persistent helpers already share requests and enforce queue/frame bounds,
  process identity, deadlines and quarantine/reaping. Direct blocking/native calls would
  weaken this boundary, not simply make it faster.
- **Builds**: installer package/UI/llama stages already run concurrently; system package
  managers remain outside that phase. Cargo is incremental; targeted skip flags and
  `--release` already exist. There is no repository CI workflow to optimize in place.

## 4. Option comparison

Evidence: **M** measured here; **S** source-supported hypothesis; **E** existing capability.
Benefits are hypotheses unless a measured cost is stated. Effort: low = local change;
medium = multiple callers/lifecycle checks; high = architectural/hardware-heavy work.

### Test feedback and measurement

| Option | Evidence / possible benefit | Effort / risk | Disposition and validation trigger |
|---|---|---|---|
| Existing duration reports and stdlib/native/browser profiling | M/E; exposes waits and real CPU/allocation/query costs | Low; profilers distort elapsed time | **Use first:** cProfile, tracemalloc, query plans, Cargo timings, browser traces; verify with uninstrumented runs. |
| Continuous profiling service/new benchmark framework | Richer ongoing reporting, no unmet need demonstrated | Medium/high dependency and operational cost | **Defer:** existing tools must prove insufficient first. |
| Controlled failed HTTP/discovery/hardware dependencies in unit tests | M/S; avoids several 1–2 s waits and machine variability | Low/medium; mocking the behavior under assertion invalidates it | **Prioritize:** patch external boundary, preserve route behavior and real integration cases. |
| Controlled clocks/events for redundant timing assertions | M/E; repeated 1.2/2 s waits; Node mock-timer pattern exists | Medium; global time patches can corrupt scheduler semantics | **Prioritize:** preserve representative real production defaults, transport and helpers. |
| Browser readiness/completion signals instead of blind sleeps | M/S; browser scenario costs 24.9 s | Low/medium; cannot erase recovery/cadence checks | **Prioritize evaluation:** phase timings, stable observable readiness, identical assertions/viewports. |
| Share immutable setup/Vite startup in compatible groups | M/S; repeated 1.5–3.2 s initialization | Medium; stores, cwd and global mocks can leak | **Evaluate:** prove resets/ownership; close all servers; share no mutable agent state. |
| Session-scoped mutable client/agent or blanket reload removal | S; lower setup possible | High; reload-sensitive state and configuration contracts | **Reject blanket approach:** only individually traced immutable reuse qualifies. |
| Unit/integration/browser/hardware category selection | E/S; faster targeted local feedback | Low; default deselection can hide coverage | **Adopt explicit selection:** complete commands retain all non-opt-in cases; category separation alone is not suite speedup. |
| Process-based pytest workers, such as xdist | S; overlaps independent waits | Medium/new dev dependency; ports, homes, native helpers and disk contend | **Conditional:** isolate first; compare 1/2/4 workers and keep serial mode; adopt only a measurable total-time gain. |
| Node file concurrency/test scheduling | S; overlaps module setup | Medium; shared generated state, globals and browser RSS | **Conditional:** demonstrate isolation; check/build/tests never overlap on shared generated files. |
| Rust runner replacement, such as nextest | M/E; bodies already total 0.23 s | Medium extra tooling, small payoff | **Defer:** native suite growth or isolation problems must dominate first. |
| Skip slow cases, weaken assertions or cut production timeouts | Would reduce reported time | Loses coverage/changes behavior | **Reject:** prohibited by scope and correctness requirements. |

### Agent, metadata and algorithms

| Option | Evidence / possible benefit | Effort / risk | Disposition and validation trigger |
|---|---|---|---|
| Log suffix sizing without repeated whole-batch encoding | M; 245.57 ms median stress | Low/medium; Unicode, changing gap metadata, exact suffix | **First runtime candidate:** compare one-pass byte accounting and bounded suffix search; exact outputs and ≥50% faster. |
| Less log ring copying/duplicate snapshots | S; fewer scans for concurrent cursors | Medium; filters, retention, epochs and locking | **Conditional:** full-ring/cursor/viewer benchmark first. |
| One fresh GPU inventory reused within each operation | S; duplicate enumerations in real call chains | Low/medium; fallback, selection and patched facades | **Next measurement:** count subprocesses; compare fresh equivalent outputs before/after. |
| Stable discovery/version separated from fresh observations | E/S; avoids capability work without stretching observation cadence | Medium; install/device/backend changes and stale free memory | **Evaluate existing caches first:** identity, bounds, hot-removal invalidation; retain freshness. |
| Bounded shared model-metadata extraction/cache | E/S; cold duplicate parses and uncached context reads | Low/medium; replacement, truncation, mutation and templates | **Evaluate:** realistic tokenizer-heavy GGUF, cold/warm inventory and stat-based replacement invalidation. |
| Seek unused metadata rather than decode/read allocations | S; `_skip_value` currently reads arrays/strings | Low/medium; length bounds, EOF/truncation semantics | **Conditional:** parser profile and equivalent malformed-input checks first. |
| DP path/state simplification, reuse costs, hoist invariants | M/S; small planner medians 0.679/1.792/16.314 ms | Medium; contiguous allocation/manual/unified/device order | **Low priority:** benchmark auto-context/manual/overflow extremes; local cleanup before algorithm replacement. |
| Rust planner/parser port, vectorization, JIT or interpreter swap | No dominant CPU hotspot proven | High; conversion costs, duplicate semantics, packaging/platforms | **Defer:** require profiles proving simpler fixes insufficient. |
| CLI import/startup reduction | S; broad CLI dependency imports | Medium; cycles, reload tests and import standards | **Conditional:** measure fresh-process help/startup; local imports require an allowed governance exception. |
| Global JSON serializer replacement or protocol compression | S; fewer encode cycles/bytes possible | Medium/new dependency; NaN/canonical bytes, CPU/wire tradeoff | **Defer:** redundant serialization first; remaining encoding/network must dominate. |

### Network, persistence and source ownership

| Option | Evidence / possible benefit | Effort / risk | Disposition and validation trigger |
|---|---|---|---|
| Existing HTTP pools reused in remaining recurring calls | E/S; control/inference pooled, activity/log paths are not | Low/medium; deadline, mocks, trust_env, lifecycle | **Evaluate:** connection/constructor counts, failures and cancellation; reuse ownership, not a second pooling service. |
| Bound metadata-search fan-out and reuse unchanged remote results | S; keyword search inspects up to eight repositories serially | Medium; ordering, rate limits, access changes, revisions and partial warnings | **Conditional:** controlled per-repository latency/rate-limit fixtures first; keep deterministic ranking and credential-scoped invalidation. |
| Download/hash/transfer buffering, resumption and integrity reuse | E/S; 1 MiB streaming chunks, throttled progress and identity-checked digest sidecars already exist | Medium; interrupted writes, checksum correctness, cancellation and I/O contention | **Reuse first:** benchmark controlled local transfer/storage; compare buffer sizes or streaming digest only if measured costs dominate; never skip verification for speed. |
| Bounded peer latency/preflight fan-out | S; serial waits grow with peers | Medium; deadlines, state mutation, caps and failure order | **Conditional:** 1/4/16 peers with slow/failing workers; never probe unauthenticated RPC with a raw TCP handshake. |
| Fully async rewrite/multiple serving workers | No bottleneck demonstrated; existing guards and blocking-route offload | High; global state, DB writers and helper/launch ownership | **Defer:** prove worker/event-loop bottleneck; do not duplicate supervision with generic multiprocess serving. |
| In-process resource collection instead of helper slots | E; helper reuse already implemented | High; native stalls cannot be safely abandoned | **Keep two owned slots:** optimize inside sources only after measurement; retain deadlines/reaping. |
| Native OS counters instead of recurring shell-based measurements | E/S; 010 already uses native Windows/macOS sources, legacy capacity still shells out | Medium; counter scope, driver/OS availability and device identity | **Reuse implemented probes where semantically compatible:** never replace capacity with live usage; require target accuracy/failure evidence for remaining replacements. |
| Targeted SQLite index/less query and decode work | S; latest-node queries/history aggregation | Low/medium; write costs, migration, ordering | **Evaluate:** query plans and 15m/1h/24h at retained-data scale; exact cursors/results. Follow-up rejected latest index: +25.6% synthetic insert time, +24.2% DB bytes and violates the existing 64 KiB minimum-storage contract. |
| Batch byte-cap eviction/reduce redundant commits | S; individual delete/commit loops | Medium; physical cap, rollback headroom, newest batch survival | **Evaluate:** retention pressure and crash/failure checks; preserve acknowledged-save meaning. |
| WAL/separate connections/incremental vacuum | S; read/write overlap possible | Medium; sidecar bytes, checkpoints and cap accounting | **Conditional:** contention measurements first; prove total physical cap/durability across journal states. |
| Lower sync durability, memory-only history or reduced retention | Faster writes/queries possible | Data loss or feature regression | **Reject as speedup:** changes explicit requirements. |
| Coalesce/avoid cluster-state JSON writes | S; full-state frequent serialization | Medium; recovery/timestamps/concurrent writers | **Conditional:** I/O profile and immediate-persistence transition rules; no suppressed meaningful state. |
| New DB, Redis, broker or monitoring daemon | No need beyond bounded local state established | High service/schema/operating cost | **Defer:** existing local/SQLite mechanisms must fail a measured requirement first. |

### Dashboard, builds and hardware-sensitive alternatives

| Option | Evidence / possible benefit | Effort / risk | Disposition and validation trigger |
|---|---|---|---|
| Bound browser deduplication state | S; `seenIds` grows beyond 2,000 visible rows | Medium; forgetting IDs can resurrect Clear/replayed entries | **Evaluate long visits:** continuity/cursor-aware identity bounds, replay and reset checks. |
| Equal-revision/hidden-view work avoidance | E; revision and visibility controls exist | Low reuse; new throttling risks terminal outcomes/freshness | **Preserve/reuse:** profile remaining work before more debounce/throttling. |
| Render simplification, virtualization, chart aggregation, worker offload | S; 2,000 log rows, up to 720 history buttons | Medium/high; focus, keyboard gaps and worker copying | **Conditional:** production browser traces first; localized derived/render cleanup before libraries/workers. |
| Lazy-load editor/routes or reduce bundle/transfer | E/S; CodeMirror installed, production effect unmeasured | Low/medium; load UX and SSR | **Conditional:** production bundle/network evidence; dev compile time is not a transfer benchmark. |
| Existing incremental/targeted flags and release native build | E; already supported | Low; development/release identity and shadowed wheel | **Use/document:** separate clean/incremental baselines and inspect actual loaded extension. |
| More build parallelism, LTO/PGO, sccache/compiler caches | E/S; stages already parallel and Cargo incremental | Medium; RSS, cache keys, platform flags | **Defer broad tuning:** breakdown first; release mode before advanced flags. |
| CI sharding/platform caches | No workflow currently exists | Medium runner/infrastructure cost | **Evaluate only:** isolation and available runners precede rollout; no hosted service assumed. |
| Engine tuning: threads, batches, KV precision, Flash Attention, speculative decoding | Available knobs; model/hardware dependent | High verification; quality/memory/settings differ | **Separate experiment:** fixed settings for codebase acceptance; performance/quality evidence before tuning defaults. |
| Placement/backend changes, more GPUs/RAM, 10GbE or different engine | Can dominate tokens/s by workload | High hardware/protocol/support cost | **Defer from implementation:** real private-cluster evidence needed; synthetic planner timing proves no inference gain. |
| CPU affinity, NUMA placement, power/driver/storage settings | Hardware-sensitive throughput or startup effects | Medium/high; portability, thermal/power variability and privileged system changes | **Separate controlled experiment:** record settings/topology; no automatic machine changes or attribution to a code-only patch. |

## 5. Acceptance measurement protocol

1. Preserve revision/lockfiles. Record OS/CPU/RAM/browser/runtime/compiler, native extension
   path, build mode, tree differences and model/backend if relevant. Missing prerequisites
   remain open, not passes.
2. Five baseline and five optimized complete runs, uninstrumented, with no simultaneous
   benchmark/build activity. Preserve case identities/outcomes, skip reasons, setup/reset
   rules and cold/warm labels. Runner elapsed and total command wall time are separate fields.
3. Profile selected expensive flows; preserve equivalence fixtures. Logs: five batches of
   20 uncached calls. Planning: include auto-context/manual/unified/overflow extremes.
   Metadata: realistic tokenizer-heavy files and cold/warm reads. No extrapolated whole-app
   gain from a single microbenchmark.
4. Runtime matrix: 1/4/16 nodes, 1/5 viewers, idle/changing/stalled/denied sources, cancelled
   streams and burst logs. History: 15m/1h/24h, growth up to physical cap, duplicates,
   out-of-order points and pruning. Controlled synthetic data for algorithms; actual local
   boundaries for integration; relevant target hardware for hardware claims.
5. Collect median/p95 latency where at least 100 operation samples exist, CPU time,
   peak/retained memory, handles/process/thread counts, I/O and acquisition counts as relevant.
   Changed long-lived paths get a ten-minute steady-state run after warm-up. Existing profiler
   deadlines and memory/handle budgets remain gates.
6. Hold inference workload/settings fixed. Reuse `scripts/compare_inference.py` where
   appropriate; supplement topology/model/backend/pin identity. It measures five requests
   and median/min/max, not p95 or a hardware manifest. `measure-idle-dashboard-requests.mjs`
   is request-count evidence only and needs valid real-time fixture behavior for that claim.
7. Complete suites, isolated changed selection and reverse order; adopted parallel mode
   also needs three clean complete runs. Run affected target checks before claiming verified
   platform behavior. Dashboard check/test/build remain sequential (shared generated state).
8. Report distributions/percent changes. Resolve >5% suspected regressions with paired
   remeasurement; confirmed regressions fail. Remove unsuccessful candidates instead of
   weakening assertions, changing workloads or silently relaxing requirements.

## 6. Open evidence and planning handoff

- No runtime throughput, actual capacity latency, history scaling, clean-build or production
  browser CPU/memory baseline was captured. These remain hypotheses. Five-run complete-suite
  baselines are also still needed.
- Initial delivery targets: complete Python -30%, dashboard -20%, oversized log preparation
  -50%. Retain all validation and log semantics.
- Use existing facades/modules. Caches, concurrency, indexes, planner and native rewrites
  require their own evidence before selection.
- Existing 010 Mac/Linux runtime and wider hardware follow-ups remain there; its older plan
  and cross checks are not current performance passes.
- No blocking product ambiguity. Planning resolves exact workload fixtures, category
  selection, shared-fixture boundaries and accepted candidates before implementation.

## 7. Planning decisions from focused source review

### Test acceleration

The focused test-flow review recommends retaining all **521 existing Python collected cases**
(516 pass/5 skip baseline) and **47 Node cases**, with complete validation serial. Highest-value
localized candidates are:

- `test_ensure_skips_proxy_when_connection_refused` performs real failed sockets twice
  (3.02 s). Mock the socket boundary and keep `probe_master`/`ensure_for_master` assertions.
- `test_worker_get_falls_back_to_local` and `test_worker_local_fallback_reports_cursor`
  use a 2-second proxy client (about 4 s combined). Mock the request transport, preserve route,
  warning, row, cursor and identity assertions.
- `test_chat_503_when_inference_down` performs real localhost failures (2.05 s). The chat route
  proxies through the lifespan-owned inference client (it does not call `require_inference`);
  inject a controlled failing transport on that client and assert the real proxy path returns 503.
- Six join routes spend time on incidental interface/peer discovery (one 2.30 s measured).
  Control only irrelevant socket/GPU/software sources; retain the actual registration and
  binding result.
- `tests/test_dashboard_live.py` has two cases waiting the full two-second cache budget to
  observe pending state. Add a per-`LiveFeed` wait-budget argument whose production default
  remains 2 seconds; keep cold/slow-source tests at production defaults and preserve real
  native helper deadlines of 1.8 seconds.
- Close owned `LiveFeed`, servers, sockets, clients and subprocesses; a test that starts
  blocked async work must release and await it. Use Node mock timers after SSR module loading
  to replace the 300-ms store debounce; check four-second toast timer tails.
- Browser readiness polls must wait for observable successful refresh/render barriers, not
  assume shorter sleeps. Preserve actual Chrome, all 2,200 log identities and capped-row,
  omission, keyboard/layout, cursor and Clear assertions. Keep full SvelteKit config for browser
  tests. A focused NodeMap SSR probe measured full Vite setup/load at 873/334/284 ms and lean
  Svelte-plugin-only setup/load at 316/157/149 ms over three runs; applied the proven lean
  helper only to the component-only `usage-live` and `usage-history` SSR tests, retaining full
  config for route-loading/browser test files.
- Add in-place pytest `unit` and `integration` markers or equivalent. Default complete
  validation continues to include every existing non-opt-in check. No blanket mutable fixture,
  global time patch, suite deselection, `--test-force-exit`, pytest-xdist or parallel Node run.

Controlled reverse-order validation: reverse pytest collected items in a temporary plugin
passed to `pytest.main`, not shell-glob arguments; Node's glob runner sorts paths, so use
`node:test` `run({files: sortedFiles.reverse(), concurrency: 1})` for file order. Reverse-order
cases within one Node file still require explicit local test registration control if that gate
is adopted; file reversal alone is not represented as same-process isolation evidence.

### Log exactness and envelope budget

Replace `LiveFeed._log_result` prefix-removal loop with binary search over the **smallest
omitted prefix**, using existing `_encoded` for each full envelope candidate. Complexity is
`O(B log n)` over encoded bytes, with at most nine probes for 500 rows, without duplicating
Unicode/JSON byte accounting. Validate the complete original candidate before trimming so
invalid omitted rows cannot turn into successful payloads. Freeze `gap=true` and
`gap_reason=batch_limit` while sizing trimmed candidates. Keep count-limit, retention precedence,
highwater, reset and discontinuity data correct. An impossible empty candidate remains an
explicit oversized result.

The Python log preparer currently caps encoded `snapshot` at 256 KiB; the browser validator
caps the **complete logs result** at 256 KiB. Use the complete result envelope as the server
predicate, matching the existing dashboard contract. This deliberately may keep fewer rows
than the earlier 261,269-byte snapshot-only synthetic baseline. The acceptance fixture must
compare the same 256-KiB complete-result definition and verify aggregate live frames.

### User decision and bounded identity protocol

Focused review proved a scope conflict: the existing dashboard contract requires exact
session-wide event-ID dedup; log ingestion preserves UUID retries but does not promise exactly
once or a retirement signal. UUID LRU/FIFO/TTL, visible-row caps, receiver cursors, timestamp
watermarks and epoch resets cannot provide bounded exact replay suppression under arbitrary
old-ID replay.

**User selected “Preserve exact replay”**: plan an additive negotiated stable producer-position
and retirement protocol, preserve legacy UUID behavior, and explicitly exclude legacy-server
mode from the new bounded-bookkeeping guarantee. For a live visit that downgrades to a legacy
server, retain its exact seen-UUID set and mark that visit legacy/unbounded until it ends; do
not silently clear it or cap it. This resolves the transition UX conservatively.

Proposed research contract (not yet implementation evidence): up to 64 durable producer slots;
each event has stable `(slot, generation, sequence)` position bound to its existing UUID; retries
retain the position; retired/future generations are rejected. Snapshot publishes a contiguous
retirement frontier, not the maximum observed row. Dashboard retains per-slot watermarks plus at
most 4,096 out-of-order positions per active slot and prunes only at an atomically validated
frontier. Ingest/hub uses the same finite reorder bound. A hole or full window backpressures or
returns explicit `identity_window_exhausted`; it never skips, reassigns or evicts silently.

Reuse `cluster_log.LogHub`, existing POST/GET paths, `live_ws` mirror, dashboard live types and
`LogTrail`. Receiver-local cursors stay separate from producer positions. Preserve legacy rows
as legacy; never assign durable identity from journal load order. Fix the domain authority,
durable allocation and restart/rotation atomicity before enabling negotiated capability. Local
fallback, old upstream, master replacement and role change without provable domain continuity
retain legacy exact tracking and expose an explicit transition/gap. If authority, durable slot
reservation or contiguous-hole proof cannot be implemented inside these ownership boundaries,
the negotiated capability remains explicitly unsupported; the existing exact legacy behavior
continues. This is the hard stop gate, not a license for a new monitoring service.

### Executed MVP measurements (2026-09-30)

- `scripts/bench_log_prep.py --batches 5 --per-batch 20` before the binary-suffix change:
  500 × 1,024 ASCII characters; median **0.227692 s**, p95 **0.255399 s**, 228 rows,
  **261,358-byte complete result**. Sample output is retained in ignored
  `artifacts/log-prep-baseline.json`.
- Same command after `_fit_log_suffix`: median **0.010215 s**, p95 **0.015084 s**, 228 rows,
  261,358-byte result. Measured median reduction **95.5%** (22.3× faster) for this synthetic
  workload; boundary tests and complete post-change suite runs still gate acceptance. Output:
  ignored `artifacts/log-prep-after-binary-search.json`.
- Python wait-budget and log-suffix targeted tests: 7 passed. Default 2-second cold-source and
  slow-log deadline checks: 2 passed in 4.66 s. `npm --prefix dashboard run check`: zero errors,
  zero warnings. Reverse-order Node dashboard run: 47 passed, zero skipped, 46.49 s.
- T038 final complete-suite comparison: Python post-change elapsed values **56.544, 55.317,
  56.309, 54.087, 54.551 s**, median **55.317 s** versus baseline **81.372 s** (32.0%
  lower); each run 521 passed, 5 same hardware skips, 2 same deprecation warnings. Dashboard
  post-change elapsed values **41.782, 41.895, 41.634, 42.069, 41.851 s**, median
  **41.851 s** versus baseline **46.892 s** (10.8% lower); each 47 passed, zero skipped.
  Suite logs and run metadata are retained under ignored `.specify/perf-final-*`.
- Python benchmark target (30%) passes in the measured five-run median. Dashboard target (20%)
  misses by 9.2 percentage points (~4.34 s at baseline median); no cases/assertions were
  dropped to make up the difference. Other SSR-only setup/caching/parallelism options remain
  deferred unless an isolated benchmark justifies another implementation increment.
- No inference/runtime hardware, metadata cache, history durability/index, native planner or
  negotiated identity protocol changes were made. The replay protocol remains unimplemented
  pending authoritative durable slot/generation/frontier semantics; existing exact legacy
  behavior is unchanged. This is a deliberate open feature requirement, not a verified pass.
