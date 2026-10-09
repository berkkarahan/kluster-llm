# Async SQLite Access: Implementation Evidence

## Baseline (T001)

- Revision: `93f45fcb065dbc2f9b60e6720f02d1c83049bf7f` (before persistence changes).
- The original T001 note recorded the host-default interpreter as Python 3.9.6 and SQLite 3.54.0; it did not capture the interpreter used by `uv run` for those tests. The later comparable workload explicitly used the project interpreter listed below.
- Existing focused command: `uv run python -m pytest -q tests/test_usage.py tests/test_dashboard_api.py tests/test_dashboard_live.py tests/test_application_lifecycle.py tests/test_control_api_v2.py tests/test_managed_network_critical.py tests/test_node_directory.py tests/test_guided_network.py tests/test_managed_network_reuse.py tests/test_managed_network_e2e.py tests/test_rpc_lifecycle.py tests/test_runtime_versions.py`.
- Result: **263 passed, 1 skipped, 8 warnings in 14.30s**, exit 0. Existing httpx/Starlette deprecation warnings; skipped case is not a pass.
- Pre-change DDL preserved independently in `tests/sqlite_fixtures.py`. Inspected 5 usage and 14 network tables with current index definitions. Existing cursor secret, INTEGER reading ID and composite/partial constraints are compatibility expectations.
- Ignore files: existing Python/Rust/Node/editor/env patterns cover generated artifacts. No Docker/ESLint/Prettier/Terraform/Helm setup is present requiring a new ignore file; dashboard is not an npm publishing target.
- All checks use isolated test state. No live store, running cluster or host networking changed.

## Implementation Progress

Implementation and the macOS local acceptance suite are complete. Cross-platform execution remains unavailable on this host; follow-ups are recorded below.

## Shared foundation (T005–T012)

- Initial red check: collection failed because SQLiteOptions/SQLiteStore did not exist; subsequent nested-result escape regression failed before its fix.
- Implemented immutable validated settings, bounded owner/QueuePool, explicit transactions, schema validation, operation admission, tracked cancellation/commit arbitration, retired broken connections, close quarantine and sanitized outcomes.
- Focused serial checks: `uv run python -m pytest -q -n 0 tests/test_sqlite_persistence.py`: **19 passed in 0.43s** after fixes. Real file-backed checks include both sides of commit/cancel arbitration, unknown commit, rollback/reset failure, legacy schema refusal, no uncommitted visibility, pool timeout and result escape.
- Common async dispatch primitives are included in the foundation to exercise cancellation ownership; production caller migration and dispatcher acceptance remain US2 tasks.

- T013 configured four-worker foundation gate: **19 passed in 0.75s**, exit 0.

## Store adoption and maintenance (T014–T025)

- Red checks: 7 integration cases rejected the missing options API or showed that incompatible schemas were silently adopted; 2 compound snapshot cases failed before adoption.
- Added named Core schemas preserving all 5 usage / 14 network tables, legacy nullability, indexes, foreign keys and unique/partial constraints. Routine queries/writes in both domains now use Core expressions.
- Preserved usage DELETE/FULL/FULL-auto-vacuum and network WAL/FULL/foreign keys. Usage insertion and grouped retention are separate units; the execution file/mutex fence remains same-thread with a finite five-second acquisition boundary.
- Migrated raw connection fixtures in `tests/test_usage.py` and `tests/test_dashboard_api.py` to scoped maintenance leases. Fixed SQLAlchemy row-mapping conversion exposed by the existing restart-recovery check.
- Adopted-store focused subset including usage/dashboard/control/network/directory/RPC/runtime checks plus initial new checks: **234 passed, 1 skipped, 8 warnings in 12.09s**, exit 0. This selection excludes the baseline live-feed/application-lifecycle files; they remain later affected gates.
- Expanded new foundation/integration checks including the future field/filter maintenance exercise for both private schemas: **28 passed in 0.89s**, exit 0.
- Existing retained-history query-plan, exact pagination, corruption, deduplication and 64/96 KiB physical-budget checks pass. No product field or incidental index was added.

## Async integration and workload evidence

- Environment for the current and baseline workload: macOS 27 arm64, Python 3.12.14, SQLite 3.53.4, SQLAlchemy 2.0.54. This is the `uv` project interpreter; the original T001 test-interpreter version was not recorded.
- The named current workload command `uv run python -m pytest -q -s -n 0 tests/test_sqlite_integration.py -k responsiveness --tb=short` passed: **2 passed, 19 deselected in 4.94s**. At pool sizes 1 and 4, 20 mixed history/status consumers ran while a barrier held one real usage-store lease for two seconds. The actual v2 status handler was registered through the production router and read the independent managed-network store. Each case returned 200/200 unrelated health probes with p50 **0.0002s**, p95 **0.0002s**, 10,005 retained observations, **8,318,976** SQLite bytes and zero HTTP errors.
- Baseline comparison used the actual synchronous `UsageStore`, `NetworkStore` and v2 status-handler sources from revision `93f45fcb065dbc2f9b60e6720f02d1c83049bf7f` in a detached temporary worktree, with the same 10,005 retained observations, 20 mixed consumers, 200 health probes, network status read and two-second SQLite UDF barrier. Result: p50 **2.0412s**, p95 **2.0475s**, 10,005 retained observations, **8,318,976** SQLite bytes and zero HTTP errors. The baseline's blocking SQLite call was inside the async HTTP handler; the changed owner ran the held lease on its bounded worker. This is a controlled local ASGI comparison, not a hardware-independent speedup claim.
- `resource_cycles` covers 1,000 mixed operations and 100 open/close lifecycles for each store at capacities 1 and 4, checking created/owned connection bounds and zero connections, leases, admitted operations, jobs, waiters and workers after close. It passed in the focused integration run recorded below.
- Real-file two-process initialization and execution-fence checks use spawned processes and isolated temporary files. They passed on this macOS host; Linux and Windows remain untested here.
- HTTP cancellation checks cover cancellation before commit (retained lease, rollback, then safe reuse) and after the commit boundary (durable batch and duplicate-safe retry). A separate saturation test confirms the unavailable response while `/health` remains available.
- `uv sync` passed. `uv run python scripts/build.py` completed the CPython 3.12 wheel, native helper build and dashboard build. `uv run python -c 'import sqlalchemy; import kllm; from kllm import native; assert native.native_available(); print(sqlalchemy.__version__)'` printed **2.0.54** and passed.

## Lifecycle and directory replacement

- Added a real `UsageRuntime` directory replacement check. A populated first directory is closed before the second owner is published; the new owner reads no records from the old directory. If closing the published owner raises, replacement raises and the old owner remains published. Focused command `uv run python -m pytest -q -n 0 tests/test_sqlite_integration.py::test_usage_runtime_directory_replacement_closes_before_publish`: **1 passed**.
- Shutdown deadline regression checks cover a hung live usage producer and a hung managed-network stop callback. Both report expiry, continue remaining cleanup, and reject new storage work where the store is affected. Store-level close-timeout tests verify CLOSING quarantine until the lease actually settles.

## Final verification and limits

- `uv run ruff check src/kllm tests`: **All checks passed**.
- `uv run python -m pytest -q`: **1,153 passed, 14 skipped, 8 warnings in 24.74s**, exit 0. The skips and existing httpx/Starlette deprecations remain explicitly excluded from pass counts.
- `uv run python scripts/build.py`: completed the CPython 3.12 macOS arm64 wheel, native network helper, and Svelte dashboard build. The script reported five npm audit findings (3 low, 2 high); dashboard source was not changed by this feature.
- Package/native import check printed SQLAlchemy **2.0.54** and confirmed `kllm.native.native_available()`.
- Platform exercised: macOS 27 arm64, project Python 3.12.14 / SQLite 3.53.4. Linux and Windows, and other supported Python versions, were not available. Run real-file initialization/fence races, cancellation, lifecycle deadline, directory replacement, and resource-cycle checks on those CI/platform targets before claiming cross-platform acceptance.
- No physical cluster, GPU, or multi-node inference claim is part of this local SQLite/ASGI evidence.

## Requirement reconciliation

| Requirement | Evidence | Result |
|---|---|---|
| FR-001–FR-002 | SQLAlchemy Core declarations for both domains, shared documented lease/snapshot conventions, temporary field/filter exercise | Met |
| FR-003–FR-005 | bounded async worker/admission bridge; responsiveness workload; explicit pool and finite wait configuration | Met on recorded macOS workload |
| FR-006–FR-008 | transaction isolation/rollback, cancellation/commit arbitration, legacy-file adoption and schema-refusal checks | Met in local file-backed checks |
| FR-009–FR-010 | retained-history/cursor/retention regressions, network conflict/recovery tests, spawned two-process fence check | Met in local suite |
| FR-011–FR-012 | existing API regression suites, sanitized persistence errors, bound Core statements and domain validation | Met in local suite |
| FR-013 | canonical owner paths, directory replacement and failed-close refusal test | Met in local suite |
| FR-014 | persistence, install, troubleshooting and control API documentation | Met |
| FR-015 | pre/post-commit cancellation tests, explicit unknown outcome classification and durable identity-based safe retry checks | Met in local suite |
| SC-001 | legacy CRUD plus field/filter workflow for usage and network stores | Met |
| SC-002 | 20 consumers, 200 probes, 10,005 observations, 2-second held lease, p50/p95 and zero errors; all probes under 1 second | Met for controlled local workload |
| SC-003 | populated legacy fixtures, cursor/page parity, stored identities/relationships, process restart and recovery regression coverage | Met in local suite |
| SC-004 | `resource_cycles`: 1,000 operations and 100 owner cycles for each store at capacities 1/4; reuse/bounds/zero-close counters | Met in local suite |
| SC-005 | injected transaction/prune faults, precommit rollback and commit-boundary durable batch checks | Met in local suite |
| SC-006 | duplicate/conflict/recovery suite and spawned competing-process execution-fence test | Met on macOS; cross-platform follow-up remains |
