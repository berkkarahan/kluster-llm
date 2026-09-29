# Responsive Node Workspace

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — Home layout, node-local resource readings, and contextual inspection

**Specification:** [003-responsive-node-workspace](../specs/003-responsive-node-workspace/spec.md)
**Spec Kit entry point:** [plan.md](../specs/003-responsive-node-workspace/plan.md)

## Conventions captured at creation

- `dashboard/src/routes/+layout.svelte` owns the header and main area, starts/stops `appStore`,
  and uses a minimum-height shell. Its `h-10` header is rem-based; `dashboard/src/app.css`
  sets the root font to 13px and wraps the header at 700px. Home/chat currently assume 40px.
- `dashboard/src/routes/+page.svelte` owns controls, `displayNodes`, selection/history loading,
  a permanent 356px inspector, and non-modal chat. `displayNodes` qualifies cached readings
  when the cluster is not live. Preserve those status and control-availability rules.
- `dashboard/src/app.css:.usage-workspace .node-map-graph` caps graph height at 420px/52vh.
  Node cards are at most 160px wide and the list replaces the graph at widths up to 700px.
- `dashboard/src/lib/components/NodeMap.svelte:fit` is manual and height-only. The component
  duplicates RAM-only summaries between buttons/list and assumes 124px worker spacing.
- `dashboard/src/lib/types.ts:UsageNode` and `UsageReading` already contain host CPU/RAM and
  per-device measurements. `dashboard/src/lib/format-memory.ts:formatGib` supplies binary
  GiB. Process, host, device, reported capacity, and planning estimates have distinct scope.
- `dashboard/src/lib/store.svelte.ts:refreshUsage` polls live usage every two seconds;
  `selectUsageNode`, `selectUsageLifetime`, `selectUsageWindow`, `loadUsageHistory`, and
  `nextUsagePage` provide history with response-sequence checks. Home currently loads history
  eagerly on selection. Collection is agent-owned and continues without the browser.
- `NodeUsageDetails.svelte` and `UsageHistory.svelte` already implement scoped diagnostics
  and UTC history. `JinjaEditor.svelte` supplies the repository's native-dialog precedent.
  These components live under `dashboard/src/lib/components/`.
- The dashboard uses Svelte 5/SvelteKit 2, TypeScript 5, Tailwind 4, and existing Node tests
  plus a Chrome/Edge CDP harness. Feature-002 source is present in the working tree, including
  uncommitted/untracked files; this plan describes that inspected snapshot.
- The existing OpenDesign artifact is recorded in
  [docs/node-usage-monitor.md](../docs/node-usage-monitor.md#reviewed-opendesign-direction).
  Its source was retrieved successfully on 2026-09-29. New feature-003 variants remain to be
  generated/reviewed before visual implementation.

## Intended result

Home fills the browser area below navigation immediately. Compact controls sit above the
remaining-space graph/list; every node exposes Host CPU, Host RAM, and per-GPU usage.
Details and History open on demand, with existing chat and cluster actions reachable.

Use the current telemetry/store/components and native layout/dialog behavior. The complete
[data projection](../specs/003-responsive-node-workspace/data-model.md) and
[interaction contract](../specs/003-responsive-node-workspace/contracts/workspace-ui.md)
define state and edge-case behavior.

## Implementation sequence

### 1. Adapt the OpenDesign reference

In the existing OpenDesign project, produce and review the desktop/mobile variants described
by the [revision brief](../specs/003-responsive-node-workspace/contracts/workspace-ui.md#opendesign-revision-brief).
Show the full-area workspace, populated node telemetry, multi-GPU and unavailable states,
and contextual History/Details. Record artifact references and reviewed choices in the
existing monitor documentation before visual code changes.

### 2. Correct the layout chain

Use a dynamic-viewport-height shell with intrinsic header and shrinkable main region. Carry
available height through Home and NodeMap; retain scrolling for other routes. Remove fixed
Home side columns and the independent graph-height cap. Replace the left rail with a compact
native disclosure, with bounded scrolling and visible operational status while closed.
Anchor Chat to the actual Home area instead of a hard-coded header offset.

### 3. Move live readings onto the nodes

Inside NodeMap, share one summary snippet between graph/list buttons. Use host CPU, sample
RAM bytes/percentage, and device-ID-matched inventory/reading rows. Reuse `formatGib`, display
every reported GPU, and preserve zero/missing/initializing/stale/shared-memory semantics.
Expose the same values to assistive technology without a competing identity-only label.
Leave verbose process and sensor diagnostics available through the existing details view.

Size cards for the summary rather than truncating values. Use a deterministic compact grid
with readable world-space widths, row heights derived from card dimensions, and existing
membership edges. Observe container/card sizes using untransformed bounds; clean up on
unmount. Fit both width and height automatically until a user navigates. Manual transforms
survive polling, selection, and panel use; explicit Fit restores automatic mode. Relative
zoom must work even below the previous 0.5 minimum.

### 4. Make inspection explicit

Add Details and History toolbar actions for the selected node. Reuse one native dialog with
the appropriate existing component; keep Chat non-modal. Restore focus on dismissal and
prevent dialog Escape from also closing Chat. Hidden content must be inert or unmounted.

Gate the existing history-loading effect on History being requested. Preserve lifetime/window
on reopening the same context, retry failed loading, and keep cached history bound to its
node identity. Use existing store callbacks/sequence protection. Selecting or inspecting a
node must not alter cluster membership or inference.

### 5. Verify behavior and update operator documentation

Extend `dashboard/tests/usage-live.test.mjs` with scoped card/list values and availability
cases. Extend `dashboard/tests/usage-workspace.test.mjs` using real readings, 1/4/16-node
layouts, initial fit, resize, card bounds, manual-transform stability, history-request counts,
keyboard focus, Chat, and existing control rules. Reuse history/store regression tests; only
extend them where changed behavior needs coverage.

Follow [quickstart.md](../specs/003-responsive-node-workspace/quickstart.md), running checks
sequentially. Update `docs/node-usage-monitor.md` and `dashboard/DESIGN.md` for the new default
layout, navigation, and design references. Record commands/results, browser/viewport evidence,
and the separate five-operator usability outcome.

## Expected change surface

Primary implementation files are `dashboard/src/routes/+layout.svelte`,
`dashboard/src/routes/+page.svelte`, `dashboard/src/app.css`, and
`dashboard/src/lib/components/NodeMap.svelte`. Existing details/history/store modules are
reused; modify them only if the documented focus or request-context checks expose a required
integration fix. No new runtime dependency or backend contract is needed.

## Completion criteria and remaining work

The [specification's SC-001 through SC-007](../specs/003-responsive-node-workspace/spec.md#measurable-outcomes)
and [validation guide](../specs/003-responsive-node-workspace/quickstart.md) govern completion.
There is no unresolved technical decision. Revised OpenDesign variants, implementation,
browser verification, and the five-operator check are future work; the existing prototype's
HTTP success and source review do not establish those results.

This is the saved implementation-sequence snapshot. The Spec Kit entry point holds its
technical context and constitution gates; `/speckit.tasks` is the next workflow phase.
