# Data Model: Async SQLite Access

**Status**: Phase 1 design. Existing logical and physical record formats are retained; new entities below describe process-local resource ownership, not product tables.

## Existing usage domain

| Table | Fields and keys | Relationships / invariants |
|---|---|---|
| `nodes` | `node_id TEXT PK`, `host TEXT NOT NULL`, `role TEXT NOT NULL`, `context TEXT NOT NULL DEFAULT '{}'` | Logical node owner for readings/lifetimes; context stays serialized JSON |
| `lifetimes` | `lifetime_id TEXT PK`, `node_id TEXT NOT NULL`, `pid INTEGER NOT NULL`, `role TEXT NOT NULL`, `started_at TEXT NOT NULL`, nullable `ended_at TEXT`, `state TEXT NOT NULL`, nullable `model TEXT` | Identifies a process lifetime; retain running/ended/unobserved transitions and current timestamp rules |
| `associations` | `lifetime_id TEXT NOT NULL`, `model TEXT NOT NULL`, `observed_from TEXT NOT NULL`, nullable `observed_until TEXT`; composite PK `(lifetime_id, observed_from)` | Logical model interval within a lifetime; preserve existing interval construction/ending |
| `readings` | `id INTEGER PK`, `lifetime_id TEXT NOT NULL`, `node_id TEXT NOT NULL`, `observed_at TEXT NOT NULL`, `received_at TEXT NOT NULL`, `payload TEXT NOT NULL` | Unique `(lifetime_id, observed_at)`; preserve ID/order and JSON validation |
| `meta` | `key TEXT PK`, `value TEXT NOT NULL` | Cursor secret, base bytes, last successful save, storage truncation metadata and existing keys |

Preserve indexes `readings_time(observed_at, id)` and `readings_filter(node_id, lifetime_id, observed_at, id)`. Relationships are logical in the existing schema; do not introduce physical foreign keys or stricter nullability as an incidental ORM conversion. Core declarations must reproduce actual legacy nullability, including SQLite primary-key behavior; verify with schema inspection.

Timestamp values remain current UTC text, not a new date type/format. Keep existing validation: node and process identity, observation window/future allowance, metadata size, valid reading shape and process lifetime. Default retained history is 24 hours with 256 MiB physical storage budget; smaller supported budgets remain valid. File accounting includes DB and sidecars.

**Transitions**: new process observation creates/updates its lifetime; end/unobserved closes appropriate associations; duplicate observation is ignored according to current contract; pruning removes old/excess readings and unreferenced metadata. A cancelled ingestion before commit changes none of those records. Committed ingestion is not undone by later retention failure.

## Existing managed-network domain

Most record bodies remain validated Pydantic JSON in `body TEXT NOT NULL`. Do not change key encoding, UUID text, serialization, or denormalized fence columns.

| Table | Fields and keys | Constraints / meaning |
|---|---|---|
| `metadata` | `key TEXT PK`, `value TEXT NOT NULL` | Local identity and cluster binding, plus current metadata |
| `peers` | `node_id TEXT PK`, `role TEXT NOT NULL`, `body TEXT NOT NULL` | Partial unique `one_master(role)` where role='master' |
| `node_directory` | `node_id TEXT PK`, `body TEXT NOT NULL` | Current node network identity |
| `authority` | `binding TEXT PK`, `body TEXT NOT NULL` | Current master authority per binding |
| `channels` | `peer TEXT PK`, `binding TEXT NOT NULL`, `term INTEGER NOT NULL`, `session TEXT NOT NULL`, `channel TEXT NOT NULL` | Accepted channel fence |
| `networks` | `id TEXT PK`, `binding TEXT NOT NULL`, `state TEXT NOT NULL`, `revision INTEGER NOT NULL`, `body TEXT NOT NULL` | Partial unique `one_live_network(binding)` where state!='stopped' |
| `assignments` | `network TEXT FK networks(id)`, `node TEXT`, `address TEXT NOT NULL`, `revision INTEGER NOT NULL`, `body TEXT NOT NULL`; PK `(network,node)` | Unique `(network,address)` |
| `operations` | `id TEXT PK`, `network TEXT FK networks(id)`, `binding TEXT NOT NULL`, `request TEXT NOT NULL`, `digest TEXT NOT NULL`, `revision INTEGER NOT NULL`, `body TEXT NOT NULL` | Unique `(binding,request)` |
| `results` | `operation TEXT FK operations(id)`, `node TEXT`, `phase_sequence INTEGER`, `body TEXT NOT NULL`; PK `(operation,node,phase_sequence)` | Phase result identity |
| `journals` | `id TEXT PK`, `network TEXT FK networks(id)`, `operation TEXT FK operations(id)`, `node TEXT NOT NULL`, `phase TEXT NOT NULL`, `body TEXT NOT NULL` | Ownership and restoration intent/observation |
| `commands` | `key TEXT PK`, `digest TEXT NOT NULL`, `state TEXT NOT NULL`, nullable `result TEXT` | Idempotent command intent/completion |
| `reservations` | `id TEXT PK`, `network TEXT FK networks(id)`, `state TEXT NOT NULL`, `body TEXT NOT NULL` | Partial unique `one_live_run(network)` where state!='released' |
| `leases` | `id TEXT PK`, `network TEXT FK networks(id)`, `node TEXT NOT NULL`, `generation INTEGER NOT NULL`, `state TEXT NOT NULL`, `body TEXT NOT NULL` | Partial unique `one_live_lease(network,node)` where state!='released' |
| `evidence` | `network TEXT FK networks(id)`, `node TEXT`, `body TEXT NOT NULL`; PK `(network,node)` | Durable verification evidence; no reusable live deadline |

Retain nullable columns where existing DDL does not declare NOT NULL. Do not add cascade actions or change conflict resolution. Run `foreign_keys=ON` on each connection. Current Pydantic validators and store methods remain the authority for state transitions: revisions start/increment under existing rules; authority/channel supersession fences old requests; request identity and digest reconcile duplicates; released reservations/leases are terminal as defined today; journal tombstones/recovery remain preserved.

Receive-relative monotonic admission/evidence deadlines are zeroed before persistence and invalid after restart. Authority adoption/recovery does not become valid merely because records were loaded. Check phase/ownership/endpoint constraints at existing mutation boundaries.

## Process-local ownership entities

### SQLiteOptions

Immutable fields: `pool_size` (default 1, integer 1–4), `wait_timeout` (default 5 seconds, finite positive), `shutdown_timeout` (default 10 seconds, finite positive). Internal constants: overflow 0, admitted operation maximum 32. Parsed before publishing any owner. Options cannot mutate a live engine.

### StoreOwner

Identity: canonical path, domain and monotonic owner generation. Attributes: options, engine/pool, supported schema profile, writer/init gates, operation registry, source-job accounting, and sanitized last failure. Registry synchronization never encloses SQL, pool waits, external mutation or worker joins.

State transitions: `NEW → INITIALIZING → OPEN → CLOSING → CLOSED`. Initialization failure runs cleanup and yields a terminal failed/unavailable construction; no usable half-owner is published. Close failure in any lifecycle phase retains CLOSING and explicit failure context until cleanup actually finishes. The lifecycle coordinator creates a single absolute monotonic deadline before producer quiescence using the minimum configured timeout among affected owners, and passes it unchanged through reconciliation and owner close; it is transient operation context, not a persisted field. CLOSED requires no live leases, queued work, acquisition waiters, or owned workers/connections. Directory replacement cannot publish a new owner while its old generation is unsettled.

### Operation

Identity: internal operation ID plus any existing durable reading/command/request identifiers; owner generation. Attributes: kind (read/write/composition), admission/checkout deadlines, cancellation token, current lease identity, commit arbitration lock, outcome and sanitized error.

Transitions: `WAITING → QUEUED → RUNNING → PRE_COMMIT → COMMITTING → COMMITTED`; read completion goes RUNNING → COMPLETED. Failure/cancellation before commit goes through ROLLING_BACK → FAILED/CANCELLED. Commit failure with unresolved result goes to OUTCOME_UNKNOWN. Terminal tracking retains no connection; enough sanitized context remains for diagnostics. A composition may have several separately committed domain units; cancellation cannot erase completed units.

### ConnectionLease

Identity: owner generation, connection identity and lease generation. Belongs exclusively to one running operation/worker. Acquired → IN_USE → CLEANING → RETURNED, or CLEANING → INVALIDATED when reuse is unsafe. The raw connection is not a shared store attribute. Progress/interrupt handlers are scoped to the lease and removed before RETURNED. A committing/unknown lease is never reassigned until completion or invalidation is confirmed.

## Adoption and future maintenance

No new persisted tables, metadata version, indexes, or field changes are required for this feature. Initializers inspect and validate current required schema, create missing objects only for known compatible states, and apply the existing additive context upgrade repeatably. Preserve extra additive columns when harmless; reject missing/changed structures that cannot be safely adopted. Keep unsupported files for diagnosis.

For later stored-field changes: update the appropriate Core declaration, define an explicit repeatable compatibility upgrade in that domain, keep domain validation/serialization coherent, and add old-file adoption plus filtered-query behavior checks. Connection handling belongs entirely to the shared owner. An illustrative field-change exercise for SC-001 uses a temporary test schema; it does not add unrequested product fields.
