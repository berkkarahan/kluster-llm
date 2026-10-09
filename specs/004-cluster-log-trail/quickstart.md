# Validation guide: Master & Worker Log Trail

**Date**: 2026-09-29
**Status**: Implementation validation instructions; these runtime outcomes are not yet verified.

## Prerequisites

- Follow [the agent installation guide](../../docs/agent-install.md). The primary setup is
  `python scripts/install.py`; use the existing uv environment and pinned llama.cpp release.
- Review [the OpenDesign artifact/evidence](design.md) before visual implementation.
- Use synthetic log fixtures and temporary state for deterministic tests. Never use real
  credentials, user prompts, or production log deletion as test data.
- Browser tests need installed Chrome/Edge or `CHROME_BIN`. Record an unavailable browser
  as skipped; do not report it as an accessibility pass.

## Automated checks after implementation

Run from the repository root. Planned `logs.test.mjs` does not exist until implementation.

```text
uv run pytest -q tests/test_cluster_log.py tests/test_dashboard_api.py
node --test --test-concurrency=1 dashboard/tests/logs.test.mjs dashboard/tests/store.test.mjs dashboard/tests/usage-workspace.test.mjs
npm --prefix dashboard run check
npm --prefix dashboard run build
```

Keep dashboard check and build sequential: concurrent generated SvelteKit state previously
caused mismatched app IDs. Because the shared agent read/ingest contract changes, also run
`uv run pytest -q` and `node --test --test-concurrency=1 dashboard/tests/*.test.mjs` before
integration. Report failures with evidence and separate pre-existing failures.

### Focused regression matrix

- **Hub/journal:** Two identical messages with different IDs remain separate. A repeated
  recorded ID is not rendered twice. IDs survive relay/reload. Startup ingestion cannot
  bypass retained journal history. Snapshot checkpoint/data agree when ingest races a read.
- **Cursor contract:** Empty read, normal incremental read, malformed/future cursor,
  foreign epoch, ring expiry, batch truncation, and legacy journal rows follow
  [the contract](contracts/logs.md). No timestamp-based filtering.
- **Worker boundary:** Identified worker POST -> master snapshot -> worker-proxied snapshot
  retains IDs and checkpoints. Simulate master failure, local-only fallback, and recovery;
  verify explicit reset/coverage, preserved cached rows, bounded waits, and old-master error.
- **Compatibility:** Old POST bodies, unversioned JSON reads, and CLI follow output still
  work. No metadata is printed as a fake CLI log message; Clear causes no write request.
- **Controller:** Use controlled timers and deferred response/body promises. Cover every
  directed mode switch, Clear during fetch and decoding after a committed snapshot, hidden
  node rows, duplicate replay, reset after effective Clear, tab visibility, unmount, and no
  overlap. For a first-load regression: hold the initial read, press Clear, make a new entry
  readable, then return a snapshot containing it. Assert Clear changed neither generation
  nor timer/state, the snapshot is consumed, the new entry appears, and `hasCleared` stays
  false. Repeat after an initial read error before any checkpoint has been committed.
- **Read deadline:** Independently stall fetch before headers and body completion after
  headers. At 5,000 ms from dispatch, assert abort/invalidation, freed request slot, timeout
  text, unchanged committed rows/cursor/coverage/last success, and retry at the first future
  selected cadence deadline. Deliver the expired result after a retry starts: it must
  neither commit nor clear the newer request's slot. Also delay the timer callback past
  the deadline and verify the elapsed-time commit guard rejects the late result. Check
  timer cleanup on success, failure, cancellation, hide, and unmount; a pre-load no-op Clear
  leaves its timer unchanged. Use fake timers for these checks, not wall-clock sleeps.
- **Display:** Buffer never renders more than 2,000 rows. Omission is labeled. Literal
  markup remains text; unknown identities and UTC labels are accurate. Scroll position
  remains stable, including when an anchored old row is trimmed.

## Browser acceptance

Serve the dashboard with the existing isolated browser harness and a synthetic master plus
three workers. For manual development with an already running local agent:

```text
npm --prefix dashboard run dev -- --host 127.0.0.1
```

Use the local URL reported by Vite and visit `/logs`.

1. Confirm All nodes and Live initially; identify master and each worker. Select each node,
   then return to All nodes. Use duplicate hostnames to verify stable-ID labels.
2. Switch through Live, 5s, 30s, and 1m. Verify one selected radio, visible focus, node
   selection retained, and one refresh schedule. New entries appear at the chosen cadence.
3. Scroll up, emit more logs, and confirm no jump. Return to the bottom and confirm following.
4. Clear with a node filter active. Switch to All nodes and through all modes; old rows stay
   absent. Generate new entries and confirm they appear. Verify a second viewer and journal
   were unaffected. Repeat with an in-flight response and a subsequent feed restart; a
   reset after effective Clear must report the intentionally omitted recovery window,
   including any unseen post-Clear entries in it. Separately verify that Clear before first
   successful loading does not suppress that initial result or newly arriving entries.
5. Test loading, empty, cleared, master-unreachable/local-only, disconnected cached, and
   retained-history-gap states. Recover and verify accurate coverage rather than stale success.
6. At 1280-by-720 and 390-by-844, repeat using only the keyboard. Verify labeled controls,
   clear focus, full long-message access, no horizontal page overflow, and no focus theft.
   Enable reduced motion; check status with color disabled and with a screen reader.

## Timing, volume, and real-cluster evidence

- Instrument the existing `dashboard/tests/usage-workspace.test.mjs` fixture harness for
  SC-002. After initial load, use All nodes and steady foreground Live. The harness records
  `t_available` on its monotonic clock when inserting each ID into the serving fixture feed,
  and `t_rendered` on that same clock when it first observes the browser-confirmed rendered
  row after an animation frame. This includes render-observation transport overhead; record
  the observation method/frequency and overhead rather than subtracting an assumed delay.
  Record per-ID samples and calculate `t_rendered - t_available`. Initial retained history
  is excluded; every newly inserted matching ID remains in the denominator, and an ID not
  rendered within two seconds is a miss. Require at least 95% within two seconds and report
  missing IDs. Clear, mode changes, and fault injection are separate scenarios, not silently
  excluded failed samples. Do not substitute log `ts`, request start, or response receipt.
- With one master plus three workers, emit synthetic 128–1,024-byte messages at 100 total
  entries/second for ten minutes per mode. Record request start/finish times, row appearance,
  identity counts, omission notices, control latency, and retained session metadata memory.
- Prove p95 Live visibility <=2 seconds from serving-feed readability to rendered row, and p95 control
  latency <=1 second. For timed modes, check foreground intervals within one second when
  reads finish within one second. Slow/failed reads must not create overlap or false freshness.
- Repeat relay/proxy/failure/recovery on a private master and worker; confirm Clear cannot
  restore previously displayed local events when they later reach the master. Keep RPC
  port 50052 private. Record OS/network, versions, workload, timestamps, and results.
  Claim real-cluster feed-to-render latency only when its two measurement clocks have a
  documented alignment and uncertainty; otherwise record that latency as unverified.
  The single-clock fixture result alone is not real multi-node timing evidence.
- Run shared Python changes on Windows/Linux/macOS or explicitly identify unavailable
  platforms. Mocked tests alone do not establish real multi-node latency or platform behavior.
- With five operators, record whether at least four locate Logs and isolate a named worker
  within 30 seconds. Automated keyboard tests do not establish this human usability outcome.

Update existing `docs/cli.md` and `docs/troubleshooting.md` during implementation with page
location, refresh semantics, display-only Clear, bounded history, and local-only limitations.
Document measured results separately from planned targets.

## Implementation results (2026-09-29, Windows)

Baseline before changes: `uv run pytest -q tests/test_cluster_log.py tests/test_dashboard_api.py`
→ 47 passed; `npm --prefix dashboard run check` → 0 errors, 0 warnings.

| Command | Result |
|---------|--------|
| `uv run pytest -q tests/test_cluster_log.py tests/test_dashboard_api.py` | 60 passed (baseline 47; convergence validation, journal retry, fallback scope and proxy protocol checks added) |
| `node --test --test-concurrency=1 dashboard/tests/logs.test.mjs` | 8 passed, 0 failed (snapshot validation, failures/recovery, mode/visibility cancellation, timeout, Clear, and buffer cap) |
| `node --test --test-concurrency=1 dashboard/tests/*.test.mjs` | 16 passed, 0 failed (browser harness includes `/logs`, second-viewer Clear isolation, degraded/recovery status, and trimmed-anchor recovery) |
| `npm --prefix dashboard run check` | 0 errors, 0 warnings |
| `npm --prefix dashboard run build` | passed; `build/` includes `logs/_page` |
| `uv run pytest -q` | 371 passed, 6 failed |

The 6 failures are **pre-existing and unrelated** to this feature (already recorded in
`docs/node-usage-monitor.md`): three `tests/test_agent_auth.py` join-discovery mocks omit the
pre-existing `port` keyword passed by `_join_cluster`, and three `tests/test_install_script.py`
macOS CMake-cache expectations do not hold on Windows. `tests/test_cluster_log.py` passes in full.

Covered by the automated checks: stable event identity through relay/reload; invalid UUID and
non-finite timestamp rejection; retryable journal initialization and 503 reporting; atomic
incremental snapshots with malformed/future/foreign/noncanonical-cursor handling and
retention/batch gaps; legacy POST/JSON/SSE compatibility; local-node-only fallback and distinct
upstream HTTP/protocol failures; missing-cursor rejection on the first worker-proxied snapshot;
decoded snapshot field validation; the four refresh modes and all 12 directed transitions;
mode-switch and hidden-tab request invalidation; visible read errors with retained rows and
recovery-warning clearing; the 5,000 ms header/body deadline with retry and late-commit rejection;
display-only Clear (including the first-load no-op), a second independent viewer, inert markup,
the 2,000-row cap and scroll-anchor recovery; keyboard/narrow-layout operation at 1280×720 and
390×844.

### Deferred and unverified (not passes)

At the user's request on 2026-09-29, T028 and T029 are marked closed/waived in `tasks.md`.
This does not mean the checks ran or passed; the outstanding evidence and follow-up steps below remain applicable.

1. **Real-cluster feed-to-render latency and volume (SC-002/SC-003/SC-006):** no runnable
   multi-node cluster was available. The single-clock fixture instrumentation (T028) and the
   ten-minute per-mode measurements with one master plus three workers at 100 entries/second
   are **not executed**. Follow-up: run the instrumented `usage-workspace.test.mjs` scenario and
   the real master/worker workload, recording OS/network, versions, event-ID-correlated
   `t_available`→`t_rendered` samples and control latency.
2. **Human usability and screen-reader acceptance (SC-001/SC-005):** the automated browser check
  covers keyboard operation, contrast, and no horizontal overflow, but five real operators were
  not available. `SC-001` (locate Logs and isolate a named worker within 30 seconds) is
  **unverified**. Follow-up: run the five-operator check at both viewports with a screen reader.

## Convergence implementation results (2026-09-29, Windows)

Implemented convergence tasks **T031–T041**. The checks above now cover each corresponding
regression. No new dependencies were introduced. T028 and T029 remain unchecked and
unverified as described above; they require a real multi-node workload and human participants.
