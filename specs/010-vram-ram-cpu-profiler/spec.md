# Feature Specification: VRAM / RAM / CPU Resource Profiler

**Feature Branch**: `main` (current branch; no branch-creation hook registered)

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "write a custom VRAM + RAM + CPU profiler in rust for serving vram + ram probes in both windows and macos"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read live resource usage on demand (Priority: P1)

As a cluster operator (or the monitoring layer acting on their behalf), I want a single on-demand
probe that returns current VRAM, RAM, and CPU utilization so I can see how a machine is consuming
resources right now, without guessing from capacity figures.

**Why this priority**: Live usage — not static capacity — is the core of a profiler. Without this,
the feature is a re-label of already-existing capacity reporting.

**Independent Test**: On a machine with known live activity, invoke a probe and compare the returned
VRAM used/free, RAM used/free, and CPU utilization against an independent, trusted reading taken at
the same moment.

**Acceptance Scenarios**:

1. **Given** a machine with a running workload, **When** a probe is requested, **Then** the result
   reports current VRAM used and free, RAM used and free, and CPU utilization, each with units and
   a measurement timestamp.
2. **Given** two successive probe requests during changing load, **When** both are evaluated, **Then**
   the returned values reflect the load change rather than a cached or constant figure.
3. **Given** a measurement that the host cannot provide, **When** it is requested, **Then** the
   result reports that measurement as unavailable (with a reason where known), never as zero or a
   fabricated value.
4. **Given** a first CPU probe with no valid baseline, **When** it returns, **Then** CPU is marked
   warming up while available memory readings are returned; a later valid interval reports CPU
   percentage and its measurement interval.
5. **Given** GPU discovery fails on the first probe before any device is identified, **When**
   the probe returns, **Then** available host/process readings are preserved and the empty
   device list carries an explicit discovery failure, not a claim that the machine has no GPUs.

---

### User Story 2 - Get consistent readings across Windows and macOS (Priority: P1)

As an operator running a mixed cluster, I want the same probe surface to work on Windows and macOS
nodes so a worker's readings are comparable regardless of operating system.

**Why this priority**: The request explicitly targets both platforms; a profiler that only works on
one does not satisfy it.

**Independent Test**: Run the same probe on a Windows node and a macOS node and verify the returned
shape, units, and availability semantics are consistent for the resources each platform can report.

**Acceptance Scenarios**:

1. **Given** a Windows node, **When** a probe is requested, **Then** VRAM, RAM, and CPU readings are
   returned for the supported hardware, with unavailable measurements marked explicitly.
2. **Given** an Apple Silicon macOS node, **When** a probe is requested, **Then** RAM and CPU
   readings are returned and GPU memory is labeled shared/unified; an Intel Mac with a discrete
   GPU instead identifies its dedicated memory correctly. Unsupported GPU usage remains unavailable.
3. **Given** the same logical probe on both platforms, **When** results are compared, **Then** RAM and
   CPU use the same units and percentage semantics (total logical CPU capacity = 100%).

---

### User Story 3 - See per-device VRAM when multiple GPUs are present (Priority: P2)

As an operator with multi-GPU nodes, I want per-device VRAM usage so I can see which accelerator is
consuming memory, not just a single blended total.

**Why this priority**: Multi-GPU nodes are common in this product, and a blended total hides
imbalanced memory use; however, single-GPU and aggregate views are the higher-value first slice.

**Independent Test**: On a node with two known GPUs, request a probe and verify each device's used
and free memory is reported separately and identifiably, with a combined total that counts each
device once.

**Acceptance Scenarios**:

1. **Given** a node with two GPUs, **When** a probe is requested, **Then** each device is reported
   individually with its own used/free VRAM, and a combined total counts each device once.
2. **Given** a device whose live usage cannot be read, **When** the probe is requested, **Then** that
   device is marked unavailable without corrupting other devices' readings; any sum of the known
   readings is explicitly partial, not a complete total.

---

### User Story 4 - Reuse the profiler from the existing agent (Priority: P2)

As the monitoring layer, I want the profiler callable from the existing agent runtime so resource
readings feed the node workspace without a separate service or manual step.

**Why this priority**: The profiler only delivers value when the existing dashboard/agent path can
consume its probes; it is lower priority than getting the readings right because it builds on stories
1–3.

**Independent Test**: Request readings through the existing agent and verify the same units,
scope, availability, and controlled resource values as a direct probe.

**Acceptance Scenarios**:

1. **Given** the agent runtime, **When** it requests a resource probe, **Then** it receives the same
   VRAM/RAM/CPU readings a direct probe returns.
2. **Given** an agent on a platform without a supported measurement, **When** it requests a probe,
   **Then** it receives an explicit unavailable result rather than an error that aborts the caller.
3. **Given** an existing capacity consumer, **When** the profiler is introduced, **Then** its
   capacity queries still work and live usage is not substituted for capacity or allocation budgets.
4. **Given** one measurement source stalls, **When** a probe is requested, **Then** within two
   seconds the caller receives unaffected readings and an explicit timeout for the stalled source,
   while inference continues.
5. **Given** an invalid process selection, **When** a probe is requested, **Then** the caller
   receives an actionable validation error. Valid results contain only resource readings and
   managed-process identities within the existing private-cluster boundary, without unrelated
   process inventories, conversation content, or credentials.

---

### User Story 5 - Inspect managed-process CPU and RAM (Priority: P1)

As an operator, I want CPU utilization and resident RAM for each managed inference-server or
worker process alongside host totals, so I can distinguish my workload from other activity.

**Why this priority**: The user confirmed both host-wide and managed-process CPU/RAM are required.

**Independent Test**: Observe a managed process with known resident memory and CPU activity,
then stop and restart it; verify attribution, values, and distinct lifetimes.

**Acceptance Scenarios**:

1. **Given** a process using one fully occupied logical processor on an eight-logical-processor
   host and 2 GiB of resident RAM, **When** a valid interval is sampled, **Then** its CPU reads
   12.5% and resident RAM reads 2 GiB, separately from host CPU and memory.
2. **Given** a process exits or its numeric identifier is reused, **When** another probe runs,
   **Then** the ended lifetime receives no new usage and a replacement receives a new identity
   and CPU baseline.
3. **Given** no managed process or permission denied for one process, **When** a probe runs,
   **Then** it reports no managed process or the affected process's unavailable status respectively,
   and continues returning host/device readings.

---

### Edge Cases

- A machine with no discrete GPU (or a CPU-only macOS/Windows node) reports VRAM as unavailable or
  clearly shared rather than zero.
- Apple Silicon unified memory is labeled as shared and never double-counted as both RAM and VRAM.
- A GPU that advertises capacity but cannot report live usage returns capacity-independent
  "unavailable" usage, not a fabricated zero or a copy of capacity.
- CPU utilization is bounded to 0–100% of total logical capacity. Missing baselines, invalid
  elapsed intervals, reset counters, and suspend/resume trigger warm-up or unavailable status,
  not a fabricated zero or out-of-range value.
- Rapid repeated probes do not leak native resources, crash the process, or return stale cached
  values that no longer match observed load.
- A measurement failure on one resource (e.g., VRAM) does not prevent correct RAM and CPU readings
  from being returned.
- Concurrent probes cannot mix process lifetimes or consume one another's CPU baselines.
- GPU removal, duplicate discovery of a device, and mixed shared/dedicated memory preserve
  per-device identity and do not create duplicate totals.
- Samples may be collected at different instants; timestamps and CPU intervals remain truthful
  rather than implying that the entire machine was measured atomically.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The profiler MUST return current VRAM usage (used and free) on request, with explicit
  units when measurable, distinguishing physical capacity, observed usage, and allocation budget.
  Free memory MAY be derived only from compatible capacity and usage for the same memory pool;
  otherwise it MUST be unavailable. A probe's own GPU allocations MUST NOT represent device-wide usage.
- **FR-002**: The profiler MUST return host total physical RAM, used RAM, free RAM, and available
  RAM with explicit byte units and definitions. Available RAM includes reclaimable memory where
  reported and MUST NOT be relabeled as free RAM. Unprovided counters follow FR-006.
- **FR-003**: The profiler MUST return current CPU utilization on request, expressed as a percentage
  of total logical CPU capacity (100% = all logical processors busy).
- **FR-004**: Every reading MUST carry a measurement timestamp and identify whether it describes a
  device, the whole machine, or a process. CPU MUST also identify its observation interval;
  percentages MUST be finite and within 0–100%.
- **FR-005**: The profiler MUST work on both Windows and macOS, using the same probe surface and
  consistent units and percentage semantics across platforms.
- **FR-006**: Measurements the host cannot provide MUST be reported as unavailable (with a reason
  where known), never as zero or a fabricated value.
- **FR-007**: When multiple GPUs are present, VRAM usage MUST be reported per device, and any
  combined total MUST count each distinct memory pool exactly once. Missing contributors MUST
  make the aggregate unavailable or explicitly partial, with the missing devices identified.
- **FR-008**: On Apple Silicon and other unified-memory systems, VRAM MUST be labeled as shared
  memory and MUST NOT be double-counted against RAM in combined figures.
- **FR-009**: The profiler MUST be callable from the existing agent runtime and MUST NOT require a
  separate always-running service to return a reading.
- **FR-010**: Repeated or concurrent probes MUST be safe (no resource leaks, no crashes, no stale
  values presented as fresh) and MUST preserve resource identity, observation times, and CPU
  intervals. Reusing a recent observation MUST retain its original timestamp.
- **FR-011**: CPU and RAM measurements MUST cover both the host and each requested managed
  inference-server or worker process. Process readings MUST contain machine-normalized CPU
  percentage and resident RAM in bytes, distinct from host memory and device-wide VRAM.
- **FR-012**: Process readings MUST identify the node, process number, and actual start identity.
  Exited processes MUST stop receiving measurements; reused process numbers MUST start a separate
  lifetime and CPU baseline. When no managed process is selected, the probe MUST return an
  empty process collection.
- **FR-013**: First CPU observations and invalid intervals MUST report warm-up or unavailable
  status until a valid interval exists. Process exit, access denial, or a device/source failure
  MUST leave unaffected readings usable, with a specific reason on the affected reading.
- **FR-014**: Probing MUST return within two seconds, including when a source stalls, reporting
  unavailable timed-out readings instead of blocking inference or concealing the failure.
- **FR-015**: Existing capacity consumers MUST remain compatible. Probes MUST use existing
  local/private-cluster access boundaries and MUST NOT collect unrelated process inventories,
  conversation content, or credentials. Invalid process selections MUST return an actionable error.

### Key Entities *(include if feature involves data)*

- **Resource reading**: A timestamped observation of a resource (VRAM, RAM, or CPU) with a value,
  unit, scope (device / machine / process), and availability status.
- **Device**: An accelerator (GPU) or unified-memory pool with identity, reported capacity, and live
  used/free memory where measurable.
- **Probe**: A single on-demand request that returns a consistent set of resource readings for the
  host.
- **Managed process lifetime**: An inference-server or worker execution identified by node,
  process number, and start identity, with scoped CPU and resident-memory observations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On both Windows and macOS, across 30 paired observations under steady load, host
  and managed-process CPU agree with an independent reference over matched two-second intervals
  within five percentage points in at least 95% of observations. Host memory counters and process
  resident RAM agree within the greater of 5% of the reference value or 64 MiB, using matching
  counter definitions and observations taken within one second.
- **SC-002**: A Windows discrete-GPU system supplies live dedicated-memory usage and free memory
  within the greater of 5% of the reference value or 64 MiB for 95% of 30 paired steady-load
  observations. An Apple Silicon system identifies its shared pool correctly; every supplied GPU
  usage counter passes the same comparison with a reference of matching scope. Unsupported counters
  are explicitly unavailable; an all-unavailable Windows result does not satisfy this criterion.
- **SC-003**: Every returned reading includes a timestamp, units, and scope; zero fabricated
  values appear across the tested available/unavailable cases (no GPU, unified memory, multi-GPU,
  unreadable device).
- **SC-004**: During a ten-minute run with four callers each requesting a probe every two seconds,
  after CPU baseline establishment, all callers use the same managed-process selection on a
  healthy host. At least 95% of requests return measured host CPU/RAM and all supported requested
  process/device counters, with observations no older than two seconds at response time.
  Shared observations are permitted only with their original timestamps. At least 95% of all
  timed requests return within one second and every request within two seconds. A healthy run
  excludes deliberate fault injection and host suspension; collector-generated busy states,
  errors and timeouts remain in the denominator and count as unsuccessful measurements.
  Unsupported counters are declared before the run and excluded from its measured-counter
  requirement; initial CPU warm-up occurs before the timed interval. Failure to establish a
  baseline fails the run, and later reinitialization is not excluded. Separate injected-failure
  runs verify the two-second deadline and preservation of unaffected readings. No crashes,
  mixed process identities or relabeled stale timestamps occur; after stopping collection,
  retained memory grows by no more than 16 MiB and open handles by no more than five versus
  the warmed baseline.
- **SC-005**: A unified-memory node reports VRAM as shared and its RAM+VRAM combined figure does not
  count the same physical pool twice. In all shared-pool and partial-data cases, reported physical
  capacity is counted once and incomplete usage totals are visibly partial or unavailable.
- **SC-006**: On both target platforms, process exit, process-number reuse, missing CPU baseline,
  permission denial, and no-managed-process checks yield zero mixed lifetimes or fabricated zeros.
  Operators can distinguish host usage, managed-process usage, and unavailable readings in every
  acceptance scenario using the returned identity, scope, units, and status.

## Assumptions

- "Profiler" means a component that reports current *usage* on demand; the existing one-time capacity
  probe remains the source for capacity and this feature complements it rather than replacing it.
- Platform scope is Windows and macOS, as stated. Linux is not a delivery target for this feature;
  the existing Linux/non-macOS code paths remain intact and must not regress.
- Rust is an explicit user-supplied delivery constraint; implementation choices belong in planning.
  The existing agent is the consumer, rather than a new standalone monitoring service.
- CPU percentage uses total logical processor capacity as 100%, consistent with existing monitoring
  requirements (see 002-node-usage-monitor).
- GPU counter availability depends on the OS, device, driver, and permissions. Planning must record
  a tested vendor/device support matrix; missing optional counters must not hide the whole device.
  Apple Silicon shares RAM; Intel Macs may have dedicated GPU memory. A memory budget is not live usage.
- The caller owns sampling cadence; CPU describes an elapsed interval, not instantaneous utilization.
  The normal acceptance workload uses two-second intervals after baseline establishment.
- This feature serves readings for the existing node monitor. It does not add recording/history,
  a dashboard redesign, per-process GPU attribution, stack tracing, temperature, or power profiling.
- The capacity semantics in [001](../001-fix-vram-layer-default/spec.md) and monitoring semantics in
  [002](../002-node-usage-monitor/spec.md) are alignment dependencies, not evidence of implemented
  behavior. Existing OS facilities and drivers are prerequisites; ordinary-user operation is the
  default, with permission limitations surfaced per reading.
