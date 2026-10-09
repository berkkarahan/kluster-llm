# Contract: Dashboard Snapshot and WebSocket Feed

**Version**: 1 (proposed)
**Created**: 2026-09-29
**Entities**: [data-model.md](../data-model.md)
**Scope**: New dashboard-only delivery. Existing HTTP commands, reads, CLI log follow,
usage ingestion, and inference streams keep their contracts.

## Endpoint and admission

Both interfaces use the serving agent's existing control origin:

- `GET /v1/dashboard/live` — versioned snapshot plus capability information.
- `WS /v1/dashboard/live` — multiplexed server-to-browser observations and small controls.

Use `ws:` for an `http:` page and `wss:` for an `https:` page. No new port or publicly
exposed worker service. The current dashboard is a private-network application without
session-based read authentication; this interface does not invent one.

WebSocket admission requires a valid non-null `Origin` equal to the serving HTTP origin
(scheme, normalized host, effective port). Reject missing/foreign Origin. A narrow dev
exception allows `http://localhost:5173` and `http://127.0.0.1:5173` only when the connection
peer and target host are loopback. Preserve Origin through Vite; do not rewrite it to
bypass admission. Do not introduce trust in arbitrary forwarded headers.

GET accepts optional `page_origin` for browser admission diagnostics. When supplied, it
must pass the same origin rule; a present actual Origin must also pass. This value is not
authentication and cannot override the actual socket Origin. Non-browser GETs without
Origin/page_origin retain existing private read access. Existing applicable access rules
and upstream topic denials are enforced independently of origin checks.

## HTTP request and response

| Query | Rule |
|---|---|
| `topics` | Comma-separated unique subset of `status,usage,load,download`; defaults to all four. Empty value requests no state areas. |
| `logs` | `0` (default) or `1`. |
| `after` | Optional log cursor, valid only with `logs=1`. |
| `continuity_id` | Optional UUID of the client's last accepted log interval; valid only with `logs=1`. |
| `page_origin` | Optional valid HTTP(S) origin only, maximum 512 characters; no path, userinfo, query, or fragment. |

No node/source filter: the visit-wide trail is acquired unfiltered and filtered locally.
At least one topic or Logs must be requested. New GETs acquire a five-second demand lease,
share in-flight source acquisition, and return available cached records without serially
blocking on every source. A cold/due source is allowed up to two seconds to become
available before returning explicit loading/unavailable state. Never run hardware probes
or upstream reads independently per HTTP caller.

Successful body:

```json
{
  "version": 1,
  "transport": "websocket",
  "epoch": "a5b7c9d1-e3f5-4789-a012-3456789abcde",
  "server_time": "2026-09-29T12:00:00Z",
  "areas": {
    "download": {
      "revision": 4,
      "sampled_at": "2026-09-29T12:00:00Z",
      "availability": "available",
      "coverage": "local",
      "upstream_last_success_at": null,
      "data": {"current": null, "last_terminal": null},
      "error": null
    }
  },
  "logs": null
}
```

`areas` contains every requested state area, including loading/error records; it contains
no unrequested areas. `logs` is null when not requested, otherwise a log result:
`{availability, sampled_at, snapshot, error}`. `snapshot` follows the log model and is
null unless available. This allows a log/usage denial to coexist with available local
status. Each topic gets the same revisions through HTTP and WebSocket.

Use `Cache-Control: no-store`. Request/admission errors are HTTP 400/401/403/422 as
appropriate; unsupported protocol is 426. Source-specific unavailable/denied records are
returned inside a successful aggregate response, with explicit safe error codes. An invalid
supplied log cursor is a request error, not a successful empty log result. No diagnostics
echo arbitrary query values or upstream bodies.

The browser enforces five seconds from dispatch through decode/validation. Snapshot reads
never alter cluster/operation/log storage; an accepted log checkpoint is client state only.

## WebSocket lifecycle

1. Server validates Origin and any applicable access rule, then accepts the connection.
2. Server sends `hello` immediately. Client sends a complete `subscribe` within three seconds.
3. Server sends current records for all requested areas, including loading/error records,
   and the initial/recovery log result when requested in one `initial:true` data frame.
   It must contain all requested results within three seconds, without waiting for another
   change. Incomplete initialization is a failure; there is no multipart initial snapshot.
4. Subsequent data frames contain changed area records and/or incremental log results.
5. Client validates and commits a complete frame, then ACKs it. Only one data frame may
   be outstanding; state coalesces in shared caches and logs remain in the shared hub.
6. Client replaces interests on navigation, log-mode changes, Clear, or explicit refresh
   handoff. Disconnect/hidden/unmount releases interests and invalidates late work.

### Server hello

```json
{"type":"hello","version":1,"epoch":"a5b7c9d1-e3f5-4789-a012-3456789abcde","server_time":"2026-09-29T12:00:00Z","heartbeat_ms":2000}
```

The epoch belongs to the serving runtime, not an individual socket. New runtime/scope
epochs force current-state recovery. Close existing sockets with 1012 on scope replacement
rather than mixing epochs within an active subscription.

### Client replacement subscription

```json
{"type":"subscribe","version":1,"generation":3,"topics":["status","usage","load","download"],"logs":{"after":null,"continuity_id":null}}
```

`logs:null` means no live Logs. Its object uses the last **accepted** checkpoint/continuity
when resuming. Topics have the same allowed names as GET, with no duplicates. An empty
topic list is allowed when Logs is requested. This replaces the entire previous set; the
server never adds another parallel subscription for the same viewer.

Generation is a positive safe integer strictly increasing within the connection. Replacement
invalidates prior pending log work and releases the prior ACK gate. Old-generation ACKs
may be ignored; future/otherwise invalid ACKs are protocol errors. A sender cannot retract
an already transmitted old frame, so the browser must reject obsolete generations.

### Server data and client ACK

```json
{"type":"data","generation":3,"batch":17,"initial":false,"epoch":"a5b7c9d1-e3f5-4789-a012-3456789abcde","server_time":"2026-09-29T12:00:00Z","areas":{},"logs":{"availability":"available","sampled_at":"2026-09-29T12:00:00Z","snapshot":{"lines":[],"cursor":"bf89a306-43ee-4585-ab51-f3a03e673b63:81","reset":null,"gap":false,"gap_reason":null,"source":"cluster","continuity_id":"1b4af590-05d5-4e31-99ca-376c2cd2cd3a","discontinuity":null},"error":null}}
```

```json
{"type":"ack","generation":3,"batch":17}
```

Batch is a positive safe integer increasing within the connection. It identifies a whole
data frame, not a source revision or durable replay position. ACK after synchronous
validation/commit, before awaiting model-list refresh or other side effects. For an empty
successful log snapshot, commit its cursor too. On disconnect before ACK, replay is safe:
the client reconnects with its accepted cursor and still deduplicates stable event IDs.

Reject a whole malformed frame before mutation and start bounded recovery. Do not ACK
uncommitted data. Duplicate/equal topic revisions do not repeat domain effects; stale
frames cannot overwrite newer accepted data. Only the active recovery boundary may accept
a changed serving epoch.

During cutover, validate/stage the entire initial frame before transferring ownership and
committing it; then ACK, allowing subsequent frames. Loading/error records satisfy the
requested-area completeness check. Valid older/equal records can be committed as no-ops
and still ACKed. A recovery probe first subscribes to state only; when ready to transfer
Logs, pause/invalidate its HTTP owner and replace the subscription using the latest
accepted cursor. No later HTTP log result may advance that checkpoint behind the probe.

### Heartbeat

```json
{"type":"ping","id":8}
```

```json
{"type":"pong","id":8}
```

Server sends a ping every two seconds independently of source changes and the data ACK
gate; browser replies immediately. Each side tracks valid peer traffic and uses a
four-second silence deadline, checked at least twice per second while foregrounded.
Heartbeat does not advance topic revisions, observation timestamps, log checkpoints,
or freshness. Browser-native WebSocket protocol Ping/Pong is not this application signal.

The browser also sends a ping every two seconds for time estimation. The server's matching
pong adds `server_time` at reply serialization. IDs match outstanding probes; no unsolicited
pong counts as a time sample. Request-send/reply-receive monotonic times bound the server
clock offset. Advance cached observation ages to serialization time and include the upper
bound of transit time at receipt; do not anchor a cached age directly at receipt. Preserve
an existing observation's earlier age anchor. Clock jumps/uncertain bounds must not make
stale readings fresh; resynchronize or mark freshness uncertain/stale. See the
[age model](../data-model.md#1-feed-runtime-and-source-observations).

### Explicit errors

```json
{"type":"error","area":"connection","code":"invalid_request","message":"Invalid subscription"}
```

Standalone errors are connection-level protocol/admission failures only, scoped by the
browser's current connection generation; they are never used to change an area's state.
State-area errors/denials travel as revisioned availability records inside `data`. Log
errors use the `logs` result in a current-epoch/current-generation data frame. Thus an old
error cannot override a newer successful GET or a replacement log subscription. Codes include
`unauthorized`, `forbidden`, `invalid_request`, `invalid_cursor`, `unsupported_version`,
`source_unavailable`, `invalid_source_data`, `payload_too_large`, and `slow_consumer`.
Messages are bounded safe text (maximum 256 characters), never raw exception strings.
Versioned area errors affect that area; standalone connection-level failures close the connection.

Close codes: 1000 normal disposal, 1008 invalid controls/policy, 1009 oversized input,
1012 restart/scope replacement, 1013 slow consumer/temporary inability to deliver. Accepted
connections may use application codes 4401/4403 for denied access. Browser error/1006 is
ambiguous; it is never interpreted as an HTTP status or proof of unsupported capability.

## Bounds and overload

- JSON text frames only. Client controls: at most 4 KiB, allowed keys/types only; disconnect
  sustained control floods above 20 messages/second per connection. Normal ACK/heartbeat
  traffic at the proposed cadence stays below this bound.
- Data frame: maximum 5 MiB encoded UTF-8. Each complete state-area record is at most
  1 MiB; the complete log result is at most 256 KiB and 500 rows; outer metadata is at
  most 64 KiB. All four areas plus Logs therefore fit in one bounded initialization frame.
  A single oversized state area becomes a cached `payload_too_large` unavailable record,
  consistently revisioned for HTTP and WS; never truncate or partially apply its data.
- For logs, retain the newest suffix fitting both limits and keep its original highwater.
  Any omitted row, including a single oversized newest row, sets `gap=true` and
  `gap_reason=batch_limit`. Do not truncate the text of an apparently complete log row.
- New HTTP responses apply the same per-area/log/envelope bounds and a 5 MiB total bound.
  Browser rejects an oversized response before committing; HTTP and socket observations
  retain the same semantic revision even when delivered in differently sized batches.
- One outstanding data frame per viewer; five-second send and ACK deadlines. No pending
  per-viewer log queue. Source caches and hubs are shared; a slow viewer cannot block their
  writes or another viewer's sender. No source lock is held over a send or network read.
- Disconnect cleanup releases viewer work immediately; expired HTTP demand leases cease
  within five seconds. Canceled/obsolete source work cannot publish to a new generation.

## Delivery mode and race rules

| Situation | Required behavior |
|---|---|
| Healthy live feed | No recurring HTTP application-state reads; heartbeat is control-only. |
| Explicit successful command | Request versioned state via GET and apply revision checks; never replay the command. |
| Live failure, permitted reads available | Start labeled non-log aggregate fallback immediately; two-second deadlines, no overlap within that lane, five-second read deadline. Logs has an independent single-read lane. |
| Unclear socket failure | Diagnose using GET; allow permitted data reads without falsely naming the cause. |
| Missing/incompatible new route | Validate ordinary old reads before using labeled compatibility fallback; an HTML/404 alone proves nothing. |
| Access denied | Show the affected area's access error; stop its delivery/retries until an explicit retry or changed access context. |
| Restore live | Probe every 30 seconds ±20%, stage one complete initial frame, invalidate old HTTP generation, and transfer ownership once; transfer Logs using the paused-owner procedure above. |
| Unavailable or HTTP-only agent restart | Continue permitted two-second HTTP retries. A valid current-generation snapshot GET is a recovery boundary that can admit a new epoch and invalidate old work without waiting for a socket. |
| Hidden tab | Close dashboard socket and cancel data reads/deadlines; retain domain state; recover once on visibility return. |
| Logs timed mode | Unsubscribe live Logs. Perform log-only GET at 5/30/60-second monotonic deadlines; no ordinary reconnect-triggered early rows. |
| Clear | No-op before first success; otherwise invalidate log generation and keep checkpoint/IDs. Resubscribe only in Live; timed mode preserves its next deadline. |
| Log continuity reset after Clear | Baseline-only recovery, adopt checkpoint/IDs and label gap; no reset-tail rows restored. |

Versioned action GETs may coexist with socket state because per-area revisions arbitrate
their writes. **Log** GET and socket delivery never concurrently own the trail checkpoint;
mode/probe handoff pauses or stages one path. Legacy unversioned reads are serialized per
area and fully invalidated before versioned/live cutover. Late cleanup cannot clear a
newer request's in-flight ownership.

There is one non-log HTTP lane and one independent log HTTP lane. A stalled state/action
read cannot postpone a timed-log dispatch. Capability probes and action refreshes exclude
Logs in timed mode; they cannot create between-deadline arrivals. Denied areas are excluded
from retries until explicit retry or changed access context.

## Legacy compatibility fallback

| Existing route | Mapping / degraded capability |
|---|---|
| `/v1/status` | Existing Status payload; per-request generation ordering only. Retain worker local/mirrored scope. |
| `/v1/usage/live` | Existing UsageLive; calculate age conservatively from response/round-trip timing, preserve recording errors and unavailable values. |
| `/v1/load-progress` | Existing load payload; absent operation ID means identity is unknown, not an ID synthesized from model/PID. |
| `/v1/models/download-progress` | Existing job payload; absent operation ID prevents proven once-only operation attribution. Reconcile inventory once on a new observed terminal transition, but suppress unprovable auto-selection/notification on recovery. |
| `/v1/logs?n=200` or `n=5000&after=...` | Require valid event IDs, cursor and reset/gap fields. Without these, report incremental Logs unsupported; do not silently accept unidentified history. |

Validate every old payload before adapting it. Retain last good data on failures. If an
unidentified legacy operation crosses an interruption, label its prior outcome unknown;
the presently reported progress can still be shown with that limitation. A new/worker-
mirror to legacy log handoff is an explicit continuity boundary: retain seen IDs, discard
the old namespace's cursor, acquire a new baseline, and apply baseline-only recovery after
Clear. Reverse handoff follows the same rule. Do not repeatedly submit an incompatible
cursor and treat its rejection as network failure.

Do not spend successive five-second deadlines discovering failure and then attempting
old reads. After socket failure, run the new-route diagnostic and any needed permitted
legacy candidate reads concurrently within the remaining ten-second recovery budget;
stage candidates until access classification completes. A confirmed denial discards them
and stops that area. An ambiguous diagnostic timeout plus valid permitted old reads may
support explicitly degraded fallback, never a claim that the server is definitely old.

## Compatibility obligations

- Keep `/v1/status`, `/v1/usage/*`, progress endpoints, `/v1/logs`, and current command
  response fields available. Add operation ID/time fields without removing existing fields.
- The existing `follow=true` CLI log format does not become the dashboard protocol.
- Master and worker scopes stay as recorded in [research.md](../research.md); no browser
  connects directly to a remote worker RPC service or receives an upstream join key in a URL.
- HTTP and socket access checks cannot reinterpret an upstream denial as local fallback.
- A version 1 peer may ignore unknown additive data fields; unsupported control types or
  protocol versions produce an explicit error and permitted read-only fallback.
