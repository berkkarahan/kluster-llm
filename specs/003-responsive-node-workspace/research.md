# Research: Responsive Node Workspace

**Date:** 2026-09-29

Two read-only research passes traced layout/interaction and telemetry/test flows. The
decisions below resolve the planning questions; no technical clarification remains.

## 1. Viewport sizing follows the real header

**Decision:** Use a viewport-height application shell (`100dvh`, with `100vh` fallback), an
intrinsically sized header, and a shrinkable remaining-space main area. Home passes that
height through its toolbar, topology container, NodeMap, and graph/list. Other routes retain
scrollable content. Anchor Home's chat within the workspace instead of a fixed header offset.

**Rationale:** `dashboard/src/routes/+layout.svelte` currently has only `min-h-screen`;
`dashboard/src/routes/+page.svelte` subtracts 40px and reserves 212px/356px side columns.
`dashboard/src/app.css:.node-map-graph` independently caps height at `min(52vh, 420px)`.
The header's `h-10` is rem-based while the root font is 13px, and mobile navigation wraps.
Changing only the graph's cap or inspector cannot correct that whole sizing chain.

**Alternatives considered:** Fixed header subtraction breaks wrapping and zoom; the browser
Fullscreen API requires a separate permission/gesture flow and is not the requested behavior.

## 2. Node-local measurements use existing fields

**Decision:** Render one noninteractive Svelte summary snippet in both NodeMap's graph buttons
and narrow-list buttons. Reuse `UsageNode`, `UsageReading`, and `formatGib`. Host CPU comes
from `reading.host.cpu_percent`, RAM from its used/total bytes, and GPU rows from the union
of node inventory and `reading.devices`, matched by device ID.

**Rationale:** All requested fields exist in `dashboard/src/lib/types.ts`. The current RAM-only
card/list duplication would otherwise diverge. `NodeUsageDetails.svelte` already separates
process, host, and device measurements and remains useful for extended diagnostics. The
current graph button's overriding `aria-label` hides visible metrics from its accessible
name; the shared summary must also be exposed to assistive technology.

**Alternatives considered:** A second data store, remote-node readings sourced from the local
`appStore.gpus`, GPU utilization averages, and process CPU substituted for host CPU would
either duplicate state or misrepresent scope. A new graph/chart library is unnecessary.

## 3. Availability and hardware absence are different

**Decision:** Preserve numeric zero, per-field availability, stale/disconnected states, and
separately labeled reported capacity. Derive RAM percentage only from finite, nonnegative
sample bytes with positive total. Use "CPU backend · no GPUs reported" for empty inventory
on a CPU backend and "GPU information unavailable" otherwise. Keep every device with either
inventory or readings; partially available device fields remain visible individually.

**Rationale:** The current payload has no successful-enumeration flag. Empty discovery can
mean absent hardware or discovery failure. Host/device sampling currently accompanies a
tracked managed process; idle nodes may have no readings. `unavailable_reason` can concern
temperature/power without invalidating a device's numeric utilization or memory.

**Alternatives considered:** Adding discovery/idle-host collection would expand this into
backend telemetry work. Displaying absent values as zero or copying host RAM into GPU fields
would fabricate measurements. Shared memory is labeled, not added into a combined total.

## 4. Fit actual cards; distinguish automatic and manual navigation

**Decision:** Use a small deterministic grid, master first, with readable world-space card
width and row spacing based on the largest rendered card height in each row. Draw only
existing membership edges. Fit the full card bounds on initial nonzero layout and while in
automatic-fit mode. Pointer/keyboard pan or zoom switches to manual mode; Fit returns to
automatic mode. Observe graph/card size changes and clean up observers on unmount.

**Rationale:** `NodeMap.svelte:fit` currently uses only height and is never called initially.
The current 160px cards and 124px worker spacing cannot hold multi-GPU readings. Fit may
produce a scale below 0.5 while "Zoom out" clamps to 0.5, moving in the wrong direction.
World-space bounds and consistent relative zoom avoid viewport-percentage geometry drift.

**Alternatives considered:** Fitting every poll overrides the operator's view. Hard-coded
card heights clip multi-GPU/long-name cases. A force-directed layout adds unnecessary motion
and dependencies. Sixteen nodes get an overview plus navigable readable detail, not a
promise of simultaneous full-detail readability.

## 5. Contextual views use existing components and native behavior

**Decision:** Keep compact toolbar actions plus a collapsed cluster-controls disclosure.
Use one native dialog with Details/History content selected by explicit toolbar actions,
reusing `NodeUsageDetails` and `UsageHistory`. Keep Chat as the existing non-modal drawer.
Only opening History initiates history requests; reopening the same node preserves its
valid lifetime/window selection and refreshes that context.

**Rationale:** Home currently loads history in its selection effect even when it is not
needed. The store already supplies selection, filters, pagination, and `historySeq` response
ordering. `JinjaEditor.svelte` demonstrates native dialog usage in this repository. Native
modality provides focus containment; one dialog avoids conflicting inspector overlays.

**Alternatives considered:** Merely hiding the inspector still eagerly fetches history.
A custom focus trap and overlay framework duplicate browser behavior. Putting nested action
buttons inside node buttons is invalid interactive markup.

## 6. Reuse and adapt the OpenDesign direction

**Decision:** Use existing OpenDesign project `kluster-llm-node-usage-monitor-307e` as the
starting point and produce feature-003 desktop/mobile variants before visual implementation.
The concrete revision brief is in [the UI contract](contracts/workspace-ui.md#opendesign-revision-brief).

**Evidence:** On 2026-09-29 the existing artifact endpoint returned HTTP 200 (54,026 decoded
characters). Source review confirmed a flex-filling graph, graphite panels, amber emphasis,
IBM Plex typography, and a stacked mobile list. It also still contains permanent 212px/356px
rails; that layout is the part to replace. The previous artifact/reference is documented in
[docs/node-usage-monitor.md](../../docs/node-usage-monitor.md#reviewed-opendesign-direction).
This is source review of the existing artifact, not a generated or visually verified 003 revision.

**Alternatives considered:** Reusing the reference unchanged misses the request. Building a
new visual identity discards useful established direction. Prototype-only navigation, fixture
values, and invented link-performance labels do not belong in the production workspace.

## 7. Extend existing behavioral/browser checks

**Decision:** Extend `usage-live.test.mjs` and `usage-workspace.test.mjs`; keep existing history
tests as regression coverage. Run type checks, Node tests, and production build sequentially.

**Rationale:** The existing Chrome/Edge CDP harness can assert real viewport and card bounds,
keyboard behavior, and request counts. Its current graph fixture has null live readings and
clicks Fit explicitly, so it misses both initial sizing and telemetry-rich card geometry.

**Alternatives considered:** Markup-only checks cannot prove responsive sizing; introducing
another browser-test framework is unnecessary. Synthetic checks establish UI behavior, not
real GPU accuracy or the five-operator usability outcome.
