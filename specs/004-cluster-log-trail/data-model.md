# Data model: Master & Worker Log Trail

**Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

## LogEntry

| Field | Meaning / rule |
|-------|----------------|
| `event_id` | Canonical UUID; assigned once to a recorded event, preserved across relay and new journal records |
| `ts` | Finite Unix-seconds observation timestamp; display UTC, never use as identity/order |
| `node_id` | Existing stable node identity; label from membership when available |
| `source` | Existing `agent`, `llama`, or `rpc` source |
| `line` | Complete message text; inert escaped rendering, not HTML |

Internal hub receipt sequence is independent of event identity and worker time. Distinct
events with identical fields except `event_id` remain distinct. Legacy records lacking an
ID receive one at ingestion/load; retain existing normalization for legacy source fields.
Incoming supplied IDs must validate instead of silently becoming some other identity.

## LogSnapshot

| Field | Meaning / rule |
|-------|----------------|
| `lines` | Up to `n` retained matching entries in receipt order, oldest first |
| `source` | Existing `cluster` or `local` coverage label |
| `warning` | Existing optional human-readable fallback warning |
| `cursor` | Atomic high-water checkpoint, opaque UUID epoch plus nonnegative sequence |
| `reset` | Null, `origin_changed`, or `restart`; the supplied feed is no longer current |
| `gap` | True if continuity is unavailable or entries since the cursor were omitted |
| `gap_reason` | Null, `retention`, `batch_limit`, or `feed_reset` |

A new hub/changed serving scope has a new epoch. Sequence numbers are monotonic within
that epoch and assigned while accepting events under the hub lock. Internal sequences
are not durable event identity. A successful empty read still returns its checkpoint.

## LogNode

Node identity, known display name, known role, and membership availability. Derive choices
from current membership, buffered entries, and retained selected identity. Unknown roles
are labeled unknown; an unavailable node is not silently replaced by another node.

## LogViewSession

- `selectedNodeId`: null means All nodes; otherwise a stable identity.
- `refreshMode`: exactly `live | 5s | 30s | 1m`; new visit defaults to `live`.
- `rows`: latest 2,000 accepted entries; oldest-row trimming sets an omission notice.
- `seenIds`: ID-only set of accepted entries during this mounted visit, retained after Clear.
- `cursor`: null until the first successful snapshot; otherwise the last committed checkpoint, retained on errors and never advanced from a stale response.
- `generation`, active AbortController, cadence timer, and request-deadline timer: owned resources; close on transition/unmount except a pre-initial-load no-op Clear.
- `hasCleared`: false until Clear with a committed cursor; only then must reset snapshots be baselined without displaying their backlog.
- `state`: loading, ready, empty, cleared, reconnecting/error; coverage labels are independent.
- `coverage`, `gap`, `lastSuccessAt`, `omittedRows`: honest freshness/coverage feedback.
- `following`, scroll anchor: viewport state that survives ordinary updates.

### Transitions

| Event | State change |
|-------|--------------|
| Mount | Create session; immediate latest-200 read; arm Live cadence |
| Node change | Filter rows locally, retain transport and cursor |
| Mode change | Increment generation, abort previous request, replace deadline schedule; retain rows/cursor/selection |
| Successful current read | Validate, deduplicate by ID, append, trim, commit checkpoint and coverage atomically |
| Clear before first successful snapshot | No-op: retain the pending read or retry state, timers, generation, and `hasCleared=false`; consume the first success normally |
| Clear with committed cursor | Empty all buffered rows; retain seen IDs/cursor/selection/mode; abort and invalidate old work; set `hasCleared=true` and mark cleared |
| Error | Retain rows/cursor; report failure and last success; retry at selected cadence |
| Request reaches 5,000 ms total deadline | Abort and invalidate the request, release its slot, retain rows/cursor/coverage/last success, report timeout; retry at first future cadence deadline |
| Feed reset with `hasCleared=false` | Show gap, merge bounded snapshot after identity deduplication, adopt checkpoint |
| Feed reset with `hasCleared=true` | Show recovery gap, discard reset backlog (possibly including unseen post-Clear entries), adopt checkpoint, continue from newer events |
| Hidden tab | Suspend timer/request; retain session; resume with one read and labeled gaps if history expired |
| Unmount | Abort, clear timer, release rows/identity set; no off-page refresh |

See [contracts/logs.md](contracts/logs.md) for wire validation and lifecycle details.
The 5,000 ms request deadline is a fixed controller constant, not a user setting or a new
wire field. It covers headers, body consumption/decoding, and validation before commit;
expired work cannot commit even if its timeout callback runs late.
