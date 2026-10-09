# Feature Specification: Dashboard Live Updates

**Feature Branch**: `main` (current branch; no branch-creation hook registered)

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "consider implementing sockets or websockets for the web app instead of polling surfaces"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See cluster changes as they become available (Priority: P1)

As a cluster operator, I want the dashboard to receive current status and resource
readings automatically so I can monitor the cluster without repeated background checks
for information that has not changed.

**Why this priority**: Status and usage drive the shared header and node workspace;
replacing their recurring checks delivers the broadest reduction in dashboard traffic.

**Independent Test**: Open a dashboard on a master and on a worker, change cluster
membership and managed-process usage, and compare the displayed state with known changes.

**Acceptance Scenarios**:

1. **Given** an available cluster, **When** the dashboard opens, **Then** current status
   and available usage appear without waiting for another change; loading and unavailable
   values are distinct from an idle cluster or measured zero.
2. **Given** live updates are healthy, **When** membership, role, inference activity,
   worker-update progress, warnings, or resource readings change, **Then** relevant views
   update without a page reload or recurring per-surface checks for those changes.
3. **Given** a selected node, a positioned node map, and unsaved model settings, **When**
   routine updates arrive, **Then** those choices remain intact, valid plan results do not
   disappear solely because telemetry changed, and repeated warnings do not multiply.
4. **Given** the dashboard is opened through a worker, **When** cluster-wide information
   is unavailable but local information remains available, **Then** the affected view
   identifies its limited coverage rather than treating local state as the entire cluster.

---

### User Story 2 - Follow model operations to a trustworthy outcome (Priority: P1)

As a cluster operator, I want download and model-load progress to update as work advances
so I can tell when a model is ready, an operation failed, or cancellation completed.

**Why this priority**: These operations already expose progress and actionable outcomes;
changing update delivery must preserve the operator's ability to act on them.

**Independent Test**: Start, complete, cancel, and fail representative download and load
operations while viewing their progress, including a completion during a brief interruption.

**Acceptance Scenarios**:

1. **Given** an operation is underway before the page opens, **When** the operator opens
   its view, **Then** its current progress appears and continues updating automatically.
2. **Given** a running operation, **When** it completes, fails, or is cancelled, **Then**
   the reported final outcome replaces the running state; an error is not shown as success.
3. **Given** a completed download, **When** its completion is received more than once,
   **Then** the available-model list reflects the result and existing completion feedback
   and automatic model selection occur at most once for that operation during the visit.
4. **Given** an operation changes during a connection interruption, **When** access
   returns, **Then** the view reconciles with its available current or final state rather
   than remaining falsely active; an outcome that cannot be recovered is labeled unknown.

---

### User Story 3 - Read live logs at the chosen pace (Priority: P1)

As a cluster operator, I want Live logs to arrive as they become available while retaining
the slower refresh choices and Clear behavior I use for troubleshooting.

**Why this priority**: Logs are another recurring-check surface, with existing interaction
and continuity guarantees that must survive the move to live delivery.

**Independent Test**: Generate identified master and worker messages, switch through all
four refresh modes, clear the trail, and interrupt and restore access.

**Acceptance Scenarios**:

1. **Given** Logs is open in Live mode, **When** a new matching entry becomes available,
   **Then** it appears automatically once, with its timestamp, node, source, and message.
2. **Given** any refresh mode, **When** the operator selects Live, 5s, 30s, or 1m, **Then**
   the selected mode replaces the previous one, preserves the node filter, and controls
   when new entries appear; timed modes do not display arrivals between their deadlines.
3. **Given** a previously populated trail, **When** Clear is pressed, **Then** all buffered
   rows disappear from this visit only; late updates, mode changes, and reconnections
   cannot restore cleared entries, while subsequent eligible entries can appear.
4. **Given** the operator is reading older entries, **When** new rows arrive, **Then**
   the reading position remains stable unless its row has left the bounded trail, in
   which case the omission is disclosed and the oldest remaining row is reachable.
5. **Given** continuity cannot be recovered after interruption or a coverage change,
   **When** updates resume, **Then** a gap is identified and available subsequent activity
   continues without claiming complete history or restoring cleared rows.

---

### User Story 4 - Recover without reloading or repeating work (Priority: P1)

As a cluster operator, I want to know whether updates are live, delayed, or unavailable
and have the dashboard recover automatically so I can trust what I am seeing.

**Why this priority**: A quiet or broken update connection must not leave old information
looking healthy or cause an operator's command to be performed twice.

**Independent Test**: Interrupt the network, restart the serving agent, make live delivery
unavailable while ordinary reads still work, and hide and restore the tab.

**Acceptance Scenarios**:

1. **Given** current displayed information, **When** update delivery becomes unavailable,
   **Then** the dashboard retains the last known values, labels the interruption and last
   successful update, and attempts recovery without reloading or repeating commands.
2. **Given** live delivery is unavailable but ordinary reads are supported and permitted,
   **When** the dashboard detects that condition, **Then** automatic periodic refresh keeps
   affected views usable and is explicitly labeled; restored live delivery replaces it.
3. **Given** normal live delivery resumes, **When** current state is recovered, **Then**
   older delayed information cannot overwrite it, and selections and unsaved inputs remain.
4. **Given** a healthy but unchanged cluster, **When** no application values change,
   **Then** the dashboard continues to show a healthy update connection rather than
   diagnosing inactivity as disconnection; genuinely old usage readings still become stale.
5. **Given** a hidden tab or a departed page, **When** the operator returns, **Then**
   applicable views recover current information once without multiplied updates; closing
   the dashboard does not stop inference, usage recording, or log collection.
6. **Given** access to an area is denied under existing rules, **When** live delivery or
   fallback is attempted, **Then** access remains denied, a readable access error appears,
   and diagnostics contain no credentials.
7. **Given** an existing cluster, **When** commands, model controls, chat, saved-history
   inspection, and command-line status and log viewing are exercised alongside reconnecting
   dashboards, **Then** their results and access rules remain unchanged and each requested
   action runs only once.

### Edge Cases

- State changes while the initial view is loading or while a recovery read is underway;
  completing that read must not overwrite newer accepted state.
- A silent connection failure differs from an unchanged healthy cluster. A connection can
  be healthy while one node's readings are stale or recording has failed.
- The serving agent restarts or changes between idle, master, and worker roles. Information
  from its previous role must not overwrite the current role's view after recovery.
- Duplicate, delayed, invalid, or unrecognized updates must not corrupt valid state, repeat
  commands, or create duplicate log rows. Separate identical log messages remain distinct.
- A selected node departs or a process restarts; retained history stays attributable to the
  correct node and process lifetime, and unavailable readings are not converted to zero.
- A slow viewer or burst of logs cannot grow pending work indefinitely. Intermediate progress
  may be coalesced, but the latest available outcome and any known log omissions remain visible.
- Clear before the first successful log load remains a no-op. After Clear, a restart or
  coverage reset may omit an unrecoverable window, including unseen entries, only with a gap notice.
- Repeated route visits, mode changes, network failures, and hide/show cycles must not
  accumulate background work. One viewer's Clear, navigation, or disconnect cannot affect another.
- An access denial must be shown as an access error; recovery or periodic refresh must not
  bypass the existing access rules or expose credentials in diagnostics.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The dashboard MUST provide automatic live delivery for all five existing
  recurring-update areas: cluster/status information, live node usage, model-load progress,
  model-download progress, and the log trail in Live mode. Status includes membership,
  inference activity, worker-update progress, and reported warnings and errors.
- **FR-002**: Opening or restoring a view MUST obtain its available current state without
  requiring a subsequent change. Loading, empty, unavailable, and measured-zero states MUST
  remain distinguishable, and newer accepted information MUST survive initial-load races.
- **FR-003**: While live delivery is healthy, the browser MUST NOT make recurring
  per-surface reads merely to discover changes in these areas. Initial loading, recovery,
  explicit user actions, selected timed log modes, and labeled fallback are permitted
  exceptions. Connection-health checks MUST NOT become full per-surface state reads.
- **FR-004**: Live updates MUST preserve node and history selection, map position, log
  reading position, and unsaved settings. Telemetry-only changes MUST NOT invalidate a valid
  model plan. Existing deliberate selection changes after successful actions remain supported.
- **FR-005**: Progress MUST distinguish running, successful, failed, and cancelled outcomes
  when reported. Recovery MUST reconcile operations that ended while disconnected and label
  unavailable outcomes as unknown. Repeated delivery MUST NOT repeat completion side effects.
- **FR-006**: Logs MUST retain exactly Live, 5s, 30s, and 1m choices, defaulting to Live
  for a new visit. Timed modes MUST display new entries at the selected interval while
  foregrounded, within one second when reads finish within one second. Initial loading and
  return from a hidden tab may refresh immediately; ordinary arrivals MUST NOT bypass the
  selected interval. Mode changes MUST replace prior work without duplicating entries.
- **FR-007**: Logs MUST preserve node filtering, display-only Clear, event deduplication,
  reading-position behavior, and the 2,000-row recent-trail bound. Clear MUST affect only the
  current visit, MUST be a no-op before the first successful load, and MUST NOT allow cleared
  entries to reappear during that visit. Known retention, recovery, or trimming gaps MUST be
  labeled, including any omitted recovery window after Clear.
- **FR-008**: The dashboard MUST textually distinguish loading, live, reconnecting,
  periodic-refresh fallback, and unavailable update delivery. For an interrupted area it
  MUST retain prior values and show the last successful update time, or indicate that none
  succeeded. Foreground delivery failures MUST be reported within ten seconds. A healthy
  connection with unchanged data MUST NOT be labeled failed solely because values are unchanged.
- **FR-009**: Delivery health MUST remain distinct from data freshness, recording health,
  and cluster-versus-local coverage. Usage readings MUST still become stale at ten seconds
  without a fresh observation; live delivery MUST NOT relabel old readings as fresh. Loss
  of membership MUST retain the existing 25-second maximum from last contact to disconnection.
- **FR-010**: Following an interruption, recovery MUST be automatic, restore available
  current state, reject superseded information, and disclose unrecoverable log gaps. It
  MUST NOT replay user commands or require a page reload. Retry work MUST remain bounded,
  with at most one recovery attempt underway per affected area in a tab.
- **FR-011**: When live delivery is unsupported or unavailable but ordinary reads remain
  permitted and functional, the affected areas MUST use explicitly labeled automatic
  periodic refresh. Fallback MUST start within ten seconds of live-delivery failure and
  display available changes within ten seconds thereafter; selected timed log modes instead
  retain their deadlines. Only one delivery mode may drive an area at a time. The dashboard
  MUST detect restored live availability within 60 seconds and replace fallback; inability
  to use either mode or an access denial MUST be shown as an error rather than healthy fallback.
- **FR-012**: Leaving a page or closing the dashboard MUST release its unneeded update work;
  shared visible header information may continue while the dashboard remains open. Hidden
  tabs MUST suspend application-data refresh work, allowing only connection-health and
  recovery work. Returning MUST recover current data without accumulating hidden-period
  work. Collection, recording, and running workloads MUST remain independent of viewers.
- **FR-013**: The dashboard MUST reject invalid updates without replacing valid state,
  prevent duplicate effects, and prevent older information from overwriting newer state.
  Unusable updates MUST produce a diagnostic or recovery state without exposing secrets.
  Slow viewers MUST have bounded pending work and MUST NOT block other viewers or cluster
  operations; omitted log coverage MUST be disclosed.
- **FR-014**: Master-served and worker-served dashboards MUST retain their current scope
  and access rules. Coverage loss MUST be explicit, credentials MUST NOT appear in update
  diagnostics, and the feature MUST NOT require exposing private worker services publicly.
- **FR-015**: Existing cluster commands, model controls, chat, saved-history inspection,
  and non-browser consumers MUST retain their behavior. Connection restoration MUST NOT
  resubmit a start, stop, join, leave, download, cancellation, or chat action.
- **FR-016**: New status and recovery information MUST use accessible text, remain readable
  at desktop and narrow widths, and preserve keyboard focus during updates. Notifications
  MUST announce meaningful state changes rather than every reading or log arrival.

### Key Entities *(include if feature involves data)*

- **Dashboard view session**: One open dashboard's current views, selections, unsaved input,
  log refresh choice, and display-clear boundary, independent of other viewers.
- **Observed state**: Current cluster status, node reading, or operation progress, with its
  subject identity, observation freshness, availability, and current or final outcome.
- **Log event**: A distinct recorded message with stable identity, time, node, and source;
  repeated receipt of the same event differs from separate events with identical text.
- **Update health and coverage**: The delivery mode, last successful update, availability,
  cluster or local scope, and any known interruption or missing coverage for an area.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With healthy live delivery and the dashboard foregrounded, at least
  95% of status changes, available usage readings, operation-progress changes, and Live log
  entries appear within two seconds of becoming available to the agent serving the view.
  Initial current-state display completes within three seconds in at least 95% of visits.
- **SC-002**: In matched ten-minute idle-dashboard checks after initial loading, with Logs
  in Live mode, browser-initiated background requests to check current application state
  decrease by at least 90% versus the existing dashboard. Compare one and five open tabs
  separately, with the same cluster state and no user actions or recovery interruptions.
- **SC-003**: In network-loss, silent-failure, and agent-restart checks, every affected
  foreground view reports interrupted delivery within ten seconds and recovers current
  available state within ten seconds after permitted ordinary reads become functional.
  Fallback starts within ten seconds of live-delivery failure when ordinary reads work,
  is explicitly labeled, and displays subsequent available changes within ten seconds.
  Selected timed log modes instead display recovered entries at their next future selected
  deadline, within the SC-005 tolerance. Restored live availability replaces fallback within
  60 seconds without a page reload or repeated command.
- **SC-004**: Across at least 20 route visits, refresh-mode changes, reconnects, and
  hide/show cycles per dashboard, checks find zero duplicated log rows or completion effects,
  zero restored cleared rows, and zero newer states overwritten by delayed information.
  Closing the dashboard leaves no viewer-owned update work after ten seconds.
- **SC-005**: During ten-minute checks of each timed log mode, updates respect its selected
  interval within one second when reads finish within one second. Clear takes effect within
  one second, keeps stored logs and a second viewer unchanged, and every known coverage gap
  in the interruption and high-volume checks is visibly identified.
- **SC-006**: With 1, 4, and 16 nodes and up to five simultaneous dashboards, the freshness
  targets above hold and at least 95% of node selections, mode changes, and Clear actions
  respond within one second. Include a ten-minute run with 100 log entries per second in
  total and one slow viewer; healthy viewers meet the same targets while the slow viewer
  recovers to current available state with any omission labeled.
- **SC-007**: At least four of five operators can distinguish live delivery, periodic
  fallback, unavailable delivery, and stale node readings within 30 seconds without
  assistance or relying on color. Status inspection and log controls remain usable with
  keyboard alone at 1280-by-720 and 390-by-844 without page-wide horizontal overflow.

## Assumptions

- The request is interpreted as preparing a practical migration of the dashboard's current
  recurring-update surfaces. The suggested transport is a candidate to assess during
  planning, not a mandated architecture; the requirements describe observable outcomes.
- Existing cluster state, usage collection, progress reporting, and log collection remain
  the sources of truth. Their collection intervals and private node-to-node communication
  are outside this change. Live delivery does not make an unobserved physical change available
  sooner, promise every intermediate progress value, or create a lossless log archive.
- Scope covers the five areas in FR-001. Search, configuration reads, recorded-history
  queries, and explicit commands remain on demand; existing chat streaming is not being
  redesigned. The feature does not add remote hosting, new roles, or new retention policies.
- Selected timed log refresh and labeled compatibility fallback are deliberate exceptions
  to replacing recurring browser checks. Fallback must not silently become the normal
  delivery mode on a healthy supported installation.
- The initial target is the existing local/private-cluster dashboard on supported desktop
  browsers, with 1–16 nodes and up to five concurrent viewers. Timing criteria apply to
  foreground tabs and data already available at the serving agent; hidden-period and
  source-collection delays are measured separately rather than counted as delivery latency.
- Preserve usage identity, freshness, recording independence, and history behavior from
  [002-node-usage-monitor](../002-node-usage-monitor/spec.md), the workspace interactions from
  [003-responsive-node-workspace](../003-responsive-node-workspace/spec.md), and log modes,
  Clear, bounded retention, and coverage semantics from
  [004-cluster-log-trail](../004-cluster-log-trail/spec.md). This feature replaces recurring
  Live delivery, not those behaviors. The existing five-second log-read deadline continues
  to apply to discrete reads; it is not a maximum duration for healthy continuous delivery.
- Performance validation must record the before/after workload, browser, operating systems,
  model and backend when inference is active, hardware, cluster size, and viewer count.
  These are acceptance targets, not claims of already measured performance. No exception
  to the project constitution is proposed.
