# Research: Master & Worker Log Trail

**Date**: 2026-09-29
**Scope**: [spec.md](spec.md); existing working-tree code, including concurrent dashboard work.

## 1. One snapshot transport for all four refresh modes

**Decision:** Use `GET /v1/logs` snapshots, incremental after the first read. Live polls
nominally every 1,000 ms; timed modes use 5,000/30,000/60,000 ms. No new SSE consumer.
Each read has a fixed 5,000 ms total deadline covering fetch, body consumption/decoding,
and validation before commit; it is not reset by partial progress. Abort/invalidate expired
work, retain the last committed view, and retry at the first future cadence deadline.
Check elapsed time before commit and clear per-request timers on every terminating path.

**Rationale:** `agent.get_logs` already supplies bounded snapshots and worker fallback.
One transport gives the same identity, coverage, cancellation, and Clear behavior in every
mode. Existing CLI SSE has a snapshot/subscription race and long-lived proxy lifecycle
concerns; adopting it would require unrelated streaming repairs to meet this feature.
The two-second Live target does not require server push.
An explicit browser deadline is still necessary: a bounded worker-proxy timeout cannot
release a browser read stalled elsewhere, and one stuck read would occupy the serial slot.

**Alternatives:** SSE plus polling requires two transport lifecycles; WebSocket adds an
unneeded service. Polling overlapping tails without IDs cannot distinguish replay from
legitimate identical lines. Existing `PollLoop` has +/-20% jitter and completion-relative
delays, so use a small page-owned scheduler without changing its existing consumers.

## 2. Stable events and receiver-local cursors

**Decision:** Assign a UUID `event_id` once to each collected event and preserve it through
`make_event`, `LogHub.ingest`, worker batching, `LogLineBody`, `ingest_logs`, and the journal.
Add an opaque `<feed-uuid>:<sequence>` cursor for snapshots, assigned by the serving hub.
An atomic read returns the newest matching retained events after a cursor and a high-water
cursor; indicate explicitly if ring retention or the requested batch size omitted records.

**Rationale:** Event identity survives worker-local/master delivery and persisted replay;
receiver position supplies ordering and cheap incremental reads without trusting node clocks.
`ts` is usually file-tail observation time and can be identical for many distinct messages.
Read snapshot data and its checkpoint under the hub lock, then encode outside it.

**Alternatives:** Content hashes collapse distinct messages. Timestamp watermarks fail with
clock skew and timestamp ties. A database, lossless paging, and distributed Clear barrier
are unnecessary for the bounded recent-trail requirement. Return a labeled bounded gap
instead of silently pretending to provide archival history.

## 3. Clear, restart, and legacy compatibility

**Decision:** Before the first successful snapshot (`cursor === null`), Clear is a no-op:
keep the pending read/retry state, generation, timers, and `hasCleared=false`, then consume
the first success normally. Once a cursor exists, Clear empties buffered rows, retains the
cursor and seen IDs, increments generation, aborts old work, and sets `hasCleared=true`.
It performs no server write. A feed reset with `hasCleared=true` discards the reset snapshot,
adopts its checkpoint, and reports a recovery gap before fetching newer events.

**Rationale:** Retained IDs suppress cleared worker-local messages later relayed through the
master. Ignoring reset backlog protects Clear when legacy journal records have newly assigned
IDs after restart. It intentionally may omit unseen reset history, which the bounded-trail
contract permits only with an explicit notice, including unseen post-Clear entries in that
recovery window. Discarding an ordinary first snapshot would instead move an empty Clear's
cut-off into the future and lose new messages without a continuity failure, so it is prohibited.
Normal reconnect within the same feed resumes after the old cursor. A new route visit resets
all page-session state.

**Compatibility:** Legacy four-field events and POST bodies remain accepted. Legacy journal
rows get runtime IDs on load; newly persisted rows retain their IDs. No destructive migration.
An old sender retry without identity remains a distinct ingestion; do not guess it is a
duplicate by comparing content. Existing CLI reads/SSE continue to work with additive fields.

**Known ceiling:** `ponytail: keep an ID-only seen set for one mounted visit; it grows with
observed events. Replace it with producer sequence watermarks if day-long/high-volume sessions
require bounded identity memory.` Rendered log content is separately capped at 2,000 rows;
unmount releases both content and identities. Test the specified ten-minute workload.

## 4. Existing journal and proxy boundaries

**Decision:** Initialize retained journal history before first master collection/ingestion;
do not let a nonempty startup ring silently bypass it. Preserve chronological receipt order
when merging startup history and newly collected events, deduplicating existing IDs. Changing
an already published feed's history/ownership issues a new epoch and explicit reset.

**Rationale:** `LogHub.ensure_journal_loaded` currently returns early for a nonempty ring;
`ingest_logs` can accept worker events before journal loading. Cursor assignment cannot safely
be retrofitted without accounting for that startup boundary. Keep the active-journal retention
policy; do not turn rotated `.1` into a new historical-search feature.

**Proxy:** `_try_proxy_master_logs` forwards new snapshot parameters and retains upstream
cursor/identity/scope. On failure, local fallback uses its own feed and reports local-only.
Every poll retries master access; no permanently stranded local stream. Preserve bounded
timeouts. Identify local-only scope from the actual retained local-node records, not a role
label on a ring containing former remote records.

**Scope boundary:** Collection/relay remains best effort. Declare known read-window gaps and
coverage changes; do not claim to detect every upstream loss or deliver exactly once to disk.
No changes to private RPC networking, collector rotation policy, or cluster authentication.

## 5. Page state and layout

**Decision:** One `log-trail.svelte.ts` controller, one `/logs` page, one nav insertion.
Fetch All nodes and filter buffered rows locally. Node options combine current membership
with identities present in buffered rows; preserve the selected departed identity explicitly.

**Rationale:** Local filtering avoids one cursor per node and prevents node switching from
reloading cleared history. Unknown names/roles use stable IDs and an unknown label. It is a
recent combined buffer, not a promise of 2,000 retained entries for every individual node.
The page owns start/stop; logs do not poll while visiting other routes.

**Alternatives:** Global AppStore polling wastes work off-page. A new client abstraction,
editor, virtual-list library, or chart package has no current need. Existing typography,
panels, buttons, and native form/radio semantics cover the design.

## 6. OpenDesign and verification

**Decision:** Generate a dedicated prototype through the reachable local OpenDesign MCP.
Reuse the existing dark/green utility design and review the complete generated artifact.
Record project, run, preview, and review limits in [design.md](design.md).

**Rationale:** A generic existing design or feature 002's monitor artifact does not satisfy
the requested log-page design. MCP initialization/discovery and run commissioning succeeded
through the local stdio launcher; no global MCP configuration change is required.

**Checks:** Existing `tests/test_cluster_log.py` covers basic collection/relay/read behavior,
not cursor/Clear races. Extend it and the existing Node/browser patterns. Use controlled
timers/deferred responses for cadence/cancellation; verify real worker relay and responsive
keyboard flows separately. Follow [quickstart.md](quickstart.md).

**Measurement decision:** Live latency begins when a new event becomes readable from the
currently serving feed and ends at its rendered browser row. Instrument the existing browser
fixture harness using event IDs and one monotonic clock for feed insertion and render
observation; report observation overhead. Initial history is excluded, but eligible missing
rows count as deadline misses. Real-cluster measurements require recorded clock alignment
and uncertainty; source `ts` and response receipt do not define this latency. No production
telemetry field or new measurement dependency is required for the fixture check.

All technical decisions are resolved. Artifact review and runtime verification remain
evidence to collect, not unresolved product choices.
