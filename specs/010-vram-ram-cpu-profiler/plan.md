# Implementation Plan: VRAM / RAM / CPU Resource Profiler

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — Rust resource probes and existing node monitoring

**Branch**: `main` (actual checkout) | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: `specs/010-vram-ram-cpu-profiler/spec.md`

**Setup metadata**: `setup_plan.py` returned feature label `010-vram-ram-cpu-profiler` as
`BRANCH`; `git status --short --branch` confirms `main`. No branch was created or switched.
The repository plan index is [plans/vram-ram-cpu-profiler.md](../../plans/vram-ram-cpu-profiler.md).

## Summary

Implement host CPU/RAM, managed-process CPU/resident RAM, and device-scoped GPU memory
collection in Rust. Use checked Windows and macOS facilities with explicit counter scope,
timestamps, CPU intervals, and unavailable reasons. Preserve existing capacity exports and
Linux monitoring. Serve probes through the existing Python agent and node workspace.

Windows GPU readings use adapter-wide WDDM counters, not application budgets. Apple Silicon
identifies the shared RAM pool; GPU-attributed usage remains unavailable where public APIs
cannot establish device-wide measurements. Two bounded, agent-owned helper processes isolate
host/process collection from GPU/driver collection, so a blocked source cannot block the
agent or accumulate abandoned threads. They are private children, not installed services.

Phase 0 research and Phase 1 design are complete. Implementation and hardware validation
are subsequent work; no performance or platform acceptance passes are claimed here.

## Conventions captured at creation

- `crates/kllm-native/src/hw.rs:probe_capacity` creates a `sysinfo::System` and enumerates
  capacity. The macOS path assumes an 80% RAM budget. `planner.rs:effective_vram_bytes`
  depends on that policy. Neither becomes a live usage counter.
- `crates/kllm-native/src/lib.rs` owns PyO3 exports. Cargo uses edition 2021, PyO3 0.23,
  serde/serde_json, thiserror, and sysinfo 0.33 (lockfile: 0.33.1).
- `python/kllm/native.py:list_gpus` filters/reindexes installed llama-server devices.
  Its inference indices are not OS GPU identities. Do not use that path for sampling.
- `python/kllm/usage.py:Sampler.sample` currently uses psutil and subprocess GPU counters
  and returns nothing without a managed child. `usage.validate` rejects unknown fields.
- `python/kllm/agent/__init__.py:_usage_loop`, `usage_ingest`, and `usage_live` connect
  samples to master-local `UsageStore` history and existing live delivery. Preserve facade
  names/tests and pass dependencies to siblings rather than rebinding patched globals.
- `usage.lifetime_id`, `_tracked_process_start`, and membership match PID/start timestamps.
  New exact OS identity must have one canonical compatibility timestamp used by all three.
- Existing history is process-lifetime JSON in SQLite, 24 hours/256 MiB. New host-only live
  snapshots are transient and must not create fictional lifetimes or a second history store.
- `agent/live.py:LiveFeed` and the dashboard already age cached observations. Delivery time
  cannot renew a hardware sample. Existing platform-specific functions remain live code.

## Technical Context

**Language/Version**: Rust edition 2021; installed rustc 1.98.0, no new MSRV promise.
Python 3.12 workspace/package >=3.10; TypeScript 5/Svelte 5 for existing telemetry fields.

**Primary Dependencies**: Existing PyO3 0.23, serde/serde_json 1, thiserror 2, sysinfo 0.33,
psutil, FastAPI/Pydantic/httpx. Chrono 0.4 (clock/std only) provides native UTC formatting and
checked RFC 3339 parsing; std::time supplies neither calendar formatting nor timestamp parsing,
so this avoids a custom parser/conversion algorithm at the native boundary.
Add direct target-specific Windows bindings (`windows` 0.57,
already present transitively), and macOS `libc`, `objc2` 0.6/`objc2-metal` 0.3 with only needed
device/Foundation features. Resolve and commit compatible exact versions in Cargo.lock during
implementation. These bindings supply checked native calls and GPU identity absent from the
current public sysinfo contract. No new Python dependency or async Rust runtime.

**Storage**: Existing SQLite process history unchanged. One latest resource snapshot per
known node in memory, bounded by membership; prune on membership removal. No host-only history.

**Testing**: Cargo unit checks for arithmetic/lifetimes/pools; existing pytest for adapter,
native-wheel/helper boundaries, route compatibility and timeout cleanup; existing Node tests
and Svelte checks for truthful labels/freshness. Real Windows and macOS acceptance is required.

**Target Platform**: Windows 10 1709+/11, WDDM 2.x+ for GPU counters, initially x64; macOS
Apple Silicon and Intel, using installed SDK-supported public facilities. Validate macOS 13+
as the initial hardware baseline; do not gratuitously raise the wheel's existing deployment
target. Existing Linux collector and native capacity builds remain compatible.

**Project Type**: Embedded native library in an existing private-cluster agent/dashboard.

**Performance Goals**: SC-004: four callers use the same managed-process selection every two
seconds for ten minutes after baseline establishment. At least 95% of all timed requests
return the required supported measurements no older than two seconds at response time;
p95 latency <=1 second and every probe <=2 seconds. Busy/error/timeout responses count as
unsuccessful measurements and remain in both denominators. Declare unsupported counters before
the run; only initial warm-up is outside it. Failed baseline establishment fails the run.
Measure fault injection separately, retaining the two-second deadline and healthy-source
readings. After warm-up/stop, retained growth <=16 MiB and handles <=5 above baseline across
the full agent/helper process tree.

**Constraints**: Absolute deadline starts at public request entry, including lock wait and
helper startup. One host/process helper and one GPU helper maximum per agent; one in-flight
request per source and no unbounded queue. Timeout work budget is 1.8 seconds, reserving
0.2 seconds for validation/response. No guarantee of wall-clock progress while the OS is
suspended or unable to schedule the caller; such discontinuities invalidate CPU baselines.
Normal sampling is two seconds; minimum new CPU interval 200 ms. The <=64 KiB limit covers
each complete serialized message. Reserve snapshot/resource-only envelope overhead before
allocating source reply budgets; omit optional detail first and report a source-level payload
error if needed, preserving unaffected sources. Split otherwise fitting history and resources
into separately acknowledged requests when their combined ingest body exceeds the limit.

**Scale/Scope**: Existing 1–16-node monitoring envelope; local public probe accepts 0–8
registered process selections and up to 32 discovered GPUs (excess discovery is explicit).
The agent currently has at most one managed child. No new remote arbitrary-PID endpoint,
device plugin framework, dashboard redesign, sensor expansion, or placement-policy change.

## Constitution Check

| Principle | Before research | After Phase 1 design |
|-----------|-----------------|----------------------|
| I. Simple/consistent | PASS: existing native/monitoring ownership identified | PASS: platform siblings and existing routes; two helper children justified solely by bounded blocking-source isolation |
| II. Behavior checks | PASS: CPU/PID/pool failures identified | PASS: deterministic cases plus real binding/helper/ingest checks in quickstart |
| III. Portability/integration | PASS: Windows/macOS and preserved Linux boundary identified | PASS: platform matrix, rebuilt wheel checks, and hardware acceptance commands defined; execution pending |
| IV. Explicit contracts/failures | PASS: unavailable and existing capacity compatibility required | PASS: version negotiation, exact identity, per-metric status, bounded IPC and child cleanup specified |
| V. Reproducibility | PASS: installer/build flow and docs identified | PASS: target-only dependencies/lockfile, existing-doc updates and evidence requirements specified |
| Structural standard | PASS: facade/native exports and Pydantic ownership retained | PASS: no new generic framework, no cluster-state format replacement, imports at module top |

No unresolved governance conflicts or exceptions. PASS means the design meets the gate,
not that unimplemented code or unavailable hardware has passed testing.

## Project Structure

### Documentation (this feature)

```text
plans/vram-ram-cpu-profiler.md              # repository plan index/snapshot
specs/010-vram-ram-cpu-profiler/
├── spec.md
├── plan.md                               # detailed Spec Kit handoff
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── native-probes.md
│   └── usage-transport.md
├── memory.md
└── memory-synthesis.md
```

`tasks.md` is intentionally produced by the subsequent `/speckit.tasks` command.

### Source Code (repository root)

```text
crates/kllm-native/
├── Cargo.toml, Cargo.lock
└── src/
    ├── lib.rs                            # additive private collector export
    ├── process.rs                        # capture launched-child start identity
    ├── hw.rs, planner.rs                 # compatibility checks; capacity unchanged
    ├── usage.rs                          # NEW typed readings, baselines, validation
    └── usage/
        ├── windows.rs                    # NEW checked host/process + PDH/DXGI
        └── macos.rs                      # NEW checked Mach/libproc + Metal identity
python/kllm/
├── native.py                             # public probe_resources/close wrapper
├── usage_probe.py                        # NEW private helper/supervisor, stdlib IPC
├── usage.py                              # Sampler adapter/validators; legacy history
├── agent/__init__.py                      # existing loop, ingest/live and lifecycle
├── agent/run_route.py                     # capture identity on successful launch
├── agent/join_flow.py                     # consistent registration identity
├── membership.py                         # additive optional identity/capability fields
└── runtime.py                            # additive optional child identity, old records readable
dashboard/src/lib/
├── types.ts, live-updates.ts, store.svelte.ts
└── components/NodeMap.svelte, NodeUsageDetails.svelte
tests/test_usage.py, test_dashboard_api.py, test_dashboard_live.py, test_membership.py
tests/test_native_usage.py                 # NEW rebuilt extension/helper integration
dashboard/tests/usage-live.test.mjs, live-updates.test.mjs
docs/cli.md, docs/troubleshooting.md         # existing monitoring/support documentation
```

**Structure Decision**: Platform functions in concrete sibling modules; one reusable reading
representation because host/process/device metrics share validation. The public Python wrapper
owns deadline supervision; all resource measurement and CPU arithmetic remain Rust. Preserve
the current JSON state layout, adding only optional identity fields to existing child/member
records with backward-compatible readers. No dependency inversion or provider registration layer.

## Phase 0 — Research decisions

Resolved in [research.md](research.md): checked OS calls over lossy sysinfo refreshes for
new measurements; adapter-wide Windows GPU counters; honest macOS limitations; exact process
lifetime identity; killable helper isolation; backward-compatible live resource snapshots.
All technical choices are resolved. Hardware acceptance is a required future check, not an
unresolved design decision or a claim of current certification.

## Phase 1 — Design and handoff

1. Define the typed observations and normalization in [data-model.md](data-model.md).
   Complete field/schema validation in the foundation using controlled source responses;
   actual OS identity, managed-registry matching and discovered-pool verification belong to
   their story implementations. The foundation gate does not depend on those later tasks.
   Implement host/process collection independently of GPU availability. No shell commands
   in the new Rust sampler; no name/order-based GPU attribution.
2. Add the private Rust collector export and public supervised wrapper in
   [contracts/native-probes.md](contracts/native-probes.md). Persist baselines within each
   helper; restart resets them. Capture managed start identity at launch, not first telemetry.
3. Have `Sampler` collect a resource snapshot on target platforms even without a child,
   then project verified process readings into the existing history shape. Preserve Linux
   behavior and optional existing GPU sensors, but move optional slow sensor work outside
   the critical native-probe deadline and never relabel its timestamp.
4. Integrate negotiated resources into the existing ingest/live path per
   [contracts/usage-transport.md](contracts/usage-transport.md). Keep no-child resources in
   memory only. Old masters receive old payloads; missing native support is explicit.
5. Surface host/process/device scope, original timestamps, shared pools, partial totals and
   reasons in existing details. Retain GiB presentation and existing controls/history.
6. Validate using [quickstart.md](quickstart.md), update existing operational documentation,
   rebuild native packaging, and record the actual OS/driver/support results before delivery.

### Lifecycle and risk controls

- Two owned helper slots, not two new children per request. Bounded frames and bounded reader
  queues; no unbounded pipe writes or thread creation. Concurrent identical selections coalesce;
  other callers wait only within their own deadline and can receive busy/timeout status.
- Timeout signals termination without delaying the response. Reap asynchronously and restart
  only after confirmed exit; a stuck unreaped child quarantines its slot. Normal close kills/
  reaps helpers and closes pipes/readers. Windows uses kill-on-close Job Objects; macOS helpers
  watch a parent-owned control pipe and exit on EOF even if the sampling thread is blocked.
- Host CPU baseline, process CPU baseline and GPU source times are independent. Canceled/late
  results cannot overwrite a newer generation, revive an ended process, or renew sample age.
- Remote snapshots use explicit master-issued generation and integer sequence fields with
  `<generation>:<sequence>` identity. Authenticated registration performs idempotent,
  compare-and-swap session resets; uploads cannot reset sessions or their sequence watermark.
  Local supervisor and private helper generations are distinct from remote session identity.
- Exact start tokens remain strings, avoiding floating-point loss. One canonical UTC start
  string is shared by registration, heartbeat, history projection, and comparisons.
- Removing a device retains an explicit unavailable result for that observed identity during
  transition; fresh enumeration replaces it without reusing its baseline or summing it twice.
- Validate capacities before subtraction; ambiguous pool mapping makes free unavailable.
  SC-002 requires one real Windows discrete adapter with both used and free successfully checked.
- Empty GPU results distinguish successful empty discovery from failed/unsupported discovery;
  unknown device identity never prevents a valid host/process response.
- Pool references may repeat across device views if physical identity/backing metadata agrees.
  Reject conflicting definitions, count physical capacity once and do not add device-scoped
  shared usage to host usage.

## Complexity Tracking

No constitutional deviations. The two helper processes are the minimal isolation needed to
honor FR-013/014 when either synchronous source blocks: a single helper loses healthy-source
readings, while timed-out threads cannot be safely killed or reclaimed. No new service,
packaging target, or generalized task system is introduced.
