# Kluster LLM Codebase Audit Checklist

This inspection checklist provides concrete, line-level audit criteria for auditing the `kluster-llm` repository. It incorporates verified defect patterns, architectural constraints from `.specify/memory/architecture_constitution.md`, and adversarial verification lessons.

---

## 1. Architecture Constitution & Boundary Invariants

### 1.1 Inverted Dependency & Composition Root Isolation
- [ ] **Check**: Do feature modules import from composition roots (`kllm.agent` or `kllm.cli`)?
  - **Normative Rule**: Per `architecture_constitution.md`, composition roots are strictly `kllm.agent` and `kllm.cli`. Modules may depend on `shared/` and declared gateways, NEVER on composition roots.
  - **Inspection Target**: Check all `src/kllm/modules/*/endpoints.py` for helper functions like `def _host(): from kllm.agent import runtime as host; return host` or direct imports from `kllm.agent.runtime`.
  - **Remediation**: Pass required state, schedulers, and services into module router factories (`create_router(service, state)`) from `kllm.agent.runtime` at wiring time, or expose shared accessors through `src/kllm/shared/`.
- [ ] **Check**: Does importing a package trigger runtime side effects or module reloading?
  - **Inspection Target**: `src/kllm/agent/__init__.py`.
  - **Pattern**: `if already: importlib.reload(wiring)`.
  - **Hazard**: Triggers duplicate singleton initialization, re-executes background scheduler instantiations, and breaks `isinstance()` checks across reloaded types.

---

## 2. Process Supervision & Native Lifecycle

### 2.1 Process Tree Termination & Cleanup
- [ ] **Check**: Does `stop_process` respect graceful termination and clean up the entire process tree on all platforms?
  - **Inspection Target**: `crates/kllm-native/src/process.rs:809-840`.
  - **Pattern**: Discarding `force` (`let _ = force;`) and calling `child.kill()` unconditionally for tracked children.
  - **Hazard**: On Windows, `child.kill()` calls `TerminateProcess`, which terminates only the root process. Child processes spawned by workers or model runtimes are orphaned.
  - **Remediation**:
    - On Windows, attach spawned processes to a Win32 Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
    - If `force == false`, send `SIGTERM` (POSIX) or generate `CTRL_BREAK_EVENT` (Windows) and wait for a grace period before escalating to force kill.

### 2.2 Process Liveness Polling Overhead & Parsing Ambiguity
- [ ] **Check**: Is process liveness checked via expensive CLI subprocess invocations?
  - **Inspection Target**: `crates/kllm-native/src/process.rs:860-870`.
  - **Pattern**: Spawning `tasklist.exe /FI "PID eq {pid}" /NH` and checking `.contains(&pid.to_string())`.
  - **Hazard**: Spawning console processes every 500ms incurs 20–50ms CPU latency per probe. Unparsed substring matching on stdout yields false positives if PID digits appear in headers, error messages, or memory counts.
  - **Remediation**: Use native Win32 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid)` and `GetExitCodeProcess` to check `STILL_ACTIVE` in sub-microsecond time without string parsing.

---

## 3. Platform Telemetry & System Counters

### 3.1 Cross-Platform Process Counters
- [ ] **Check**: Are process performance counters supported across all target operating systems (Windows, macOS, Linux)?
  - **Inspection Target**: `crates/kllm-native/src/usage.rs:48-65` and `crates/kllm-native/src/network/relay.rs:46-65, 162`.
  - **Pattern**: Non-Windows/macOS platforms returning `Err("unsupported_counter".into())`.
  - **Hazard**: `NetworkBridge::validate` and `serve()` on Linux workers call `check_child()`, which requires valid process counters. When unsupported, Linux worker bridges fail to start or reject all inbound RPC streams.
  - **Remediation**: Parse `/proc/<pid>/stat` (starttime ticks, utime, stime) and `/proc/<pid>/statm` (RSS pages) on Linux. Fall back gracefully if detailed counters are unavailable.

### 3.2 Hardware Discovery Subprocess Overhead
- [ ] **Check**: Does hardware enumeration invoke PowerShell scripts?
  - **Inspection Target**: `crates/kllm-native/src/hw.rs:359-395`.
  - **Hazard**: Calling `powershell.exe -NoProfile -Command ...` incurs .NET CLR initialization overhead (400–1200ms) on each hardware query.
  - **Remediation**: Query the Windows Registry (`SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}`) via `RegOpenKeyExW` or use DXGI directly.

---

## 4. Asyncio & Threading Lifecycles

### 4.1 Coroutine Task Abandonment on Timeout
- [ ] **Check**: Are timed-out coroutines explicitly cancelled during application lifecycle operations?
  - **Inspection Target**: `src/kllm/agent/application_lifecycle.py:75-90`.
  - **Pattern**: Calling `asyncio.wait({task}, timeout=...)` and raising `PersistenceError` on timeout without invoking `task.cancel()`.
  - **Hazard**: Abandoned coroutines continue running in the background while subsequent lifecycle teardown stages close databases and destroy runtime directories.

### 4.2 Indefinite Blocking in Synchronous-to-Async Bridges
- [ ] **Check**: Does synchronous code bridge into async loops without timeouts?
  - **Inspection Target**: `src/kllm/modules/managed_network/interactors/run_admission.py:23-35`.
  - **Pattern**: `asyncio.run_coroutine_threadsafe(coroutine, loop).result()`.
  - **Hazard**: If the async operation stalls (e.g. distributed socket lock, peer timeout), calling `.result()` with no timeout permanently blocks the worker thread pool, rapidly exhausting FastAPI request threads.
  - **Remediation**: Provide `future.result(timeout=30.0)` and handle `concurrent.futures.TimeoutError`.

---

## 5. Concurrency & State Safety

### 5.1 Dictionary Iteration Data Races in ClusterState
- [ ] **Check**: Is `ClusterState.save()` protected by synchronization locks?
  - **Inspection Target**: `src/kllm/shared/state.py:197-210`.
  - **Pattern**: Calling `asdict(self)` directly on shared dataclass containing mutable dictionaries (`members`, `services`).
  - **Hazard**: Concurrent background tasks (`membership_maintenance`, `usage_collector`) and FastAPI handlers mutating member records trigger `RuntimeError: dictionary changed size during iteration`.
  - **Remediation**: Guard mutations and serialization with a `threading.RLock` and take shallow copies of collection dictionaries before calling `asdict()`.

### 5.2 Sticky Gap States in Event Rings
- [ ] **Check**: Does lock contention in native event emitters cause permanent health degradation?
  - **Inspection Target**: `crates/kllm-native/src/network/address_events.rs:44-56, 115-125`.
  - **Pattern**: In `Shared::emit`, encountering `try_lock() == Err` immediately sets `self.lost.store(true)`. In reader, `lost.load()` is checked but never cleared with `swap(false)`.
  - **Hazard**: Transient lock contention marks the observer as permanently failed (`"gap"`), which causes `src/kllm/shared/platform/base.py` to abort network preflight barriers with `PlatformRefusal("OWNERSHIP_CONFLICT")`.
  - **Remediation**: Use a bounded channel or short wait instead of `try_lock()`; only set `lost` if capacity is exhausted; reset `lost` on read via atomic swap.

---

## 6. Security, Cryptography & Network Protocols

### 6.1 Constant-Time Key & Token Verification
- [ ] **Check**: Are cluster join keys and authorization tokens compared using constant-time algorithms?
  - **Inspection Target**: `src/kllm/agent/runtime.py:860-870`.
  - **Pattern**: `if x_kllm_key != state_ref.current().join_key:`.
  - **Hazard**: Standard string equality short-circuits on byte mismatches, creating timing attack vulnerabilities.
  - **Remediation**: Always use `secrets.compare_digest(provided or "", expected or "")`.

### 6.2 RFC-Compliant Bearer Token Parsing
- [ ] **Check**: Is HTTP Bearer authentication parsing case-insensitive?
  - **Inspection Target**: `src/kllm/shared/auth.py:30-40`.
  - **Pattern**: `provided.removeprefix("Bearer ").strip()`.
  - **Hazard**: Rejects standard clients sending `bearer <token>` or `BEARER <token>` per RFC 7235 / RFC 6750 with HTTP 401.
  - **Remediation**: Check `.lower().startswith("bearer ")` and slice accordingly.

### 6.3 Windows Subprocess Executable Resolution (CWE-426)
- [ ] **Check**: Are Windows system binaries invoked with absolute paths?
  - **Inspection Target**: `src/kllm/modules/managed_network/interactors/identity.py:105-130`.
  - **Pattern**: Invoking `["whoami", ...]` or `["icacls", ...]`.
  - **Hazard**: Untrusted PATH search executes planted binaries in the current working directory or user PATH.
  - **Remediation**: Resolve `whoami.exe` and `icacls.exe` explicitly under `%SystemRoot%\System32`.

---

## 7. Model Downloads & Network Transfer Resilience

### 7.1 Download Timeouts & Range Resumes
- [ ] **Check**: Does large file streaming configure granular timeouts and HTTP Range resumes?
  - **Inspection Target**: `src/kllm/modules/models/interactors/download_transfer.py:50-82`.
  - **Pattern**: Single `timeout=5.0` float passed to `httpx.stream`, and deleting `.part` file on any exception.
  - **Hazard**: Multi-gigabyte model downloads abort completely on any 5-second network stall, discarding hours of download progress.
  - **Remediation**: Configure `httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=30.0)`, preserve `.part` files on failure, and resume via `headers={"Range": f"bytes={existing_bytes}-"}`.

---

## 8. Svelte 5 Dashboard Runes & Feeds

### 8.1 WebSocket Tag Classifier Inclusivity
- [ ] **Check**: Does the live update parser reject valid logs with unexpected sources or tags?
  - **Inspection Target**: `dashboard/src/lib/live/updates.ts:108-150, 386-390`.
  - **Pattern**: `tagMatchesSource` returning `false` for unlisted sources (`primacpp`, `system`, `network`), triggering `socket.close(1008)` (Policy Violation).
  - **Hazard**: Ingesting logs from new engines or subsystems severs the dashboard's live connection.
  - **Remediation**: Default unclassified or unfamiliar sources/tags to `'unclassified'` instead of failing payload validation.

### 8.2 Bounded Collection Memory in Frontend State
- [ ] **Check**: Do deduplication collections grow unbounded in memory?
  - **Inspection Target**: `dashboard/src/lib/modules/logs/interactor.svelte.ts:140-145, 426-435`.
  - **Pattern**: `seenIds = new Set<string>()` accumulating every event ID across the lifetime of the visit without eviction.
  - **Hazard**: Browser memory leaks in long-running dashboard sessions.
  - **Remediation**: Cap `seenIds` to a maximum watermark (e.g. 5,000 items) or evict IDs corresponding to truncated rows.
