# Data Model: Dashboard Live Updates

**Created**: 2026-09-29
**Specification**: [spec.md](spec.md)
**Wire contract**: [contracts/dashboard-live.md](contracts/dashboard-live.md)

## 1. Feed runtime and source observations

One `LiveFeed` belongs to one agent lifespan. It owns a serving epoch, source caches,
single-in-flight acquisitions, viewer subscriptions, and expiring HTTP demand leases.
It does not own inference, usage recording, membership, or the persistent log journal.

| Field | Meaning / validation |
|---|---|
| `version` | Protocol integer, initially `1`. |
| `epoch` | Canonical UUID, new on lifespan start or role/master identity change. |
| `areas` | Requested subset of `status`, `usage`, `load`, `download`. |
| Per-area `revision` | Nonnegative safe integer, monotonically increasing within the epoch on semantic data/availability change. |
| `sampled_at` | UTC timestamp of the latest successful source read, or null before any success; not a new hardware observation time. |
| `availability` | `loading`, `available`, `unavailable`, or `denied`. |
| `coverage` | `local`, `cluster`, or `local_with_cluster_view`; not a connectivity flag. |
| `upstream_last_success_at` | Nullable UTC time for worker-mirrored/proxied information; expose old upstream information as stale/unavailable. |
| `data` | Valid topic payload when available; null for errors/loading. Clients retain their last accepted good value separately. |
| `error` | Null or bounded `{code, message}`; no raw exception, credentials, URL, or payload dump. |

Cache writes copy source data before publication. Epoch, revision, availability, and data
are captured together per area. Different areas are not a cross-topic atomic transaction.
An equal revision may confirm delivery/sample health without repeating domain effects.
Role/master/process generations also guard in-flight acquisition; a higher publication
number does not make a superseded process's result valid.

HTTP responses and socket hello/data/time-probe replies carry `server_time` (UTC at
serialization). Advance cached usage `age_seconds` by server monotonic cache residence
before serialization. The browser bounds the server-clock offset using local monotonic
request-send/reply-receive times, then conservatively includes estimated transit time in
received age. Use the upper age bound, not a synchronized-clock assumption. HTTP requests
and two-second client ping/server pong exchanges refresh this estimate; a clock jump or
unbounded estimate makes freshness uncertain/stale until resynchronized. Equal observation
replay never moves its existing aging anchor later. Test cached first reads and skewed clocks.

### Existing payload relationships

- **Status:** Existing `Status` fields and semantics. Local role/process/configuration
  remain local on a worker; mirrored membership/activity are identified by coverage and
  upstream success time. `join_key` remains a sensitive existing status field, never
  diagnostic output. Completed capacity/software metadata is cached separately from fast state.
- **Usage:** Existing `UsageLive` nodes, relationships, and recording information. A reading
  retains `node_id`, `lifetime_id`, PID/start identity, observation time, scopes, units,
  and availability. No new usage database or sampling process.
- **Load/download:** A small progress view with `current` and `last_terminal` observations
  as described below. Existing panels can consume the selected existing-shape observation.

## 2. Operation observation

Stored by the existing download/load source modules, independently of viewers.

| Field | Rule |
|---|---|
| `operation_id` | UUID assigned and reserved before accepting work; never derived from filename, model name, or PID. |
| `kind` | `load` or `download`. |
| `node_id` | Serving node identity. |
| `state` | `running`, `complete`, `error`, or `cancelled`. |
| `started_at`, `updated_at`, `finished_at` | UTC timestamps; finish is null while running. |
| Subject | Existing model/filename; load also retains the managed process identity when established. |
| Progress | Existing bytes/fraction/percentage/rate/ETA/phase fields, with their existing units and null semantics. |
| Error | Bounded, sanitized message when failed. |

Progress area `data` is `{current, last_terminal}`. `current` is null or the active
observation; `last_terminal` is null or the latest completed observation of that kind.
Retain only these records. Terminal transition atomically moves current to last-terminal.
Both null means no recoverable operation information, not proof that a previously seen
operation succeeded. Existing individual progress endpoints project current, otherwise
last-terminal, otherwise their existing idle payload; identity/timestamps are additive.

State machine:

```text
accepted/reserved -> running -> complete | error | cancelled
```

- A cancellation request does not itself establish `cancelled`; the running work must stop.
- Late updates for a different operation ID cannot overwrite current or terminal state.
- Terminal outcomes are immutable. Process death after a completed load is runtime status,
  not a retroactive change of that load to failed.
- Explicit and membership-driven spawn/stop paths share this lifecycle.
- On source restart, records are not fabricated from model names or files. A viewer whose
  tracked operation is no longer represented marks that outcome `unknown` locally. This
  also applies if multiple later operations supersede the one retained terminal record.
- Repeated operations for the same model/file receive distinct IDs. Retained terminal
  records do not block admission of the next operation.

## 3. Log source and continuity

Existing `LogEntry` remains `{event_id, ts, node_id, source, line}`. `event_id` is a canonical
UUID; time is finite Unix seconds; `source` is `agent`, `llama`, or `rpc`. Distinct IDs with
identical text remain distinct. Display messages as inert text in receiver order.

The master reads its existing `cluster_log.HUB`. A worker uses either its separate
nonpersistent master-log mirror or its real local HUB. The mirror preserves original
event IDs but assigns its own receiver-local sequence numbers. Its upstream checkpoint
is private acquisition state, never a client cursor.

| Log snapshot field | Meaning |
|---|---|
| `lines` | Bounded unfiltered suffix, oldest first. First load requests up to 200; incremental delivery caps at 500 and 256 KiB. |
| `cursor` | Opaque canonical `UUID:sequence`, at most 128 characters; highwater through which entries were delivered or explicitly omitted. |
| `reset` | Null, `origin_changed`, or `restart`. |
| `gap`, `gap_reason` | Existing boolean and null / `retention` / `batch_limit` / `feed_reset`. |
| `source` | `cluster` or `local`. |
| `warning` | Optional safe coverage explanation. |
| `continuity_id` | UUID identifying the current coverage/continuity interval. Changes on upstream reset/gap or source change, including coverage round trips. |
| `discontinuity` | Null or `{id, reason, detected_at}`; sticky for the interval, reasons `upstream_reset`, `upstream_retention`, `upstream_batch_limit`, `coverage_change`. |

With a supplied `after`, return entries strictly after the checkpoint and a current
highwater even for an empty result. A malformed or future same-epoch cursor is an error;
an unknown epoch yields a reset snapshot and an explicit gap. Source switch/reset and its
available suffix are published atomically. The caller's prior `continuity_id`, when
supplied, makes an otherwise invisible coverage round trip an explicit reset as well.

No persistent schema changes. Mirror lifetime, cursors, and continuity descriptors are
ephemeral. Existing journal and usage retention remain authoritative.

## 4. Viewer delivery session

| State | Lifetime / bound |
|---|---|
| Connection generation | Browser-local monotonic counter; invalidates old sockets, HTTP completions, timers and cleanup callbacks. |
| Subscription generation | Increasing safe integer within a connection; full replacement of desired topics/log cursor. |
| Accepted epoch and revisions | Latest accepted epoch plus per-area revisions. A new epoch is admitted only at the active initialization/recovery boundary. |
| Outstanding batch | At most one bounded data frame, identified by subscription generation and batch number. |
| Last accepted log checkpoint | Advanced only after complete validation and commit; supplied on recovery/replace. |
| Delivery health | `loading`, `live`, `reconnecting`, `periodic-refresh`, `unavailable`; paused while hidden. |
| Per-area health | Availability, last successful update, source freshness/coverage, and safe error separate from delivery health. |
| HTTP ownership | One non-log aggregate read plus one independently owned log read may be in flight; legacy fallback serializes each old area read. |

```text
loading -> live
loading/live -> reconnecting -> periodic-refresh | unavailable
periodic-refresh/unavailable -> staged recovery -> live
any foreground state -> paused (hidden) -> loading/recovery
any state -> disposed (unmount/close)
```

Denied areas stop their delivery/retry work and display an access error; transport
restoration cannot bypass denial. Other permitted areas may continue. Topic loading/error
does not imply the socket is disconnected. A heartbeat proves transport health only.

The first socket data frame has `initial:true` and contains every requested result;
loading/error records satisfy completeness. It is one bounded frame, never a multipart
snapshot. Validate/stage it, transfer the applicable delivery ownership, commit, and ACK.
Incomplete initialization times out after three seconds. During a live probe, acquire
state first; transfer Logs in a replacement subscription only after pausing its prior
owner and capturing the latest accepted checkpoint. Stale/equal valid records are no-ops,
not reasons to withhold an otherwise accepted frame's ACK.

While periodic refresh or unavailable is active, successful current-generation HTTP
snapshots are also recovery boundaries: a new epoch resets revision watermarks and
invalidates older work. Retry permitted ordinary reads on two-second deadlines even when
the UI says unavailable; the separate live-capability probe runs every 30 seconds.
Timed Logs retain their own deadlines and do not wait behind a non-log aggregate read.

While a batch is unacknowledged, other viewers continue and source caches coalesce current
state; no per-viewer log queue grows. The next acknowledged checkpoint reads from the
bounded shared hub and discloses retention loss. Five-second send/ACK timeouts close a
stalled viewer. Small heartbeat/control messages do not wait for a data ACK.

## 5. Browser domain state and effects

`AppStore` retains node/history selections, map ownership, drafts, plan fingerprints,
and command methods. New validated apply methods share the acceptance path for live and
versioned HTTP observations. Reconnect does not rerun configuration hydration.

- Record each successful terminal operation ID before reconciling the model inventory,
  even if its running state was never observed. Inspect `last_terminal` independently of
  a newer `current` operation. Auto-selection/notification additionally requires that this
  visit observed it running or received its ID from its own accepted command response;
  an initial historical terminal result does not unexpectedly select a model.
- Check the user-selection/lifecycle generation again after asynchronous completion work.
- Keep load-panel dismissal keyed by operation identity, not repeated `model:phase` values.
- The dedicated download topic takes precedence over duplicate status download summaries.
- Derive usage age using serialization-adjusted source age, bounded transit estimate, and
  local monotonic elapsed time. A replay/heartbeat never resets age; stale starts at ten
  seconds, or conservatively when age is uncertain. Membership
  expiry, recording errors, and missing measurements remain separate states.

`LogTrail` retains visit-scoped `entries`, `seenIds`, filter, mode, cursor, continuity ID,
`hasCleared`, deadline, scroll anchor, gap notice, and last successful read.

1. Before the first accepted snapshot, Clear is a complete no-op.
2. Effective Clear empties all rows, retains seen IDs/checkpoint/filter/mode/deadline,
   invalidates pending log work, and replaces the log subscription generation only in Live
   mode. Timed mode Clear preserves its next read deadline without subscribing.
3. After Clear, a continuity reset adopts its checkpoint and IDs as baseline only, displays
   no reset-tail rows, and labels the potentially omitted recovery window.
4. Timed modes have no live log interest; selected deadlines alone admit new rows. Initial
   mount and hidden-to-visible return may read immediately. Ordinary reconnect and Clear
   preserve the next deadline. Missed deadlines are skipped, not replayed.
5. Every discrete read has a five-second dispatch-through-decode limit and generation guard;
   a late response cannot commit or release a newer request's ownership.
6. Keep 2,000 displayed rows and the existing newest-first, top-following scroll behavior.
   Gap notices persist until intentional Clear/new visit; local coverage is independently visible.

## 6. Validation boundaries

- Validate protocol version, finite numeric ranges, safe integer identities, UTC timestamps,
  required topic fields, operation UUIDs, and all log fields before mutation.
- Reject duplicate topics, unknown control types, invalid generations, bad/future ACKs,
  malformed cursors, binary frames, oversized controls, and unsupported versions explicitly.
- Reject stale generation/epoch callbacks without applying them; unknown additive payload
  fields within version 1 may be ignored. Do not treat malformed data as valid empty state.
- No control message can execute a command. No URLs, tokens, join keys, raw data frames, or
  arbitrary upstream response bodies are included in errors.
- New delivery payloads are byte-bounded; overflow reports unavailable data or explicit log
  omission rather than silently truncating non-log state or partially committing a frame.
