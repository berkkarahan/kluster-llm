# Feature Specification: Correct VRAM Reporting and Default to Layer

**Feature Branch**: `001-fix-vram-layer-default`

**Created**: 2026-09-28

**Status**: Draft

**Input**: User description: "fix total vram estimation in system settings. For example, 16GB VRAM gpu is showing up as 18GB. Remove split mode settings, make `Layer` default since its the only one available for RPC mode."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See accurate memory capacity and estimates (Priority: P1)

As a cluster operator, I want System Settings to distinguish GPU capacity, usable budget, and
estimated consumption so I can judge whether my model fits before starting a run.

**Why this priority**: The reported figure is the primary signal for whether a model fits. A
mislabeled or conflated number causes operators to resize context, add hardware, or abort runs that
would have succeeded.

**Independent Test**: Open System Settings with known GPU capacities and compare its hardware
labels, budgets, and allocation summary against the known values.

**Acceptance Scenarios**:

1. **Given** a GPU reporting exactly 16 GiB, **When** its capacity is displayed, **Then** it reads
   **16.0 GiB**, not approximately 17–18 GB.
2. **Given** that GPU with a 95% budget and sufficient available memory, **When** the budget is
   displayed, **Then** it reads **15.2 GiB**, and is distinct from the capacity figure.
3. **Given** eligible 16 GiB and 8 GiB discrete GPUs, **When** their total capacity is displayed,
   **Then** it is **24.0 GiB**, counting each device once.
4. **Given** a model allocation spanning GPU memory and CPU RAM, **When** the estimate is
   displayed, **Then** GPU-only figures exclude CPU allocations, and any combined figure is
   explicitly labeled as total memory.

---

### User Story 2 - Run with Layer automatically (Priority: P2)

As a cluster operator, I want the supported split mode applied automatically so I cannot configure
a mode that the cluster cannot execute.

**Why this priority**: Only Layer split works over RPC. Exposing the other modes invites
configurations that fail at plan or launch time, or that silently change allocation strategy.

**Independent Test**: Open the settings pages, request a plan, and launch an RPC workload with
fresh settings and with previously saved Row/Tensor preferences.

**Acceptance Scenarios**:

1. **Given** either dashboard page containing hardware settings, **When** it is opened, **Then**
   there is no split-mode selector and no guidance inviting Row or Tensor selection.
2. **Given** fresh settings, **When** an estimate, plan, or run is requested from System
   Settings, **Then** Layer split is used automatically.
3. **Given** previously saved Row or Tensor preferences, **When** settings are loaded, saved,
   reset, or revisited after a page reload, **Then** subsequent System Settings plans and runs
   use Layer split without manual repair.

---

### Edge Cases

- A device reporting less than its advertised capacity is displayed truthfully; it is not rounded
  up to a marketing figure.
- A missing or invalid memory measurement displays as unavailable, not as a fabricated capacity or
  a complete-looking partial total.
- CPU-only operation labels its figures as RAM or memory rather than VRAM.
- Unified or shared memory is identified as shared and is counted once in any combined total.
- Changing the device selection, or running with limited available memory, updates the applicable
  totals and budgets.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System Settings MUST display memory quantities consistently in GiB, with explicit
  unit labels and one decimal place.
- **FR-002**: GPU capacity MUST reflect the reported device memory; display conversion MUST NOT
  alter the underlying quantity used for fit decisions.
- **FR-003**: Capacity, usable budget, and estimated consumption MUST remain distinguishable.
- **FR-004**: GPU-only totals MUST exclude CPU RAM and count each eligible device once. Combined
  totals MUST be labeled as total memory.
- **FR-005**: Budgets MUST respect the configured budget percentage and available-memory
  constraints.
- **FR-006**: Dashboard hardware settings MUST remove the split-mode selector and the obsolete
  mode-selection guidance.
- **FR-007**: System Settings estimates, plans, and launches MUST use Layer split consistently,
  including when legacy saved preferences specify another mode.
- **FR-008**: Saving, resetting, and reloading settings MUST preserve automatic Layer behavior.

### Key Entities

- **Memory capacity**: Reported memory available on a device or shared memory pool.
- **Memory budget**: Capacity usable for allocation after applicable limits.
- **Memory estimate**: Expected consumption attributed to GPU memory, CPU RAM, or their combined
  allocation.
- **Effective split mode**: The split strategy applied to a plan; Layer for the System Settings
  workflow.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Known 8, 16, and 24 GiB capacities display as 8.0, 16.0, and 24.0 GiB.
- **SC-002**: A fully available 16 GiB GPU at a 95% budget displays 15.2 GiB.
- **SC-003**: Every GPU-only summary excludes CPU allocations, and every combined summary
  explicitly identifies total memory.
- **SC-004**: Zero split-mode controls remain in the dashboard settings surfaces, and the fresh
  settings, legacy preference, and reload scenarios all resolve to Layer split.

## Assumptions

- GiB is the intended unit for hardware memory comparisons, since device-reported capacities are
  binary multiples.
- Existing hardware measurements remain authoritative, including reductions for reserved memory.
- The scope is the dashboard settings surfaces and the planning and launch behavior they drive.
- Explicit local-only command-line split options are outside this feature.
