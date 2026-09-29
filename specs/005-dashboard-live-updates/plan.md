# Implementation Plan: Dashboard Live Updates

**Branch**: `005-dashboard-live-updates` (Spec Kit feature identifier; Git checkout is `main`)
**Created / Date**: 2026-09-29
**Status**: Planned
**Repo / scope**: kluster-llm — agent-to-dashboard live delivery
**Spec**: [spec.md](spec.md)
**Input**: `specs/005-dashboard-live-updates/spec.md`

## Summary

Replace the dashboard's five recurring-update surfaces with one native WebSocket per
visible tab. Use the same new `/v1/dashboard/live` path for a versioned HTTP snapshot:
initial admission/state, explicit action refreshes, recovery, timed Logs, and fallback.
Share source acquisition on the agent, preserve existing HTTP/CLI contracts, and use the
existing test harnesses and installed dependencies.

WebSocket avoids competition between five dashboard SSE streams and existing chat for
HTTP/1.1 connection slots. Full topic replacements, source revisions, one acknowledged
data batch per viewer, and existing log checkpoints provide bounded delivery without a
broker, patch protocol, durable event store, or cross-tab leader election.

## Conventions captured at creation

Baseline commit: `a0a4f5259c7dfd5c204deb5ff0972f27b8e87c69`.

- `dashboard/src/routes/+layout.svelte:onMount` owns `AppStore.start/stop`. Status and usage
  use fixed timers; load/download use `PollLoop`; Logs has its own `LogTrail` lifecycle.
- `dashboard/src/lib/store.svelte.ts:refresh` also serves explicit action callers.
  `memberFingerprint`/`fp` deliberately exclude telemetry changes to preserve valid plans.
- `dashboard/src/lib/log-trail.svelte.ts:commit` owns cursor, deduplication, reset-after-Clear,
  and a 2,000-row bound. Logs renders newest-first and follows at the top.
- `python/kllm/agent/__init__.py:lifespan` owns shared agent resources. `status` has expensive
  side effects; `usage_live` reads existing recorded data; workers proxy selected reads.
- `python/kllm/cluster_log.py:LogHub.snapshot` supplies checkpointed bounded history.
  `iter_sse` lacks the dashboard's recovery guarantees and remains the existing CLI path.
- `python/kllm/models.py:download_job_snapshot` and `load_progress.py:sample` lack stable
  operation IDs. Their lifecycle paths require small shared fixes, not UI-only guards.
- `python/kllm/http_clients.py:control_client` is the thread-safe shared upstream pool.
- Python uses uv/maturin; the dashboard is Svelte 5/TypeScript with a static build.
  Node built-in tests and an existing CDP browser harness are already present.
- This Spec Kit workflow's canonical plan is this feature-local `plan.md`; related design
  artifacts are beside it. The plan records intent at this baseline, not a living runtime guide.

## Technical Context

**Language/Version**: Python 3.12 selected by `.python-version` (package floor 3.10);
TypeScript 5, Svelte 5, SvelteKit 2, Vite 6; Node 22 available for built-in test/type stripping.

**Primary Dependencies**: Existing FastAPI/Starlette, Uvicorn standard/WebSocket support,
httpx, psutil, asyncio/threading, native browser WebSocket and fetch. No new dependency.

**Storage**: Existing SQLite usage history and log journal unchanged. New feed caches,
worker log mirror, operation identities/active/latest-terminal records, and viewer state
are bounded or session-scoped memory; no migration or durable replay store.

**Testing**: Existing pytest and pytest-asyncio; Node `node:test`; existing Chrome/Edge CDP
fixture. Add a real-Uvicorn GET/WS boundary check, not solely mocked transport checks.

**Target Platform**: Windows, Linux, macOS agents; supported desktop browsers and existing
narrow-screen layout. Production same-origin control port; Vite upgrade proxy in development.

**Project Type**: Local/private-cluster web dashboard backed by a Python agent and existing
native inference runtime. No Rust/native interface change is planned.

**Performance Goals**: Spec SC-001–SC-007: 95% of available live changes rendered within
two seconds; initial state within three seconds; at least 90% fewer browser state-check
requests; delivery failure/fallback/recovery within ten seconds; restore live within 60
seconds; responsive controls at 100 aggregate log entries/second.

**Constraints**: One socket per visible tab; source work shared and single-in-flight;
one unacknowledged data batch per viewer; explicit gaps, errors, access and coverage;
no command replay. Preserve ten-second usage freshness, 25-second membership loss bound,
five-second discrete log-read deadline, all four log modes, and display-only Clear.

**Scale/Scope**: 1–16 nodes, up to five simultaneous visible dashboards, all five update
areas. Current chat, history, node-to-node collection, retention, and private RPC rules
remain their existing interfaces. Socket health and actual sensor/source freshness differ.

## Constitution Check

Pre-research and post-design gates are evaluated against constitution 1.0.0. These are
design gates; they do not certify unimplemented behavior or measured performance.

| Gate | Pre-research | Post-design evidence |
|---|---|---|
| I. Simple, consistent code | Pass: existing source/consumer flow identified. | Pass: two small production modules, existing snapshot/collector helpers, no new dependency or event framework. |
| II. Behavior-focused regression tests | Pass: existing deterministic runners available. | Pass: ordering, Clear, operation lifecycle, denial, cleanup and slow-viewer outcomes have concrete checks in quickstart. |
| III. Verified integration and portability | Pass: affected HTTP/browser/worker boundaries identified. | Pass: real-Uvicorn, Vite/browser, private worker/proxy and OS/hardware checks specified; performance is explicitly unmeasured. |
| IV. Explicit contracts and failures | Pass: preserve existing contracts and private access. | Pass: contract specifies validation, origin/admission, scope, bounded work, timeout, cancellation, fallback, restart and gap semantics. |
| V. Reproducible changes | Pass: supported install/test commands identified. | Pass: implementation must update existing operational docs; baseline and reproducible workloads recorded. |
| Repository constraints | Pass: Python/dashboard scope, uv tooling and private RPC respected. | Pass: no native ABI, llama pin, public port, packaging, or retention change planned. |

No unjustified gate failure, unresolved planning clarification, or constitutional exception.
If implementation changes dependencies/packaging, update manifests/locks and use
`uv run python scripts/build.py` or the documented installer, including native import checks.

## Project Structure

### Documentation (this feature)

```text
specs/005-dashboard-live-updates/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
└── contracts/dashboard-live.md
```

`tasks.md` belongs to the subsequent `/speckit.tasks` phase and is not generated here.

### Source Code (repository root)

```text
python/kllm/
├── agent/__init__.py             # lifespan, snapshot helpers, routes, source integration
├── agent/live.py                 # NEW: shared caches, demand, mirror and bounded WS sender
├── cluster_log.py                # reuse LogHub; bounded snapshot helper if needed
├── models.py                     # download identity/admission/terminal lifecycle
├── load_progress.py              # load identity and latched terminal state
├── membership.py                 # shared load start/stop and fast expiry integration
└── http_clients.py               # reuse private upstream pool
dashboard/
├── vite.config.ts               # enable /v1 WebSocket upgrade proxy
├── src/lib/live-updates.ts       # NEW: one browser coordinator, health, ordering, fallback
├── src/lib/store.svelte.ts       # validated apply methods and guarded action refreshes
├── src/lib/log-trail.svelte.ts   # shared live delivery; existing timed/Clear semantics
├── src/lib/poll-loop.ts          # remove after its two consumers move to coordinator
├── src/lib/types.ts              # typed protocol, source health and operation metadata
├── src/lib/components/           # existing progress/usage presentation adjustments
├── src/routes/+layout.svelte     # idempotent root ownership, textual delivery health
├── src/routes/+page.svelte       # Home usage interest; preserve map/history state
├── src/routes/logs/+page.svelte  # log interest, independent coverage/status labels
└── tests/
    ├── live-updates.test.mjs     # NEW: coordinator races, fallback, freshness, effects
    ├── logs.test.mjs            # extend transport equivalence/Clear/deadline checks
    ├── store.test.mjs           # explicit-action and plan-preservation regression
    └── usage-workspace.test.mjs # extend real-browser lifecycle and five-window checks
tests/
├── test_dashboard_live.py        # NEW: shared-source and real GET/WS boundary checks
├── test_cluster_log.py
├── test_dashboard_api.py
├── test_model_management_api.py
├── test_membership.py
└── test_cp_proxy.py
docs/
├── cli.md                       # live modes, compatibility and command behavior
└── troubleshooting.md           # interrupted/denied/periodic updates and proxy checks
```

**Structure Decision**: Keep existing domain owners and consumers. The new Python module
coordinates multiple real sources; the new plain-TypeScript module owns transport/lifecycle.
Neither module becomes a general-purpose plugin, repository, or messaging framework.

## Phase 0 — Research outcome

Completed in [research.md](research.md), using parallel read-only backend, frontend, and
browser-transport investigations. Decisions cover protocol selection, source cost/lifecycle,
versioned reads, log mirror/checkpoints, operation identity, liveness isolation, access,
timing, and test reuse. No unresolved technical-context markers remain.

## Phase 1 — Design

### Shared agent runtime

Own `LiveFeed` in the existing lifespan with explicit start/dispose. Supply narrow source
callbacks from existing modules to avoid circular imports. Keep one acquisition in flight
per source, cache results, and validate role/master/process generation before publication.
GET and WS share the same per-topic revisions. No viewer causes another hardware sampler.

Maintain cheap fast status separately from capacity/software/native work. Reuse activity
results in status and worker heartbeat callers. Split membership expiry publication from
blocking maintenance so losing periodic status calls cannot delay disconnected topology.
Publish disconnected status and cached usage connectivity before persistence/replanning.

Worker usage/log acquisition uses the existing private route and pooled HTTP client.
New GET/WS Logs share a nonpersistent worker mirror and explicit continuity identities;
local fallback remains distinct. Unexpected upstream data and denial are errors, not
successful coverage changes. Existing CLI follow behavior is untouched.

### Truthful operation source state

Reserve load/download IDs before accepting work, atomically transition current to latest
terminal state, and accept updates only for that identity. Cover all existing explicit and
membership-induced start/stop callers. Fix the shared download admission/cancel paths that
would otherwise prevent a second operation or fabricate cancellation. Use finite source
I/O waits so cancellation can be observed; do not treat a cancel request as terminal proof.
In-memory retention is intentionally limited; unknown recovery outcomes are visible.

### One browser owner and one acceptance path

Root layout owns an idempotent coordinator; Home requests usage; a mounted Live `LogTrail`
requests Logs. Header status/load/download stay subscribed while visible. Hide closes the
socket, aborts/invalidate reads and timers, and retains user state; show recovers once.

Extract existing store apply logic and retain plan fingerprints/config hydration. Apply
per-topic epoch/revision validation to pushes and versioned action reads. Deduplicate
inventory reconciliation by terminal operation ID even if a running observation was missed;
gate auto-selection/notification on this visit's tracked/accepted operation before async
work, and do not overwrite later user selection.
Only legacy versionless fallback needs serialized old reads. Commands remain explicit.

Keep LogTrail's discrete deadline scheduler and cursor/Clear rules. No live log data enters
timed views between deadlines. Log mode/Clear generation changes replace, never add, a
subscription. Preserve newest-first anchors, all-node buffer Clear, and sticky gap notices.
Advance stale usage locally from observation age; heartbeats never refresh old readings.

### Delivery, recovery, and presentation

Use the exact limits and state transitions in
[contracts/dashboard-live.md](contracts/dashboard-live.md) and [data-model.md](data-model.md).
One bounded, acknowledged data frame limits each viewer's backlog. Origin validation,
HTTP-readable errors, per-topic coverage, four-second health detection, and five-second
read/send deadlines make failures explicit. Fallback has one owner and atomic live cutover.
Initialization is one complete bounded frame so acknowledgment cannot deadlock staged
recovery. Timed-log HTTP work has its own lane, independent of non-log state/action reads.
HTTP-only epoch changes recover on ordinary retry deadlines. Serialized usage age includes
cache residence and a bounded transit estimate, avoiding synchronized-clock assumptions.

Enable `ws: true` on Vite's existing `/v1` proxy, preserving Origin. Update existing header,
progress and log status presentations with accessible delivery/coverage/unknown-outcome text;
do not remount the workspace or announce every measurement/log row to assistive technology.

### Validation and implementation handoff

[quickstart.md](quickstart.md) defines deterministic regression commands, real-transport and
browser scenarios, manual private-cluster checks, and measured before/after workloads.
Capture the current request baseline before replacing timers. Future tasks should follow
the source contracts first, then shared backend delivery, browser ownership, and integration
checks/docs. This phase produces design artifacts only; implementation and measured acceptance
remain subsequent work.

## Post-design gate result

**Pass for planning.** All 16 functional requirements map to design responsibilities and
validation scenarios. No native/runtime hardware claim is made. Real 1/4/16-node runs,
Windows/Linux/macOS operation, five visible viewers alongside chat, and usability results
must be recorded during implementation validation rather than inferred from mocks.
