# Kluster dashboard polish reference

`index.html` is a self-contained, fixture-driven design reference for translation into the existing Svelte dashboard. It does not contact the cluster, call lifecycle APIs, or modify the source checkout.

## Preserved

- Six existing areas and horizontal navigation: Home, Models, Plan & Run, Connect, Logs, Settings.
- Exact graphite palette, green global accent, yellow scoped to node monitoring, IBM Plex Sans/Mono, 13px root, square hairline panels and compact controls.
- Home fills the remaining viewport with topology. Details/History open only on demand; Chat overlays the workspace and reserves no permanent width. Mobile uses equivalent node telemetry in a scrolling list.
- Models search and token controls stay together; URL/keyword results live in the existing results dialog. Plan & Run retains planning, advanced runtime options and results. Settings retains Hardware, Network, Tokens, Software and Data sections.

## Specific refinements

1. **Shell alignment:** one-line Plan & Run label and a subtle green active underline. At tablet width, navigation gets its own row instead of being squeezed by status messages. At mobile width all six destinations remain available in two compact rows.
2. **Spacing and type:** 4/8/12px rhythm within panels, 15px page titles, clearly tracked uppercase section labels, tabular numerics. Existing page widths reflect the 13px root: Models 728px, Plan & Run 546px, Connect 624px; Settings remains 960px.
3. **Long content:** wrap hostnames, model filenames, command strings and paths. Node positions use measured card heights. Models and planning tables scroll within their own containers; mobile logs use metadata plus a full-width message.
4. **States:** dark native controls, visible green focus rings (yellow within node monitoring), distinct pressed states, disabled controls and red destructive hover. Hover keeps the text/background pair legible. Touch input increases controls to 44px without enlarging the desktop interface.
5. **Error presentation:** source-labeled compact messages have a visible Dismiss action with a specific accessible name. Startup historical snapshots do not produce old error messages. Dismissal affects presentation only; the source-health chip remains truthful. Stop cluster, Stop inference, Stop logging, Leave and Drop clear every displayed error across routes. A generation guard rejects late events from the preceding lifecycle.

## Review flows

- Open any area → **New error** → select a source → **Dismiss**. The message disappears; failed/unavailable source status remains.
- Create errors from multiple areas → Home → **Cluster → Stop**. All messages clear; cluster becomes idle and inference stops.
- Home → **Cluster → Join** after stopping → create an error → **Leave**. Membership becomes idle, messages clear.
- Home → **Cluster → Drop connection**. Messages clear, Offline remains visible, and last node readings are marked stale. **Reconnect** restores transport only; it does not claim failed sources recovered.
- Models → Search → fixture results → Download; Load opens Plan & Run. Plan reveals allocations; Run and Stop update fixture inference state. Chat replies are explicitly labeled fixtures.
- Connect supports PowerShell/Bash commands and copy with a manual-copy fallback. Logs filter by node/tag, switch refresh labels, stop/resume logging and clear only their displayed buffer.

## Boundaries and recommendations

All telemetry, file sizes, allocation numbers, logs, paths, tokens and operation results are labeled fixtures. The screenshot baselines show historical errors consuming header space, a wrapped navigation label, tall model-name cells and mixed native controls; the reference addresses those points without adding a new information architecture. No imagery, background grids, gradients, pills or decorative shadows are introduced.

For production translation, reuse existing components and centralize message visibility rather than clearing authoritative health or validation state. Key errors by source and operation identity, suppress initial snapshots, invalidate pending callbacks at lifecycle boundaries, and keep dismissals stable across refreshes. Keep log events separate from dismissible diagnostics; clearing an error must not erase stored logs.

Verified: all six generated route fragments have balanced tags, unique element IDs and one page heading. Behavior checks cover startup suppression, accessible dismissal, retained source health, Stop/Leave/Drop clearing, rejected late events, offline stale readings, log filters and command generation. All text/state tokens exceed 4.5:1 on the three graphite surfaces (lowest: danger 5.10:1). A desktop visual check confirmed the full-area graph and contextual layout; a RAM-label width correction prevents an isolated percentage line.

390/768/1440px adaptation was reviewed structurally: shrinkable form inputs, wrapping navigation/commands/paths, mobile node list, mobile log rows and table-local scrolling. It has not been browser-tested across all three widths. This is a design reference, not a production integration or live-data validation. IBM Plex fonts are embedded, with their SIL Open Font License retained inside the HTML; the page has no external runtime dependencies.

