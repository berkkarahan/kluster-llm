# Research: Async SQLite Access

**Date**: 2026-10-08
**Status**: Phase 0 complete. Design decisions below resolve the library, execution, pooling, transaction, compatibility, and lifecycle questions.

## 1. Query abstraction and dependency

**Decision**: Adopt SQLAlchemy Core from its maintained 2.0 series (`SQLAlchemy>=2.0.54,<2.1`, exact resolution in `uv.lock` during implementation), stdlib `sqlite3`, and one explicit `QueuePool` per store owner. Retain Pydantic domain models. Provide async compatibility through an owned bounded worker bridge around complete synchronous operations.

**Rationale**: Core satisfies query construction, parameter binding, SQLite upsert, and pooling in one dependency. Current storage mixes synchronous collectors/routes with async services and thread-affine network mutation fences; a single synchronous engine avoids two competing capacity/lifecycle models. This is an application integration decision, not a claim that Core's synchronous driver is inherently asynchronous.

**Alternatives considered**:

- SQLAlchemy async plus `aiosqlite` and a second synchronous engine: adds a driver and requires coordinating two pools and execution models. Useful for an async-only application, but does not simplify these existing consumers.
- `aiosqlite` alone: async execution without the requested common query-builder abstraction.
- PyPika plus a custom pool: query building is available, but pooling, binding, and driver integration would require more custom infrastructure.
- An async ORM: duplicates existing domain models and adds session/relationship semantics unnecessary for these record stores.
- Plain `sqlite3` helpers: smallest dependency footprint but does not meet the requested established ORM/query-builder capability.

**Primary evidence**: [SQLAlchemy SQLite dialect](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html), [pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html), [aiosqlite](https://aiosqlite.omnilib.dev/en/stable/), [PyPika parameterized queries](https://pypika.readthedocs.io/en/latest/2_tutorial.html#parametrized-queries). The documented driver and pool capabilities inform this choice; the fit to Kluster is our design assessment.

## 2. Pool ownership, capacity, and caller styles

**Decision**: One engine per canonical store path and owner generation, shared by synchronous operations and async bridge workers. Default pool size 1, permitted sizes 1–4, `max_overflow=0`; finite checkout wait 5 seconds. Owner admission is capped at 32 operations including active operations, with a 5-second admission wait. Async dispatcher has `max(2, pool_size)` workers per opened store, allocated lazily. Synchronous callers execute inline through the same admission/lease accounting; worker reentrancy never submits another job.

**Rationale**: The default preserves present serialization and reuses connections. Increased capacity permits independent reads but does not make parallel writes a goal. Admission happens before executor submission so its otherwise unbounded queue cannot grow. Process-local ownership does not claim a cross-process global connection limit.

**Alternatives considered**: Unbounded overflow, a process-wide pool keyed only by filename, separate sync/async pools, and one connection per request violate bounded ownership or add unnecessary coordination.

**Evidence**: Pool size plus overflow determines capacity; disposal does not close active checkouts. Those documented constraints require explicit owner accounting and draining. [SQLAlchemy pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)

## 3. Transactions, nesting, and query maintenance

**Decision**: Core table declarations preserve existing SQLite column types, nullability, keys, indexes, and text serialization. Use Core expressions for routine CRUD, filters, joins, ordering, counts, and SQLite upserts. Limit driver SQL to schema compatibility inspection, PRAGMAs, explicit transaction control, and physical maintenance.

Use explicit transaction control compatible with Python 3.10+: disable sqlite3 implicit BEGIN behavior at connect and emit one intentional BEGIN through the infrastructure transaction hook. Repository writes use `BEGIN IMMEDIATE`; consistent multi-query reads use deferred BEGIN. Never combine implicit autobegin, manual BEGIN, and a second transaction manager. Nested query helpers receive the same operation-bound connection; accidental nested public write transactions are rejected instead of silently committing or acquiring another lease.

**Rationale**: The usage query object currently retains a raw connection and calls its own coverage/filter helpers. Reacquisition deadlocks at pool size one and splits snapshots at larger sizes. Explicit transactions also avoid driver-version-specific behavior.

**Alternatives considered**: Full ORM conversion, ambient shared global connections, automatic savepoints for every helper, and dependence on Python 3.12-only connection configuration.

**Primary evidence**: [SQLAlchemy explicit SQLite transaction control](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#enabling-non-legacy-sqlite-transactional-modes-with-the-sqlite3-or-aiosqlite-driver), [Core connection use](https://docs.sqlalchemy.org/en/20/core/connections.html). Exact repository transaction boundaries are specified in the contracts.

## 4. Cancellation and truthful durable outcomes

**Decision**: Track queued/running/pre-commit/committing/terminal states per admitted operation. Cancellation before commit sets a cooperative token and rolls back; worker owns its connection until cleanup actually finishes. Token checking and the commit transition share a lock so cancellation cannot win and then be followed by commit. Cancellation after that transition does not interrupt commit or imply rollback; resolve the durable result, keep caller cancellation, and log a sanitized confirmed/unknown outcome using existing record/operation identifiers. Unknown outcome maps to unavailable, never a false success or automatic mutation retry.

Install operation-scoped SQLite progress checks and guarded interruption for cancellable statements. Verify lease generation under the owner lock before interrupting; remove handlers before reuse. Do not expose arbitrary unbounded callbacks through the production bridge. Supported callbacks are bounded repository/service operations with checks between statements and finite external waits.

**Rationale**: Thread cancellation alone cannot stop running work. A cancelled await must not release a connection to a second operation while its first worker is still executing.

**Alternatives considered**: Bare `asyncio.to_thread`, cancelling a Future and immediately returning its lease, generic retries of failed writes, and interrupting a connection without ownership verification.

**Primary evidence**: [Python futures cancellation](https://docs.python.org/3/library/concurrent.futures.html), [Python 3.10 sqlite3 interrupt/progress support](https://docs.python.org/3.10/library/sqlite3.html#sqlite3.Connection.interrupt). Operation tracking/commit arbitration are application design responsibilities beyond those primitives.

## 5. Domain-specific safety boundaries

**Decision**: Preserve usage DELETE/FULL/FULL-auto-vacuum and network WAL/FULL/foreign-key policies. Initialize persistent settings/schema before admitting consumers; apply connection-local settings on every new connection. Keep current files and schemas, including the known additive `nodes.context` upgrade. No new indexes or schema redesign are needed.

Usage ingestion is one atomic batch; retention follows as separate atomic maintenance units with existing physical-size checks and truncation metadata. If retention fails after ingestion commits, preserve the committed batch and surface recorder/unavailable state; do not claim the batch rolled back. Cancel only at defined boundaries; a committed retention unit remains durable. Keep accepted counts, duplicate semantics, cursor secrets, and last-successful-save behavior compatible.

Network `execution()` remains a same-thread reentrant OS/file fence spanning intent, external mutation, and observation. Offload a complete protected operation once; its internal store methods remain synchronous. Never hold a database transaction/lease across an OS mutation or network await. The existing executor's protected prepare/apply cancellation shielding remains authoritative; intent and reconciliation must finish even when the caller disappears.

**Rationale**: Current source demonstrates these boundaries in `usage_store.py`, `usage_store_queries.py`, `managed_network/store.py`, and `managed_network/executor.py`. They are not replaceable by pooling.

**Alternatives considered**: Unifying both journal policies, making every store call an await, holding a transaction across OS work, and promising rollback of already committed ingestion.

**Historical evidence**: `specs/011-codebase-performance/validation.md` records a rejected history-index candidate with higher insertion cost/physical size. This older result justifies avoiding incidental indexes; it is not a fresh performance measurement.

## 6. Shutdown, directory replacement, and initialization

**Decision**: Quiesce external producers and live-feed scheduling, retain access needed for active protected-operation reconciliation, then close store admission, cancel queued operations, request cancellable active work to stop, drain leases, close worker ownership, and dispose idle connections. The default whole-lifecycle budget is 10 seconds, starting before producer quiescence. Compute one absolute monotonic deadline using the minimum timeout of affected owners and pass it through quiescence, reconciliation and nested closes without resetting it. Expiry in any phase reports failure within that budget, rejects external admission and retains tracked continuation access only for already admitted protected work. SQL busy waits remain at most 5 seconds and long statements support progress cancellation. Failed startup and directory replacement use the same deadline contract.

A deadline expiration produces an explicit close failure: owner remains unavailable in CLOSING with active work quarantined and tracked until it settles. It never reports CLOSED/zero resources or allows a replacement owner for the same path while old work can mutate it. This is failure handling for abnormal stuck work, not a relaxation of normal shutdown acceptance criteria. No finite-thread-kill guarantee is claimed.

Directory replacement must drain the old owner before publishing the new one. Use a short registry lock only for state transitions, not while waiting for SQL or workers. Initialization is serialized in-process and by a secure per-store file fence across processes; inspect schema before mutation and repeatably apply only known upgrades. Unsupported/corrupt files are never deleted or reset.

**Alternatives considered**: `dispose()` as the whole close operation, `shutdown(wait=False)` as proof of cleanup, swapping owners before drain, or adopting a process-isolation framework to kill threads.

**Primary evidence**: [pool disposal limitations](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.QueuePool.dispose), [Python executor shutdown](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.shutdown). Quiescence and failure-state rules are our integration design.

## 7. Configuration, errors, packaging, and validation

**Decision**: Add immutable `SQLiteOptions` with constructor injection for tests and configuration from `KLLM_SQLITE_POOL_SIZE` (1), `KLLM_SQLITE_WAIT_TIMEOUT` (5 seconds), and `KLLM_SQLITE_SHUTDOWN_TIMEOUT` (10 seconds). Reject malformed/non-finite/nonpositive settings; pool size range is 1–4. Admission capacity 32 is an internal fixed bound, not another public option. Read configuration before publishing owners; changing it requires owner restart.

Translate SQLAlchemy DBAPI wrappers at the shared boundary to original sqlite3 errors where applicable, preserving `StoreConflict`, validation/lookup errors, `OSError`, and existing HTTP 400/404/409/503 contracts. New admission/closed/unknown-outcome failures use `PersistenceError` mapped at all affected boundaries. Never log SQL parameters, JSON bodies, or unsanitized wrapper exception text. No automatic write retries.

Update manifest and uv lock together, rebuild with the supported installer/build command, and verify package/native imports. Tests must use real temporary SQLite files, HTTP boundaries, and two-process fences. Test default capacity one and larger capacity. Record the SC-002 workload and before/after latency; no speedup is assumed.

**Alternatives considered**: Dashboard pool controls, new persistence API routes, a migration-framework dependency, and broad database retuning are outside scope.

## Research execution and limitations

Two read-only research agents independently checked official library sources and current integration code, using CodeGraph before source discovery. Root review reconciled their recommendations. No application code, dependencies, real stores, or durable project memory were changed. No runtime tests, packaging builds, responsiveness measurements, or physical cluster checks were run in this planning phase. All initial technical unknowns have design decisions; remaining work is implementation and verification, not product clarification.
