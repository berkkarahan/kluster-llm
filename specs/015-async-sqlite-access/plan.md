# Implementation Plan: Async SQLite Access

**Branch**: `main` | **Feature identifier**: `015-async-sqlite-access` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-async-sqlite-access/spec.md`

`setup-plan.sh --json` resolved the correct feature directory and returned `015-async-sqlite-access` as its logical BRANCH fallback. The actual Git branch remains `main`; this planning command does not create or switch a branch.

## Summary

Adopt SQLAlchemy Core with stdlib SQLite and a bounded QueuePool shared by synchronous callers and an owned async worker bridge. Move routine query construction into named Core table/column expressions, keeping existing domain models and both current database files. Default to one reusable connection per store, no overflow, finite waits, and explicit transaction ownership. Preserve usage retention/pagination and the managed-network execution fence across real OS mutations. Design includes cancellation/commit arbitration, quiescent shutdown, old-store parity, and real local process/HTTP checks.

## Technical Context

**Language/Version**: Python >=3.10 supported; repository default Python 3.12. Existing Rust native and Svelte dashboard boundaries remain intact.

**Primary Dependencies**: New SQLAlchemy Core `>=2.0.54,<2.1`, locked with uv during implementation; stdlib sqlite3/concurrent.futures/asyncio. Existing FastAPI, Pydantic, and pytest tooling. No ORM session layer, async driver, or migration framework.

**Storage**: Existing local `KLLM_HOME/usage.sqlite3` and `KLLM_HOME/managed-network.sqlite`. Usage DELETE/FULL/FULL-auto-vacuum; network WAL/FULL/foreign keys. Keep text timestamps and Pydantic JSON bodies byte/meaning compatible.

**Testing**: pytest, pytest-asyncio, pytest-xdist; real temporary file databases, local HTTP application boundaries, subprocess competition, fault injection, and a reproducible concurrency workload. Existing domain suites are regression evidence.

**Target Platform**: macOS, Linux, Windows; no GPU or physical network is needed for storage acceptance. Test OS-specific file fences on each supported OS.

**Project Type**: Python CLI and local control service with a dashboard and background collectors.

**Performance Goals**: SC-002: 20 concurrent consumers, 10,000 retained observations, >=95% unrelated status requests within 1 second despite a 2-second storage delay. SC-004: 1,000 mixed operations, 100 open/close cycles, no capacity violations or resources left after successful close. These are targets, not measurements.

**Constraints**: Pool size default 1, permitted 1–4; overflow 0; wait default 5 seconds; close default 10 seconds; admission capacity 32; async workers `max(2, pool_size)` per opened store. No event-loop SQL/lock waits. Same-thread network fencing, explicit commits, no lease across external mutation. Preserve 24-hour/256-MiB usage defaults and smaller-budget tests.

**Scale/Scope**: Two existing domains, 5 usage tables and 14 network tables. No new product data, schema redesign, external storage service, dashboard management controls, JSON-state migration, or physical network changes.

## Constitution Check

*GATE: Passed before research; re-evaluated after Phase 1 design.*

| Principle / boundary | Pre-research assessment | Post-design evidence |
|---|---|---|
| I: simple scoped code and justified dependencies | Pass: existing raw sqlite helpers assessed; query builder requested | One Core dependency and one shared owner with two current consumers; domain models stay local |
| II: observable regression coverage | Pass: identify original domain and failure behavior | Quickstart requires old-schema parity, atomicity, cancellation races, retention, resource bounds and malformed data |
| III: integration and portability | Pass: local SQLite/process/HTTP boundaries identified | Real files and process fences on Windows/Linux/macOS; packaging/import checks; no hardware claims |
| IV: explicit failures and cleanup | Pass: no destructive state migration | Contracts define bounded waits, outcome arbitration, quarantined failed close, error mapping and owner replacement |
| V: reproducible dependencies/docs | Pass: plan-only changes | Implementation updates pyproject/uv.lock together, supported rebuild and existing operational docs |
| Architecture and facade surfaces | Pass: current `src/kllm/` layout authoritative | Preserve `kllm.agent:app`, injected dependencies, test patch names, native/helper boundaries |
| Durable-memory authorization | Pass: use existing fallback context | New decisions stay feature-local; no durable capture or shared-memory write |

The general constitution's old `python/kllm/` reference conflicts with current user instructions and the architecture standard. Follow `src/kllm/`; no amendment is included. No other hard conflict, accepted deviation, or unresolved clarification remains. Phase 1 passes all gates. Runtime acceptance remains pending implementation.

## Project Structure

### Documentation (this feature)

```text
specs/015-async-sqlite-access/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── memory.md
├── memory-synthesis.md
├── checklists/requirements.md
└── contracts/
    ├── persistence.md
    └── external-compatibility.md
```

`tasks.md` is generated by `$speckit-tasks`; it is not produced in this phase.

### Source Code (repository root)

```text
src/kllm/
├── persistence/
│   ├── __init__.py          # Proposed small internal public surface
│   ├── options.py          # Validated immutable options/environment parsing
│   ├── sqlite.py           # Engine, lease, explicit transactions, initialization/cleanup
│   └── operations.py       # Bounded async dispatch and operation outcome tracking
├── usage_store.py          # Existing facade, domain writes/retention
├── usage_store_queries.py  # Existing query owner, operation-bound connection
├── usage_schema.py         # Proposed Core usage declarations
├── managed_network/
│   ├── store.py            # Existing facade, rules and execution fence
│   ├── schema.py           # Proposed Core network declarations
│   └── executor.py         # Preserve whole-operation fence/shield integration
├── agent/
│   ├── __init__.py         # Preserve injected facade/route test surfaces
│   ├── usage_runtime.py    # Owner generation, path replacement
│   ├── application_lifecycle.py
│   ├── live.py             # Stop scheduling and drain owned source work
│   ├── handlers/usage.py   # Async ingest boundary; history contract preservation
│   └── services/           # Existing collector/network/channel/runtime services
└── exceptions.py           # Preserve shared domain error hierarchy

tests/
├── test_sqlite_persistence.py  # Proposed lease/pool/cancellation tests
├── test_sqlite_integration.py  # Proposed old-store/HTTP/process/lifecycle checks
└── test_*.py                  # Existing usage/network/control regression suites
```

**Structure Decision**: Add a small persistence package used by the two existing stores. Keep domain validation, JSON serialization, retention and network ownership rules in their current modules. Split only along actual option/connection/operation responsibilities; no generic repository framework or plugin system. Proposed paths above are design targets, not existing files.

## Phase 0: Research Results

[research.md](research.md) resolves all technical questions. Two research agents inspected library capabilities and current integration separately. Decisions: Core plus sqlite3, one shared synchronous pool per owner, bounded whole-operation async dispatch, explicit transactions with same-lease query helpers, store-specific initialization, cooperative cancellation, truthful ingestion/retention outcomes, and drain-before-dispose lifecycle. Official documentation informed library semantics; fit and limits are explicitly application design judgments.

## Phase 1: Integration Design

### Shared owner and query conventions

`SQLiteStore` owns the engine/pool, canonical path, generation, options, admission counters, and operation registry. Store constructors accept `SQLiteOptions`; default factories parse documented environment variables. A store is never module-global across unrelated homes or event loops. No Connection object escapes a completed operation.

Synchronous `read(callback)` and `write(callback)` bind one connection to a unit of work. Async `aread(callback)`/`awrite(callback)` dispatch that complete unit through tracked bounded workers. Domain async compositions can dispatch an entire bounded synchronous service operation once; its store calls execute inline and use normal transaction boundaries. Internal helpers receive the current connection explicitly. Public write reentrancy is rejected unless it is a helper participating in the same transaction; no implicit savepoints.

Tables declare current storage types and keys without ORM relationships. Ordinary CRUD/filter/order/upsert code uses Core statements with bound values. Specialized schema/PRAGMA/maintenance commands stay at infrastructure boundaries. Consume result rows before returning a lease. Existing query and domain method names remain available to supported callers; replace indefinite `store.db` ownership with an explicit test/maintenance lease.

### Usage integration

Replace the single permanent connection with leases and pass a single connection through nested query helpers. Keep history pagination, timestamp parsing, cursor signing secrets, indexes, metadata and deduplication unchanged. Compound public reads share one snapshot. Serialize ingestion and pruning with a finite per-store writer gate; synchronize in-memory recorder metadata under a short lock with no wait/checkout while holding that lock.

Validated batch insertion commits atomically. Retention is explicitly separate: each prune/truncation-metadata unit commits atomically and physical-byte checks occur outside its active transaction. Preserve the original boundary that ingestion may already be committed when retention fails. Keep history/recording error semantics and original acknowledgement policy; confirm metadata only after successful save/retention. No retry duplicates committed effects.

`UsageIngestHandlers.ingest` performs network/body validation on the request path, then awaits a tracked bounded storage composition. Synchronous history routes, collector and live-feed sources retain synchronous access. Cold initialization and close called from async lifecycle code are offloaded through tracked ownership, not run on the event loop.

### Managed-network integration

Convert data statements to Core while preserving Pydantic bodies, StoreConflict, explicit write transactions, and schema constraints. Keep secure DB/lock-file creation and permissions. Do not change protected helper storage.

For async callers in managed-network HTTP handlers, channel and runtime services, move each blocking persistence segment off the event loop. In `src/kllm/agent/handlers/managed_network.py`, await tracked bounded compositions around synchronous storage-backed views, selections and authorization-dependent lookups, preserving synchronous service methods for existing consumers. Audit the other async route handlers for the same pattern and test real HTTP requests during network-store contention. Integrate PersistenceError-to-unavailable mappings before the US2 acceptance gate, not in a later story. Offload cold usage/network owner construction from async startup before that gate as well. Preserve service sequencing and revalidate authority/revisions after awaits where the current contract requires it. A fenced mutation remains one synchronous worker operation including `execution()` acquisition, intent commit, external mutation and observed-result commit. It never awaits or submits another worker from inside the fence. `BEGIN IMMEDIATE` protects short writes; the file execution fence protects the larger operation. No connection is held during OS work.

During a cancelled protected mutation, preserve the existing shield/reconciliation semantics rather than abandoning a journal midway. Do not allow generalized pre-commit cancellation to erase already committed intents or interrupt a required observation phase.

### Cancellation, contention, and lifecycle

Implement the states and commit barrier in [contracts/persistence.md](contracts/persistence.md). Maintain cancellation-scoped lease ownership, bounded admission/checkout/fence/write waits, and progress cancellation. Statement interruption is prohibited once commit arbitration has started. Commit exceptions produce an explicit unknown outcome when resolution is impossible.

At the beginning of application shutdown, failed-start cleanup or directory replacement, compute one absolute monotonic deadline using the minimum configured shutdown timeout among affected owners (default 10 seconds). That deadline covers the complete lifecycle sequence: stop producer scheduling and await existing source jobs; reconcile protected active operations; close store admission; cancel queued work; drain active operations; close workers/connections. Pass the same deadline through every phase and nested owner close; never restart a timeout at the drain or disposal stage. Live-feed `shutdown(wait=False)` and fixed joins are not cleanup proof. Track outstanding source jobs and make failure visible. Use the same cleanup path after partial startup. Expiry in any phase reports explicit failure within the lifecycle budget, marks affected owners CLOSING/unavailable, rejects new external work, and keeps unsettled jobs/fences tracked. Already admitted protected compositions retain only the continuation access needed to record/reconcile their existing effects; do not force-kill workers or abandon durable journals. Successful close requires zero operations, connections, waiters and worker jobs. Directory replacement drains before publication and refuses replacement when old ownership remains unresolved.

### Schema adoption and external behavior

No schema redesign or data copying is planned. Declare the current schema and validate required columns/types/constraints, permitting harmless extra additive columns. Repeatably initialize empty stores and apply only the existing `nodes.context` upgrade when absent, under a secure per-store initialization fence. Do not use `create_all` as a migration engine or rebuild tables to force metadata parity. Corrupt, incompatible and partially initialized files produce actionable failures and remain on disk.

Wrap/translate SQLAlchemy exceptions at the shared boundary and audit every sqlite3/OSError catch site. Preserve existing HTTP/CLI/dashboard behavior per [external-compatibility.md](contracts/external-compatibility.md). New unavailable/unknown-outcome conditions use the shared PersistenceError hierarchy and existing unavailable response semantics, with sanitized details.

## Verification and Delivery Boundaries

[quickstart.md](quickstart.md) defines existing regression commands and proposed implementation acceptance checks. Real file-backed tests cover capacity one/four, mixed sync/async operations, cancellation before/at commit, shutdown failure and recovery, old schema, physical budget, and subprocess fencing. Record before/after latency for the spec workload. Package changes require uv lock, supported build and package/native imports.

Update `docs/agent-install.md`, `docs/troubleshooting.md`, and `docs/control-api-v2.md` where their existing storage/configuration/error sections are affected; add focused maintainer persistence conventions under `docs/`. No new dashboard behavior or native implementation change is intended. Run their checks only if implementation touches those surfaces.

Planning is complete after these artifacts and hook checks. No tasks, runtime code, dependency installation, real-store migrations, or performance/hardware acceptance are included in this command.

## Complexity Tracking

No constitutional violations require exceptions. The small worker bridge is justified by mixed caller styles, bounded cancellation ownership, and same-thread execution fencing. A pure async driver plus separate sync pool would increase coordination; a custom query builder and pool would duplicate established functionality. The supported callback set stays scoped to current storage/service operations.
