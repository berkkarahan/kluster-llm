# Data Model: Performance Evidence and Negotiated Log Identity

This is planning-time domain and validation data. Existing log/API shapes remain unchanged
unless a client explicitly negotiates the additive `position-v1` capability.

## Performance evidence entities

### WorkloadRun

Represents one measured unit of work or suite execution.

| Field | Meaning | Validation |
|---|---|---|
| `run_id` | Unique local identifier | Nonempty; not an authentication token. |
| `revision` | Source revision and declared tree state | Record commit plus dirty/untracked state. |
| `environment` | OS, CPU/RAM, runtime/compiler/browser and loaded native-module path where relevant | Include only details required to reproduce/interpret the measure. |
| `workload` | Input sizes, topology/concurrency, build and cache state | Same workload and reset method on compared revisions. |
| `started_at`, `elapsed` | Wall-clock execution interval/duration | Monotonic elapsed; explicit unit. |
| `phase_durations` | Collection/setup, case execution, teardown; optional per-flow phases | Sum need not equal whole command when runner overhead is separately reported. |
| `outcome` | Collected/passed/failed/skipped cases and cleanup state | Case identities and skip reasons retained; no case disappears from denominator. |
| `resource_samples` | Optional CPU time, RSS, handles, I/O and acquisition counts | Source/measurement method named; never equate estimate with direct observation. |

### OptimizationCandidate

Evidence-linked option and disposition; exists in the research/implementation record, not a
new production database.

| Field | Meaning | Validation |
|---|---|---|
| `candidate` | Option and affected existing flow | Concrete module/function references. |
| `evidence_level` | Measured, source-supported hypothesis, or existing capability | Explicitly labeled. |
| `benefit`, `cost`, `risks` | Expected effect, engineering effort and correctness/platform impact | Unknowns remain unknown. |
| `disposition` | Select, conditional, defer or reject | Has rationale and measurement/acceptance trigger. |
| `comparison_runs` | Linked baseline/optimized WorkloadRuns | Matched revisions/workload, at least five for suite target claims. |

## Negotiated log identity entities

### IdentityDomain

Defines the authority in which positions and retirement frontiers are meaningful.

| Field | Meaning | Validation |
|---|---|---|
| `domain_id` | Stable UUID for the position authority/history lineage | Canonical UUID; never inferred from a receiver cursor. |
| `capability` | Negotiated protocol token | Exactly `position-v1`; absent/unsupported selects legacy behavior. |
| `slot_limit` | Maximum simultaneous producer slots | Fixed at 64 in this design; peers reject values outside the agreed contract. |
| `reorder_limit` | Maximum pending out-of-order positions per slot | Fixed at 4,096; overflow explicitly backpressures/errors. |

### ProducerSlot

One durable allocation record owned by the identity-domain authority.

| Field | Meaning | Validation |
|---|---|---|
| `slot` | Fixed producer table position | Integer 0–63. |
| `producer_id` | Authenticated/stable producer identity | Bounded nonempty node identity; ownership checked at registration/ingest boundary. |
| `generation` | Durable allocation incarnation | Monotone safe integer; persist before accepting events. |
| `next_sequence` | Next reserved position | Monotone; never reused after restart, failure or process-number reuse. |
| `retired_through` | Largest contiguous position proven final | Safe integer; never exceeds fully committed dispositions. |
| `state` | `active`, `retiring`, `retired`, or `unavailable` | Transitions are persisted and validated under hub ownership. |

Slot reuse is forbidden until retirement and next-generation allocation are durably committed.
If state recovery cannot prove no reuse, the negotiated mode is unavailable. A fixed 64-slot
table keeps old-generation rejection bounded without unbounded tombstones.

### PositionedLogEvent

An ordinary event with an additive stable producer position.

| Field | Meaning | Validation |
|---|---|---|
| `event_id` | Existing UUID identity | Canonical UUID; bound to position by negotiated producer/hub, never arbitrary pair. |
| `position.slot` | ProducerSlot | Integer 0–63, registered in the negotiated domain. |
| `position.generation` | ProducerSlot generation | Equals current admitted generation; retired/future values rejected. |
| `position.sequence` | Producer sequence | Positive safe integer; idempotent same-position retry. |
| `ts`, `node_id`, `source`, `line` | Existing log data | Existing validation, redaction/access policy and render semantics retained. |

Different events with identical text/timestamp have different positions/IDs. An identical
retry has the same position/ID. Conflicting contents at one position are invalid, not overwrite.

### PositionFrontier

Atomically returned high-water safety metadata for a complete set of producer positions.

| Field | Meaning | Validation |
|---|---|---|
| `domain_id` | IdentityDomain | Must match negotiated session or require explicit transition. |
| `positions[]` | At most one `{slot,generation,retired_through}` per active/retired slot | No duplicate slots; values match durable authority state. |
| `frontier_revision` | Monotone snapshot metadata revision | Safe integer; reject regressions within domain. |
| `availability` | `available` or explicit unsupported/busy/exhausted state | Failure cannot advance client checkpoints. |

A frontier proves **no matching event at or below the value can appear in a later response**
within the same domain/generation. It is not the receiver-local HTTP/WebSocket cursor, nor the
maximum position seen in the current batch. It advances only across a contiguous accepted or
explicitly retired sequence range.

### ClientVisitIdentityState

Transient dashboard-owned dedup state for one mounted visit.

| Field | Meaning | Bound/transition |
|---|---|---|
| `mode` | `negotiated`, `legacy`, or `unsupported` | Legacy transition is visible; exact suppression is retained. |
| `domain_id` | Active IdentityDomain | Domain change without explicit proof transitions to legacy/unbounded, not reset. |
| `watermarks` | Contiguous committed PositionFrontier per slot/generation | At most 64 entries. |
| `pending_positions` | Already accepted IDs above frontier for overlap/reordering | At most 4,096 per slot; no eviction to make room. |
| `legacy_seen_ids` | Exact event UUIDs for a legacy visit | Intentionally unbounded as required by existing contract; released on unmount/new visit. |
| `display_rows` | Visible recent log rows | Existing cap of 2,000; Clear empties rows but not visit identity state. |
| `committed_cursor`, `continuity_id` | Existing receiver checkpoint and transport continuity | Separate from producer positions. |

If negotiated `pending_positions` reaches its limit before safe frontier advance, do not
discard the oldest position or move the watermark. Mark delivery unavailable with
`identity_window_exhausted`; recovery/backpressure must come from producer progress or a new
explicit domain session. Malformed/stale/denied/cancelled frames never partially mutate state.

## Required state transitions

- **Event reservation**: active slot reserves and durably commits a unique sequence; event ID
  is bound; journal/relay retry carries the same position.
- **Ingest**: identical same-position retry is idempotent. Conflicting duplicate position,
  unknown producer, retired/future generation or invalid ID-position binding is rejected.
- **Frontier advance**: atomically commit accepted/explicitly retired consecutive positions;
  gaps block advancement. Journal rotation cannot erase state needed to prove the frontier.
- **Slot retirement/reuse**: stop admission, prove/commit final frontier, durably increment
  generation before next producer admission. Full table is explicit backpressure, not eviction.
- **Worker forwarding**: preserve upstream event position/frontier; worker receiver cursor is
  local transport only. Legacy upstream yields legacy mode.
- **Coverage/master/role change**: keep negotiated state only with proven same domain and
  ownership handoff; otherwise exact legacy set remains for the visit, visibly unbounded.
- **Dashboard commit/Clear**: validate full snapshot first; then apply identities/frontier and
  cursor atomically. Clear removes display rows only. Reset after Clear remains baseline-only.
- **Unmount**: release all per-visit bounded state and legacy UUID set.

## Persistence and privacy

Position and frontier values contain no message text or credentials. Persist only where the
existing journal ownership can provide atomic recovery and physical limits; do not create an
independent database/service. Existing legacy rows remain readable as legacy rows. No migration
may fabricate source positions based on load order. Failure to prove durable no-reuse disables
the negotiated capability rather than claiming exactness.
