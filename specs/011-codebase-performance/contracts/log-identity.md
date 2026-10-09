# Negotiated Exact Log Identity Contract

**Status**: Proposed additive contract for feature 011; legacy behavior remains supported.

## Scope and negotiation

Use existing private log/dashboard endpoints and worker-authenticated ingestion. This contract
does not add a listener, deletion route, service or credential. A client opts into
`position-v1`; capability acceptance is explicit in the handshake/snapshot. Missing capability
or an old upstream is legacy mode. Never infer negotiation from UUID presence, protocol version,
or a receiver cursor.

- HTTP dashboard log read: optional `log_identity=position-v1` request parameter.
- WebSocket subscription: optional `log_identity: "position-v1"` member.
- Response/hello: explicit negotiated capability and stable `domain_id`.
- POST ingest: additive optional `position` per event and explicit negotiated request mode;
  old bodies and old generated UUID behavior remain unchanged.
- A proxy may forward negotiated data only if the entire required identity/frontier contract
  is supported upstream. It must not synthesize negotiated metadata for legacy logs.

If actual API shape requires a separate additive request envelope to avoid changing current
query/subscription schemas, retain these semantics and update this document before code.

## Event identity

Negotiated event:

```json
{
  "event_id": "<canonical UUID bound by producer to position>",
  "position": {"slot": 0, "generation": 1, "sequence": 42},
  "ts": 1790632000.25,
  "node_id": "worker-02",
  "source": "rpc",
  "line": "worker ready"
}
```

The identity authority maintains at most 64 slots per domain. Slot is `0..63`; generation and
sequence are positive safe integers. A producer reserves positions durably before admission.
An exact retry uses the same slot/generation/sequence and UUID. A conflicting payload at that
position, arbitrary UUID-position pairing, unknown slot, retired generation, or unallocated
future generation is rejected. Exact derivation/binding of UUID from position must be defined
consistently by producer/authority before negotiation is enabled.

## Frontier and snapshot

Every successful negotiated snapshot includes:

```json
{
  "capability": "position-v1",
  "domain_id": "<canonical UUID>",
  "frontier_revision": 12,
  "retired_through": [
    {"slot": 0, "generation": 1, "sequence": 41}
  ],
  "lines": [],
  "cursor": "<existing receiver-local checkpoint>",
  "reset": null,
  "gap": false,
  "gap_reason": null
}
```

`retired_through` is the per-slot contiguous frontier. It asserts that, within this domain and
generation, no event at or below the stated sequence can occur in any later response. The
frontier is not the greatest row in this response and is not the receiver-local `cursor`.
Rows, domain, frontier and cursor are one atomic snapshot. Each response carries at most 64
unique slots; duplicate, regressing, future, unsafe or mismatched positions invalidate the
whole frame before state commit.

The source may advance a frontier only through consecutive events whose disposition (accepted
or explicitly retired/omitted) is durable and final. A sequence hole blocks advancement.
At most 4,096 out-of-order positions are retained per slot. Reorder-window exhaustion returns
explicit `identity_window_exhausted`/busy/unavailable and backpressures ingestion. It must not
skip a hole, retire an unknown event, reassign a slot, drop an ID or report healthy partial
success.

## Durable authority and relay rules

- Fixed 64-slot table stores producer ownership, current durable generation, next sequence and
  contiguous retired frontier. Slot state/allocation must be recovered atomically with journal
  data sufficiently to prevent sequence reuse. If journal loss/rotation prevents that proof,
  negotiated mode is unavailable.
- Slot reuse only follows explicit retirement and durable generation increment. Full registry
  or uncertain ownership is an explicit admission error, not LRU/age eviction.
- Journal rows persist negotiated positions. Legacy rows stay legacy; never backfill positions
  from journal line order or runtime `_seq_counter`.
- Worker mirrors preserve upstream position/domain/frontier unchanged. Mirror cursors are only
  local receiver checkpoints. Retry forwarding preserves exact identity.
- Master/role/coverage switch remains negotiated only when authority proves same domain and
  slot mapping. Otherwise return an explicit transition/gap and use legacy mode; no old domain
  is reinterpreted as a new origin with empty client identity state.

## Dashboard state machine

1. Validate complete result and all positions/frontiers before changing cursor, rows, domain,
   watermarks, pending IDs, ACK or Clear state.
2. In negotiated mode keep ≤64 watermarks and ≤4,096 seen out-of-order positions per slot.
   Suppress positions at/below committed frontier and already-seen pending positions. A high
   observed sequence alone does not advance the watermark or permit eviction of lower IDs.
3. Commit advertised retirement frontier atomically with its response, then remove pending
   positions covered by it. Ignore any later position at/below that committed frontier.
4. On effective Clear, remove display rows only; preserve domain, frontiers and pending IDs.
   Reset after Clear remains baseline-only and commits frontier only after full frame validation.
5. Errors, denials, stale scope/generation, cancellation and incomplete frames do not advance
   position state. Unmount releases the visit state.
6. If a visit drops to a legacy server or a domain cannot be proven continuous, preserve the
   exact existing UUID set for that visit and expose `legacy/unbounded identity mode`; no silent
   set clear/cap. A new visit begins fresh.

## Size and errors

The complete logs area/result is limited to 256 KiB as required by the existing dashboard
validator; event lists remain bounded by the existing 500-row live cap. Account for wrapper,
continuity and retirement metadata bytes. If a valid empty/metadata-only result cannot fit,
return explicit oversized/unavailable. A byte-trimmed suffix sets `gap=true` and
`gap_reason=batch_limit`; an existing retention reason keeps precedence. Malformed negotiated
input returns an actionable client error; slot/window pressure returns an explicit retryable
availability result. Existing non-negotiated response and POST formats remain backward
compatible.

## Required contract tests

- Negotiation accepted/refused across HTTP, WebSocket and worker-to-master POST.
- Legacy request/response, legacy journal reload and old upstream fallback unchanged.
- Stable position/event ID across retry, restart, relay and worker mirror; duplicate retry is
  idempotent; conflicting duplicate and retired/future generations fail.
- Contiguous frontier stalls at a hole; no advance based on max row; restart/rotation recovers
  frontier only when durable proof exists.
- Full 64-slot table and 4,096-position reorder window return explicit backpressure without
  eviction/reassignment/partial commit.
- Worker/local/master/role transition preserves legacy exact ID state when domain handoff is
  unavailable; Clear/filter/reset/overlap cases remain exact.
- Malformed frontier or oversized complete result cannot partially commit/ACK/cursor-advance.
- Real authenticated worker-ingest → master journal → worker mirror → dashboard boundary.
