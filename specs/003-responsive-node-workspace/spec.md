# Feature Specification: Responsive Node Workspace

**Feature Branch**: `main` (current checkout; no branch-creation hook registered)

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "cluster nodes ui must be responsive(occupying the whole screen once it launches). display ram + cpu + gpu usage values on the node itself rather than the right bar. [Image 1] . benefit from opendesign for the ui refactor."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open a screen-filling cluster workspace (Priority: P1)

As a cluster operator, I want the node workspace to fill my browser's available viewing
area immediately, so screen space is used for monitoring.

**Why this priority**: The supplied screenshot leaves substantial unused space below a
small topology while permanent side panels constrain the nodes.

**Independent Test**: Open Home at desktop, tablet, and mobile sizes, then resize the window.

**Acceptance Scenarios**:

1. **Given** a desktop window, **When** Home opens, **Then** the workspace fills the available
   width and height beneath navigation, without requiring a maximize or Fit action.
2. **Given** a one-node or four-node cluster, **When** its initial data arrives, **Then** its
   nodes appear within the initial view with readable resource summaries.
3. **Given** the window is resized or rotated, **When** the layout adapts, **Then** nodes and
   controls remain accessible without page-wide horizontal scrolling.
4. **Given** a narrow screen, **When** Home opens, **Then** a full-width node list presents the
   same resource information with vertical scrolling as needed.

---

### User Story 2 - Read resource usage directly on each node (Priority: P1)

As a cluster operator, I want CPU, RAM, and GPU usage on each node so I can compare machines
without selecting them individually.

**Why this priority**: Resource comparisons are the primary monitoring task; selecting every
node to read a separate inspector adds unnecessary work.

**Independent Test**: Supply distinct known readings for several nodes and inspect their
cards without opening another panel.

**Acceptance Scenarios**:

1. **Given** available measurements, **When** a node is displayed, **Then** its card shows
   host CPU percentage, host RAM used/total and percentage, and GPU utilization plus
   used/total GPU memory.
2. **Given** multiple GPUs, **When** the node is viewed, **Then** readings identify each device
   separately rather than presenting an ambiguous combined GPU percentage.
3. **Given** missing or stale readings, **When** the card is viewed, **Then** those states are
   explicit and distinguishable from measured zero, with measurement age available.
4. **Given** a narrow-screen node list, **When** a node appears, **Then** its CPU, RAM, and GPU
   summary remains visible without hover or selection.
5. **Given** shared memory or additional process-level measurements, **When** their values
   are inspected, **Then** shared memory is labeled and counted once, and process CPU and
   resident memory are distinguished from host and device measurements.

---

### User Story 3 - Access history and controls without a permanent inspector (Priority: P2)

As a cluster operator, I want history and detailed diagnostics available when needed while
keeping the default workspace focused on nodes.

**Why this priority**: Reclaiming the inspector's space must retain useful troubleshooting
and the existing cluster workflows.

**Independent Test**: Select a node, inspect recorded usage, close the contextual view, and
access cluster controls and chat.

**Acceptance Scenarios**:

1. **Given** the initial workspace, **When** it opens, **Then** no permanent right-hand
   inspector occupies node-viewing space and cluster controls use compact or collapsible
   presentation.
2. **Given** retained history, **When** the operator opens a node's history, **Then**
   process-lifetime selection, 15-minute/one-hour/24-hour windows, and exact recorded readings
   remain available.
3. **Given** keyboard-only navigation, **When** contextual views are opened and closed,
   **Then** focus is visible and returns to the activating control; status information is
   understandable without color or hover.
4. **Given** routine measurement updates, **When** the operator is inspecting the cluster,
   **Then** selection and intentional pan/zoom remain stable.
5. **Given** existing cluster, Plan & Run, and Chat workflows, **When** their controls are
   accessed, **Then** they retain their existing availability rules; collapsed controls do
   not conceal the presence of operational errors.
6. **Given** reduced-motion preferences, **When** contextual views are opened or status
   changes, **Then** nonessential animation is suppressed and labels remain readable with
   WCAG AA text contrast.

---

### Edge Cases

- Empty, loading, offline, and recording-failure states still occupy the workspace and show
  truthful status and relevant existing actions.
- CPU-only machines, unsupported GPU sensors, and partially available measurements retain
  all known values. No reported GPUs is distinguished from confirmed hardware absence.
- Multiple GPUs are individually identified. Shared/unified memory is labeled and never
  summed with host RAM as though it were a separate physical pool.
- Long hostnames, duplicate display names, and long model assignments do not clip resource
  values or prevent distinguishing nodes.
- A selected node disconnecting or rejoining retains its identity and access to retained
  history; cached values are not presented as fresh measurements.
- Sixteen-node clusters may require navigation at a readable inspection scale; an overview
  fit does not promise that sixteen detailed cards can all be read simultaneously.
- Browser zoom, narrow landscape windows, wrapped navigation, and reduced-motion preferences
  must not make controls or node summaries inaccessible.
- A node without a managed process may have no readings. Reported hardware capacity remains
  distinguishable from measured consumption.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Home MUST use the available browser viewing area on launch. The desktop topology
  MUST expand vertically with the window rather than stop at a fixed graph height.
- **FR-002**: Default controls MUST use compact or collapsible presentation so permanent
  sidebars do not reserve space beside the topology.
- **FR-003**: Each node MUST display its identity, role, connection state, measurement
  freshness, and labeled CPU, RAM, and GPU summaries without requiring selection.
- **FR-004**: CPU MUST identify whole-node utilization. RAM MUST show used/total GiB and
  percentage. Each reported GPU MUST identify its utilization percentage and used/total
  memory in GiB. Missing fields MUST remain explicitly unavailable.
- **FR-005**: Process CPU and resident memory MUST remain accessible and explicitly
  distinguished from whole-node and device-wide measurements. Shared memory MUST remain
  labeled and must not be double-counted.
- **FR-006**: Unavailable, initializing, stale, disconnected, and measured-zero states MUST
  remain distinguishable. Existing ten-second staleness semantics MUST be preserved, with
  measurement age available on the node.
- **FR-007**: History and extended diagnostics MUST open on demand and be dismissible.
  Current CPU, RAM, and GPU summaries MUST remain part of the nodes.
- **FR-008**: Node selection, pan, zoom, and Fit MUST remain available. Routine updates MUST
  preserve selection and intentional map position.
- **FR-009**: Cluster actions, Plan & Run, and Chat MUST remain reachable with their existing
  availability rules. Operational error status MUST remain discoverable while controls are
  collapsed.
- **FR-010**: Layouts MUST support desktop, tablet, and mobile sizes without page-wide
  horizontal overflow or clipped resource values. Larger node sets MAY require topology
  navigation or vertical list scrolling.
- **FR-011**: Controls and readings MUST support keyboard use, visible focus, textual status,
  WCAG AA text contrast, and reduced motion.

### Key Entities *(include if feature involves data)*

- **Node**: An existing cluster machine with identity, role, hardware, connection state, and
  assignment.
- **Resource reading**: A timestamped CPU, RAM, or GPU measurement with scope, units, and
  availability.
- **Node inspection context**: The selected node and its process details or recorded history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At 1280-by-720, 1440-by-900, and 1920-by-1080, the initial workspace fills the
  space beneath navigation without a permanent inspector or unused band below a height-capped
  graph.
- **SC-002**: At 390-by-844 and 768-by-1024, all primary controls and node summaries are
  accessible with zero page-wide horizontal overflow.
- **SC-003**: Every displayed node exposes CPU, RAM, and GPU values, or explicit availability
  states, with zero node-selection or inspector-opening actions.
- **SC-004**: Known-reading checks correctly display all supplied values and distinguish
  zero, missing, stale, CPU-only, multi-GPU, and shared-memory cases.
- **SC-005**: At least four of five operators can identify the highest-CPU and highest-RAM
  nodes in a four-node cluster within 15 seconds without opening details.
- **SC-006**: Keyboard-only checks complete node selection, history inspection, contextual-view
  dismissal, and Chat access at desktop and mobile sizes.
- **SC-007**: One-, four-, and sixteen-node checks preserve selection and intentional map
  position through routine updates; all nodes remain reachable.

## Assumptions

- "Occupying the whole screen" means filling the available browser window automatically;
  browser or operating-system fullscreen mode is not required.
- Primary summaries describe whole-node CPU/RAM and device-wide GPU usage. Memory uses
  binary GiB with one decimal place, consistent with the existing monitor.
- Existing telemetry, recording, and retention from
  [002-node-usage-monitor](../002-node-usage-monitor/spec.md) provide the data. This feature
  changes presentation, not collection coverage or the availability of hardware sensors.
- The requested OpenDesign workflow will adapt the existing project into desktop and mobile
  variants showing the expanded workspace and on-node metrics. Those variants should be
  reviewed before visual implementation. The existing reference is recorded in
  [the monitor documentation](../../docs/node-usage-monitor.md#reviewed-opendesign-direction).
- The initial supported envelope is 1-16 nodes and window widths from 390 pixels; larger
  node sets remain navigable without promising simultaneous readable fit of every card.
