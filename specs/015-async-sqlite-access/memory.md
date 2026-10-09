# Feature Context: Async SQLite Access

**Prepared**: 2026-10-08
**Request**: Introduce an async-compatible ORM or query builder and lightweight connection pooling to simplify SQLite data maintenance.

## Scope and constraints

- Existing application-owned SQLite stores are usage history and managed-network state. Reuse one consistent data-access approach across both; preserve their separate files and domain responsibilities.
- Support asynchronous service operations and existing synchronous/background consumers. Do not make the entire application asynchronous merely to adopt the persistence layer.
- Preserve current stored data, external CLI/control/dashboard behavior, history ordering and cursors, retention limits, and network authority/revision/idempotency/recovery safeguards.
- Preserve non-database cluster state and credentials in their existing storage. No general conversion of JSON files to SQLite.
- Library selection, connection limits, transaction integration, and migration mechanics belong in planning. An ORM and a query builder are alternatives, not two required layers.

## Current evidence

- `src/kllm/usage_store.py` owns `usage.sqlite3`, a synchronous connection guarded by a reentrant lock, history writes, and physical storage pruning. `usage_store_queries.py` receives that connection and lock.
- `src/kllm/agent/usage_runtime.py` lazily owns/closes the usage store; collectors and history handlers consume it.
- `src/kllm/managed_network/store.py` owns `managed-network.sqlite`, explicit write transactions, foreign-key/uniqueness constraints, and a separate cross-process execution fence across intent, OS mutation, and observation.
- Usage uses DELETE journaling with FULL auto-vacuum; network state uses WAL. A shared access layer must not assume one global storage policy fits both.
- Callers currently recognize SQLite/storage errors and network `StoreConflict`; preserve equivalent boundary behavior.

## Governance and retrieval

- Read `.specify/memory/workflow.md`, `constitution.md`, and `architecture_constitution.md` before feature authoring.
- Read repository memory index plus A1 structural standards and D1 facade patch-surface decision through the workflow's markdown fallback. `PROJECT_CONTEXT.md` remains an unfilled scaffold; it provides no additional product facts.
- Source discovery used CodeGraph first; focused text search verified the application SQLite owners and callers.
- Memory MCP/cache tools are unavailable. The required memory-md specify hook was executed as an in-session markdown fallback; no cache refresh or token-saving estimate is claimed.
- Constitution mentions the older `python/kllm/` path; current user instructions and architecture standards require `src/kllm/`. Preserve the current layout without amending governance.

## Planning decisions (2026-10-08)

- Select SQLAlchemy Core 2.0 with stdlib sqlite3 and QueuePool; provide async compatibility through a bounded whole-operation worker bridge. Retain synchronous consumers and Pydantic domain models.
- Default capacity is one connection per store, no overflow, configurable up to four; finite admission/checkout/contention waits and tracked cancellation cleanup apply to both caller styles.
- Explicit write transactions use BEGIN IMMEDIATE; nested helpers share the operation-bound connection. Do not introduce a second lease/transaction inside query helpers.
- Usage ingestion and retention are distinct commit units. Preserve committed ingestion on retention failure and retain truthful acknowledgement/recorder behavior.
- Protected network mutation fences stay on one thread across committed intent, OS mutation, and observation. No lease spans external mutation.
- Quiesce producers and reconcile protected work before closing admission; drain before disposal. A failed close remains unavailable and tracked until actual cleanup; it does not claim zero resources.
- Existing schemas/files stay intact, including the known context-column upgrade. Historical index/storage-budget evidence was confirmed in specs/011-codebase-performance/validation.md; no incidental indexes are planned.
- Phase 0 research and Phase 1 plan/data model/contracts/quickstart were complete before implementation. Implementation evidence and limitations are maintained in `validation.md`.

## Implementation outcome (2026-10-09)

- SQLAlchemy Core and the shared bounded SQLite owner are adopted by usage and managed-network stores. Async callers use bounded tracked worker operations; synchronous/background paths remain supported.
- Local macOS validation completed: 1,153 passed, 14 skipped; Ruff, package/native/dashboard build, legacy compatibility, cancellation, lifecycle/resource-cycle checks, and controlled responsiveness workload passed. Linux/Windows and alternate supported Python versions remain follow-ups; see `validation.md`.
- No durable project memory was captured. This feature-local file is scratch context and must not be treated as cross-feature memory.

See research.md for decisions/alternatives and contracts/ for exact ownership and external compatibility rules. No unresolved product-scope clarification remains.
