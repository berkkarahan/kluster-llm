# Research: VRAM / RAM / CPU Resource Profiler

**Created:** 2026-09-29 | **Status:** Decisions resolved; hardware acceptance pending

Research covered native OS counters and the existing monitoring integration in parallel.
The pre-research governance gate passed: existing ownership and compatibility requirements
were identified; no exception was needed. Sources below support API semantics, not a claim
that this feature has been implemented or certified on all devices.

## 1. Reuse existing capacity and monitoring boundaries

**Decision:** Add a separate Rust usage collector; keep `hw::probe_capacity`, `list_gpus`,
and planner capacity/budget semantics unchanged. Integrate at `usage.Sampler` and existing
usage routes, not a new HTTP service.

**Rationale:** The native/Python GPU inventory describes inference backend selection and
reindexes devices. `Sampler` already owns telemetry, but currently requires a process.
`UsageStore` history needs a lifetime and `usage.validate` rejects extra keys. Host-only live
snapshots therefore require an additive transport shape, not a fake process/history record.

**Alternatives considered:** Replacing capacity globally changes fit/placement and Metal
headroom behavior. A sampler-only swap loses host-only readings and per-counter metadata.
A new service duplicates agent transport/lifecycle and is outside the spec.

**Evidence:** `crates/kllm-native/src/hw.rs`, `src/planner.rs`, `src/lib.rs`;
`python/kllm/native.py:list_gpus`; `usage.py:Sampler`, `validate`, `UsageStore`;
`agent/__init__.py:_usage_loop`, `usage_ingest`, `usage_live`;
`agent/live.py:LiveFeed`; `dashboard/src/lib/types.ts`.

## 2. Checked host/process readings rather than silently failed refreshes

**Decision:** Keep sysinfo for existing capacity. New Rust usage paths call checked native
facilities through target-specific bindings and enumerate only requested managed PIDs.

**Rationale:** sysinfo 0.33.1's public refresh API does not expose all needed per-counter
failure information. Windows `free_memory()` reuses available memory; process start time
loses subsecond precision; selected-PID refresh internally lists system processes.
These conflict with free-versus-available, exact lifetime, and inventory-scope requirements.

**Windows:** `GlobalMemoryStatusEx` supplies total/available RAM; used is total minus
available (non-available physical bytes). True free RAM is unsupported initially, not an
alias of available. PDH `Processor Information(_Total)\\% Processor Time` supplies host CPU
across processor groups after warm-up. `GetActiveProcessorCount(ALL_PROCESSOR_GROUPS)` supplies
the denominator. PID-targeted handles with minimum query/read rights, `GetProcessTimes`, and
`GetProcessMemoryInfo` supply exact creation FILETIME, cumulative CPU, and WorkingSetSize.
Check every call; a memory permission failure need not discard readable CPU/start identity.

**macOS:** `sysctl(hw.memsize)`, Mach VM/CPU statistics and page size provide host counters;
`proc_pidinfo` task/BSD information provides requested process CPU, resident bytes and start
seconds/microseconds. Document free as free pages, available as free plus reclaimable inactive
pages (bounded by physical total), and used as active plus wired pages (excluding compressed
backing unless explicitly represented separately). These are named counter definitions,
not an assertion that used+available=total or that Activity Monitor's displayed "Memory Used"
has identical accounting. Validate against a reference using the same formula; reject invalid
page arithmetic. Use raw CPU tick deltas and monotonic elapsed time.

**CPU rule:** `100 * delta_process_cpu_seconds / (delta_monotonic_seconds * logical_cpus)`;
host CPU uses all-CPU busy/total deltas or valid PDH equivalent. Intervals <200 ms reuse the
previous CPU observation without changing its timestamps, or initialize if none exists.
Counter reset, invalid delta, CPU-count change, lifetime change or detected suspend resets
the baseline. Out-of-range/non-finite values are unavailable, not clamped into a false reading.

**Alternatives considered:** Rebuilding `System` for every request repeatedly loses CPU
baselines. Calling `GetSystemTimes` without processor-group handling omits CPUs above 64.
Continuing Python psutil as the primary target-platform collector does not deliver the
requested Rust profiler; it remains the explicit Linux/old-extension compatibility path.

Sources:
- [sysinfo Windows implementation, v0.33.1](https://raw.githubusercontent.com/GuillaumeGomez/sysinfo/v0.33.1/src/windows/system.rs)
- [sysinfo Apple implementation, v0.33.1](https://raw.githubusercontent.com/GuillaumeGomez/sysinfo/v0.33.1/src/unix/apple/system.rs)
- [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)
- [GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo)
- [GetSystemTimes group limitation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemtimes)
- [Apple process structures](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/proc_info.h)

## 3. Windows device-wide GPU memory

**Decision:** Enumerate DXGI adapters and bind PDH `GPU Adapter Memory` dedicated/shared
usage to LUID and physical index. Use English counter registration to avoid localized names.
Dedicated free is derived only where one adapter/physical pool and capacity accounting match.

**Rationale:** VidMm adapter memory is system-wide and vendor/API-independent on supported
WDDM drivers. `DXGI_QUERY_VIDEO_MEMORY_INFO.CurrentUsage` is the calling application's usage;
`Budget` and `AvailableForReservation` are application limits, not device-wide free memory.
`DXGI_ADAPTER_DESC.SharedSystemMemory` is a maximum share, not a separate memory bank.

Preserve each counter's validity and observation time. Require successful instance discovery,
collection and counter status; registration alone is not a valid measurement. Multiple physical
nodes behind a LUID, duplicate adapters, hardware reservations or unknown mappings make capacity/
free unavailable rather than guessed. Dedicated capacity and usage must be tested as compatible
before enabling free derivation for that topology. Shared usage references host RAM and is
never added to it; shared free is unavailable. Do not assume NVIDIA/llama indices equal LUIDs.

**Alternatives considered:** PowerShell per sample is costly and locale/format fragile; the
existing tab-format path is not proven by mocked tests. NVML is vendor-specific and has WDDM
accounting qualifications. AMD ADLX and Intel Sysman expand dependencies/support effort; defer
until a concrete unsupported platform requires them. DXGI application-budget subtraction is wrong.

Sources:
- [Microsoft GPU accounting](https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/)
- [DXGI memory-info scope](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/ns-dxgi1_4-dxgi_query_video_memory_info)
- [DXGI adapter capacity fields](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc)
- [PdhAddEnglishCounterW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhaddenglishcounterw)
- [PdhGetFormattedCounterArrayW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw)

## 4. macOS GPU scope and unified memory

**Decision:** Enumerate Metal devices using registryID and hasUnifiedMemory. Unified devices
reference the host physical pool. Return device-wide GPU used/free unsupported initially;
Intel dedicated devices remain individually identified even when capacity/usage is unavailable.

**Rationale:** `currentAllocatedSize` concerns resources allocated by the caller's Metal
device; it is not an established cross-process counter. `recommendedMaxWorkingSetSize` is a
performance budget, not capacity/free. No stable public device-wide counter was established
by the research. The specification explicitly permits unavailable counters on macOS.

**Alternatives considered:** Treating every Mac as shared repeats the legacy capacity
assumption; treating 80% of RAM as usage invents data. IORegistry/private accelerator statistics
are not a verified stable initial contract. Reading the profiler's own Metal allocations
would mostly report its own near-zero use, not the inference workload.

Sources:
- [Metal device inspection](https://developer.apple.com/documentation/metal/device-inspection)
- [hasUnifiedMemory](https://developer.apple.com/documentation/metal/mtldevice/hasunifiedmemory)
- [currentAllocatedSize binding and SDK-derived semantics](https://docs.rs/objc2-metal/latest/objc2_metal/trait.MTLDevice.html#method.currentAllocatedSize)
- [recommendedMaxWorkingSetSize](https://developer.apple.com/documentation/metal/mtldevice/recommendedmaxworkingsetsize)

## 5. Bounded synchronous native sources

**Decision:** Two lazily started persistent Python helper children load the Rust collector:
host/process and GPU. The public wrapper supervises bounded newline-delimited JSON pipes,
source deadlines, process termination/reaping and per-source generation IDs. Collection itself
is Rust. Helpers are launched through `sys.executable -m kllm.usage_probe --source host|gpu`.

Implementation refinement: Windows venv launchers create a redirector plus a real interpreter.
The supervisor uses the base interpreter with `__PYVENV_LAUNCHER__` to retain the venv context
without an extra process, and binds its Job Object to the real helper. Acceptance workloads
likewise measure the actual child rather than an idle redirector. Other platforms retain
the direct sys.executable command.

**Rationale:** Timing out a waiter does not stop a blocked driver call. A fixed thread can
remain bounded but cannot guarantee reclaimable handles/cleanup on close. A separate helper
can be terminated; separate source slots preserve RAM/CPU if GPU blocks and vice versa.
This meets the deadline without a separate service installation or Rust async runtime.

Healthy helpers persist for baselines and latency. Source timeout initiates kill/reap; the
slot remains quarantined until exit is confirmed and cannot spawn another child meanwhile.
One reader/writer control path per slot, bounded input/output and no request-per-thread model.
Parent-death handling is explicit (Windows Job Object; macOS control-pipe watcher). Late
responses from terminated generations are discarded. Close and shutdown do not join a blocked
native call on the agent thread. Whole-OS scheduling/suspension is outside a user-space deadline.

**Alternatives considered:** Spawn-per-sample loses CPU state and increases startup cost.
Two immortal worker threads cannot reclaim resources after a permanent driver hang. A single
helper serializing host/GPU loses healthy-source readings. A second network daemon is unnecessary.

## 6. Identity, integration and backwards compatibility

**Decision:** Capture exact OS start token at managed launch, retain it in existing child
records as an optional field, and use a single canonical UTC compatibility timestamp. The
new helper validates requested identity; it never silently adopts a reused PID. Reattach after
agent restart only when saved start identity matches. Legacy records lacking precise identity
need verified saved start-time matching before binding, or process-unavailable until relaunch.

Send unchanged process readings plus an optional versioned live resource snapshot. Advertise
supported resource versions on existing live responses; workers omit new fields for old masters.
Add transient resources to `usage_live` independently of process history. UI uses raw resource
device IDs and pool labels; legacy chart attribution requires a unique verified device mapping.
Do not guess mapping from array order or an ambiguous duplicate name.

**Rationale:** Exact start tokens prevent PID reuse and conversion mismatches. Current ingest
rejects both unknown fields and worker readings without a registered process. Separate resource
validation allows no-child nodes while preserving authenticated worker identity.

**Alternatives considered:** Loosening all validation accepts malformed payloads; repurposing
existing `device_id` breaks inventory/history. Persisting host-only resources expands retention
scope. Failing hard on an old extension breaks working monitoring; expose compatibility fallback.

## Proposed support and required evidence

| Target | New host/process | GPU memory | Acceptance evidence |
|--------|------------------|------------|---------------------|
| Windows NVIDIA/AMD/Intel discrete, WDDM | Checked native counters | Adapter used; free only for compatible dedicated pool | 30 paired observations; at least one discrete device MUST pass used and free |
| Windows integrated | Same | Shared used when provided; shared free unavailable | Verify host pool counted once; no capacity/budget substitution |
| Windows missing/WDDM-incompatible provider | Same | Explicit unsupported counters | Failure/status check, not GPU accuracy pass |
| Apple Silicon macOS | Checked native counters | Unified identity; GPU used/free unsupported | Real host/process accuracy, shared-pool and lifetime checks |
| Intel macOS | Checked native counters | Actual device type; unavailable counters as needed | Real classification and host/process check |
| Linux | Existing psutil/native capacity | Existing behavior | Regression build/tests; no new counter certification |

Research observed local AMD WDDM counter availability (RX 9070 XT), but did not validate
identity/capacity pairing or run SC-002. All acceptance rows remain pending. Mixed/linked GPUs,
driver changes, and >64-logical-CPU hosts need explicit evidence before claiming coverage.
No unresolved technical clarification remains; missing hardware evidence is a delivery gate.
