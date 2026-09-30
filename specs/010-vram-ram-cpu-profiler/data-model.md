# Data Model: Resource Probes

**Status:** Phase 1 design; fields below describe the new contract, not current output.

## ResourceSnapshot

One bounded observation result for one node. This is transient live data, not a history row.

| Field | Type / meaning |
|-------|----------------|
| `schema_version` | Integer `1` for this additive resource contract |
| `node_id` | Nonempty string, <=128 characters; matches authenticated membership remotely |
| `generation` | 32 lowercase hexadecimal characters; identifies the authorized resource session |
| `sequence` | Integer 1–9007199254740991, not bool; strictly increases for new snapshots within a generation |
| `snapshot_id` | Exactly `<generation>:<sequence>` with sequence in canonical decimal; <=128 characters |
| `requested_at`, `completed_at` | UTC ISO 8601, microseconds, trailing `Z`; not counter timestamps |
| `host` | HostReading |
| `devices` | 0–32 DeviceReadings, unique `device_key`; an empty list means no GPUs only when discovery succeeded |
| `device_discovery` | Status (`measured`, `unsupported`, `error`), reason and observation time; distinguishes no GPU from failed enumeration |
| `processes` | 0–8 ProcessReadings corresponding to requested managed selections |
| `totals` | Dedicated/physical pool totals with completeness metadata |
| `collector` | `native` or explicit `legacy`; platform and bounded diagnostic status |

No hostname, environment, command line, credentials, prompts, unrelated processes, or raw
driver output is needed. Node display names continue to come from membership.

If discovery fails before any device identity is known, return `devices=[]` with discovery
status `error` or `unsupported` and an explicit reason. Preserve available host/process
readings. Successful discovery with no devices instead has status `measured` and null reason.
Never fabricate device identities to fill a failed-discovery result.

Direct local probes create a fresh random generation for each supervisor lifetime. Remote
uploads use the master-issued generation established by the authenticated registration
handshake in contracts/usage-transport.md. The agent assigns outgoing sequence/ID once per
new snapshot; retries preserve them. These delivery identities never change measurement
times or CPU intervals. At sequence exhaustion, establish a new generation before sending.

## Metric

Concrete shared value representation used by CPU/memory fields; not a pluggable metrics system.

| Field | Validation |
|-------|------------|
| `value` | Nonnegative integer bytes or finite percentage 0–100, or `null` |
| `unit` | `bytes` or `percent_machine`; field fixes which unit is allowed |
| `scope` | `host`, `process`, or `device`; owner supplies corresponding identity |
| `status` | `measured`, `initializing`, `unsupported`, or `error` |
| `reason` | `null` for measured; stable reason code for other states |
| `detail` | Optional sanitized text <=256 characters, never raw command output |
| `observed_at` | Original UTC measurement/failed-attempt time; required even if unavailable |
| `interval_start`, `interval_end`, `duration_ms` | Required together for measured CPU, otherwise null; UTC `Z` endpoints, end equals observed_at, monotonic duration 200–86400000 ms |
| `source` | Bounded counter/formula identifier, e.g. `win.pdh.gpu_adapter.dedicated_usage` |

Measured implies non-null value and null reason; other statuses imply null value and a reason.
Zero is valid measured usage. CPU interval endpoints describe observations, while duration is
computed from a monotonic clock, never from subtraction of wall-clock timestamps. A wall-clock
discontinuity invalidates the interval instead of producing an apparently continuous sample.
Reused observations retain all original time/interval metadata.

Reason codes include `baseline_required`, `interval_too_short`, `counter_reset`,
`clock_discontinuity`, `unsupported_counter`, `permission_denied`, `process_exited`,
`process_identity_mismatch`, `identity_unverified`, `device_removed`, `ambiguous_pool`,
`timeout`, `source_busy`, `source_quarantined`, `invalid_counter`, `native_unavailable`,
`enumeration_limit`, and `source_error`. Unknown codes are rejected in version 1 input.

## HostReading

- `logical_cpu_count`: positive integer or null with explicit discovery error.
- `cpu`: Metric, scope host, percent_machine.
- `memory`: `pool_id="host:ram"` plus `total`, `used`, `free`, `available` byte Metrics.
- Memory definitions are source-specific and fixed in research/contract: Windows used is
  total minus available; macOS used is active+wired, available free+inactive, free free-page
  bytes. macOS compressed-memory categories are not silently reclassified as active/wired.
- Free is not available; available is not a guaranteed allocation. Never imply all categories
  add to physical total. Reject individual counters exceeding physical total where comparable.

## DeviceReading and memory views

- `device_key`: opaque <=128-character OS identity scoped to this node/boot. Windows uses
  LUID/physical index; macOS uses Metal registryID. No permanent cross-reboot guarantee.
- `name`: sanitized <=256 characters; `vendor`: bounded identifier or `unknown`.
- `memory_kind`: `dedicated`, `shared`, `mixed`, or `unknown`, based on actual device metadata.
- `inventory_device_id`: optional legacy inference-device ID only after unique verified mapping.
  The verified mapping is a normalized display name (casefold, collapsed whitespace, `(TM)`/`(R)`
  removed) that occurs once on the native device and once in the llama.cpp inventory. Duplicate
  or empty names stay unlinked. A linked dedicated total is the `--list-devices` capacity; the
  previous OS total remains in that metric's `detail`.
- `memory`: up to two MemoryViews, dedicated and shared. Each has `kind`, `pool_id`,
  `backing_pool_id` (null or `host:ram`), `total`, `used`, `free` byte Metrics.
- Shared views reference `host:ram`; their total/free are unsupported if they would imply an
  independent capacity or allocation entitlement. The backing physical total is on the host.
- Shared device usage, when measurable, is a scoped view into host RAM, not extra used memory
  to add to host usage. Dedicated system-memory carve-outs with unclear overlap are classified
  unknown for total aggregation until the accounting relationship is verified.

Discrete `free = total - used` is allowed only for the same pool and compatible counter
definitions. Derived metrics carry the operand observation interval/source identifiers;
capacity and usage from incompatible sources produce `ambiguous_pool`, never a clipped result.
Multiple aliases of one physical pool share its ID and count once; unknown aliasing makes
the combined result unavailable instead of asserting uniqueness.

Device keys and process identities are unique in their respective collections. Pool IDs are
references to physical pools, not unique identifiers of device views: repeated references
are allowed when the pool's identity, kind and backing relationship agree. Reject conflicting
definitions for the same pool ID, not legitimate shared references. In particular, two GPUs
may reference `host:ram` without duplicating host capacity. Their device-scoped usage values
may differ and MUST NOT be summed as additional physical memory. Physical-pool kind describes
the backing pool; a device's `memory_kind` may independently be `mixed`.

## ProcessSelection and ProcessReading

Selection fields: `pid` (positive platform PID, not bool), `role` (`master`/`worker`),
`start_token` (opaque exact OS creation token), `started_at` (canonical compatibility UTC).
Selections must match the caller's managed-child registry; 0–8 unique selections. Duplicate,
unregistered or malformed selections are request errors. No remote arbitrary-PID query exists.

ProcessReading echoes the requested identity, node ID, and adds:

- `state`: `running`, `exited`, or `unavailable`.
- `identity_verified`: boolean, false if start cannot be read or does not match.
- `cpu`: machine-normalized Metric; `rss`: resident-byte Metric.
- `lifetime_id`: existing node/PID/canonical-start encoding for old history projection.

Native exact token: Windows full creation FILETIME encoded as a decimal string, macOS
start seconds plus microseconds encoded losslessly. Tokens are never JavaScript numbers.
Keep the exact token in new resources and optional child/member metadata; the historical UTC
string remains stable throughout that lifetime, including across module/helper restarts.
The legacy UTC alone is not the new collector's sole identity check.

If PID reuse occurs, report the old requested lifetime as exited/identity mismatch with null
metrics. Do not silently return the replacement as managed. Only a new registered launch
creates a new managed selection. Permission failure is unavailable, not proof of exit.

## MemoryTotals

- `dedicated_capacity` and `dedicated_used`: sums of distinct verified dedicated pools.
- `physical_capacity`: host RAM plus non-overlapping dedicated GPU pools exactly once.
- Each total has `value`, `unit="bytes"`, `status` (`complete`, `partial`, `unavailable`),
  `included_pool_ids`, `missing_pool_ids`, and contributing observation times.
- Complete means discovery succeeded and every relevant counter/pool identity is known.
  Partial requires at least one valid contributor and lists exclusions. No valid contributors
  means null/unavailable. Empty dedicated inventory is unavailable/no dedicated devices,
  not a fictional zero-capacity GPU.
- Do not expose a combined host-plus-GPU-used figure: host used includes shared pages and
  platform used definitions differ. The dedicated-used sum is an explicitly GPU-only figure.

## State transitions and ownership

| Owner | Transitions |
|-------|-------------|
| Source slot | stopped → starting → ready → collecting → ready; deadline/failure → terminating → stopped only after reap; unconfirmed exit → quarantined |
| CPU baseline | absent → initializing → valid interval → measured; invalid interval/identity/count/resume → initializing |
| Process | registered identity → running → exited; permission failure → unavailable → running only with same identity |
| Device | discovered identity → observed/unavailable → removed; rediscovery establishes a new valid observation |
| Live snapshot | accepted newer observation → fresh (<10s per metric) → stale; disconnect forces stale regardless of arrival |

Source generations prevent late child output from overwriting newer results. Concurrent
equivalent requests share results; different selections never receive another caller's
process list. Remove unused process baselines/handles once no registered selection needs them.

## Existing persistent data

`UsageStore` tables, retention, lifetime IDs, and old reading JSON remain readable. New live
resources are not persisted; valid process observations are projected into legacy history
fields. Optional exact identity fields in existing child/member JSON are additive and ignored
by older readers; no schema replacement or new database. On restart live resources are empty
until measured again. Never reconstruct current measurements from saved capacity or history.
