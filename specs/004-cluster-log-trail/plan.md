# Implementation Plan: Master & Worker Log Trail

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — dashboard Logs page and cluster-log read contract

**Branch**: `main` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-cluster-log-trail/spec.md`

## Summary

Add `/logs` with All nodes/master/worker selection, Clear, and an accessible four-choice
Live / 5s / 30s / 1m refresh control. Reuse the current log collector and snapshot endpoint.
Live uses one-second incremental reads; all modes share the same page-owned lifecycle.
Add stable event IDs and receiver-local resume cursors so replay, mode changes, and Clear
are correct without timestamp/content deduplication. Existing CLI streaming remains compatible.
Generate and review the requested feature-specific OpenDesign prototype before visual work.

## Conventions captured at creation

- `python/kllm/cluster_log.py:LogHub.relay_tick`, `recent`, `ingest`, and
  `ensure_journal_loaded` own collection, bounded reads, and the master journal.
- `python/kllm/agent/__init__.py:_logs_snapshot`, `_try_proxy_master_logs`, `get_logs`, and
  `ingest_logs` expose local/cluster logs and worker proxy/fallback. GET uses the existing
  private control surface; POST requires the cluster key. Same-origin is not authentication.
- `python/kllm/cli.py:logs_cmd` reads snapshots or `data:` SSE frames; do not insert
  unrequested metadata records into its stream.
- `dashboard/src/routes/+layout.svelte:nav` owns the shell; `AppStore` provides membership
  metadata. Log selection/lifecycle must remain independent of telemetry and global polling.
- `dashboard/src/lib/poll-loop.ts:PollLoop` includes jitter; retain existing callers' behavior.
- `dashboard/DESIGN.md` and `dashboard/src/app.css` supply the shared design tokens.
- Feature 003 is concurrent workspace work. Apply a small navigation insertion and scoped
  logs styles without replacing its layout, node components, or theme changes.

## Technical Context

**Language/Version**: Python 3.12 workspace (package supports >=3.10); TypeScript 5/Svelte 5.

**Primary Dependencies**: Existing FastAPI, Pydantic, httpx, SvelteKit 2, Vite 6, Tailwind 4;
browser fetch/AbortController and native form semantics. No new dependencies.

**Storage**: Existing 8,000-event memory ring and rotating master JSON-lines journal.
Browser state is ephemeral for one mounted page visit; no localStorage log archive.

**Testing**: Existing pytest and Node `node:test`/Vite tests; existing Chrome/Edge browser
harness. Documentation checks during planning, runtime checks during implementation.

**Target Platform**: Current desktop/mobile browsers; existing Windows/Linux/macOS agents.

**Project Type**: Static dashboard served by the existing Python agent.

**Performance Goals**: Live visibility p95 <=2 seconds from first readability in the serving
feed to browser-rendered row, for new matching entries after initial load; timed foreground
refresh within one second of 5/30/60-second cadence; Clear <=1 second; responsive at 100
total lines/second from one master plus three workers, with 128–1,024-byte synthetic messages
for ten minutes. Correlate event IDs on one measurement clock as defined in the quickstart;
do not infer latency from worker/source timestamps or omit eligible non-rendered entries.

**Constraints**: Initially load 200 recent records; incremental requests cap at 5,000;
render at most 2,000 recent rows. Explicit omission/gap notices. Preserve complete message
text and escape it normally. A page-session ID-only deduplication set is released on unmount.
Do not use timestamps as cursors or introduce a destructive Clear endpoint.
Every browser read has a fixed 5,000 ms total dispatch-to-commit deadline, including body
consumption/decoding; expiry invalidates/aborts the request and retries at the first future
selected cadence deadline. Clear before any successful read is a no-op; the first result
is consumed normally. Discarding recovery backlog is restricted to a feed reset after
Clear with a committed cursor and is always labeled as a gap.

**Scale/Scope**: One new route, bounded recent inspection, existing cluster identities and
sources. One-second/5/30/60-second polling only; no new streaming infrastructure or archive.

## Constitution Check

| Principle | Pre-research gate | Post-design gate |
|-----------|-------------------|------------------|
| I. Simple, consistent code | PASS: existing hub/endpoint/UI primitives identified | PASS: one snapshot transport and page controller; no new dependency |
| II. Behavior-focused tests | PASS: Clear/refresh/identity races require checks | PASS: hub/API/proxy/controller/browser boundaries in quickstart |
| III. Integration and portability | PASS: shared CLI and worker contract acknowledged | PASS: legacy calls and relay/proxy integration specified; real multi-node timing remains to verify |
| IV. Explicit contracts and failures | PASS: private access and nondestructive Clear retained | PASS: cursor validation, reset/gap/local-only states, abort and cleanup defined |
| V. Maintainable changes | PASS: existing docs and tooling identified | PASS: update CLI/troubleshooting guidance; preserve package/lockfile configuration |

No exceptions requested. Gates assess this design, not implementation test results.
The OpenDesign artifact and review evidence are tracked in [design.md](design.md).

## Project Structure

### Documentation (this feature)

```text
plans/cluster-log-trail.md              # Point-in-time saved plan
specs/004-cluster-log-trail/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── design.md                          # OpenDesign brief/artifact/review evidence
├── quickstart.md
├── checklists/requirements.md
└── contracts/logs.md
```

`tasks.md` is the next `/speckit.tasks` output, not created by this command.

### Source Code (repository root)

```text
python/kllm/cluster_log.py              # Add identity/cursor snapshot semantics
python/kllm/agent/__init__.py           # Additive reads, validation, ingest/proxy forwarding
dashboard/src/lib/types.ts             # Wire types
dashboard/src/lib/log-trail.svelte.ts   # New page-owned state/lifecycle controller
dashboard/src/routes/logs/+page.svelte  # New route; scoped styling
dashboard/src/routes/+layout.svelte    # Logs navigation insertion
tests/test_cluster_log.py              # Hub, journal, relay, API and compatibility checks
dashboard/tests/logs.test.mjs          # New deterministic state/timing checks
dashboard/tests/usage-workspace.test.mjs # Reuse existing browser harness for /logs
docs/cli.md
docs/troubleshooting.md
```

**Structure Decision**: Keep shared collection correctness in existing Python modules;
one small testable logs controller owns route-scoped state rather than growing `AppStore`
or creating one-use component layers. Membership labels read existing store state.

## Implementation sequence

1. **Read contract:** Add event IDs at initial collection, preserve them through relay and
   journal loading, and add atomic incremental snapshots with epoch/sequence cursors.
   Initialize journal history before new master events; preserve legacy records and reads.
2. **Proxy boundary:** Forward cursor inputs and envelope metadata unchanged. Validate new
   inputs. Local fallback has its own feed identity and explicit warning; each later poll
   retries the master. Bound network reads and report errors rather than clearing rows.
3. **Page lifecycle:** Implement one serial, abortable polling controller with generation
   guards after decoding, a 5,000 ms total request deadline, precise foreground cadence,
   cursor/ID deduplication, display-only Clear, buffer cap, and disconnected/gap states.
   Keep Clear before the first successful load a no-op. Use local node filtering.
4. **Visual integration:** Apply the reviewed OpenDesign direction to `/logs`, integrate the
   navigation item, preserve reading position, and support keyboard/mobile/reduced-motion.
5. **Verification/documentation:** Follow [quickstart.md](quickstart.md); update existing
   CLI/troubleshooting log guidance. Measure serving-feed-to-render latency on a shared clock,
   and record measured results and unavailable-platform checks.

## Complexity Tracking

No constitution violations. Stable IDs and cursors address actual replay/Clear ambiguity;
timestamps and content hashes fail for identical messages and clock skew. SSE resume,
lossless delivery, journal migration, and a new logging service are unnecessary for this UI.
