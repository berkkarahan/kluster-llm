# Local SQLite maintenance

Kluster uses SQLAlchemy Core as a query builder with the standard-library SQLite driver.
`UsageStore` and `NetworkStore` retain validation and domain behavior; `SQLiteStore`
owns each file's bounded pool and transaction resources. Existing database files remain
in place. JSON cluster state, model files, credentials and privileged-helper storage
keep their existing formats.

Declare columns and constraints in `src/kllm/usage_schema.py` or
`src/kllm/managed_network/schema.py`. Routine CRUD/filter/order/join/upsert operations
use Core expressions with bound values. Keep storage types, nullability, primary and
compound keys, partial indexes, UTC strings and Pydantic JSON serialization unchanged
when changing access code. Never interpolate caller values into SQL.

A synchronous `read(callback)` or `write(callback)` receives one Core connection;
consume query results inside the callback and return records/scalars. Async callers
use `aread(callback)`, `awrite(callback)`, or `arun(callback, ...)`; the bridge
admits work before submission and runs the full unit of work on the store's bounded
worker pool. Cancellation never releases a connection before its worker finishes.
`initialize(factory)` tracks cold owner construction and reclaims unattached owners
if the awaiting caller is cancelled. A compound history response uses
`UsageStore.snapshot()` so nested read helpers share one lease
and snapshot, including at pool capacity one. Helpers participating in a write receive
its connection; public nested write transactions are rejected. `maintenance()` is an
explicit scoped lease for known schema upgrades and test/fault seeding, not a permanent
shared `.db` connection.

Writes begin IMMEDIATE and commit atomically. Multi-query reads begin a deferred
transaction. sqlite3 implicit transaction emission is disabled; infrastructure owns
BEGIN/commit/rollback. Usage ingestion commits before retention. Each retention unit
commits deletion and truncation metadata together, with physical-byte checks between
units. A retention failure can leave earlier ingestion committed and must not be
reported as its rollback. Existing reading identity deduplicates subsequent retries.
Before commit, cancellation interrupts SQLite and rolls the unit back. Once the
commit boundary wins, ownership remains until SQLite confirms commit or reports an
unknown outcome. Unknown outcomes require inspection through the existing durable
identifier; never retry the mutation automatically.

Usage retains DELETE journaling, FULL synchronization and FULL auto-vacuum; network
state retains WAL, FULL synchronization and foreign-key checks on every connection.
Do not apply one global journal policy or add incidental indexes: the history budget
counts the database and its journal/WAL/SHM sidecars.

The bounded acquisition order is operation admission → network execution fence or
usage writer gate → pool lease → transaction. A network OS mutation holds the
same-thread execution fence from durable intent through observation, but releases
its SQL lease before any platform or socket await. Usage resource-state locks never
span a pool wait, SQL statement, worker join or owner close. Shutdown uses one
absolute monotonic deadline for producer quiescence, protected reconciliation,
operation drain and pool disposal. If the deadline expires, the owner remains
`CLOSING` and unavailable until tracked work settles; do not publish a replacement
for that file or report shutdown success.

For a future field change, update the domain's Core declaration, add a small explicit
repeatable compatibility upgrade before schema validation, preserve domain validation
and serialization, and test reopening an old file plus a query filtering that field.
Schema inspection and known additive DDL belong at initialization; `create_all` alone
is not a migration strategy. Unknown/incompatible schemas remain on disk for diagnosis.
`tests/test_sqlite_integration.py` exercises the same field/filter workflow against
private extended test schemas for both domains without adding product fields.

The same store instance serves synchronous collectors and async HTTP/orchestration
callers, so both consume the same finite admission bound. Call `aclose(deadline=...)`
when an async lifecycle owner closes a store; synchronous maintenance/test scopes
use `close()` and must not retain a lease beyond their context manager.

See `specs/015-async-sqlite-access/contracts/persistence.md` for the state and
error contracts, and `specs/015-async-sqlite-access/validation.md` for the exact
platform and workload evidence currently captured.
