# Implementation Plan: Interactive Node and Process Usage Monitor

> **Created:** 2026-09-28
> **Status:** Planned
> **Repo / scope:** kluster-llm — agent telemetry/history and dashboard workspace

**Branch**: `002-node-usage-monitor` (setup target; current checkout is `001-fix-vram-layer-default`, so no checkout was changed) | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-node-usage-monitor/spec.md`

## Conventions captured at creation

- `python/kllm/agent/__init__.py:lifespan` starts background tasks; `_membership_loop` / `_tick_membership` run outside the browser but can spend time on capacity probes and sequential worker latency checks. `status` serves `/v1/status`; `heartbeat` accepts authenticated worker snapshots. `_require_key` checks master join keys. Add API routes before `_mount_dashboard` mounts the SPA.
- `python/kllm/state.py:ClusterState` stores the stable `node_id`, tracked `process_pid` / `process_role`, and JSON `cluster.json` under `home_dir()` (`KLLM_HOME` override). `python/kllm/membership.py:sweep_liveness` removes workers after 20 seconds; `snapshot_payload` mirrors current membership and activity to workers. `python/kllm/agent/__init__.py:_worker_post_heartbeat` demonstrates the private worker-to-master HTTP path.
- `python/kllm/agent/__init__.py:_refresh_local_capacity` already reads the tracked child's RSS for *planner reclaimable RAM*, not monitoring history. `pyproject.toml` already depends on `psutil`; `python/kllm/inference_activity.py:sample` reports master-side cluster activity, not per-worker computation.
- `dashboard/src/lib/store.svelte.ts:AppStore.refresh` polls status only while the UI is open; `viewState` distinguishes loading, offline and idle. `dashboard/src/routes/+page.svelte` owns cluster controls, topology and chat; `dashboard/src/lib/types.ts` defines wire types. `dashboard/src/lib/format-memory.ts:formatGib` displays binary GiB; `specs/001-fix-vram-layer-default/data-model.md` distinguishes reported capacity, planning budgets and estimates.
- Python uses uv/pytest; dashboard uses Svelte 5, SvelteKit 2, TypeScript, npm `check`/`build`. `dashboard/DESIGN.md` defines existing dark-panel styling. Port 50052 is unauthenticated RPC and stays private.

## Summary

Record only Kluster-managed inference-server/worker process and host/device usage every two seconds in agent-owned collection, including while no dashboard is open. Aggregate on the master in a bounded, restart-safe SQLite history (24 hours / 256 MiB cluster-wide), expose scoped live and filtered historical readings over the existing private control API, and replace the home topology table with an accessible exo-inspired node map, details and history while retaining existing controls/chat. Missing measurements and discontinuities remain explicit; placement estimates never masquerade as usage.

## Technical Context

**Language/Version**: Python >=3.10; TypeScript with Svelte 5/SvelteKit 2

**Primary Dependencies**: Existing FastAPI/httpx/psutil, Svelte/Tailwind; Python stdlib `sqlite3`; no required new dependency

**Storage**: Master-local `home_dir()/usage.sqlite3`; bounded cluster history and lifetime/node metadata; no telemetry in `cluster.json` or browser storage

**Testing**: Existing pytest with isolated `KLLM_HOME` and mocked subprocess/network; HTTP boundary checks; `npm --prefix dashboard run check`, `npm --prefix dashboard run build`, existing dashboard tests and manual keyboard/responsive/hardware scenarios

**Target Platform**: Browser connecting to a private master or worker agent on Windows, Linux or macOS; CPU/RSS on all three; optional sensors where available

**Project Type**: Existing distributed Python web-service + Svelte dashboard

**Performance Goals**: 1–16 nodes; target one sample / 2 seconds per running process; 95% live updates within 5 seconds; 95% interactions within 2 seconds; at most 5% median generated-token/s regression under matched measured runs

**Constraints**: Live stale at 10 seconds; removal from connected membership within 25 seconds of last contact; persist healthy samples within 10 seconds; rolling 24 hours / 256 MiB including SQLite sidecars; never expose RPC 50052; no prompts, secrets or unrelated process inventory

**Scale/Scope**: One tracked managed child per node, up to 16 nodes; 15-minute/1-hour/24-hour filters; ~691,200 possible cluster samples/day at 16 nodes, subject to physical-size cap

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Pre-research gate — PASS (2026-09-28)**

| Principle | Gate and required evidence in implementation |
| --- | --- |
| I. Simple, consistent code | Inspect all process mutations (`run`, `stop`, worker join/leave, membership replan); reuse existing tracked PID, psutil, member IDs and UI controls. Isolate sampler and history store instead of coupling them to browser polling. |
| II. Behavior-focused tests | Assert process CPU normalization, RSS, lifetime reuse/restart, invalid/late samples, gaps, retention and failure preservation with isolated state. Add observable dashboard/HTTP checks, not implementation-mirroring fixtures. |
| III. Integration and portability | Check private worker→master ingest and worker→master history read boundary, real agent restart and master liveness; run platform-specific psutil checks on Windows/Linux/macOS and label missing hardware checks unverified until run. Document matched inference performance workload/results. |
| IV. Contracts and failures | Validate ingest source/identity, timestamps, numeric bounds and sizes; preserve current status fields and control availability. Report failed saves and missing sensors without stopping inference; close DB/thread resources; no prompts, credentials or public worker/RPC exposure. |
| V. Reproducible changes | Update existing dashboard/CLI operational docs when implemented, keep uv/npm checks and test results in implementation change record, and use existing dependency/packaging flows if changed. |

No proposed constitution exception or unresolved technical clarification. The requested OpenDesign MCP artifact is a **pre-implementation design dependency**, not a telemetry assumption: create/review it and record its reference during implementation before visual work; existing `dashboard/DESIGN.md` is general direction, not that feature-specific artifact. This session has no OpenDesign MCP tool available, so no remote artifact is claimed here.

## Project Structure

### Documentation (this feature)

```text
specs/002-node-usage-monitor/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── usage-monitor.md
```

### Source Code (repository root)

```text
python/kllm/agent/__init__.py       # process lifecycle, private HTTP endpoints, worker/master forwarding
python/kllm/usage.py                 # scoped psutil collection and bounded SQLite history
python/kllm/membership.py            # membership timeout and known relationship context
python/kllm/state.py                 # stable node ID, tracked PID, home directory
dashboard/src/routes/+page.svelte   # existing cluster, topology and chat workspace
dashboard/src/lib/store.svelte.ts   # status/usage fetch and connection state
dashboard/src/lib/types.ts          # browser wire contracts
dashboard/src/lib/format-memory.ts  # measured/physical/estimated binary GiB display
dashboard/src/app.css               # scoped workspace accents and keyboard focus
tests/test_dashboard_api.py         # existing HTTP boundary checks
tests/test_membership.py            # existing contact/liveness checks
tests/test_usage.py                 # proposed focused sampler/history checks
dashboard/tests/                   # existing dashboard behavioral checks
docs/                              # existing operator documentation updated at implementation
```

**Structure Decision**: Keep collection/storage behind the existing Python agent, while the home page consumes a private same-origin API. The new `usage.py` is justified by the long-lived sampler and transactional history responsibility; no extra service, chart/graph dependency, or native extension is required.

## Phase 0 — Research

Resolve measurement identity, per-platform sensor support, storage/retention, transport, placement semantics, UI patterns and operational failure behavior in [research.md](research.md). All Technical Context fields have concrete choices; no `NEEDS CLARIFICATION` remains.

## Phase 1 — Design and contracts

1. Define node, known relationship, managed process lifetime, reading and recording coverage (including gaps, truncation, state transitions) in [data-model.md](data-model.md).
2. Define bounded private ingestion, live status and history HTTP schemas plus accessible UI interactions in [contracts/usage-monitor.md](contracts/usage-monitor.md). Worker history requests must forward server-side through the existing private master route, never pass join keys into new browser requests. Keep current `/v1/status` and cluster actions compatible.
3. Use a dedicated sampler independent of `_tick_membership`; capture one trusted PID only, prime process CPU, verify creation time across restarts, and write master-local samples plus validated worker samples in SQLite within ten seconds. A failed write marks recording failed, retains prior history, and never blocks inference. Keep SQLite physical DB and sidecars under the single allowance; retire oldest samples first and report effective coverage.
4. Derive cards/edges from current membership and master-worker relationships, retaining departed nodes from recorded metadata for selection. Present separate process, host and per-device readings; show assignment/activity as context rather than measured per-node work. Keep node selection/map transforms stable across refreshes. Reuse the home controls, `/run` and chat; adapt keyboard/history inspection and stacked 390px layout. Create/review the requested feature-specific OpenDesign artifact before implementing the visual design, and record its reference in the implementation change.
5. Validate end-to-end scenarios via [quickstart.md](quickstart.md), including dashboard-closed capture, restarts, synthetic time gaps, cap enforcement, unsupported sensors, portable process identity, browser keyboard/contrast/motion, hardware prerequisites and matched performance runs. Record real checks and any unavailable hardware/platform validation rather than claiming passes.

**Post-design constitution gate — PASS (2026-09-28):** The chosen contracts remain additive, bound all network/history inputs, and isolate collection from UI and inference. Data identity/availability and old-history preservation are explicit. Tests and docs are specified at affected boundaries; no exception or technical clarification remains. The OpenDesign artifact is tracked as a required implementation prerequisite, with no assertion that it already exists.
