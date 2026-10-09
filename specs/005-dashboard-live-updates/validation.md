# Validation

## Environment

Windows 10 (26200); uv Python 3.12.13; Node v22.23.2; dashboard dependencies installed; Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe`. Browser harness used for workspace and usage tests.

## Acceptance (T002, T048, T049, T059, T067, T077)

Closed 2026-09-29. Cross-network and hardware matrix rows are marked done by operator sign-off. Local harness evidence below is the measured sample; it is not a physical 16-node or five-operator session.

| Item | Record |
|------|--------|
| Baseline commit | `a0a4f5259c7dfd5c204deb5ff0972f27b8e87c69` |
| Method | `node scripts/measure-idle-dashboard-requests.mjs` — headless Chrome CDP, Logs route, 15s warmup then idle window, application-state HTTP paths only |
| Host | Windows 10.0.26200, Chrome, 1 viewer, synthetic local origin (no cluster) |
| Baseline (60s idle) | 45 requests: `/v1/status` 22, `/v1/usage/live` 23. Extrapolated 10 minutes: **450** |
| Current implementation (60s idle) | **0** of those HTTP state checks (live WebSocket path; no recurring status/usage GETs in the window) |
| Request reduction (status+usage) | 450 → 0 on this sample, above the 90% SC-002 bar for those checks |
| Scale / chat / log-load / platforms | Closed with the hardware and cross-network sign-off (1/4/16 nodes, five viewers, five chat streams, 100 logs/s, Linux/macOS) |
| SC-007 operators and viewports | Keyboard/viewport checks already covered by `dashboard/tests/usage-workspace.test.mjs` (1280×720 and 390×844). Five-operator session closed with the same sign-off |

## Quality gate (T047) — 2026-09-29

Run from repository root, sequential:

| Command | Result |
|---------|--------|
| `uv run pytest -q tests/test_dashboard_live.py tests/test_cluster_log.py tests/test_dashboard_api.py tests/test_model_management_api.py tests/test_membership.py tests/test_cp_proxy.py` | **139 passed**; 2 Starlette/httpx deprecation warnings. |
| `npm --prefix dashboard run check` | **Passed** (0 errors). |
| `node --experimental-strip-types --test --test-concurrency=1 dashboard/tests/*.test.mjs` | **41 passed** (controller recovery, logs, store, live-updates, usage-workspace browser). |
| `npm --prefix dashboard run build` | **Passed**. |
| `uv run pytest -q` | **410 passed**, **3 failed** — `tests/test_install_script.py` macOS CMake/OpenSSL cache assertions on Windows only. Join auth tests fixed (`browse_masters` `port` kwarg). |

## Story verification (automated)

| Checkpoint | Evidence |
|------------|----------|
| US1 (T020) | Membership 25s disconnection test, shared acquisition counts, live-updates stale/clock tests, store/layout integration; no status/usage poll intervals in store. |
| US2 (T029) | `test_model_management_api.py` terminal/current identity, cancellation, late-sample guards; download reconciliation in Node tests. |
| US3 (T037) | `logs.test.mjs` modes/Clear/gaps/2000-row cap; `test_dashboard_live.py` log GET/WS parity, worker mirror, denial. |
| US4 (T045) | `live-updates.test.mjs` HTTP-only epoch, denied retry suppression, 20 visibility cycles, fallback deadlines; Python slow-peer isolation. |

## T050 review (FR/SC / constitution)

- **Constitution II–III**: Focused pytest and Node suites cover bounded transport, validation-before-mutation, and regression gates; full-suite Windows CMake failures are installer-platform scope, not live-feed scope.
- **Constitution IV**: No secret-bearing live errors; join_key/apply_error preserved in status (T068); upstream 401/403 → usage `denied` (T070).
- **FR-001–FR-016 / SC-001–SC-007**: Automated suites cover transport, recovery, logs, and operations. SC-002 idle-request sample is in the acceptance section. Cross-network and hardware rows are closed by operator sign-off.
- **Contracts**: GET `/v1/dashboard/live` and WebSocket paths exercised in `test_dashboard_live.py`; legacy CLI/RPC port 50052 unchanged.
