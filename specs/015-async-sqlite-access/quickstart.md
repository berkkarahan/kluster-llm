# Quickstart Validation: Async SQLite Access

This is the implementation acceptance guide for the completed feature. Use isolated temporary data, not a running node's real `KLLM_HOME`. Captured results and unavailable platform follow-ups belong in `validation.md`.

## Prerequisites and setup

Use the repository's supported Python/uv/native environment; bootstrap and recovery are documented in `docs/agent-install.md`. Default Python is 3.12, supported minimum is 3.10. No GPU, model download, admin networking action, or live cluster is needed for these SQLite checks.

After implementation updates `pyproject.toml` and `uv.lock`, from repository root:

```bash
uv sync
uv run python scripts/build.py
uv run python -c 'import sqlalchemy; import kllm; from kllm import native; assert native.native_available(); print(sqlalchemy.__version__)'
```

Expected: locked Core version installs, supported build finishes and application/native import succeeds. Record failures instead of treating dependency install alone as packaging verification. Do not introduce a plain pip editable-install alternative.

## Existing regression suites

Run these existing suites after integration:

```bash
uv run python -m pytest -q tests/test_usage.py tests/test_dashboard_api.py tests/test_dashboard_live.py tests/test_application_lifecycle.py tests/test_control_api_v2.py tests/test_managed_network_critical.py tests/test_node_directory.py tests/test_guided_network.py tests/test_managed_network_reuse.py tests/test_managed_network_e2e.py tests/test_rpc_lifecycle.py tests/test_runtime_versions.py
```

Expected: exact history/cursor/corruption/retention results, domain conflict/idempotency/recovery and HTTP contracts remain passing. Preserve test facade injection and convert direct `.db` seeding to explicit scoped leases. Keep `_write` and equivalent fault-injection behavior meaningfully covered.

After targeted checks pass, run the wider Python suite because lifecycle and exception boundaries are shared:

```bash
uv run python -m pytest -q
```

Record environment, selected/skipped checks, and pre-existing failures separately. Do not claim physical networking or GPU inference from these suites.

## New persistence and integration acceptance

The implementation must create the proposed files, then run:

```bash
uv run python -m pytest -q tests/test_sqlite_persistence.py tests/test_sqlite_integration.py
```

Expected scenarios:

| Scenario | Required evidence | Spec outcome |
|---|---|---|
| Routine maintenance | CRUD/filter/order/upsert in both domains; parameter-looking values remain data; temporary field/filter exercise uses shared conventions | SC-001 |
| Nested history at pool size one | Compound coverage/filters/meta return correctly without reacquisition/deadlock; capacity four produces the same snapshot semantics | SC-001/003 |
| Legacy adoption | Files created with pre-change DDL, fixed IDs/cursor secret and network fixtures reopen unchanged; absent context upgrades repeatably | SC-003 |
| Failure atomicity | Induce mid-batch error and pre-commit cancellation; zero partial changes; verify previously committed rows | SC-005 |
| Commit arbitration | Barriers before/at commit force cancellation-first and commit-first outcomes; no false save, no unsafe connection reuse | SC-005 |
| Retention after ingestion | Fail pruning after confirmed insertion; retained committed data and unavailable status agree; retries deduplicate | SC-003/005 |
| Pool admission/timeout | Hold all leases; exceed capacity; timed/cancelled waiters release once; no overflow or parameter leakage | SC-004 |
| Contended initialization | Two processes open/create/adopt same file; no partial schema, secret regeneration or data reset | SC-003/006 |
| Network mutation ownership | Two processes race a protected mutation/revision/idempotency request; only valid mutation wins; same-thread reentrancy works | SC-006 |
| Lifecycle/path changes | 100 open/close cycles and directory replacement; zero owned resources after completed close; failed-start cleanup | SC-004 |
| Abnormal close | Separately block a source job, protected reconciliation and an active SQL worker beyond the single whole-lifecycle deadline; each attempt fails within the total budget, owner stays unavailable, replacement is refused; release barriers and verify tracked safe completion/drain | FR-007/013 |
| Existing boundary errors | Local HTTP validation/auth/missing/conflict/unavailable cases preserve response semantics; US2 saturation maps PersistenceError to unavailable before its gate | FR-011 |
| Async network routes and startup | Held network-store leases/delayed construction do not block the event loop; storage-backed route views time out with sanitized unavailable behavior while unrelated status continues | FR-003/011 |

Use real file databases, synchronized barriers, local process/HTTP boundaries and fixed observation times. Avoid elapsed sleeps as synchronization and global `.kllm` state. On platforms without a supported process test environment, record unavailable coverage; do not mark it passed.

## Resource and responsiveness workload

Create the SC-002/004 workload as a reusable pytest integration scenario in `test_sqlite_integration.py`, using controlled native/network dependencies and the real persistence/application boundary. This is a validation fixture, not a production benchmark service.

- Seed 10,000 retained valid observations with fixed IDs/times and enough temporary budget to avoid unintended retention during latency measurements.
- Run 20 concurrent consumers mixing history reads and valid writes; use both synchronous and async paths. Collect at least 200 unrelated `/health` responses, and exercise `/v2/status` while usage storage is delayed and managed-network storage remains available.
- Inject a 2-second wait inside a supported storage operation with a test barrier. Verify event-loop progress and >=95% unrelated requests within 1 second; never delay the event loop itself to simulate storage work.
- Separately run 1,000 mixed operations and 100 open/close lifecycles; verify max owned connections, connection reuse and final zero connections/jobs/waiters/workers.
- Repeat with pool sizes one and four. Measure same workload on the pre-change and changed checkout; record OS/CPU/Python/SQLite/library versions, parameters, sample count, percentiles, errors, retained IDs, and physical bytes.
- Run timing-sensitive checks serially to avoid pytest-xdist contention (the proposed file contains only this named timing acceptance selection):

```bash
uv run python -m pytest -q -n 0 tests/test_sqlite_integration.py -k 'responsiveness or resource_cycles'
```

This command requires those named tests during implementation. A passing controlled workload proves the stated local target, not a hardware-independent speedup. Unexpected baseline behavior is evidence to report, not a reason to silently weaken the specification.

## Configuration and diagnostics

Within isolated subprocess test environments, test `KLLM_SQLITE_POOL_SIZE=1` and `4`; test positive finite wait/close settings. Reject zero/negative/nonnumeric/non-finite timeouts, pool size zero/five, and invalid integer syntax. Confirm timeout/closed/unknown-outcome errors remain sanitized and use existing unavailable semantics. See [persistence contract](contracts/persistence.md) for bounds and lifecycle states and [external contract](contracts/external-compatibility.md) for HTTP/CLI expectations.

## Supported-platform and documentation checks

Run persistence, initialization-race, process-fence, failed-start and directory replacement checks on macOS, Linux and Windows with supported Python versions. No protected helper is invoked by these tests. If a platform is unavailable, list the exact outstanding scenarios.

Verify maintainer docs demonstrate table declarations, operation-bound query helpers, future additive upgrades, both caller styles, connection cleanup and diagnosis without full implementation bodies. Verify operational docs reflect environment defaults, failure behavior and supported packaging commands. Only run dashboard or Rust source checks when those surfaces actually change; package/native import remains necessary for the Python dependency rebuild.

## Evidence record

During implementation, record commands, results and limitations in a feature-local validation report. At present there are no test/build/benchmark results for this feature. Planning validation only checks artifact consistency, links, scope and constitution gates.
