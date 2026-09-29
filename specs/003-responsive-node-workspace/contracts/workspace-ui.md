# Interface Contract: Responsive Node Workspace

**Date:** 2026-09-29
**Scope:** Home presentation and interactions over the existing private monitor interface.

## Existing data interface

The payloads, authentication boundary, and pagination rules remain those documented in
[feature 002's usage contract](../../002-node-usage-monitor/contracts/usage-monitor.md).

| Interface | When consumed |
| --- | --- |
| `GET /v1/usage/live` | Existing two-second browser polling; feeds all node summaries. |
| `GET /v1/usage/lifetimes?node_id=...` | On first History opening for a node, context change while open, or retry. |
| `GET /v1/usage/series?node_id=...&lifetime_id=...&window=...` | History opening/refresh and lifetime/window selection. |
| `GET /v1/usage/readings?node_id=...&lifetime_id=...&window=...` | Same history context; existing More action supplies the opaque cursor. |

Use the existing store methods; node selection with History closed must not initiate history
requests. Details uses live data. No new service, collection policy, persisted UI preference,
or native-runtime boundary is introduced. Existing private access remains in force; port
50052 stays private.

## Layout contract

1. Home fills the actual area below the navigation header, including wrapped headers. Closed
   controls/inspectors reserve no side columns. A compact toolbar and status strip are allowed.
2. The graph expands to the remaining height and width; it has no independent 420px/52vh cap.
   Loading, empty, and offline views occupy the same workspace.
3. At widths up to 700px use the existing equivalent full-width list; above that use the
   graph. Test 700/701px as well as 768px to detect intermediate-width regressions.
4. The list scrolls vertically within the available workspace. Large control disclosures and
   dialogs have bounded height and reachable close controls; they cannot push the workspace
   permanently outside the window.
5. Initial one-/four-node views are framed with readable normal fixtures. Fit contains all
   card bounds for sixteen nodes; pan/zoom permits readable inspection of the overview.
6. Layout changes must retain scroll access on Models, Plan & Run, Connect, and Settings.

## Node summary contract

Every graph card and list entry displays the same identity, role, connection, freshness,
measurement age, Host CPU, Host RAM, and per-GPU utilization/memory information. Metrics are
visible before selection. Memory uses one-decimal binary GiB, and RAM percentage uses the
sample's unrounded used/total bytes. The [data model](../data-model.md) defines missing data,
inventory reconciliation, and shared-memory behavior.

- Render every reported device's ID/name and values without requiring a separate inspector.
  Cards/list entries may grow vertically; graph spacing/Fit must include the resulting bounds.
- Keep process CPU/RSS, PID, detailed timestamps, temperature/power, and collector diagnostics
  available in Details. Do not substitute process CPU for Host CPU.
- Zero, unavailable, initializing, stale, disconnected, and shared-memory states have text.
- Long names/assignments may wrap; stable identity remains accessible. Resource values are
  not ellipsized. Use normal text sizing at inspection scale, not tiny text to force Fit.
- Native node buttons expose their visible summary as an accessible name/description, with
  `aria-pressed` for selection. Do not hide measurements behind an overriding identity-only
  label or announce all readings on every poll.

## Interaction contract

| Action | Outcome |
| --- | --- |
| Select node | Update selected identity; retain transform; no cluster/process mutation. |
| Initial layout / Fit | Frame all node bounds with padding in automatic-fit mode. |
| Manual pan or zoom | Enter manual mode; polling and modal operations do not reset view. |
| Resize | Automatic views refit; manual views preserve transform; Fit remains reachable. |
| Cluster controls | Open a native disclosure containing the existing actions and their disabled rules. |
| Details / History | Open the appropriate content in one native modal dialog for the selected node. |
| Dismiss dialog | Close via visible button or Escape; restore focus to its trigger or stable toolbar fallback. |
| Open/close Chat | Preserve existing non-modal interaction, model/sampling controls, and streaming behavior. |

The dialog receives focus and contains keyboard focus while modal. Escape closes only the
active dialog, not Chat underneath it. Hidden content is unmounted, hidden, or inert. Opening
and closing views never starts/stops inference or cancels a chat stream. Keep History's UTC
readings, gap-versus-zero distinction, retained lifetimes, windows, and pagination.

Start/Join retain idle/busy and join-key rules; Stop and Update retain master rules, including
the worker-presence condition for Update; Leave retains worker rules. Operational errors and
recording interruption remain evident in compact status even with controls closed.

## OpenDesign revision brief

Use project `kluster-llm-node-usage-monitor-307e` and existing artifact
`kluster-node-usage-monitor.html`. Its local preview is
[available from the existing daemon](http://127.0.0.1:7456/api/projects/kluster-llm-node-usage-monitor-307e/raw/kluster-node-usage-monitor.html).

Before visual implementation, generate/review desktop (1280x720 and 1920x1080) and narrow
(390x844) variants against this brief:

- Retain near-black graphite, hairline borders, amber selection/focus, IBM Plex Sans/Mono,
  tabular readings, and known master-worker relationship lines.
- Replace both permanent rails with compact controls, a full-remaining-area graph/list,
  and on-demand contextual views. Show a populated one-node and four-node state.
- Put Host CPU, Host RAM used/total/percent, and separate GPU utilization/memory rows directly
  on nodes. Include a multi-GPU card and clear unavailable/stale states.
- Show History/Details opening and returning to the workspace, mobile scrolling, and the
  compact failure/recording status. Keep actual application navigation and control rules.
- Review readable telemetry, keyboard focus, contrast, and reduced-motion treatment. Prototype
  fixture data must stay identified as fixtures; invented traffic labels are excluded.

Record the revised project/artifact references and reviewed choices in
[the existing monitor documentation](../../../docs/node-usage-monitor.md). The existing
artifact was source-reviewed during planning; feature-003 variants are a required subsequent
design step, not a completed or visually validated deliverable of this planning run.

## Acceptance evidence

The runnable scenario matrix is in [quickstart.md](../quickstart.md). Observable checks cover
window/card bounds, field values, keyboard behavior, network request timing, and control
availability. Real-operator SC-005 and physical sensor checks are recorded separately from
synthetic browser evidence.
