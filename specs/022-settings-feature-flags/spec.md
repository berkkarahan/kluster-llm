# Feature Specification: Settings Feature Flags Admin View

**Feature Branch**: `022-settings-feature-flags`

**Created**: 2026-10-27

**Status**: Draft

**Input**: User description: "design a small feature flags admin view within or below settings to be able to flip them on the go."

## Clarifications

### Session 2026-10-27

- Q: Where on the Settings page should the Feature Flags admin view be placed? → A: Dedicated Feature flags section in the Settings left-hand navigation menu (alongside Hardware, Network, Tokens, Software, runtime, and Paths), supporting deep-linking via `?section=flags`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Toggle Feature Flags On the Go from Settings (Priority: P1)

An operator working in the dashboard wants to enable or disable an experimental or gated capability (such as one-click auto-deployment or guided private network setup) immediately without switching to a terminal or restarting the node. They open Settings, locate the Feature Flags admin view, and flip the desired flag on or off. The change takes effect right away across the dashboard and persists across restarts.

**Why this priority**: Giving operators a direct, in-dashboard toggle eliminates context-switching to the command line and allows immediate switching between standard and experimental workflows on the go.

**Independent Test**: Can be fully tested by navigating to Settings, toggling a feature flag from disabled to enabled, verifying the toggle state updates immediately, and confirming that the gated workflow in the dashboard reflects the new state without a page reload or node restart.

**Acceptance Scenarios**:

1. **Given** an operator is on the Settings page, **When** they view the Feature Flags section, **Then** they see every supported feature flag along with its current state (enabled or disabled) and a concise description of what it controls.
2. **Given** a feature flag is currently disabled, **When** the operator flips the toggle to enable it, **Then** the flag state is saved immediately, the UI confirms the enabled state, and any dashboard view gated by that flag immediately unlocks.
3. **Given** a feature flag is currently enabled, **When** the operator flips the toggle to disable it, **Then** the flag state is saved immediately, the UI confirms the disabled state, and any dashboard view gated by that flag immediately reverts to its default behavior.

---

### User Story 2 - Persistent Synchronization Between Settings UI and Command Line (Priority: P2)

An operator toggles a feature flag in the Settings UI, later refreshes the browser, stops and restarts the node, or checks feature flags from the command line. Both the Settings UI and the command line consistently report the same persisted state.

**Why this priority**: Operators use both the dashboard and the command line; both interfaces must reflect a single source of truth that survives browser reloads and node lifecycle stops/restarts.

**Independent Test**: Can be tested by enabling a flag in the Settings UI, refreshing the browser and checking the command-line flag status, and conversely toggling a flag via the command line and verifying the Settings UI reflects the updated value.

**Acceptance Scenarios**:

1. **Given** an operator enables a feature flag in the Settings UI, **When** they reload the dashboard or stop and restart the node, **Then** the Settings UI still displays the feature flag as enabled.
2. **Given** an operator changes a feature flag from the command line while the dashboard is open, **When** the dashboard receives its next status update, **Then** the Feature Flags admin view in Settings automatically reflects the updated state.

---

### User Story 3 - Clear Feedback and Safe Recovery on Failed Toggle (Priority: P3)

An operator attempts to flip a feature flag while the node control service is unreachable or returns an error. The Feature Flags admin view prevents silent desynchronization by reverting the control to its last confirmed state and displaying a clear error message.

**Why this priority**: Operators must never be misled into believing a feature flag was changed when the underlying update failed.

**Independent Test**: Can be tested by simulating a failed update when flipping a flag in Settings and verifying that the toggle remains in its prior state and displays an actionable error notice.

**Acceptance Scenarios**:

1. **Given** an operator flips a feature flag while the node is unreachable or rejects the update, **When** the update fails, **Then** the toggle returns to its last confirmed state and a visible error message explains that the change could not be saved.
2. **Given** a feature flag update is currently in flight, **When** the operator views that flag row, **Then** the control indicates a pending save state and prevents duplicate conflicting toggles until the request completes.

---

### Edge Cases

- What happens when the operator rapidly clicks a feature flag toggle multiple times in succession? The control disables or serializes pending changes for that flag until the in-flight update completes so state never races.
- How does the system handle an unrecognized feature flag identifier in an update request? The system rejects unrecognized flag identifiers or non-boolean values with a clear validation error without modifying any existing flag states.
- What happens when a feature flag has never been explicitly toggled on a fresh installation? The Feature Flags admin view displays all registered flags in their default disabled state (`off`).
- What happens if a feature flag is toggled while an inference run is already active? Changing the flag updates the UI and applies to subsequent planning/deployment actions without terminating or corrupting the active inference run.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Settings page MUST include a dedicated "Feature flags" section in its left-hand navigation menu (alongside existing sections and supporting `?section=flags` deep-linking) that renders the compact Feature Flags admin panel when selected.
- **FR-002**: The Feature Flags admin view MUST list all supported feature flags (`private-network-setup` and `prima-auto-deploy`), displaying each flag's identifier, human-readable title, brief purpose description, and current boolean state.
- **FR-003**: Operators MUST be able to toggle any listed feature flag between enabled and disabled with a single interaction.
- **FR-004**: Toggling a feature flag MUST persist the new boolean state in the node's durable feature flag storage so it survives page refreshes, node stops, and node restarts.
- **FR-005**: Toggling a feature flag MUST immediately update the live dashboard state so that views gated by that flag react on the go without requiring a full browser reload or node restart.
- **FR-006**: The system MUST validate feature flag update requests and reject unknown flag names or non-boolean states with a clear error message.
- **FR-007**: While a feature flag update is in progress, the UI MUST prevent duplicate concurrent submissions for that flag and display a clear error while restoring the last known state if the update fails.

### Key Entities *(include if feature involves data)*

- **Feature Flag**: A named operational toggle that gates an experimental or optional workflow across the node and dashboard. Attributes include its unique identifier, human-readable label, short description of the gated capability, current boolean state (enabled/disabled), and last-updated timestamp.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can locate the Feature Flags admin view in Settings and flip any feature flag on or off in under 5 seconds.
- **SC-002**: 100% of successful flag toggles reflect immediately in gated dashboard views within 1 second of completion, with zero browser reloads or node restarts required.
- **SC-003**: 100% of flag changes made in the Settings UI remain intact across browser refreshes, node stops, and restarts, and match the state reported by the command-line interface.
- **SC-004**: 100% of failed or rejected flag update attempts leave the UI toggle in its true persisted state and present a clear error message to the operator.

## Assumptions

- The Feature Flags admin view is presented as a dedicated section in the Settings left-hand navigation menu so operators can switch to it cleanly and deep-link via `/settings?section=flags`.
- Toggling a feature flag applies at the node level for the operator dashboard being viewed, matching the scope of existing Settings panels and the `kllm flags` command-line interface.
- Unlike hardware or runtime backend changes that require a worker restart, feature flags are designed to be flipped on the go and take effect immediately in the UI and subsequent operations.
