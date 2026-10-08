# Tasks: Central Node Network Directory and Runtime Sync Recovery

## Phase 1 — Directory store

- [x] T001 `NodeNetworkIdentity` record in `src/kllm/managed_network/models.py`.
- [x] T002 `node_directory` table + `directory`/`put_directory`/`put_directory_many`/`remove_directory`
  in `src/kllm/managed_network/store.py`.
- [x] T003 Carry `hostname` in `RegisterBody` and heartbeat payload.
- [x] T004 Upsert/remove directory rows in `MembershipHandlers`; include `node_directory` in
  snapshot responses.
- [x] T005 Assignment-driven private fields + startup backfill + `directory_view()` in
  `src/kllm/agent/services/managed_network.py`.
- [x] T006 Worker mirror in `_apply_cluster_snapshot` (`src/kllm/agent/__init__.py`).

## Phase 2 — Shared read model

- [x] T007 Runtime status uses directory name/address (`runtime_control.py`).
- [x] T008 `network_directory` in `DashboardStatusService` status + live payload.
- [x] T009 Usage overview and guided setup use directory names.
- [x] T010 Dashboard types + node lists prefer directory (LlamaRuntimes row shows name + address).

## Phase 3 — Sync repair

- [x] T011 Same-key master rotation allowed with a live network (`store.pair` + `bootstrap`).
- [x] T012 Target states (current/label_stale/binding_stale/absent) + master re-stamp.
- [x] T013 Bounded auto-retry for failed/interrupted preparation (master and worker).
- [x] T014 Per-node channel state in runtime status consumers.

## Phase 4 — Tests and docs

- [x] T015 Pairing recovery tests (live network adopts, changed key still refused).
- [x] T016 Runtime version tests (re-stamp, stale binding, retry, directory labels).
- [x] T017 Directory store/mirror tests.
- [x] T018 Dashboard API/browser tests.
- [x] T019 Docs: troubleshooting, control-api-v2, engine-decision, agent-install.
- [x] T020 Run targeted pytest, dashboard tests, ruff.

## Verification record

- `uv run pytest -q` — 1005 passed, 16 skipped (includes new directory, pairing, runtime,
  membership, and dashboard tests).
- `npm --prefix dashboard run check` — 0 errors, 0 warnings.
- `node --experimental-strip-types --test tests/runtime-browser.test.mjs` — 2 passed (Chrome).
- Non-browser dashboard suite — 54 passed.
- `uv run ruff check src/kllm/agent src/kllm/managed_network src/kllm/membership_state.py tests/...`
  — all checks passed; repo-wide ruff still reports the pre-existing `placement.py` F821 errors
  (unrelated to this feature).
- Hardware two-node rotation with a live private network remains an unverified follow-up.
