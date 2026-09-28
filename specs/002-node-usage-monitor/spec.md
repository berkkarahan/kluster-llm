# Feature Specification: Interactive Node and Process Usage Monitor

**Feature Branch**: `main` (current branch; no branch-creation hook registered)

**Created**: 2026-09-28

**Status**: Draft

**Input**: User description: "develop a similar interactive node user interface that will record the process' usage similar to exo using open design's MCP. context;[Image 1] and https://github.com/exo-explore/exo/tree/main"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Explore the live cluster and inspect a process (Priority: P1)

As a cluster operator, I want an interactive map of my machines with resource readings and
process details so I can see which nodes are available, which are assigned to inference, and
where resources are being consumed.

**Why this priority**: The node map and trustworthy usage readings are the core requested
experience and make a distributed workload understandable at a glance.

**Independent Test**: Open the cluster view with known node identities, relationships, and
usage readings; select each node and compare its card and detail view with those readings.

**Acceptance Scenarios**:

1. **Given** a master and three joined workers, **When** the view opens, **Then** all four
   nodes appear with names, roles, connection states, memory summaries, and master-worker
   relationships; no unobserved worker-to-worker links are implied.
2. **Given** a managed process consuming one fully occupied logical processor on an
   eight-logical-processor machine and 2 GiB of resident memory, **When** its node is selected,
   **Then** process CPU reads 12.5% of machine capacity and process resident memory reads
   2.0 GiB, separately from whole-machine usage and model placement estimates.
3. **Given** a node reporting device utilization, memory, temperature, and power, **When** its
   details open, **Then** those measurements include their device or process scope and units;
   unsupported measurements read unavailable rather than zero.
4. **Given** an active cluster, **When** the operator selects nodes, pans, zooms, or fits the map,
   **Then** details follow the selected node, updates preserve selection and view position,
   and navigating the map does not change the running workload.
5. **Given** a node stops reporting, **When** its measurements are ten seconds old, **Then**
   they are marked stale with their last-update time; loss of cluster membership is reflected
   within 25 seconds of the last contact, without erasing recorded history.
6. **Given** a node assigned to a generating workload but without device utilization readings,
   **When** its card is viewed, **Then** assignment and cluster activity remain visible while
   unavailable local measurements are not represented as proof of local computation.

---

### User Story 2 - Review recorded process usage (Priority: P1)

As a cluster operator, I want usage recorded while managed processes run, including when the
dashboard is closed, so I can inspect earlier resource spikes and completed process lifetimes.

**Why this priority**: A live-only display would not satisfy the request to record process usage.

**Independent Test**: Run a managed process through known usage changes, close the dashboard,
stop the process, and reopen its history. Verify readings, gaps, and process boundaries without
requiring the node map.

**Acceptance Scenarios**:

1. **Given** a managed process is running while cluster monitoring is available, **When** the
   dashboard is closed for five minutes, **Then** reopening it shows timestamped usage for
   that interval, with any unavailable intervals explicitly marked.
2. **Given** retained records for several nodes and process lifetimes, **When** the operator
   selects a node, a process lifetime, and a 15-minute, one-hour, or 24-hour window, **Then**
   the view shows only matching readings, units, timestamps, and gaps; inspecting a point
   reveals its recorded values.
3. **Given** a process stops and another starts on the same node, even with the same process
   number, **When** history is viewed, **Then** their lifetimes remain distinguishable and the
   stopped process receives no new usage readings.
4. **Given** persisted readings within retention limits, **When** the monitoring service
   restarts, **Then** those readings remain available, the interruption is visible, and a
   continuing process is not mislabeled as newly started solely because monitoring restarted.
5. **Given** records older than 24 hours or a full recording allowance, **When** history is
   opened, **Then** the earliest retained time and any shortened coverage are visible, and
   newer retained records remain readable.
6. **Given** recording becomes unable to save new readings, **When** the operator views the
   dashboard, **Then** it reports recording failure and the last successful save time,
   preserves previously saved history, and does not stop inference.
7. **Given** inference requests and unrelated applications are active on a monitored node,
   **When** saved usage records are inspected, **Then** they contain only managed-process
   identity, workload associations, and resource measurements, with no conversation content,
   credentials, or unrelated process inventory.

---

### User Story 3 - Use an exo-inspired monitoring workspace (Priority: P2)

As a cluster operator, I want the supplied reference's clear node-centered presentation while
retaining familiar Kluster LLM controls, so monitoring fits my existing workflow.

**Why this priority**: The visual direction is explicit in the request; usability and existing
controls make it useful beyond a standalone graph.

**Independent Test**: Review the workspace against the supplied image and perform node
inspection and history navigation with a pointer and keyboard at desktop and narrow widths.

**Acceptance Scenarios**:

1. **Given** a 1280-by-720 desktop viewport, **When** the workspace opens, **Then** a dark,
   node-centered view uses high-contrast yellow/amber accents, compact monospaced telemetry,
   memory-fill indicators, connection lines, and a contextual detail panel with Kluster LLM
   branding; the current model, cluster controls, and chat remain reachable.
2. **Given** a 390-by-844 viewport, **When** the operator inspects a node and its history,
   **Then** stacked panels or an equivalent node list expose the same information without
   page-wide horizontal scrolling or inaccessible controls.
3. **Given** keyboard-only navigation or reduced-motion preferences, **When** a node and a
   history point are selected, **Then** visible focus, textual status, and accessible values
   provide the same information without relying on hover, color, or animated links.
4. **Given** an empty cluster, an initial load, or a disconnected dashboard, **When** the view
   opens, **Then** it presents the corresponding state and an appropriate existing action
   or reconnection message rather than sample nodes or apparently healthy stale readings.
5. **Given** an operator using the existing cluster controls, model planning/launch, or chat,
   **When** those workflows are accessed from the new workspace, **Then** their availability
   and behavior match the existing rules and unsupported execution modes are not offered.

---

### Edge Cases

- A CPU-only node still supplies process CPU and resident memory. Missing accelerators,
  temperature sensors, or power readings do not prevent monitoring other measurements.
- Multiple devices are identified individually. Shared/unified memory is labeled and counted
  once; device-wide memory is never presented as process-exclusive usage.
- A joined node without a managed process shows "no managed process" rather than a fabricated
  zero-usage process. A process exit between readings closes its observed lifetime.
- Duplicate host names remain distinguishable by node identity. Rejoining an existing node
  restores that identity without creating a second live card.
- A selected node that leaves remains inspectable as disconnected while its history is retained;
  a rejoin updates its connection state without discarding earlier process lifetimes.
- Late, duplicated, invalid, or out-of-order readings do not overwrite newer live values,
  create duplicate history points, or appear as valid zero readings. Time labels use one
  explicit timezone, and discontinuous timing is not shown as uninterrupted measurement.
- Monitoring or network interruptions leave gaps; reconnecting does not invent missing values.
  History before monitoring began displays as unrecorded.
- Limited disk space or a damaged record produces a recording/history error while unaffected
  saved history remains readable and the inference workload continues.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The cluster view MUST present one card per known node, identifying its name,
  stable identity, role, processing hardware, connection state, memory usage/capacity, and
  workload assignment when known. Links MUST represent known cluster relationships and
  MUST NOT imply measured traffic or physical connectivity that has not been observed.
- **FR-002**: Operators MUST be able to select a node, inspect its details, pan and zoom the
  topology, and fit all nodes into view. Routine updates MUST preserve selection and map
  position. These interactions MUST NOT alter cluster membership or process execution.
- **FR-003**: Each managed inference-server or worker process MUST have CPU usage and resident
  memory measured on supported operating systems. CPU percentage MUST use total logical
  processor capacity as 100%. Details MUST identify the process number, role, process start
  time, current state, and measurement time; measurement failures MUST be explicit.
- **FR-004**: Available host/device readings MUST include host CPU and memory usage, device
  utilization and used/total memory, temperature in degrees Celsius, and power in watts.
  Each reading MUST state whether it describes a process, device, or whole node. Process-level
  accelerator usage MUST appear only when directly attributable; unsupported readings MUST
  show unavailable with a reason when known.
- **FR-005**: Memory MUST use explicitly labeled GiB with one decimal place. Measured usage,
  physical capacity, and estimated allocation MUST remain distinct. Shared memory MUST be
  labeled and counted once in combined totals; incomplete totals MUST be labeled partial.
- **FR-006**: Live values MUST show their age and become stale after ten seconds without a
  fresh reading. Node and dashboard connection loss MUST be distinguishable from an idle
  process. A node removed from membership MUST cease appearing connected within 25 seconds
  of its last contact, while retained history and selected details remain accessible.
- **FR-007**: The workspace MUST distinguish no workload, starting/loading, ready, processing
  input, generating, and error when the cluster reports those states. Cluster activity and
  node assignment MUST be labeled separately from measured per-node computation.
- **FR-008**: Usage recording MUST start automatically when a managed process is observed and
  continue independently of open dashboard tabs, targeting one reading every two seconds per
  process. Records MUST include identity, observation time, measurement scope, available
  values, and availability status. Closing the dashboard MUST NOT stop recording.
- **FR-009**: Saved history MUST survive dashboard reloads and monitoring-service restarts.
  During healthy recording, no more than the latest ten seconds may remain unsaved. The
  workspace MUST show recording, interrupted, or failed status and the last successful save.
- **FR-010**: History MUST retain a rolling 24 hours, subject to a single 256 MiB recording
  allowance for the entire cluster. Reaching either limit MUST retire the oldest records
  first. The available time range and any storage-limited truncation MUST be visible;
  retirement MUST preserve readable remaining records and the identities needed to interpret them.
- **FR-011**: Operators MUST be able to filter history by node, process lifetime, and the last
  15 minutes, one hour, or 24 hours, including stopped processes and departed nodes within
  retention. Timelines MUST show CPU and resident memory plus available optional measurements,
  permit inspection of timestamped values, and visibly distinguish gaps from measured zero.
- **FR-012**: Process histories MUST distinguish lifetimes using node identity and actual
  process start identity, not a process number alone. Model/workload associations MUST be
  recorded when known without assuming a worker restart for every workload change.
- **FR-013**: Invalid and duplicate readings MUST NOT become valid history points; late data
  MUST NOT replace newer live values. Recording, collection, and history-read failures MUST
  provide an actionable status without stopping inference or concealing prior saved data.
- **FR-014**: The desktop workspace MUST reflect the supplied image's dark canvas, yellow/amber
  accents, monospaced readings, memory-fill node cards, relationship lines, and adjacent detail
  area while using Kluster LLM identity. Loading, empty, disconnected, and unavailable-data
  states MUST be designed alongside the populated state.
- **FR-015**: Existing cluster start/stop/join/leave actions, model planning/launch access, and
  chat MUST remain reachable with their existing availability rules. Visual similarity MUST
  NOT introduce unsupported model execution modes or imply multiple simultaneous model
  instances where the current product does not support them.
- **FR-016**: Node selection and history inspection MUST be keyboard-operable with visible
  focus and accessible labels. Status MUST use text in addition to color, text contrast MUST
  meet WCAG AA, and nonessential animation MUST respect reduced motion. At widths from
  390 pixels, core information and controls MUST remain available without page-wide horizontal
  scrolling, using an equivalent list or stacked layout when needed.
- **FR-017**: Recording MUST cover only Kluster-managed inference processes and associated
  resource measurements; it MUST NOT record prompts, responses, credentials, or unrelated
  process inventories. History MUST remain within the existing local/private-cluster access
  boundary and monitoring MUST NOT require exposing private worker services publicly.

### Key Entities *(include if feature involves data)*

- **Node**: A cluster machine with stable identity, display name, role, hardware, connection
  state, last contact, and any known workload assignment.
- **Cluster relationship**: A known relationship between nodes, with its meaning and observed
  availability; it is not evidence of measured data transfer.
- **Managed process lifetime**: One execution of an inference-server or worker process,
  associated with a node, process number, start identity, observed end, and known workload
  associations over time.
- **Usage reading**: A timestamped observation linked to a node and, where applicable, a
  process lifetime or device, containing resource values, units, scope, and availability.
- **Recording coverage**: The retained observation range for a process, including recording
  status, last successful save, interruptions, and retention-related truncation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a usability check, at least four of five operators can identify the master,
  select a named worker, and distinguish its process memory from whole-node memory within
  30 seconds without assistance.
- **SC-002**: For clusters of 1, 4, and 16 nodes on a healthy private network, at least 95% of
  observed usage changes appear within five seconds; node selection and history-window
  changes complete within two seconds in at least 95% of attempts.
- **SC-003**: In a 30-minute healthy run, at least 95% of two-second intervals contain a CPU
  and resident-memory reading for every continuously running managed process, including a
  five-minute interval with all dashboard tabs closed. Every missing interval is visible.
- **SC-004**: All saved readings within both retention limits remain inspectable after reload
  and service restart; an abrupt monitoring interruption during otherwise healthy recording
  loses at most the latest ten seconds. Process restart, process-number reuse, disconnect,
  and rejoin checks produce zero mixed lifetimes or fabricated zero readings.
- **SC-005**: Known 8, 16, and 24 GiB capacities display as 8.0, 16.0, and 24.0 GiB. All tested
  shared-memory, unsupported-sensor, and missing-reading cases display the correct scope or
  unavailable state instead of double-counted totals or invented measurements.
- **SC-006**: Node inspection and history navigation are completable using only a keyboard
  and at both 1280-by-720 and 390-by-844 viewports. At least four of five usability participants
  can distinguish live, stale, and recorded readings without relying on color.
- **SC-007**: Recording stays within its 24-hour/256 MiB limits during a sustained-run check,
  with the actual coverage visible. Enabling monitoring reduces median generated tokens per
  second by no more than 5% across five matched runs of the same model, input, and hardware;
  verification records the workload, operating system, and hardware used.

## Assumptions

- The primary user is the existing local/private-cluster operator. This extends the current
  dashboard rather than introducing a separate hosted monitoring product.
- "Process usage" means resource consumption of managed inference-server and worker processes,
  with node/device context. It does not mean recording conversation contents, billing usage,
  or monitoring arbitrary applications on participating machines.
- The supplied image and the [exo repository](https://github.com/exo-explore/exo/tree/main)
  are interaction and visual references: node-centered layout, memory indicators, adjacent
  telemetry, and workload context. Kluster LLM's own capabilities govern the available controls.
- The requested design workflow is a design-preparation dependency for the next phase.
  MCP access was confirmed on retry; no project was active. The design artifact still needs
  to be generated and reviewed, and its reference recorded before that workflow is complete.
- Core process CPU and resident-memory monitoring targets Windows, Linux, and macOS. Device
  utilization, process-attributed accelerator memory, temperature, and power depend on what
  each machine can report; unavailable readings are an expected supported state.
- The initial performance envelope is 1–16 nodes. Larger clusters may be browsed but are not
  covered by this feature's responsiveness targets.
- Two-second sampling, a ten-second persistence window, and 24-hour/256 MiB retention are
  bounded defaults for troubleshooting. Collection occurs while cluster monitoring is running;
  uninterrupted history during a monitoring outage or disconnected-node backfill is not promised.
- Existing membership, process identity, placement, and activity information provide context,
  but existing activity labels alone do not establish measured process resource usage.
- Memory presentation follows the GiB and shared-memory semantics in
  [001-fix-vram-layer-default](../001-fix-vram-layer-default/spec.md). This specification does
  not assume that feature is implemented; planning must reconcile the shared presentation
  requirements and avoid reintroducing unsupported split-mode controls.
