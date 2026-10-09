# Feature Specification: Async SQLite Access

**Feature Branch**: `main` (no branch-creation hook configured)

**Created**: 2026-10-08

**Status**: Draft

**Input**: User description: "introduce async compatible ORM(or query builder) and lightweight connection pooling for easily maintaining data inside sqlite"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Maintain stored data consistently (Priority: P1)

As a maintainer, I can define and change how usage history and managed-network records are stored and retrieved through a consistent data-access approach, so routine changes do not require duplicating query construction and connection handling throughout the application.

**Why this priority**: Easier, safer maintenance is the primary purpose of the feature, and both existing storage domains must benefit.

**Independent Test**: Exercise create, retrieve, update, delete, and filtered-list operations on temporary records in each storage domain. Make one representative stored-field change using the same conventions in both domains and verify existing behavior remains compatible.

**Acceptance Scenarios**:

1. **Given** an empty local data directory, **When** the application first saves usage and managed-network records, **Then** each storage domain is initialized and its records can be retrieved after restart.
2. **Given** existing records, **When** a maintainer adds a stored attribute and a filter that uses it in each domain, **Then** the change uses the same documented data-access conventions without adding connection-management logic to business operations.
3. **Given** records containing quotes or text resembling a query, **When** those values are saved and searched, **Then** they are treated as data and cannot alter unrelated records or query behavior.
4. **Given** existing usage history and managed-network state, **When** the application is upgraded, **Then** record identities, relationships, history pagination, and recovery information remain usable without deleting or manually recreating the stored data.

---

### User Story 2 - Keep the application responsive during storage work (Priority: P1)

As an operator, I can inspect status and use the dashboard while the application reads or writes local records, including when storage access is waiting, so local persistence does not stall unrelated work.

**Why this priority**: Async compatibility must result in responsive operation, including during contention, rather than merely changing how callers invoke storage operations.

**Independent Test**: Run concurrent history reads and record updates while deliberately delaying one storage operation. Verify unrelated status requests continue completing and all acknowledged writes are retained.

**Acceptance Scenarios**:

1. **Given** ongoing record collection, **When** multiple consumers request history while another storage operation is delayed, **Then** unrelated status work continues and history results remain correctly ordered and scoped.
2. **Given** asynchronous service consumers and existing synchronous or background consumers, **When** they access the same storage domain concurrently, **Then** both receive supported results without sharing another operation's uncommitted changes.
3. **Given** all available connections are occupied, **When** another consumer requests access, **Then** it waits within a documented finite limit or receives an actionable unavailable result while unrelated work continues.
4. **Given** a cancelled operation, **When** a later consumer accesses storage, **Then** the later operation is not blocked by an abandoned connection or transaction, and the cancelled operation is not reported as successfully saved without confirmation.

---

### User Story 3 - Preserve trusted state during contention and failure (Priority: P1)

As an operator, I can rely on saved history and network recovery state after failures, cancellations, and restarts, so adopting the new access approach does not weaken the application's existing safeguards.

**Why this priority**: Managed-network records govern ownership and recovery; incomplete or conflicting updates can affect operations beyond the database.

**Independent Test**: Force a multi-record update to fail before completion, race conflicting state updates, and restart against saved records. Verify atomic results, conflict rejection, retained records, and unchanged recovery decisions.

**Acceptance Scenarios**:

1. **Given** an operation that changes related records, **When** it fails or is cancelled before committing, **Then** none of its partial changes are visible and previously committed records remain intact.
2. **Given** two attempts using the same prior network revision or conflicting ownership, **When** both attempt an update, **Then** only the valid update is accepted and the other receives the existing conflict behavior.
3. **Given** a repeated operation identifier and unchanged request, **When** the operation is retried, **Then** existing idempotency behavior prevents duplicate effects; a conflicting request with that identifier is rejected.
4. **Given** concurrent processes attempting a protected network mutation, **When** they compete for ownership, **Then** the existing execution boundary still prevents overlapping intent, external mutation, and observation.
5. **Given** an unreadable store, invalid stored data, or exhausted local storage, **When** an operation cannot complete, **Then** the application reports unavailable or failed persistence with diagnostic context and does not replace saved state with empty healthy state.

---

### User Story 4 - Reuse and release bounded local resources (Priority: P2)

As an operator, I can run and restart the application without continuously accumulating database connections or requiring an additional storage service, so persistence remains lightweight for a local node.

**Why this priority**: Connection pooling should reduce repeated setup while retaining predictable resource use and cleanup.

**Independent Test**: Run repeated mixed reads and writes with a small connection limit, then repeat application startup and shutdown. Verify reuse, enforcement of the limit, isolation between data directories, and release of owned resources.

**Acceptance Scenarios**:

1. **Given** an initialized store, **When** successive operations finish and new ones begin, **Then** eligible connections are reused within the configured maximum rather than being created for every operation.
2. **Given** a configured connection limit, **When** concurrent demand exceeds that limit, **Then** the number of owned connections stays within it and unsuccessful operations receive bounded waiting or explicit failure.
3. **Given** a failed transaction, **When** its resources become available again, **Then** the next consumer receives a usable connection without leftover transaction state.
4. **Given** shutdown or a change of local data directory, **When** cleanup completes, **Then** no owned connections or pending access requests remain attached to the old store and later access cannot return its records.
5. **Given** an invalid resource limit or wait limit, **When** storage starts, **Then** it rejects the invalid setting with an actionable error before serving operations.

### Edge Cases

- Cancellation while waiting for a connection must remove the waiting request and consume no connection capacity.
- Cancellation during commit must not produce a false save acknowledgement; any indeterminate outcome must be reported and resolvable using the operation's existing identity or stored state.
- Shutdown during active work must have bounded completion, preserve committed records, and leave no reusable connection containing an unfinished transaction.
- Multiple initializers accessing a new or existing store must not race into partial initialization or destructive schema changes.
- Store locks held by another process must result in finite waiting and explicit failure without freezing unrelated work.
- Reusing connections must retain each store's required integrity, durability, and storage-budget behavior; the two stores need not use identical settings.
- Duplicate readings, stale revisions, invalid references, and conflicting operation identifiers must retain their existing acceptance or rejection behavior.
- Changing the local data directory must not reuse connections or records from the previous directory.
- An unsupported or damaged store must remain available for diagnosis; startup must not silently reset it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The application MUST provide one consistent, async-compatible ORM or query-builder approach for maintaining records in both existing application-owned SQLite domains: usage history and managed-network state. Either approach satisfies the request; both are not required.
- **FR-002**: Maintainers MUST be able to express creation, retrieval, updates, deletion, filtering, ordering, and bounded result retrieval through that approach, with documented conventions for stored-field changes and compound queries. Routine business operations MUST NOT manage connection acquisition or cleanup themselves.
- **FR-003**: Asynchronous storage operations MUST allow unrelated application work to progress during storage execution, connection waits, and contention. Existing synchronous and background consumers MUST retain a supported access path.
- **FR-004**: Connection pooling MUST reuse eligible connections, enforce a configurable finite maximum for each store owner, and provide a documented small default. It MUST operate within the node without requiring an additional storage service.
- **FR-005**: Connection acquisition and contention waits MUST have documented finite limits. Invalid limits MUST be rejected, and exhaustion MUST produce an explicit unavailable result rather than unbounded resource growth or waiting.
- **FR-006**: Each operation MUST retain exclusive ownership of its transaction until completion. Related changes MUST commit atomically or roll back on failure or cancellation before commit; concurrent consumers MUST NOT observe another operation's uncommitted state.
- **FR-007**: Connection and transaction resources MUST be released or safely retired after success, failure, cancellation, initialization failure, and shutdown. Reused connections MUST contain no abandoned transaction state. Shutdown MUST stop admission of new work, settle or cancel pending work within a documented finite limit, and close owned resources.
- **FR-008**: Existing database locations and committed records MUST remain usable after adoption without operator deletion or manual recreation. Any necessary storage-format change MUST be automatic, repeatable, and recoverable after interruption, and MUST reject unsupported formats without resetting data.
- **FR-009**: Usage history MUST preserve existing record validation, duplicate handling, lifetime/model associations, ordering, cursor behavior, retention, physical storage limits, and truthful recording/error status.
- **FR-010**: Managed-network state MUST preserve authority and channel checks, revision conflicts, uniqueness and reference constraints, idempotency, ownership and recovery journals, and the existing execution fence across processes and external mutations.
- **FR-011**: Persistence adoption MUST preserve existing observable CLI, control-interface, and dashboard behavior, including validation, conflict, and unavailable outcomes. Diagnostic failures MUST retain useful context without exposing credentials or private record contents.
- **FR-012**: Values supplied by callers MUST be handled as data rather than executable query instructions, and existing domain validation MUST continue to run before accepting writes.
- **FR-013**: Storage ownership MUST be isolated by local data directory and store. Restart or directory changes MUST NOT reuse connections belonging to a previous owner or allow cross-directory record access.
- **FR-014**: Maintainer documentation MUST explain the common data-access conventions, resource limits and defaults, transaction boundaries, lifecycle cleanup, supported caller styles, and how to diagnose contention or storage failures.
- **FR-015**: A cancelled operation MUST NOT be acknowledged as saved without confirmation. When interruption prevents determining whether a commit completed, the application MUST report that uncertainty and support checking the durable outcome rather than assuming failure and duplicating effects.

### Key Entities *(include if feature involves data)*

- **Storage domain**: A locally owned collection of related records with its own location, integrity requirements, lifecycle, and retention policy; usage history and managed-network state remain distinct.
- **Usage record**: A timestamped observation associated with a node, process lifetime, and optional model/context; participates in ordered, bounded history retrieval.
- **Managed-network record**: Durable identity, authority, network, assignment, operation, ownership, or recovery information with existing revision and relationship rules.
- **Storage operation**: A read or atomic group of changes with a defined outcome, ownership boundary, and failure or cancellation behavior.
- **Connection pool**: A bounded collection of reusable local connections owned by one store lifecycle, with finite acquisition waits and cleanup responsibilities.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All acceptance scenarios for creating, reading, updating, deleting, and filtering representative records pass in both storage domains. A representative stored-field/filter change in each domain follows one documented maintenance workflow and adds no connection-lifecycle handling to business operations.
- **SC-002**: Across 20 concurrent consumers performing mixed record reads and updates against 10,000 retained observations, at least 95% of unrelated status requests complete within 1 second. A deliberately delayed storage operation lasting 2 seconds does not stop unrelated status requests from meeting that target.
- **SC-003**: In compatibility checks against populated pre-change stores, 100% of retained record identities, relationships, pagination results, and recovery decisions match expected prior behavior, excluding only records removed by the existing retention policy.
- **SC-004**: Across 1,000 mixed operations and 100 application open/close cycles, owned connections never exceed the configured maximum, eligible connections are demonstrably reused, and every completed shutdown leaves zero owned connections and zero pending acquisition requests.
- **SC-005**: Every exercised failure and pre-commit cancellation leaves zero partial changes from the interrupted transaction. All confirmed saves remain readable after restart; all commit-outcome uncertainty is explicitly reported rather than acknowledged as a save.
- **SC-006**: All existing conflict, duplicate-request, history-retention, and network-recovery acceptance checks pass with the new access approach, including two competing processes attempting the same protected mutation.

## Assumptions

- Maintainers are the primary users of the data-access abstraction; operators benefit through responsiveness, reliability, and unchanged workflows. No new database-management screen or public query interface is requested.
- SQLite remains the embedded local persistence engine. External databases, distributed connection pools, and cross-node database sharing are outside scope.
- Both current application-owned SQLite stores are in scope. Existing JSON cluster state, model files, credential storage, logs, and privileged-helper storage remain outside this persistence change.
- An ORM or a query builder is acceptable. Library selection, dependency justification, exact pool defaults, and integration mechanics will be decided in planning against the repository's portability and simplicity requirements.
- Existing synchronous and background consumers remain supported; the request does not require converting the whole application to asynchronous execution.
- Existing retention, durability, security, authorization, and supported-platform requirements remain authoritative. No new product data or retention policy is introduced.
- Responsiveness and resource outcomes will be checked with an isolated, reproducible local workload and a recorded environment. The proposed concurrency and latency targets are acceptance defaults, not measured performance claims or guarantees for all hardware.
- Implementation depends on the existing usage and managed-network domain contracts and application lifecycle. Any chosen dependency must follow the existing reproducible packaging and supported installation workflow.
