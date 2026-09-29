# Feature Specification: Master & Worker Log Trail

**Feature Branch**: `main` (current branch; no branch-creation hook registered)

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "use opendesign mcp to design a log trail page for master + worker logs. the new ui should have; clear button, a toggle for log refreshing(live/5s/30s/1m)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inspect master and worker logs (Priority: P1)

As a cluster operator, I want one log page showing master and worker activity so I can
troubleshoot without switching between machines.

**Why this priority**: Identifying which machine produced a message is the core value of
a cluster log trail.

**Independent Test**: Open the page with known logs from a master and two workers, then
inspect each node individually and return to the combined view.

**Acceptance Scenarios**:

1. **Given** available master and worker logs, **When** the page opens, **Then** it displays
   a combined trail identifying each entry's time, node, log source, and message.
2. **Given** multiple nodes, **When** the operator selects the master or an individual
   worker, **Then** only that node's entries appear; selecting All nodes restores the
   combined view without restoring entries previously cleared in this session.
3. **Given** no logs, initial loading, or unavailable log access, **When** the page is
   viewed, **Then** it clearly distinguishes these states.
4. **Given** only local logs are available because the master is unreachable, **When**
   logs are displayed, **Then** the page explicitly labels that limited coverage.
5. **Given** a keyboard-only operator at desktop or narrow-screen width, **When** they
   open Logs and inspect a long message, **Then** all controls and message content are
   reachable with visible focus, without page-wide horizontal overflow.

---

### User Story 2 - Choose the refresh mode (Priority: P1)

As a cluster operator, I want live updates or a slower refresh interval so I can choose
how frequently the trail changes.

**Why this priority**: The four refresh modes are an explicit part of the requested UI.

**Independent Test**: Produce identifiable entries while selecting each refresh mode
and observe their appearance with the page foregrounded on a healthy connection.

**Acceptance Scenarios**:

1. **Given** a newly opened page, **When** logs become available, **Then** Live is selected
   and new entries appear automatically.
2. **Given** the refresh control, **When** the operator selects 5s, 30s, or 1m, **Then**
   the active selection is visible and subsequent automatic updates follow that interval.
3. **Given** an active refresh mode, **When** another mode is selected, **Then** it replaces
   the previous schedule without duplicating entries or resetting node selection.
4. **Given** the operator has scrolled upward, **When** new entries arrive, **Then** the
   reading position remains stable; returning to the bottom resumes following entries.
5. **Given** an interruption followed by recovery, **When** updates resume, **Then**
   retained entries remain readable and any known missing coverage is labeled rather
   than presented as uninterrupted history.
6. **Given** a log read stops making progress while the page is foregrounded, **When**
   five seconds have elapsed since that read began, **Then** the page reports a read
   timeout, retains prior entries, and retries on the next future selected refresh deadline.

---

### User Story 3 - Clear the displayed trail (Priority: P2)

As a cluster operator, I want to clear the displayed trail so I can focus on subsequent
activity.

**Why this priority**: Clear provides the requested way to begin a fresh troubleshooting view.

**Independent Test**: Clear a populated view, generate new entries, and change refresh
modes and node selection.

**Acceptance Scenarios**:

1. **Given** visible entries, **When** Clear is pressed, **Then** the entire buffered
   trail, including entries hidden by node selection, empties while node selection and
   refresh mode remain unchanged.
2. **Given** the trail was cleared and log coverage remains uninterrupted, **When** new
   entries arrive, **Then** they appear according to the selected refresh mode.
3. **Given** cleared entries, **When** refresh modes change or the connection recovers,
   **Then** previously cleared entries do not reappear during the current page session.
4. **Given** stored logs and another open dashboard, **When** Clear is pressed, **Then**
   neither stored logs nor the other dashboard's view is changed.
5. **Given** buffered entries and an update underway, **When** Clear is pressed before
   it completes, **Then** its delayed result cannot restore cleared entries.
6. **Given** the first load has not succeeded and nothing has been displayed, **When**
   Clear is pressed and a new message then becomes available, **Then** Clear is a no-op:
   loading continues normally and that message is eligible for the first completed read
   that includes it; Clear does not discard that read or delay the refresh schedule.
7. **Given** a successful load followed by Clear, **When** a service restart or change
   between cluster and local-only coverage prevents continuing from the previous position,
   **Then** the page reports a recovery gap, keeps cleared entries absent, and resumes
   subsequent activity from the new position; the omitted recovery window can include
   previously unseen entries generated after Clear.

### Edge Cases

- A cluster with no workers still shows master logs.
- Duplicate node names remain distinguishable by node identity. Unknown or departed
  nodes retain their identity on existing entries; unknown roles are not invented.
- A disconnected view retains its entries and reports the interruption. Silence on a
  healthy connection is distinct from a failed connection.
- An update already underway when Clear is pressed cannot restore cleared entries.
- Repeated identical messages remain distinct when they represent separate log events;
  receiving the same recorded event again does not add a second row.
- Different node clocks do not imply guaranteed chronological ordering across machines.
- Long or markup-like messages remain readable as text without page-wide overflow or
  activating content embedded in a message.
- High-volume output uses a bounded recent trail and identifies when older displayed
  entries have been omitted. Known collection or retention gaps are visible.
- Clear before the first successful load is a no-op, including while an initial read is
  pending or has failed. It does not discard the first successful result or its new entries.
- A read that stalls before its result is complete times out after five seconds. A result
  arriving after timeout cannot replace the current view or prevent a later retry.
- Repeated mode changes and leaving the page do not leave background refresh work running.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The dashboard MUST provide a clearly labeled Logs navigation entry.
- **FR-002**: The page MUST show a combined master-and-worker trail by default and allow
  selection of the master or an individual worker, including departed nodes represented
  in buffered entries.
- **FR-003**: Entries MUST identify timestamp, node, log source, and message. Time labels
  MUST use an explicitly identified timezone; unavailable metadata MUST not be fabricated.
- **FR-004**: A single-choice refresh control MUST offer exactly Live, 5s, 30s, and 1m,
  defaulting to Live when a new page session opens.
- **FR-005**: Live MUST show new available entries continuously. Timed modes MUST update
  automatically every 5, 30, or 60 seconds respectively while foregrounded on a healthy
  connection. Changing modes MUST replace the previous schedule; late work from an old
  mode MUST NOT overwrite the active view or duplicate entries.
- **FR-006**: Clear MUST empty the current page session's buffered trail without deleting
  stored logs, affecting other viewers, or changing node selection and refresh mode.
  Before the first successful load, Clear MUST be a no-op that preserves normal loading.
- **FR-007**: Refreshes, node changes, reconnections, and mode changes MUST NOT restore
  entries removed by Clear during that page session.
- **FR-008**: Updates MUST preserve the operator's reading position when they have
  scrolled away from the newest entries. Following resumes when they return to the bottom.
- **FR-009**: Loading, empty, cleared, disconnected, and local-only coverage states MUST
  be textually distinguishable. Known gaps MUST be labeled, and previously received rows
  MUST remain readable during a connection failure. A log read that has not completed
  within five seconds while foregrounded MUST report a timeout and allow retry on the
  next future selected refresh deadline; a late result MUST NOT overwrite the view.
- **FR-010**: The page MUST follow the dashboard's existing dense, dark visual style.
  Controls MUST support keyboard operation, visible focus, accessible labels, and textual
  status. Text MUST meet WCAG AA contrast and nonessential motion MUST respect reduced motion.
- **FR-011**: The page MUST remain usable at desktop and narrow-screen widths, including
  access to node selection, all refresh modes, Clear, and complete log messages.
- **FR-012**: The page MUST show at most 2,000 recent rows, disclose omission of older rows,
  preserve distinct repeated events, and display log messages as inert text. Viewing or
  clearing logs MUST remain within the existing private-cluster access boundary.

### Key Entities *(include if feature involves data)*

- **Log entry**: An individual recorded message with timestamp, node identity, and source.
- **Log node**: A master or worker associated with entries, including known name and role.
- **Log view session**: Node selection, refresh mode, buffered entries, reading position,
  and cleared state for one mounted visit to the Logs page.
- **Coverage status**: Whether the trail represents cluster logs, local-only logs,
  previously received entries during interruption, or a bounded/gapped recent history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At least four of five operators can locate Logs and isolate a named worker's
  logs within 30 seconds without assistance.
- **SC-002**: After initial loading, on a healthy connection with the page foregrounded,
  at least 95% of new entries matching the selected node appear in the trail within two
  seconds of first becoming readable from the cluster or local log source currently
  serving the page. The interval ends when the entry is rendered in the trail, not when
  its contents are merely received; initial retained history is outside this live sample.
- **SC-003**: During a ten-minute foreground-page check for each timed mode, automatic
  updates occur at the selected 5-, 30-, or 60-second interval within a one-second tolerance,
  provided reads complete within one second.
- **SC-004**: Clear empties the visible trail within one second. Cleared entries remain
  absent through node changes, all mode transitions, and reconnection checks; stored logs
  and a second viewer remain unchanged.
- **SC-005**: Node selection, all four refresh modes, Clear, and long-message inspection
  are usable with keyboard alone at 1280-by-720 and 390-by-844 without page-wide overflow.
- **SC-006**: In checks with one master and three workers emitting 100 entries per second
  in total for ten minutes, controls respond within one second in at least 95% of attempts,
  older-row omission is visible, and every tested interruption reports its known coverage
  limitation instead of claiming complete history.

## Assumptions

- Clear means clear the display, not delete logs. Its scope is the entire buffered trail,
  including entries hidden by the current node filter. A full reload or a new page visit
  may show retained history again.
- A previously unseen message first received after Clear may appear even if it originated
  earlier. Clear does not synchronize clocks or discard unobserved messages across machines.
- Clear before initial loading succeeds does not establish a cut-off for future messages.
  After a successful load and Clear, losing log continuity during restart or a coverage
  change can omit the recovery window, including unseen post-Clear entries. This exception
  must be labeled as a recovery gap and does not apply to an ordinary first load.
- Existing cluster log collection supplies master and worker records. This is a bounded
  troubleshooting trail, not a lossless archive; existing retention remains authoritative.
- Existing dashboard/private-cluster access rules apply. This feature adds no public log
  service, unrelated process collection, search/export workflow, or retention management.
- Refresh timing targets apply to foreground operation on a healthy private connection;
  browser background throttling and interruptions are reported rather than hidden.
- The requested OpenDesign MCP workflow must produce and review a feature-specific design
  for populated, loading, empty, cleared, disconnected, local-only, and narrow-screen states.
  Record its artifact reference before visual implementation; another feature's design is
  background context, not a substitute for this deliverable.
