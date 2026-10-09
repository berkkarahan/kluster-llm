# Tasks: VRAM / RAM / CPU Resource Profiler

**Input**: Design documents from `specs/010-vram-ram-cpu-profiler/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [native contract](contracts/native-probes.md),
[transport contract](contracts/usage-transport.md), [quickstart.md](quickstart.md).

**Tests**: Included to implement the spec's explicit SC-001–SC-006 acceptance checks and
constitution II/III behavior/boundary requirements. Use existing Cargo/pytest/Node tools.
Tests listed before implementation must expose the missing behavior first, then pass;
reuse existing checks instead of duplicating them. Hardware checks require their actual OS/device.

**Organization**: Preserve spec story IDs; execute P1 stories US1, US2, US5 before P2 stories
US3, US4. Each story has its own acceptance checkpoint; shared prerequisites are explicit.
This document assigns work only. All checkboxes remain unchecked until implementation and
the relevant verification are complete.

## Format: `[ID] [P?] [Story] Description`

- `[P]` permits parallel execution only within the listed ready batch, after its prerequisites.
- `[USn]` maps directly to the numbered story in spec.md, not the phase number.
- Paths are repository-relative. New files: `crates/kllm-native/src/usage.rs`, its platform
  siblings, `python/kllm/usage_probe.py`, `tests/test_native_usage.py`, and feature `validation.md`.
- Keep existing exports/facade names, current state/history formats and Linux behavior.
  Add only optional identity fields; no new service, framework, history database or inference policy.
- The two helper slots are host/process and GPU; they are private owned children, not managed
  inference processes. No thread-per-probe or respawn before confirmed reap.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the actual baseline and native dependencies in the existing repository.

- [X] T001 Check the installed toolchain and existing native/monitoring tests using `docs/agent-install.md` and `specs/010-vram-ram-cpu-profiler/quickstart.md`; create `specs/010-vram-ram-cpu-profiler/validation.md` recording commands, current failures, OS/driver/CPU inventory, available Windows/macOS hardware, and unverified acceptance rows without changing runtime state.
- [X] T002 Add the plan's minimal target-specific bindings in `crates/kllm-native/Cargo.toml` and resolve `crates/kllm-native/Cargo.lock`: Windows `windows` 0.57 features for PDH/DXGI/process information, macOS `libc`/`objc2` 0.6/`objc2-metal` 0.3 needed features; preserve existing PyO3/sysinfo dependencies and Linux compilation, with no new Python package.

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the typed contract, private binding and bounded ownership shared by all stories.

**Gate**: Complete T001–T008 before story implementation. Platform measurement is introduced
in the story phases; unsupported source responses must already be truthful and structured.
The foundation completes pure field/schema validation with controlled source responses;
actual OS identity, managed-registry matching and discovered-pool verification remain in
their story phases and are not prerequisites for this gate.

- [ ] T003 Add contract and actual helper-process boundary checks in `tests/test_native_usage.py` for the planned exports, measured/null consistency, duplicate/non-finite/oversized frames, no-child requests, first-probe discovery failure with empty devices and surviving host data, consistent shared-pool references versus conflicting definitions, source isolation, concurrent selection isolation, deadline accounting, generation rejection, kill/reap/quarantine, idempotent close and parent death; use controlled source responses, test-owned subprocesses and temporary state, never the operator's runtime records. Exercise individually valid source replies whose merge would exceed 64 KiB, assigned reply budgets, optional-detail removal and source-only payload-limit errors.
- [ ] T004 Define and fully validate the concrete resource/host/metric/device/process/pool schemas with serialization checks in `crates/kllm-native/src/usage.rs` from `specs/010-vram-ram-cpu-profiler/data-model.md`: `schema_version` is "Integer `1` for this additive resource contract"; `node_id` is "Nonempty string, <=128 characters; matches authenticated membership remotely"; `generation` is "32 lowercase hexadecimal characters; identifies the authorized resource session"; `sequence` is "Integer 1–9007199254740991, not bool; strictly increases for new snapshots within a generation"; `snapshot_id` is "Exactly `<generation>:<sequence>` with sequence in canonical decimal; <=128 characters"; request/completion times are "UTC ISO 8601, microseconds, trailing `Z`; not counter timestamps"; devices are "0–32 DeviceReadings, unique `device_key`; an empty list means no GPUs only when discovery succeeded"; processes are "0–8 ProcessReadings corresponding to requested managed selections"; collector is "`native` or explicit `legacy`; platform and bounded diagnostic status"; logical CPU count is "positive integer or null with explicit discovery error"; host memory uses `pool_id="host:ram"` and total/used/free/available byte metrics. Enforce metric value "Nonnegative integer bytes or finite percentage 0–100, or `null`", unit "`bytes` or `percent_machine`; field fixes which unit is allowed", scope "`host`, `process`, or `device`; owner supplies corresponding identity", status "`measured`, `initializing`, `unsupported`, or `error`", reason "`null` for measured; stable reason code for other states", detail "Optional sanitized text <=256 characters, never raw command output", observation time "Original UTC measurement/failed-attempt time; required even if unavailable", CPU interval fields "Required together for measured CPU, otherwise null; monotonic duration >0", and source "Bounded counter/formula identifier, e.g. `win.pdh.gpu_adapter.dedicated_usage`". Reject unknown version-1 reason codes and enforce "Measured implies non-null value and null reason; other statuses imply null value and a reason." Implement all structural device/process constraints quoted in T013/T026/T033 here, including bounds/enums, unique device/process identities and consistent repeated pool references. Discovery error/unsupported with an explicit reason permits empty devices; reject conflicting pool definitions, not valid aliases. Later story tasks reuse these schemas for runtime validation, with no untyped wire escape hatch or deferred schema prerequisite.
  T004 reason-enum constraint, verbatim: "Reason codes include `baseline_required`, `interval_too_short`, `counter_reset`, `clock_discontinuity`, `unsupported_counter`, `permission_denied`, `process_exited`, `process_identity_mismatch`, `identity_unverified`, `device_removed`, `ambiguous_pool`, `timeout`, `source_busy`, `source_quarantined`, `invalid_counter`, `native_unavailable`, `enumeration_limit`, and `source_error`. Unknown codes are rejected in version 1 input."
  CPU interval refinement: "Required together for measured CPU, otherwise null; UTC `Z` endpoints, end equals observed_at, monotonic duration 200–86400000 ms". The earlier positive-duration wording does not waive this bound.
- [X] T005 Export private `_UsageCollector(source)` and `collect_json(request_json)` from `crates/kllm-native/src/lib.rs`, dispatching to concrete cfg-gated collectors in `crates/kllm-native/src/usage.rs`; validate host/gpu selection, release the GIL around native work, bound serialized input/output, and preserve `probe_capacity`, `list_gpus`, planner and launch compatibility. Until a source implementation lands, emit explicit unsupported metrics rather than fabricated measurements; do not route sampling through legacy capacity probes.
- [ ] T006 Implement the private `--source host|gpu` helper entry in `python/kllm/usage_probe.py` using the rebuilt native export, version/generation/request correlation and bounded UTF-8 newline-delimited JSON: <=64 KiB including envelope/newline, strict fields, no duplicate keys/non-finite values/invalid UTF-8 and empty GPU process selections. Validate `reply_budget_bytes` as a positive integer <=65536; remove optional diagnostic detail first, then return a compact source_error identifying the payload limit if a complete reply still exceeds its assigned budget, never silently truncating measurements. Keep stdout protocol-only; run one native sample at a time while the control reader can observe parent EOF, and close resources on normal exit.
- [ ] T007 Implement the two persistent source slots in `python/kllm/usage_probe.py`: one request per source, bounded reader/writer queues, identical-request coalescing, per-request deadline including startup/lock wait, stop waiting at 1.8s and respond within 2s, partial-source results, stale-generation rejection and no unbounded worker creation. Reserve complete snapshot/totals/failure-shape and resource-only ingest-envelope overhead before assigning source reply budgets; verify final UTF-8 serialized size and preserve unaffected sources on payload-limit failure. Apply "stopped → starting → ready → collecting → ready; deadline/failure → terminating → stopped only after reap; unconfirmed exit → quarantined"; kill/reap before replacement, Windows kill-on-close Job Objects, macOS parent-pipe EOF exit, and bounded shutdown. Satisfy T003 failure/cleanup/merged-budget checks before integration.
- [ ] T008 Add public `probe_resources(*, node_id, managed_processes=None)` and `close_resource_probes()` in `python/kllm/native.py`, reusing T007 supervision and one active node identity per parent; `None`/`[]` yields host/device requests with empty processes, invalid schema input raises ValueError before dispatch, all counter failures remain structured. Check `_UsageCollector` capability explicitly; missing/old exports return native_unavailable plus rebuild guidance and Linux returns unsupported without replacing its existing Sampler. Enforce eight unique selections/32 devices/complete-message 64 KiB boundaries and T004 schema validation. Pass all schema, serialization, helper-lifecycle and deadline checks using controlled source responses; real platform measurement and runtime identity checks belong to their story phases. Run T003 against the rebuilt wheel through `uv run python scripts/build.py --skip-ui` and record outcomes in `specs/010-vram-ram-cpu-profiler/validation.md`.

**Checkpoint**: The real binding/helper boundary returns validated structured failures and
meets source-isolation/cleanup checks; no platform measurement is falsely claimed.

## Phase 3: User Story 1 — Read live resource usage on demand (Priority: P1)

**Goal**: Deliver a working direct probe on Windows with host CPU/RAM and device-wide memory;
the next P1 increment supplies macOS parity. All collection remains Rust.

**Independent Test**: Request two probes around a controlled load change on a Windows WDDM
machine. RAM is available immediately, first CPU initializes, later CPU has a valid interval;
device used/free match a scope-compatible reference and blocked sources preserve healthy readings.

### Tests for User Story 1

- [ ] T009 [P] [US1] Add deterministic Rust acceptance cases in `crates/kllm-native/src/usage.rs` for CPU initialization/200ms minimum intervals, reset/count/suspend/wall-clock discontinuities, checked byte arithmetic, real zero versus unavailable, and unchanged timestamps on reused observations; assert public result semantics rather than duplicate the formula implementation.
- [X] T010 [P] [US1] Extend `tests/test_native_usage.py` with direct host-only probe and source-failure integration cases from US1/SC-001–SC-004, exercising actual PyO3/helper serialization, changing memory/CPU inputs, four callers and original timestamps; prepare opt-in hardware comparison recording under `KLLM_TEST_HARDWARE=1` without claiming hardware success from mocked inputs.

### Implementation for User Story 1

- [X] T011 [US1] Implement persistent host CPU baselines and common observation arithmetic in `crates/kllm-native/src/usage.rs`: "absent → initializing → valid interval → measured; invalid interval/identity/count/resume → initializing", monotonic durations, <200ms reuse without renewed timestamps, finite machine-normalized values and explicit errors for invalid deltas; pass T009.
- [X] T012 [US1] Implement checked Windows host sampling in `crates/kllm-native/src/usage/windows.rs`: GlobalMemoryStatusEx total/available, used=total−available, true free unsupported rather than relabeled, group-aware PDH Processor Information total CPU and all-group logical count; isolate counter failures, retain valid observations and deterministically close native handles.
- [ ] T013 [US1] Implement Windows DXGI discovery and adapter-wide PDH dedicated/shared memory in `crates/kllm-native/src/usage/windows.rs`; validate each instance/status and match LUID plus physical index, not process budget/current usage or inference index. Reuse T004 schemas enforcing device key "opaque <=128-character OS identity scoped to this node/boot", name "sanitized <=256 characters", vendor "bounded identifier or `unknown`", kind "`dedicated`, `shared`, `mixed`, or `unknown`", memory "up to two MemoryViews, dedicated and shared" with backing_pool_id "null or `host:ram`". Shared free remains unsupported; derive dedicated free only from compatible same-pool capacity/usage and return ambiguous_pool otherwise. On first-probe discovery failure with no known identities, return empty devices plus discovery error/unsupported and an explicit reason while preserving host data; only measured empty discovery means no GPUs.
- [ ] T014 [US1] Connect the Windows sources to snapshot assembly in `crates/kllm-native/src/usage.rs`, `crates/kllm-native/src/lib.rs`, and `python/kllm/native.py`: populate node/generation/source times, host/device values, explicit missing-source observations and safe one-pool totals without calling inference capacity enumeration; ensure host-only `processes=[]` and pass T009/T010 through the public deadline-bearing wrapper.
- [X] T015 [US1] Execute the Windows direct-probe and 30-pair host/GPU comparisons from `specs/010-vram-ram-cpu-profiler/quickstart.md`, using `tests/test_native_usage.py` hardware checks and a documented independent reference; record actual OS/driver/pool compatibility and 29/30 pass thresholds in `specs/010-vram-ram-cpu-profiler/validation.md`. An all-unavailable Windows discrete used/free result cannot pass SC-002; unavailable hardware leaves this task open, not falsely checked.

**Checkpoint**: US1 is a Windows technical demonstration with real measured data and bounded
failure behavior. It is not completion of the requested two-platform feature.

## Phase 4: User Story 2 — Consistent Windows and macOS readings (Priority: P1)

**Goal**: Apply the same direct-probe contract to macOS, identifying actual shared versus
dedicated memory, and retain existing Linux/capacity behavior.

**Independent Test**: Run the identical direct smoke on Windows and macOS; verify byte units,
machine-normalized CPU, matching status/interval semantics, and Apple Silicon shared-pool
identity without invented GPU usage. Check an Intel Mac's discrete classification separately.

### Tests for User Story 2

- [ ] T016 [P] [US2] Add platform parity and macOS source cases in `tests/test_native_usage.py` for first/second CPU samples, defined Mach memory categories, unsupported GPU used/free, Apple Silicon shared backing and Intel discrete classification, including macOS source-block/parent-death behavior.
- [X] T017 [P] [US2] Extend compatibility checks in `tests/test_gpus.py` and existing `crates/kllm-native/src/hw.rs` tests to preserve old capacity/Metal budget signatures and Linux paths while the new optional collector is present, absent or unsupported; do not change expected legacy capacity semantics to make the new profiler pass.

### Implementation for User Story 2

- [X] T018 [US2] Implement checked macOS host RAM/CPU in `crates/kllm-native/src/usage/macos.rs` using sysctl/Mach page and CPU counters: "used is active+wired, available free+inactive, free free-page bytes"; reject overflow/inconsistent values, label these definitions explicitly, preserve compressed-memory distinctions, and use T011 interval/reset handling with per-counter unavailable states.
- [X] T019 [US2] Implement Metal registryID discovery and hasUnifiedMemory classification in `crates/kllm-native/src/usage/macos.rs`, applying T013's device field constraints; identify Apple Silicon backing `host:ram`, distinguish Intel dedicated devices and return unsupported device-wide used/free where public facilities do not establish them. Never use currentAllocatedSize, recommendedMaxWorkingSetSize or the legacy 80% budget as device-wide usage/capacity.
- [ ] T020 [US2] Wire both platform implementations and target-gated capability behavior in `crates/kllm-native/src/usage.rs`, `crates/kllm-native/src/lib.rs`, and `python/kllm/native.py`; pass T016/T017, preserve cfg-gated Linux code, and rebuild actual Windows/macOS wheels through `scripts/build.py` with no unsupported deployment-target bump.
- [ ] T021 [US2] Run the Windows/macOS direct parity checks, macOS 30-pair host comparisons and Linux compatibility checks from `specs/010-vram-ram-cpu-profiler/quickstart.md`; record exact platform prerequisites, counter definitions and unverified Intel/>64-CPU rows in `specs/010-vram-ram-cpu-profiler/validation.md`, leaving required missing hardware checks open.

**Checkpoint**: Direct host/device probes use one truthful contract on both target platforms;
unsupported macOS GPU attribution is explicit, not substituted with a process-local measurement.

## Phase 5: User Story 5 — Managed-process CPU and RAM (Priority: P1)

**Goal**: Attribute normalized CPU and resident bytes to verified managed process lifetimes,
independently of the dashboard or new transport.

**Independent Test**: Register a test-owned child, observe one busy core on eight as 12.5%
and 2 GiB resident memory with controlled readings, then verify real launch/exit/restart and
permission-denial paths. The old lifetime never acquires the replacement's measurements.

### Tests for User Story 5

- [ ] T022 [P] [US5] Add Rust process behavior checks in `crates/kllm-native/src/usage.rs` for exact start tokens, 12.5%-on-eight normalization, RSS units, PID reuse between separate reads, baseline cleanup, no-child output, and permission failure distinguished from exit.
- [ ] T023 [P] [US5] Add registry/identity propagation checks in `tests/test_usage.py` and `tests/test_membership.py`: one canonical start string across launch/heartbeat/history, legacy records with/without trustworthy saved start, rejection of unregistered/duplicate selections, reuse of PID, and host/device survival when process collection fails.

### Implementation for User Story 5

- [X] T024 [P] [US5] Implement PID-targeted Windows GetProcessTimes/GetProcessMemoryInfo sampling in `crates/kllm-native/src/usage/windows.rs`, retaining full creation FILETIME, cumulative CPU and WorkingSetSize with checked minimum-right handles; verify requested identity across reads and never enumerate unrelated processes.
- [X] T025 [P] [US5] Implement PID-targeted macOS task/BSD information reads in `crates/kllm-native/src/usage/macos.rs`, retaining start seconds+microseconds, cumulative CPU and resident bytes with before/after identity checks and permission/exit distinction; reuse the shared CPU baseline contract.
- [ ] T026 [US5] Implement runtime process identity validation and lifetime orchestration in `crates/kllm-native/src/usage.rs`, reusing the completed T004 schemas: pid "positive platform PID, not bool", role "`master`/`worker`", start_token "opaque exact OS creation token", started_at "canonical compatibility UTC", "0–8 unique selections", state "`running`, `exited`, or `unavailable`", identity_verified "boolean, false if start cannot be read or does not match", and lifetime_id "existing node/PID/canonical-start encoding for old history projection". Apply "Tokens are never JavaScript numbers", compare actual OS identity, return requested-old-lifetime mismatch with null metrics rather than adopting a replacement, preserve each process's independent CPU baseline and retire unused handles; pass T022. Do not defer or redefine structural validation already completed in the foundation.
- [X] T027 [US5] Capture exact creation identity during successful managed launch in `crates/kllm-native/src/process.rs`, expose optional metadata via existing launch results in `crates/kllm-native/src/lib.rs` and `python/kllm/native.py`, and persist optional token/canonical start fields in existing `python/kllm/runtime.py` child records; retain old record readability, existing mandatory fields and native child ownership, with no helper children entered as inference processes.
- [ ] T028 [US5] Propagate one canonical captured timestamp and optional exact token through `python/kllm/agent/run_route.py`, `python/kllm/agent/join_flow.py`, `python/kllm/agent/__init__.py` launch/relaunch and `_tracked_process_start` paths, and `python/kllm/membership.py`; preserve facade patch surfaces, verify saved start before legacy adoption, and return identity_unverified instead of adopting a bare PID.
- [X] T029 [US5] Enforce caller-registry selection matching in `python/kllm/native.py` and populate process results through `python/kllm/usage_probe.py`; reject malformed/duplicate/unregistered selections before dispatch, preserve healthy host/device data on process errors, keep CPU interval and actual-start verification together, and pass T023 plus real rebuilt-binding lifetime checks in `tests/test_native_usage.py`.
- [ ] T030 [US5] Extend and run opt-in process CPU/RSS and real child exit/restart acceptance in `tests/test_native_usage.py` on Windows and macOS using temporary test-owned registration; record SC-001/006 tolerances and identity evidence in `specs/010-vram-ram-cpu-profiler/validation.md`, including deterministic PID-reuse/access-denied cases where live reproduction is unavailable.

**Checkpoint**: The public probe independently supplies host, device and verified process
readings; process failures do not erase node readings or cross lifetime boundaries.

## Phase 6: User Story 3 — Per-device multi-GPU memory (Priority: P2)

**Goal**: Identify each GPU/pool once, preserve valid peers during a device failure and report
truthful complete/partial totals without adding shared RAM twice.

**Independent Test**: Probe two controlled devices with different memory use, then duplicate
one discovery alias and make one device unreadable. Verify stable attribution, one-count pools,
partial contributors and unchanged valid-device values; exercise real multi-GPU hardware where present.

### Tests for User Story 3

- [ ] T031 [P] [US3] Add Rust multi-device tests in `crates/kllm-native/src/usage.rs` for distinct dedicated pools, two GPU views sharing host:ram with different device-scoped usage and one-count physical capacity, rejection of conflicting backing metadata, shared/mixed backing, duplicate aliases, hot removal/reappearance, invalid free subtraction, unknown discovery, partial/null totals and the 32-device bound.
- [ ] T032 [P] [US3] Add device projection checks in `tests/test_usage.py` for duplicate display names, reordered/filtered inference indices, unique OS identity mapping and unmatched devices retained in raw resource results with legacy values unavailable rather than guessed.

### Implementation for User Story 3

- [ ] T033 [US3] Implement discovered-device/pool runtime verification in `crates/kllm-native/src/usage.rs`, reusing T004 structural validation and T013/T019 field bounds; verify inventory_device_id "optional legacy inference-device ID only after unique verified mapping", shared `backing_pool_id="host:ram"`, at most two dedicated/shared views and unique device keys. Accept repeated references to the same physical pool when identity/kind/backing agree, reject conflicting definitions, count physical capacity once and never sum differing shared device usage as extra physical memory. Use the transition "discovered identity → observed/unavailable → removed; rediscovery establishes a new valid observation" and reject unknown aliasing as ambiguous_pool instead of assuming distinct capacity.
- [ ] T034 [P] [US3] Extend Windows adapter collection in `crates/kllm-native/src/usage/windows.rs` to all discovered adapters up to the declared bound, handling LUID/physical-index aliases, mixed vendors, multiple physical nodes, per-device failures and removal/re-enumeration without order-based identity or silently complete truncation.
- [X] T035 [P] [US3] Extend macOS Metal collection in `crates/kllm-native/src/usage/macos.rs` to all registry identities, preserving per-device unified/dedicated classification, removal and unsupported statuses without inventing multiple host physical pools.
- [ ] T036 [US3] Implement checked MemoryTotals in `crates/kllm-native/src/usage.rs`: dedicated_capacity/dedicated_used sum verified distinct pools, physical_capacity includes host RAM once, unit `"bytes"`, status "`complete`, `partial`, `unavailable`", included_pool_ids/missing_pool_ids and contributing times; enforce "Partial requires at least one valid contributor and lists exclusions. No valid contributors means null/unavailable." Treat empty dedicated inventory as unavailable/no dedicated devices and do not expose a combined host-plus-GPU-used figure; pass T031.
  T036 completeness constraint, verbatim: "Complete means discovery succeeded and every relevant counter/pool identity is known." Retain the data model's contributing observation times on every total; aliases share pool IDs and are counted once.
- [X] T037 [US3] Implement unique OS-to-inference-device projection helpers in `python/kllm/usage.py` using available verified identity metadata without changing capacity inventory semantics; keep raw device identity visible when no safe mapping exists and legacy device values unavailable, never match ambiguous names or list positions; pass T032.
- [ ] T038 [US3] Execute deterministic two-device/shared/partial checks and available real multi-GPU verification from `specs/010-vram-ram-cpu-profiler/quickstart.md`; record pool identities, unsupported cases and SC-003/005 evidence in `specs/010-vram-ram-cpu-profiler/validation.md`, explicitly distinguishing simulated topology from hardware validation.

**Checkpoint**: Each device/pool remains independently interpretable; unknown readings or
aliases cannot inflate memory or disguise an incomplete total as complete.

## Phase 7: User Story 4 — Serve probes through the existing agent (Priority: P2)

**Goal**: Deliver native resources through existing monitoring and node details, including
no-child nodes, while retaining old workers/masters, process history and optional legacy sensors.

**Independent Test**: A local master and registered worker publish native resource snapshots
with/without a managed child; compare controlled direct and agent values. Verify old-version
payloads, unavailable/blocked sources, no-child history absence, and stale cached UI behavior.

### Tests for User Story 4

- [ ] T039 [P] [US4] Extend `tests/test_dashboard_api.py` with strict optional resources envelopes, empty readings only with valid resources, exact registered node/PID/start matching, complete-body 64 KiB limits, individually fitting resources/history requiring split delivery, independent acknowledgements when only live delivery succeeds, valid repeated pool references versus conflicting definitions, unknown fields/version, invalid intervals/non-finite values, whole-envelope validation before mutation, durable-versus-transient acknowledgement, recording failure with usable host-only data, and old/new master-worker negotiation cases.
  Include authenticated session establishment, idempotent reset retry, obsolete compare-and-swap reset rejection, delayed previous-generation uploads, duplicate/lower sequences and fresh establishment after master restart, per contracts/usage-transport.md.
- [X] T040 [P] [US4] Add resource replay/freshness and existing-details checks in `tests/test_dashboard_live.py`, `dashboard/tests/usage-live.test.mjs`, and `dashboard/tests/live-updates.test.mjs`: no-child host readings, initializing versus zero, shared/partial labels, free versus available, disconnect, metric-specific age and no timestamp renewal through caches; keep history and keyboard interactions intact.

### Implementation for User Story 4

- [X] T041 [P] [US4] Add resource validation and a once-per-cycle native Sampler path in `python/kllm/usage.py`, enforcing T004/T026/T033/T036 constraints at the trust boundary and maintaining sample's legacy point/None contract plus independently accessible resources. Collect host/device data without a child, project only verified process lifetimes with conservative original observation times, preserve old history JSON and Linux/old-extension fallback with explicit collector=legacy notices.
- [X] T042 [P] [US4] Add optional ResourceSnapshot/Metric/process/device/pool types and node resource_age_seconds/resource_freshness fields in `dashboard/src/lib/types.ts` directly matching `specs/010-vram-ram-cpu-profiler/data-model.md`; retain existing required usage/history fields, nullable values and string start tokens, with no frontend-only alternative status or unit enums.
- [X] T043 [US4] Integrate host-only sampling, one transient snapshot per registered node and managed sampler ownership into `_usage_loop`/lifespan in `python/kllm/agent/__init__.py`; prune resources on membership removal, discard obsolete generations, update membership before publishing a confirmed process exit and close/reap native helpers on shutdown without unbounded joins. Do not make live-only resources depend on a process or SQLite row.
  Track active master-issued generation, last reset request ID and highest accepted sequence per node in transient state; implement the contract's authenticated, idempotent compare-and-swap registration reset and reject uploads from inactive generations without mutation.
- [X] T044 [US4] Extend existing `usage_ingest`/`usage_live` in `python/kllm/agent/__init__.py` using the transport contract: preserve X-Kllm-Key/private-node checks and old payloads, advertise `resource_schema_versions: [1]`, enforce whole-body 64 KiB bounds, permit zero legacy rows only with valid resources, accept consistent shared-pool references and reject conflicting definitions. Commit history before resources within a combined request; acknowledge separate history/resource requests independently without cross-request atomicity, and never advance last_successful_save for resource-only data or hide failed history behind a live acknowledgement. Retain usable live resources with visible recording errors; pass the receiver cases in T039.
- [X] T045 [US4] Implement worker capability discovery/caching and reconnect invalidation in `python/kllm/agent/__init__.py`, `python/kllm/agent/join_flow.py`, and `python/kllm/membership.py`; send new envelopes only to an advertised compatible master, send unchanged process bodies to old/unknown masters, expose missing remote resource support and never let ingest renew membership liveness. Before transmission, size the complete UTF-8 envelope: if history and resources fit separately but exceed the combined limit, send bounded legacy-history batches and a resource-only request, preserving per-request acknowledgements and recording failures without silent truncation. Reject advertised-version protocol errors visibly rather than downgrading rejected malformed data; pass the rollout/split-delivery cases in T039.
  Serialize session resets, ignore mismatched reset responses, stamp each outgoing snapshot once with the acknowledged generation/sequence, preserve IDs on retry, and explicitly re-establish after restart/exhaustion without changing observation times.
- [X] T046 [US4] Preserve existing optional GPU utilization/temperature/power collection in `python/kllm/usage.py` under a bounded single-in-flight path separate from the new probe deadline; retain original sensor times, require safe device mapping, avoid duplicate native memory probing and never overwrite native readings with capacity/legacy estimates. Add focused regressions to `tests/test_usage.py` and verify optional sensors cannot extend a native request beyond two seconds.
- [X] T047 [US4] Carry resource snapshots through `python/kllm/agent/live.py`, `dashboard/src/lib/live-updates.ts`, and `dashboard/src/lib/store.svelte.ts` without refreshing observation times on replay; apply "accepted newer observation → fresh (<10s per metric) → stale; disconnect forces stale regardless of arrival", summary age from the oldest measured dynamic metric, no dynamic metrics→unavailable, and immediate disconnection aging while preserving existing process-history freshness.
- [X] T048 [US4] Update existing `dashboard/src/lib/components/NodeMap.svelte` and `dashboard/src/lib/components/NodeUsageDetails.svelte` to prefer optional native resource data, show host/process scope, true free/available distinctions, device identities, shared/dedicated pools, partial totals and unavailable reasons using existing GiB/accessibility conventions; retain old payload behavior, process-history charts and existing controls, and pass T040.
- [ ] T049 [US4] Run rebuilt native→Sampler→authenticated ingest→live delivery and dashboard checks in `tests/test_native_usage.py`, `tests/test_usage.py`, `tests/test_dashboard_api.py`, `tests/test_dashboard_live.py`, and `dashboard/tests/usage-live.test.mjs`; exercise the real private-cluster no-child/launch/stop/reconnect workflow from `specs/010-vram-ram-cpu-profiler/quickstart.md` and record scope/compatibility/timeout outcomes in `specs/010-vram-ram-cpu-profiler/validation.md` without replacing real boundary checks with mocks.

**Checkpoint**: All five stories are integrated. Old capacity consumers and process history
remain usable; resource collection works independently of an open dashboard or managed child.

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Complete the explicit delivery evidence, documentation and repository checks.

- [X] T050 [P] Update existing monitoring/support sections in `docs/cli.md` and `docs/troubleshooting.md` with the public probe surface, ordinary-user prerequisites, actual supported counter matrix, macOS GPU limitations, free-versus-available definitions, shared totals, fallback/timeout/recovery behavior and `uv run python scripts/build.py --skip-ui` rebuild guidance; describe only implemented, evidenced behavior.
- [X] T051 [P] Complete and run SC-004 sustained/hardware acceptance in `tests/test_native_usage.py`: establish baselines or fail, then four callers request the same managed-process selection every two seconds for ten minutes; >=95% of all timed requests must return all required supported host/process/device measurements <=2s old at response time, with original timestamps on coalesced results, p95<=1s/max<=2s from public entry. Declare unsupported counters before the run; only initial warm-up is outside it. Busy/error/timeout and later reinitialization remain in the denominator and count as unsuccessful measurements. Run on a healthy host without deliberate faults/suspension; separately verify source-stall/late-reply/parent-death deadlines and preserved unaffected readings. Require process-tree retained growth <=16 MiB and <=5 handles after cleanup and at most two helpers; retain successful/total counts and raw summaries for T053, never claiming skipped/manual GPU comparisons as passes.
- [X] T052 Run `cargo test --manifest-path crates/kllm-native/Cargo.toml`, rebuilt-wheel checks via `uv run python scripts/build.py`, full `uv run pytest -q`, Node tests listed in `specs/010-vram-ram-cpu-profiler/quickstart.md`, `npm --prefix dashboard run check`, and `npm --prefix dashboard run build`; record results in `specs/010-vram-ram-cpu-profiler/validation.md`, preserving known baseline failures and identifying missing platform checks explicitly. Repeat tests only for new edits/failures or previously unverified boundaries.
- [ ] T053 Consolidate T015/T021/T030/T038/T049/T051/T052 evidence in `specs/010-vram-ram-cpu-profiler/validation.md`, including a FR-001–FR-015 and SC-001–SC-006 completion matrix, real Windows used/free reference pairs, macOS host/process comparisons, actual vendor/device coverage, unavailable hardware and exact remaining follow-up commands; complete only after required Windows/macOS acceptance succeeds, not merely because the design or mock cases pass.
- [X] T054 Perform the mandatory memory review/capture workflow against `specs/010-vram-ram-cpu-profiler/memory.md` and `specs/010-vram-ram-cpu-profiler/memory-synthesis.md`, recording only feature-local implementation/verification watchpoints there and proposing genuinely durable lessons through `.specify/extensions/memory-md/commands/speckit.memory-md.capture.md`; honor governance's explicit user-approval requirement before any durable `docs/memory/` change and retain the plan as a point-in-time artifact.

## Dependencies & Execution Order

### Phase dependencies

```text
Setup T001–T002
  → Foundation T003–T008
    → US1 T009–T015       (Windows live probes)
      → US2 T016–T021     (macOS/parity)
        → US5 T022–T030   (managed processes, P1)
        → US3 T031–T038   (multi-device, P2)
          US5 + US3 → US4 T039–T049 (agent/live/UI)
            → Polish T050–T054
```

- Default serial order is task ID order, respecting P1 before P2. US3 has no semantic
  dependency on US5, but both edit platform/core files: do not run their edits concurrently.
- US1 requires Foundation only. US2 needs US1's shared working probe, but its validation
  does not need process attribution or the agent. US5 and US3 can each be tested through
  the direct probe after US1/US2; neither requires dashboard integration.
- US4 requires verified output shapes from US1/US2/US5/US3 and uses their public interfaces.
- Source implementation can proceed when an earlier story's hardware run is unavailable,
  but that acceptance task stays unchecked. This permits development, not false completion:
  T053/full delivery remains blocked by required missing platform evidence.

### Within-phase dependencies and safe parallel batches

All batches below start only after earlier phase prerequisites; tasks not named in a batch
are serial. A task marked `[P]` is not permission to ignore its tests or upstream prerequisites.

| Ready prerequisite | Parallel batch | Why safe |
|--------------------|----------------|----------|
| T008 | T009 + T010 | Rust behavior checks versus Python boundary checks |
| US1 implementation | T016 + T017 | Native integration file versus capacity compatibility test files |
| US2 implementation | T022 + T023 | Rust lifetime checks versus Python registry/member checks |
| T022/T023 written, T011 baseline available | T024 + T025 | Windows and macOS process source files |
| US1/US2 implemented; US5 file edits finished in serial strategy | T031 + T032 | Rust pool checks versus Python projection checks |
| T033 | T034 + T035 | Separate OS GPU files |
| All source stories implemented | T039 + T040 | API tests versus live/browser tests |
| T039/T040 written | T041 + T042 | Python sampling/validation versus TypeScript type declarations |
| T049 | T050 + T051 | Operational docs versus sustained-run test implementation/execution |

T036 follows both OS GPU changes; T037 follows its projection tests and the validated device
contract. T026 follows both process sources; T027→T028→T029 propagate identity before validating
full selection integration. US4 implementation runs T041/T042→T043→T044→T045→T046→T047→T048→T049.
Do not parallelize edits to `usage.rs`, `native.py`, `usage.py`, the agent facade, or validation.md.

## Parallel Examples by User Story

These examples describe ready work, not an instruction to spawn agents during task generation.

```text
US1, after T008:
  T009: crates/kllm-native/src/usage.rs behavioral tests
  T010: tests/test_native_usage.py direct/protocol tests

US2, after US1 implementation:
  T016: tests/test_native_usage.py platform parity
  T017: tests/test_gpus.py + crates/kllm-native/src/hw.rs compatibility

US5, after T022/T023 and common CPU logic:
  T024: crates/kllm-native/src/usage/windows.rs process counters
  T025: crates/kllm-native/src/usage/macos.rs process counters

US3, after T033:
  T034: crates/kllm-native/src/usage/windows.rs multi-adapter handling
  T035: crates/kllm-native/src/usage/macos.rs multi-device handling

US4, after contract checks T039/T040 are written:
  T041: python/kllm/usage.py native adapter and validation
  T042: dashboard/src/lib/types.ts additive resource types
```

## Coverage and Completion Signals

| Spec requirements / outcome | Primary tasks |
|-----------------------------|---------------|
| FR-001, FR-002, FR-003: GPU memory, RAM semantics, CPU | T004, T011–T015, T018–T021, T024–T026 |
| FR-004, FR-006: scope, units, timestamps, explicit unavailable | T004–T010, T014, T016, T026, T041–T048 |
| FR-005: both platforms with existing Linux compatibility | T002, T016–T021, T024/T025, T052 |
| FR-007, FR-008: per-device/partial/shared pool totals | T013/T019, T031–T038, T048 |
| FR-009, FR-015: existing agent, compatibility and private boundary | T005–T008, T017, T039–T049 |
| FR-010, FR-014: concurrency, original timestamps, bounded latency/cleanup | T003, T006–T011, T040, T046/T047, T051 |
| FR-011, FR-012, FR-013: verified managed-process CPU/RSS and failure isolation | T022–T030, T041/T043/T045 |
| SC-001: 30 matched host/process reference observations | T010/T015, T016/T021, T030 |
| SC-002: real Windows used/free; truthful Apple shared pool | T013/T015, T019/T021, T053 |
| SC-003, SC-005: metadata, no invented values, no double counting | T009/T010, T031–T038, T040/T048 |
| SC-004: ten-minute four-caller performance/resource envelope | T003/T007, T051/T053 |
| SC-006: exits, PID reuse, permissions, no managed process | T022–T030, T039/T043/T049 |

Protocol version 1 and all quoted data-model constraints are enforced at Rust serialization,
Python input/ingest validation, and TypeScript representation as applicable. Cross-platform
source checks prove defined semantics, not a universal vendor capability claim.

## Implementation Strategy

### MVP first

1. Complete Setup/Foundation, then US1 to demonstrate trustworthy direct Windows probes.
2. Complete US2 before describing the profiler as Windows/macOS capable.
3. Add US5 to satisfy the user's confirmed host-wide plus managed-process CPU/RAM requirement.
   **Suggested useful cross-platform native MVP: US1 + US2 + US5**, validated through the public
   local probe. It is an incremental deliverable, not completion of all feature requirements.
4. Add US3 multi-device guarantees and US4 existing-agent/UI delivery; complete Polish before
   claiming the whole specification delivered. No selected user requirement is silently dropped.

### Incremental delivery

- Keep capacity/planning and legacy history behavior stable throughout. New counters must use
  explicit unavailable states until implemented/validated; never mark a missing backend measured.
- Execute each phase's independent checks and keep earlier checks passing. Preserve capture
  identity and observation timestamps before adding remote transport or UI projection.
- Use ready parallel batches only; preserve fixture patch surfaces and keep imports consistent
  with `.specify/memory/architecture_constitution.md`.
- Update validation evidence as work completes. No task is complete merely because it is written,
  a mocked test passes, or unavailable hardware caused an acceptance check to skip.

## Notes

- Implementation checkpoint: code and Windows acceptance are present; checked rows denote the
  independently recorded completed outcomes. Some unchecked rows contain implemented code
  but retain incomplete platform/conformance verification. Mac runtime/wheel acceptance,
  wider device/fault matrices, and full T053 certification remain open; see validation.md.
- Verification follow-up: 508 Python tests pass with five explicit skips; all 54 installer
  checks pass after shared Windows/quoted SDK-path repair. Intel/Apple Silicon Mac and Linux
  type checks pass. The user elected to run Mac runtime/Metal verification independently;
  T020/T021/T030/T053 stay unchecked until actual results are supplied. No CI snapshot was pushed.
- T052 records the latest green local suite; the former installer failures and their fix are
  documented. T054 completed proposal review; durable registration awaits approval
  and available memory tools, with no permanent project-memory writes.

- Memory preparation used the repository's documented Markdown fallback; cache/token savings
  statistics are unavailable. No new governance exception or durable-memory approval was created.
- Maintain all commands/paths in the existing quickstart. A discovered design conflict must be
  resolved in the relevant contract before implementing an incompatible workaround.
- The task-generation workflow ends here; implementation is a separate command.
