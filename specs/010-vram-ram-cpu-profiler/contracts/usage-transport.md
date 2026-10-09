# Existing Usage Transport — Additive Resource Contract

**Status:** Planned version-1 resource extension; existing process-history contract remains valid.

## Capability discovery and rollout

Add `resource_schema_versions: [1]` to GET `/v1/usage/live` and its existing dashboard-live
usage payload. A new worker checks this through the existing authenticated private master
connection at join/reconnect and caches the result for that master session. Missing field
means legacy master; timeout means unknown capability, not permission to send new fields.

New masters accept legacy bodies unchanged. New workers talking to legacy/unknown masters
send only legacy process readings, omit host-only uploads, and surface unsupported remote
resources locally. Recheck on master reconnect/software-identity change and at most every
30 seconds so a legacy-master upgrade at the same address cannot leave resource support
disabled indefinitely. An advertised version
followed by rejection is a visible protocol error, not silent downgrade/retry of malformed data.
This adds no endpoint and does not refresh membership liveness merely because telemetry arrived.

### Authorized generation establishment and reset

After discovering version 1 support, use the existing authenticated registration endpoint to
request a resource session through optional `resource_session` metadata containing
`request_id` (32 lowercase hexadecimal characters) and `previous_generation` (null initially,
otherwise the previously acknowledged generation). This is additive; legacy registration and
heartbeats do not rotate resource generations.

The master issues a fresh random 32-character lowercase hexadecimal `resource_generation`
in the registration response. Session state is transient and scoped to the registered node:
active generation, last reset request ID and highest accepted sequence. Initial establishment
requires no active generation and previous_generation=null. A reset requires the expected
previous generation to equal the active one; otherwise return 409 without changing state.
Include the current generation in that conflict response so an authorized restarting worker
can explicitly retry the compare-and-swap handshake. Retrying the current request_id returns
its existing generation without resetting the sequence watermark.

The worker serializes reset handshakes and accepts only the response matching its active
request_id. A successful new session clears the old live snapshot and starts sequence at 1.
Routine reconnect with the same acknowledged session keeps its generation/watermark;
supervisor/worker restart or sequence exhaustion requires an explicit reset. The master
restarts with no accepted resource sessions and must issue a fresh generation; previously
issued generations cannot be reactivated by an upload. Membership removal drops session state.

Each resource snapshot contains generation and sequence plus snapshot_id exactly equal to
`<generation>:<sequence>`. Only the active registered generation is accepted. Sequence must
be an integer 1–9007199254740991 (not bool). Equal or lower sequence is an acknowledged stale/
duplicate resource result (`resources_accepted=false`) that cannot mutate live state; another
generation returns 409 and cannot establish/reset a session. Advance the watermark only when
the resource snapshot is successfully installed, after any same-request history commit.
Helper IPC generations remain private source-lifecycle tokens, distinct from this session.

## POST `/v1/usage/ingest`

Keep `X-Kllm-Key`, registered-worker identity checks, private-cluster boundary, 64 KiB body
limit, strict JSON validation, and durable history acknowledgements. Accept either:

```json
{"node_id":"worker-id","readings":["legacy process-reading objects"]}
```

or a negotiated envelope:

```text
{
  "node_id": "worker-id",
  "readings": [0..8 unchanged validated process readings],
  "resources": ResourceSnapshot(schema_version=1)
}
```

The first illustrative array denotes legacy objects, not literal accepted strings.
`readings=[]` is allowed only with valid resources. Snapshot node_id must equal the outer
node_id and registered member. Unknown fields/version, invalid metric status/value pairs,
duplicate device keys or process identities, conflicting physical-pool definitions,
malformed byte values, over-limit selections and excessive
timestamps are rejected with 400. Existing resource limits remain; do not raise them silently.

Repeated pool references are valid when identity, kind and backing relationship agree.
Two GPU views may reference `host:ram` and report different device-scoped usage; accept both
and count the physical pool once. Conflicting definitions of that pool are rejected.

### Complete-message size and split delivery

The 64 KiB limit applies to the complete UTF-8 JSON body, including all envelope fields.
Validate final serialized size, not just the size of each reading or helper reply. The
native supervisor reserves the resource-only envelope overhead when budgeting its snapshot.
Optional diagnostic detail is omitted before measurements are replaced by explicit
source-level payload-limit failures, as specified in the native contract.

If resources and legacy readings fit separately but not together, split before transmission:
send existing-format legacy-history requests and a resource-only request with `readings=[]`.
Partition history into batches that each satisfy both the eight-reading and complete-body
limits; never split a reading or silently discard a measurement. Each request is independently
validated and acknowledged; there is no cross-request atomicity. A resource-only request may
include valid registered process observations even though it carries no history rows.
An oversized request rejected by the receiver remains an error, not permission to retry
malformed data as a silent downgrade.

### Identity validation

Legacy nonempty readings still require the current registered PID/start/role. New resource
processes must also match the registered exact start token when present; new-version workers
advertise that token and canonical timestamp through additive RegisterBody/member fields.
For no-child resources, require an empty process list but allow host/device observations.
For confirmed exit/mismatch, the agent updates membership first and emits host-only snapshots;
it does not submit foreign process observations disguised as the previous child.

Validate incoming observation times using the existing 30-second future skew and 24-hour
past bounds, plus valid ordered CPU intervals and bounded monotonic duration. Old/duplicate
resources never overwrite newer readings. Order snapshots by sequence only within the active
master-issued generation; use the registration handshake above for any reset. Inside
one snapshot, metric timestamps can differ; no uniform artificial acquisition time is added.

### Acknowledgement and persistence

Unchanged history response fields: `accepted`, `duplicates`, `last_successful_save`.
For new envelopes add `resources_accepted: bool`. It acknowledges validated **transient live
storage**, never durable history. A resource-only upload must not advance last_successful_save.

Validate the complete envelope before applying it. Valid legacy rows commit through existing
`record_many`; install the resource snapshot only after any requested durable write succeeds.
If that write fails return existing 503 and retain prior history/live state for that combined
request. A separate resource-only upload does not open/write the history database and can
succeed when recording is unavailable. In split delivery, acknowledge each request separately;
success of live resources must not hide a failed history request or imply durable recording.

One latest resource snapshot per registered node, not an unbounded request cache. Discard
obsolete generations and prune on membership removal; prior durable process history follows
its existing retention policy. A restarted master begins with no live resources.

## GET `/v1/usage/live`

Keep all existing node, relationship and recording fields. Add optional fields per node:

```text
resources: ResourceSnapshot | null
resource_age_seconds: number | null
resource_freshness: "fresh" | "stale" | "unavailable"
```

These fields are independent of `reading`, `process_state`, and old history freshness.
A node with `reading=null` and no child can have fresh host/device resources. Source metric
timestamps govern individual values: a fresh host sample cannot make a stale GPU sample fresh.
The summary age is the oldest measured dynamic metric's age; summary is fresh only while
connected and all supplied measured dynamic values are younger than ten seconds. With no
measured dynamic values it is unavailable. Static identity/capacity does not establish liveness.

`LiveFeed` and browser replay retain original snapshot/metric IDs and times; neither live
envelope sampled_at nor HTTP receipt restarts the ten-second deadline. Node disconnect marks
remaining displayed data stale immediately; CPU initializing remains distinct from idle zero.
When history access fails, return available live resources with existing recording failure
details instead of making host-only monitoring depend on a functioning history database.

## Legacy projection and UI integration

- `usage.Sampler.sample` can continue returning the legacy point/None for existing callers;
  a new sibling method or last-resource property exposes the independently collected snapshot.
  One cycle collects once; do not probe again to obtain the second representation.
- Project a point only for a verified managed lifetime. Process CPU/RSS and host fields map
  to their old names; no extra keys enter legacy `usage.validate` or persisted history rows.
- Use a conservative legacy observed_at equal to the oldest included dynamic observation
  (never before process start); omit/null metrics that cannot be consistently represented.
  Keep the full separate per-metric times in resources. Do not claim new history metadata.
- Match legacy device IDs only through a verified unique inventory mapping. If ambiguous,
  leave those old values unavailable; the raw resource device remains visible by stable OS ID.
  Never transfer values to an inference index simply because it shares an array position.
- Existing optional utilization/temperature/power remain separately scoped legacy sensors.
  Reuse their existing collector asynchronously under its own bounded single-in-flight policy;
  do not append serial sensor time to the native probe's two-second deadline or overwrite new
  native memory with legacy estimates. Missing identity mapping yields unavailable sensors.
- Existing node/details components prefer new resource fields when supplied, preserve old
  behavior when absent, and show free versus available, shared/dedicated pool labels,
  partial aggregates, actual age and reason text. Process-history charts remain unchanged.
- Collector notices/failures do not stop inference. New fields never change cluster planning,
  launch/stop controls or unauthenticated upstream RPC; port 50052 stays private.

## Required compatibility cases

| Case | Expected |
|------|----------|
| Old worker → new master | Existing ingest/history/live still accepted |
| New worker → old master | Capability absent; legacy body only; explicit resources unavailable |
| New worker with no child → new master | Empty readings + host/device resources accepted; no lifetime inserted |
| Replayed/cached snapshot | No younger timestamps, freshness renewal or replacement of newer metrics |
| Previous-generation upload arrives after an acknowledged reset | 409; current snapshot and sequence watermark unchanged |
| Equal/lower sequence in the current generation | resources_accepted=false; no live-state mutation |
| Delayed reset request has an obsolete previous_generation | 409; no rotation, snapshot clearing or watermark reset |
| Retry of current reset request_id | Same acknowledged generation; watermark preserved |
| Master restart followed by an old-generation upload | Rejected until fresh authenticated registration establishes a new generation |
| Registered worker sends unrelated PID/node | 400; no partial state/history mutation |
| Invalid version/non-finite/oversize body | Existing bounded validation/error conventions |
| Two devices reference the same host pool | Accepted; capacity counted once and device-scoped usage not added to host RAM |
| Same pool ID has conflicting backing metadata | 400; no partial state/history mutation |
| Individually fitting resources/history exceed the combined body limit | Sender splits into bounded independently acknowledged history/resource requests |
| Split history request fails while resource-only request succeeds | Live values update; history failure remains visible and is not acknowledged as saved |
| Recording database unavailable | Host-only live path works; recording failure remains visible |
| Dashboard connected with no managed child | Host/GPU readings visible, process reads no managed process |
