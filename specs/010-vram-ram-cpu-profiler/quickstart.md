# Validation Guide: Native Resource Probes

**Status:** Planned acceptance guide. The new functions and `tests/test_native_usage.py`
below are implementation deliverables, not commands claimed to pass in this planning run.
Run from the repository root after implementation. Contract details live in
[native-probes.md](contracts/native-probes.md), [usage-transport.md](contracts/usage-transport.md)
and [data-model.md](data-model.md).

## 1. Prerequisites and supported setup

- Windows WDDM discrete GPU for required live dedicated-used/free verification; Apple Silicon
  Mac for unified-memory verification. Intel Mac and integrated/multi-GPU Windows hosts verify
  their respective support rows. Record OS build, GPU model/driver and logical CPU count.
- Ordinary user account by default. Permission restrictions should produce counter-specific
  unavailable results; do not elevate simply to conceal an access-denied case.
- Existing repository environment, Rust toolchain and Python 3.12 through uv. Follow
  [agent-install.md](../../docs/agent-install.md) if not installed; primary bootstrap is:

```text
python scripts/install.py
```

After changes to native dependencies/bindings, rebuild the actual wheel through supported tooling:

```text
uv run python scripts/build.py --skip-ui
uv run python -c "from kllm import native; assert native.native_available(); assert hasattr(native.require_native(), '_UsageCollector'); print('native collector loaded')"
```

Keep `llama-release` unchanged for this feature. Existing inference validation uses the same
model/backend and pinned binaries before and after; a new native collector needs no pin change.

## 2. Deterministic and boundary checks

```text
cargo test --manifest-path crates/kllm-native/Cargo.toml
uv run pytest -q tests/test_native_usage.py tests/test_usage.py tests/test_dashboard_api.py tests/test_dashboard_live.py tests/test_membership.py tests/test_gpus.py tests/test_accel.py tests/test_cluster_optimizations.py
node --test dashboard/tests/usage-live.test.mjs dashboard/tests/live-updates.test.mjs dashboard/tests/usage-history.test.mjs
npm --prefix dashboard run check
npm --prefix dashboard run build
```

Implementation adds the native integration file using the existing pytest framework. By
default its tests use controlled counters/clock inputs, temporary managed-child records and
test-owned processes only. They must not overwrite the operator's real runtime/state files.
Cross the real rebuilt PyO3 serialization and actual private-helper pipe/termination boundary.
Do not count a mock-only `Sampler` check as proof of Rust or platform integration.

Minimum checks:

- First CPU baseline, 12.5%-on-eight normalization, invalid/short intervals, counter reset,
  suspend/time discontinuity and >64-CPU denominator arithmetic.
- Free vs available, integer byte validation, invalid overflow, measured zero vs null,
  mixed/unified pools, duplicate enumeration, partial/empty totals, and ambiguous mappings.
- First-probe GPU enumeration failure: empty devices plus explicit discovery error/unsupported
  while host readings survive. Two GPUs referencing host:ram are accepted and count the pool
  once; conflicting pool backing definitions are rejected.
- Exact process creation token, exit/reuse/access denial, no child and legacy record adoption.
- Stalled host helper with healthy GPU and stalled GPU with healthy host: response <=2s;
  four callers still use at most two children. Inject malformed/oversized helper frames,
  delayed old-generation replies, EOF, kill/reap failure and parent death. No new child
  starts in a quarantined slot. Ordinary close returns to the warmed resource baseline.
- Individually valid helper outputs that exceed the merged-message budget: strip optional
  detail first, then produce only the offending source's payload-limit error. Resources and
  legacy history that fit separately but exceed one complete ingest body are split before
  transmission, with independent acknowledgements and visible history failure if only live
  delivery succeeds. Check actual UTF-8 envelope/framing bytes at and above the 64 KiB limit.
- Old extension, Linux fallback, old/new master-worker combinations, strict payload validation,
  no history row for no-child snapshots, and resource freshness through cached live replay.
- After an acknowledged generation reset, delayed old-generation uploads and obsolete reset
  requests must not change live values/watermarks; retries of the current reset are idempotent
  and duplicate/lower sequences cannot renew freshness. A restarted master requires a newly
  issued generation. Exercise these cases through the registration/ingest routes.
- Existing capacity/planner/UI history tests remain unchanged in meaning. Run the full
  `uv run pytest -q` suite before merge because process identity touches shared agent paths.

## 3. Host-only direct smoke on Windows and macOS

```text
uv run python -c "import json,time; from kllm import native; a=native.probe_resources(node_id='probe-check', managed_processes=[]); print(json.dumps(a,indent=2)); time.sleep(2); b=native.probe_resources(node_id='probe-check', managed_processes=[]); print(json.dumps(b,indent=2)); native.close_resource_probes()"
```

Expected: processes is empty; first CPU is initializing, later CPU has a valid interval;
valid host RAM returns on the first probe. All metrics carry source/scope/unit/time/status.
Windows free RAM may be explicitly unsupported while available is measured. Apple Silicon
references host:ram once and reports device-wide GPU used/free unsupported, never the
profiler's own allocations or an 80% budget as usage. An Intel discrete device is not shared.

Repeated calls made less than 200 ms apart may reuse a CPU observation; its original timestamp
must remain unchanged. Memory samples can still refresh. A device enumeration failure is
distinguishable from a successful empty device list.

## 4. Native accuracy and sustained-run checks

Provide opt-in hardware tests in the same new integration file using the test-only environment
variable `KLLM_TEST_HARDWARE=1`. Without it they skip explicitly. Hardware tests record the
actual prerequisites and never convert unavailable required Windows GPU readings into a pass.

Windows PowerShell:

```powershell
$env:KLLM_TEST_HARDWARE = '1'
uv run pytest -q -s tests/test_native_usage.py -k hardware
Remove-Item Env:KLLM_TEST_HARDWARE
```

macOS:

```sh
KLLM_TEST_HARDWARE=1 uv run pytest -q -s tests/test_native_usage.py -k hardware
```

The hardware selection must exercise these workloads and print/store an evidence summary:

1. **SC-001/006:** Launch a test-owned registered child with touched resident pages and a
   sustained CPU load. Compare host/process counters with an independent reader (psutil or
   OS tools using matching definitions) for 30 paired two-second observations. CPU tolerance
   five percentage points; memory max(5% of reference, 64 MiB); at least 29/30 observations
   must pass each applicable metric. Observation skew <=1s. Verify stop/relaunch and identity.
2. **SC-002:** On a discrete Windows GPU run a fixed GPU workload or steady inference workload.
   Collect 30 paired used/free readings with a trusted device-wide reference, such as Task
   Manager/driver tooling whose scope and capacity accounting are documented. Record capacity
   separately and calculate reference free only for the same verified dedicated pool. Require
   29/30 pairs within max(5% of reference, 64 MiB). If no independent GPU reference is automated,
   the test must report pending manual comparison, not claim SC-002 passed.
3. **SC-003/005:** Observe CPU-only, shared, mixed, unreadable and multiple-device cases. Use
   deterministic cases for hardware not available, recording which real configurations remain
   unverified. Sum only unique physical pools; incomplete totals explicitly list exclusions.
4. **SC-004:** Declare the supported required host/process/device counters before the run and
   establish CPU baselines; failure to establish them fails the run. Four concurrent callers
   then request the same managed-process selection once every two seconds for ten minutes.
   At least 95% of all timed requests must return all required supported measurements with
   observations <=2s old at response time. Coalesced results retain their original timestamps.
   Measure latency from public call entry, not after dispatch: p95 <=1s and max <=2s across
   all timed requests, including busy/error/timeout results. Those results count as unsuccessful
   measurements, not exclusions; later CPU reinitialization is also counted. Only initial
   warm-up is outside the timed interval, and declared unsupported counters are excluded from
   the required measurements. The healthy run excludes intentional faults and host suspension.
   Record successful/total request counts, p95/max latency, result timestamps, peak helper
   count, process-tree RSS and handle/file-descriptor counts. No crashes, mixed identities or
   stale relabeling. After close/reap and equivalent warm baseline, retained growth <=16 MiB
   and <=5 handles. Run GPU/host-stall, late-reply and parent-death injection separately;
   verify <=2s responses and preserved unaffected readings without leaking extra helpers.

For Windows group-aware host CPU, run on >64 logical CPUs before claiming that hardware row.
Accuracy reference formulas on macOS must match the source categories; Activity Monitor's
compressed-memory total is not interchangeable with active+wired. Do not compare unlike counters.
If measuring inference overhead, record before/after matched workload/model/backend and raw runs;
the planning documents make no inference-throughput improvement claim.

## 5. Existing agent end-to-end

Use the existing start/join/run workflow in [cli.md](../../docs/cli.md) on a private cluster.
After a normal local master start, or against an already running local master:

```text
uv run kllm status
uv run python -c "import json,httpx; from kllm.state import DEFAULT_CONTROL_PORT; print(json.dumps(httpx.get(f'http://127.0.0.1:{DEFAULT_CONTROL_PORT}/v1/usage/live').raise_for_status().json(),indent=2))"
```

If the local control port was overridden, substitute that configured port. Validate:

1. Before launching a model/worker child, live resources show host/device observations,
   reading is null and process_state says no managed process. No new lifetime/history row.
2. Launch a managed workload using existing controls. Compare resource CPU/RSS with the legacy
   projected process reading, accounting for observation time; heartbeat/register start matches.
3. Stop and restart the child. Host/device readings continue; old process lifetime stops;
   replacement CPU initializes and receives a new exact identity.
4. Close dashboard tabs for a minute, then reopen. Existing process recording continues;
   new host-only live data does not imply new host history retention.
5. Disconnect a worker. Last visible observations become stale; reconnect does not invent
   missing measurements or renew a cached source time.
6. Test an old master in an isolated compatibility fixture: new worker sends old payloads only.
   Test an old worker/new master: legacy history remains usable with resources absent.
7. Simulate recording failure using the test fixture, not a real user's database: host-only
   live resources still work and recording failure stays visible.

UI verification: select host-only and busy nodes, distinguish process/host memory and actual
free/available RAM, inspect shared/dedicated and partial totals, and confirm missing counters
display unavailable rather than zero. Existing controls, history and keyboard access remain usable.

## Evidence and completion

### User-owned Mac verification

The user elected to run physical Mac checks independently; no remote snapshot or workflow
was pushed. Use the current working-tree implementation on that Mac (including the new
usage Rust modules, Python helper and tests). For an already bootstrapped repository:

```sh
uv run python scripts/build.py --skip-ui
uv run python -c "from kllm import native; assert native.resource_probe_available(); print(native.require_native().__file__)"
cargo test --manifest-path crates/kllm-native/Cargo.toml
uv run pytest -q
KLLM_TEST_HARDWARE=1 uv run pytest -q -rs -s tests/test_native_usage.py -k hardware
```

For a fresh machine first follow docs/agent-install.md's `python3 scripts/install.py` flow.
The native import assertion prevents an old extension's module-level test skips from looking
like successful verification. Save the hardware command output; it includes reference-pair
and ten-minute latency/resource summaries and takes roughly 12 minutes. The Windows GPU
reference test is intentionally skipped on Mac. No exposed Metal device means topology
acceptance is explicitly skipped and still unverified. Apple Silicon must show a shared pool
before that hardware row is closed. Intel Mac classification must be checked on Intel hardware.

Mac runtime tasks are not marked passed until those outputs are available. Linux runtime and
additional GPU/CPU hardware rows likewise require their actual environments; cross-compilation
alone is never substituted for them.

Record commands/results and the actual support matrix in the change record: OS versions,
GPU/driver, CPU count, model/backend if used, sample counts/tolerances, latency percentiles,
resource-growth results and all skipped/manual scenarios. Missing macOS/Windows hardware is
an explicit outstanding verification item, never a pass. Update existing monitoring and
troubleshooting docs with supported counters and recovery (`uv run python scripts/build.py`).
