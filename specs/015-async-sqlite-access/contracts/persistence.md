# Contract: Local Persistence Ownership

**Audience**: Internal store/service maintainers. These are design contracts for implementation; no new public query API is introduced.

## Interface surface

| Interface | Inputs | Result / responsibility |
|---|---|---|
| `SQLiteOptions` | Constructor values or documented environment defaults | Validate all values before engine creation; immutable once owned |
| `SQLiteStore.open` / tracked async initialization | Canonical file path, generation, domain schema/profile, options | Validate/adopt schema and settings; publish OPEN only after successful initialization |
| `read(callback)` | Synchronous bounded callback using its supplied Core Connection | One admitted unit, one connection and read snapshot; fully materialized result |
| `write(callback)` | Synchronous bounded callback using its supplied Core Connection | One admitted unit, one exclusive write transaction, confirmed commit or explicit failure |
| `aread(callback)` / `awrite(callback)` | Same callback semantics | Await tracked bounded worker execution including acquisition/cleanup; no event-loop driver/lock waits |
| Async bounded composition dispatch | Existing synchronous service/domain composition | One offload; nested store calls execute inline and retain separately defined commits/fences |
| `close()` / `aclose()` | Existing owner and configured deadline | Stop admission, settle tracked work, close resources; return success only at zero owned resources |
| Explicit maintenance/test lease | Existing owner, access mode | Scoped access for schema/fault fixtures; no indefinitely owned `.db` compatibility alias |

Keep `UsageStore` and `NetworkStore` as domain facades. Existing synchronous method signatures and return meanings remain supported. Provide async entry points where actual event-loop callers need them, instead of changing every public method to an await. Composition callbacks are not an arbitrary task-execution API.

## Configuration and bounds

| Setting | Default | Validation / effect |
|---|---|---|
| `KLLM_SQLITE_POOL_SIZE` | `1` | Integer 1–4; each store owner, sync and async combined; overflow always zero |
| `KLLM_SQLITE_WAIT_TIMEOUT` | `5` seconds | Finite positive number; maximum for admission, writer/fence wait and checkout/contention stage; no infinite blocking |
| `KLLM_SQLITE_SHUTDOWN_TIMEOUT` | `10` seconds | Finite positive number; total lifecycle-attempt budget beginning before producer quiescence; expiration is explicit failure |
| Internal admitted-operation cap | `32` | Bound active plus queued work before submitting to executor; no bypass for sync callers |
| Internal async worker count | `max(2, pool_size)` | Lazily allocated per opened store; connection capacity remains independently bounded |

Constructor injection overrides defaults for tests/internal use. Options are snapshotted at initialization; a restart/replacement is required to change them. Admission waiters are cancellable and never own a connection; bound the owner-managed waiter list as well and reject excess admission with unavailable rather than accumulating it indefinitely. Acquisition uses interruptible short waits with an overall configured deadline. Network's existing cross-process execution fence keeps its current five-second domain deadline; it is not extended by this environment option.

A separate per-store writer gate coordinates mutation/retention and uses finite waiting. No owner-state lock is held while waiting for a lease, writer gate, SQL, file fence, worker, or external operation. Define and test lock order: owner admission → domain fence/writer gate → connection lease; release in reverse. State/commit locks are short critical sections.

## Transaction semantics

- Acquire a lease for the entire unit. Begin exactly once with the requested mode: deferred for consistent reads, IMMEDIATE for writes.
- sqlite3 implicit transaction emission is disabled; the infrastructure owns the Core begin event and commit/rollback. Never issue a second BEGIN after Core autobegin.
- Helpers receive their connection explicitly. Nested coverage/filter/meta calls do not acquire a second lease. Reentrant compositions reuse admission accounting but do not accidentally inherit an unrelated transaction.
- No connection object, open cursor, or lazy query result may escape the unit. Fully consume rows inside ownership.
- Failures before commit roll back the whole write unit. Rollback/reset failure invalidates the lease; it cannot return to the pool as usable.
- No uncommitted changes can be read by another operation. Enable required connection-local integrity settings on every physical connection.
- No database transaction or lease spans OS mutation or a network await. Network execution fence is a separate ownership mechanism, not a database transaction.
- Initialization and known upgrades are serialized across processes under a secure dedicated initialization lock. Do not acquire initialization locks from within ordinary transactions.

## Cancellation and outcome arbitration

1. Before admission/submission: cancellation removes the waiter/job; connection count is unchanged.
2. While queued: cancel/remove the job before execution and release its admission token exactly once.
3. Running statements/pre-commit: signal cancellation. Worker checks it, requests safe statement interruption if still the same lease generation, and rolls back. Await/drain cleanup before marking resources released.
4. The worker tests cancellation and transitions PRE_COMMIT → COMMITTING under the same commit lock. Either cancellation wins with rollback or commit wins; both cannot occur.
5. COMMITTING: do not interrupt commit. Track until driver outcome resolves. Caller cancellation is not success and is not proof of rollback. If confirmed, record the durable outcome in sanitized operation tracking; if unresolved, classify OUTCOME_UNKNOWN and require read/reconciliation by existing IDs. Never retry a mutation automatically.
6. Before lease reuse, clear progress/interrupt handlers and verify transaction reset. An interrupt request verifies operation ID plus lease generation under ownership synchronization, so it cannot target a newer consumer.
7. Protected network compositions keep shielded intent/mutation/observation semantics. Caller cancellation requests reconciliation; it cannot stop mandatory journal completion or transfer the fence to another thread.

An active Future remains owned after the awaiting task disappears. The bridge holds and observes it, including exceptions, until terminal cleanup. Do not use Future.cancel, to_thread cancellation, or executor `wait=False` as proof of stopped work. Production callbacks have bounded external waits/checkpoints; pathological OS/driver hangs result in explicit failed close rather than a false CLOSED state.

## Usage-specific units and acknowledgement

Validated insertion of a batch is one atomic write unit. Retention/truncation cleanup follows as separate maintenance units; each grouped deletion/metadata change is atomic, with physical-byte checks between completed units. A post-insert retention failure cannot roll back committed insertion. Keep existing acknowledgement policy: accepted/duplicates/save metadata is returned only when the current recording operation succeeds; otherwise existing unavailable/error state remains truthful and retained records are not reset. A retry uses existing reading uniqueness, not a new retry mechanism.

Synchronous queries gather all related coverage/page fields on one lease/read snapshot. Preserve cursor serialization/signature secret and ordering. In-memory recorder metadata changes use short state locking after durable outcomes; state locks never enclose blocking database access.

## Network-specific fences

Keep `NetworkStore.execution()`'s secure file lock, reentrant thread lock, and thread-local depth. Acquire, nest and release it on one thread. Protected compositions cover committed intent, external effect, observation and recovery state; each short SQL segment obtains/releases its own lease. `LocalNetworkExecutor` prepare/apply remain one shielded offload. Store conflict, phase fencing and idempotent duplicate semantics remain unchanged.

## Lifecycle contract

- Start one absolute monotonic deadline on entry to application shutdown, failed-start cleanup or directory replacement, before producer quiescence. Use the minimum configured shutdown timeout among affected owners; each phase and nested close receives that same deadline and remaining budget. A standalone owner close starts its own deadline only when no outer deadline exists.
- Quiesce collection, live-feed refresh scheduling, and new service orchestration first. Await source jobs and protected reconciliation only within the remaining deadline; already admitted protected compositions retain the access needed for their existing intent/effect/observation sequence. No phase obtains a fresh timeout.
- Set CLOSING, reject new operations, cancel queued/acquisition waiters, request cancellable active work to stop, and drain all tracked leases/jobs.
- Close workers and then dispose idle connections. Remove the owner registry entry only after successful closure. Initialization failure uses this cleanup for partially allocated resources.
- CLOSED means zero physical connections, leases, pending jobs, acquisition waiters and owned workers. Verify actual accounting, not only a disposal call.
- Deadline expiration in any phase, including producer quiescence or protected reconciliation, raises sanitized PersistenceError within the total attempt budget. Mark affected owners CLOSING/unavailable, reject new external admission, and leave outstanding jobs/fences quarantined and tracked until settlement. Allow only continuation of already admitted protected compositions to complete necessary observation/reconciliation; do not interrupt commit, kill workers, release an owned fence prematurely or admit a replacement generation. This abnormal result is a test failure for any scenario requiring successful shutdown; no cleanup success is claimed.
- Path replacement quiesces/drains old ownership before publishing a new generation. If old ownership cannot close, refuse replacement rather than permit two owners to mutate the same store. Do not share a pool across forked processes or unrelated loop lifetimes.

## Errors and observability

Preserve original sqlite3/OSError exceptions where caller contracts already use them, including damaged reading/timestamp checks. Translate Core DBAPI wrappers centrally without their raw parameterized text. Preserve StoreConflict and validation/lookup errors. Shared PersistenceError covers admission timeout, closed owner, failed cleanup and unknown outcome; map it at every affected route/service/collector boundary.

Diagnostic context may include domain, sanitized operation kind/ID, owner generation, wait category and confirmed/unknown phase. Do not include SQL parameter values, JSON record contents, keys, tokens, full private paths or signing data. Maintain low-cost counters for tests: created/closed/in-use connection counts, admitted/queued/waiting jobs and worker ownership. No new public metrics endpoint is required.
