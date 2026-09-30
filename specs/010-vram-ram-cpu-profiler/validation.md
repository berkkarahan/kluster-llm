# Resource profiler implementation evidence

## Delivery status

Native collection, the supervised Python API, existing agent transport, and dashboard
presentation are implemented. Windows hardware acceptance is verified for the machine
below. Full specification completion remains open for user-owned Mac runtime/packaging
acceptance and broader hardware/compatibility cases; unchecked tasks
must not be read as implementation or hardware passes.

## Environment and baseline

- Windows 11 build 26200, x64; 16 logical processors; Python 3.12.13; Rust 1.98.0.
- GPU: AMD Radeon RX 9070 XT, driver 32.0.31041.1004.
- Initial Rust baseline: 28 passed. Initial existing monitoring selection: 156 passed,
  two Starlette/httpx deprecation warnings.
- No usable Mac hardware/SDK runtime is available in this session. Cross-checks below are
  type checks, not linked Mac wheels or real Metal/Mach validation.
- Existing ignore rules cover build outputs and credentials. No llama.cpp pin, inference
  placement policy, public RPC binding or durable history schema was changed.

## Implemented boundaries

- Rust checked Windows CPU/RAM/process and adapter-wide GPU counters; macOS Mach/libproc
  CPU/RAM/process and Metal identity/shared-pool discovery. Unsupported Mac device-wide
  usage/free remains explicit rather than measured through the caller's own allocations.
- Typed, bounded source serialization and validation, CPU intervals/reset rules, exact
  process start tokens, duplicate identity checks and bounded hot-removal/discovery state.
- Two owned helper processes, deadline-bearing public probes, generation correlation,
  bounded frames, quarantine/reaping and parent-death cleanup.
- Authenticated versioned resources beside legacy process history; idempotent session
  reset, replay rejection, older-master fallback and host-only snapshots independent of DB writes.
- Node/details UI consumes resource values, free/available distinctions, shared/partial
  labels and original observation age, with cache/browser staleness checks.
- A legacy device projection requires `inventory_device_id`, set only when a normalized
  display name is unique on both the native device and the llama.cpp inventory. Duplicate
  names stay unlinked, so raw native devices remain visible and name/order coincidence is
  not history attribution. Linked dedicated capacity is the `--list-devices` total; the OS
  total stays in `detail`. Extra CUDA sensors run outside the critical probe path only for
  verified mappings.

## Build and regression results

| Check | Result |
|-------|--------|
| `cargo test --manifest-path crates/kllm-native/Cargo.toml` | 34 passed |
| `uv run python scripts/build.py --skip-ui` | Rebuilt and installed Windows wheel successfully |
| `uv run maturin develop` | Refreshed the checkout's editable extension successfully |
| `uv run pytest -q` | 508 passed, 5 opt-in/platform checks skipped, zero failures |
| Node usage/live/history tests from quickstart | 25 passed |
| `npm --prefix dashboard run check` | 0 errors, 0 warnings |
| `npm --prefix dashboard run build` | Production build succeeded |
| Cross `cargo check`, `aarch64-apple-darwin` | Passed |
| Cross `cargo check`, `x86_64-apple-darwin` | Passed |
| Cross `cargo check`, `x86_64-unknown-linux-gnu` | Passed |

Cross commands set `PYO3_CROSS_PYTHON_VERSION=3.12`; rustup's Apple Silicon standard-library
target was added for the aarch64 check. Chrono supplies RFC 3339 parsing/UTC formatting instead
of a custom calendar implementation. All dependency manifests and lockfiles are synchronized.

The three failures in the previous full-suite run were:

- `tests/test_install_script.py::test_discards_cmake_build_with_dangling_openssl_path`
- `tests/test_install_script.py::test_dangling_linker_flag_alone_does_not_discard_build`
- `tests/test_install_script.py::test_cache_reset_flags_only_scrub_dangling_linker_flags`

They reproduced in isolation in the unchanged installer before this follow-up. The user then
requested cross-platform verification closure. The root cause in the shared
`scripts/install_llama_source.py:dangling_cached_paths` was fixed: Windows absolute paths were
ignored, and whitespace tokenization could damage SDK paths containing spaces. Native absolute
path detection, whole package-path values and stdlib shell parsing for linker flags now retain
quoted paths and Windows backslashes. Two new real-filesystem regression checks failed before
the fix and pass after it; all **54 installer tests** pass. The latest full suite is green.

## Verification ownership / Mac handoff

- No local Mac runner or WSL distribution is installed, and no SSH host or self-hosted GitHub
  runner is configured. GitHub access to the private repo was available, but the user declined
  hosted verification and stated they will run it independently.
- No verification branch, CI workflow, commit or remote push was created.
- Apple Silicon and Intel Mac runtime/wheel/Metal checks remain explicitly **unrun, user-owned**.
  Linux runtime checks remain unrun; the passing Linux result is a cross-target type check.
- The Mac topology check now skips explicitly when Metal exposes no devices, preventing an
  empty device list from falsely satisfying GPU hardware acceptance.
- Copy/use the current profiler working-tree changes on the Mac, including untracked source
  modules and tests; an untouched checkout of remote main does not contain this implementation.
- Run the Mac handoff commands in quickstart.md and return the captured output before checking
  off T020/T021/T030/T053. A skipped hardware case remains unverified, not passed.

## Windows hardware acceptance

Opt-in commands use `KLLM_TEST_HARDWARE=1` with pytest selections in
`tests/test_native_usage.py`. Default tests use a controlled GPU helper and do not depend
on GPU drivers. The hardware reference/stress tests assert they measure the real workload
PID with a touched allocation, not an idle virtualenv redirector.

### CPU and RAM — 30 paired observations

`-k hardware_host_and_process_accuracy`: passed.

- Host CPU, process CPU, resident RAM, host total, used and available RAM each passed **30/30**.
- CPU differences <=5 percentage points. Memory differences <=max(5% of reference, 64 MiB).
- Matched two-second intervals; reference observation skew <=1 second.
- Busy child used approximately 6.2% of this 16-logical-processor host; resident memory
  approximately 78 MB matched psutil's actual-process working set.
- Windows true-free RAM is unsupported, not substituted with available RAM.

### GPU memory — 30 paired observations

`-k hardware_windows_gpu_reference`: passed **30/30** dedicated-used/free comparisons.

- Independent PowerShell WDDM readings matched the same adapter instance; registry QWORD
  memory capacity was read independently from DXGI.
- Registry capacity: 17095983104 bytes. DXGI capacity and that registry figure differ by
  approximately 115 MiB on this driver; the raw figures are retained, not rounded or replaced.
- Usage and derived-free comparisons both stayed within the declared max(5%, 64 MiB)
  tolerance. This certifies this tested topology/driver, not every GPU's accounting behavior.
- Existing desktop GPU activity was the workload; no new model download or inference
  throughput comparison was performed.

### Ten-minute concurrent collection

`-k hardware_sustained_probe_delivery`: passed using real helper/workload PIDs.

| Measurement | Final recorded result |
|-------------|-----------------------|
| Requests / fresh successful requests | 1200 / 1200 |
| Four-caller cadence | Every two seconds, ten minutes |
| p95 / maximum elapsed latency | 0.016 s / 0.016 s (Windows monotonic timer resolution) |
| Agent/helper process-tree RSS growth | 475136 bytes (~0.45 MiB) |
| Retained parent RSS change after close | -159744 bytes |
| Parent handle change after close | -29 |
| Owned helper count | 2 |

Raw run output: ignored local `artifacts/resource-profiler-stress-final.log`.
Separately, timeout injection for either source, malformed/oversized/duplicate/non-finite
responses, previous-generation replies, credential filtering, explicit close and parent
death passed their boundary checks. Constant bound/error refinements made after this run
were rebuilt and covered by the final regression checks; the stress results identify the
executed workload, not an inference-speed claim.

### Real private-agent transport

`-k hardware_private_agents`: passed. Two real temporary loopback agents registered using
their own homes/key/ports, and the worker sent native snapshots to the master. Both nodes
appeared in live results. Worker CPU/RAM values were measured and fresh, processes were empty,
and the process history had **zero lifetimes**. Owned processes were terminated and reaped.
Existing user cluster state was not used for this test.

## Packaging/process corrections encountered

- A running Python process (PID 18056) mapped the old in-tree extension, preventing editable
  replacement with Windows error 32. After explicit user approval it was verified and stopped;
  the extension rebuild then succeeded. It was not automatically restarted.
- The earlier virtualenv launcher-based accuracy/stress observations were superseded: on
  Windows its Popen PID was an idle redirector, not the real workload/helper. The supervisor
  now owns the base interpreter with `__PYVENV_LAUNCHER__` preserving its venv context.
- No user process was stopped without that approval; later validation processes were test-owned.

## Requirement/outcome follow-up matrix

| Coverage | Verified here | Still needed |
|----------|---------------|--------------|
| FR-001–006 | Windows measurements, metadata/failures; all three target type checks | Mac runtime values and wheel import |
| FR-007–008 | Shared/partial schema checks, pool deduplication, removal/cache-bound tests | Real mixed/multi-GPU and Apple Silicon shared-pool checks |
| FR-009–010 | Real private-agent delivery, replay/caching and source supervision | Wider mixed-version/network recovery cases |
| FR-011–013 | Windows CPU/RSS/start identity, exit/mismatch, legacy adoption checks | Mac exit/restart/permission cases and real restricted-process checks |
| FR-014–015 | Deadline/fault and private validation checks; legacy capacity tests | Remaining maximum-payload/quarantine-failure matrix audit |
| SC-001 | Windows 30-pair pass | Mac 30-pair reference pass |
| SC-002 | Windows 30-pair pass | Apple Silicon topology/runtime acceptance |
| SC-003/005 | Metadata/shared/partial checks | Real additional hardware configurations |
| SC-004 | Windows 1200-request stress pass | Mac sustained-run evidence |
| SC-006 | Windows lifecycle/negative checks | Mac lifecycle/negative checks |

Required Mac follow-up, on the target machine after supported setup:

```sh
uv run python scripts/build.py --skip-ui
KLLM_TEST_HARDWARE=1 uv run pytest -q -s tests/test_native_usage.py -k hardware
```

Run the same normal Cargo/Python/dashboard commands in quickstart.md there. Linux runtime
regression, >64-logical-CPU Windows, Intel/NVIDIA/integrated/linked adapters, and real multi-GPU
rows remain unverified where hardware was unavailable. Full T053 delivery remains unchecked.
