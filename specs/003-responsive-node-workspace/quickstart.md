# Validation Quickstart: Responsive Node Workspace

Run this guide after implementing [plan.md](plan.md) and the
[UI contract](contracts/workspace-ui.md). Commands below run from the repository root.

## Prerequisites

- Use the existing setup from [docs/agent-install.md](../../docs/agent-install.md), including
  installed dashboard dependencies and a supported Node runtime (Node 22 is available in the
  inspected environment).
- Chrome, Chromium, or Edge must be available for the existing browser test. Set `CHROME_BIN`
  to its executable if it is not at a path recognized by `usage-workspace.test.mjs`.
- Complete and record the revised OpenDesign variants from the
  [revision brief](contracts/workspace-ui.md#opendesign-revision-brief) before visual comparison.
- Automated UI checks use the existing isolated fake API and synthetic readings. Manual
  live checks use an already configured private agent/cluster; port 50052 remains private.

## Automated checks

Run in this order, waiting for each command to finish:

```sh
npm --prefix dashboard run check
node --test --test-concurrency=1 dashboard/tests/*.test.mjs
npm --prefix dashboard run build
```

Expect no new diagnostics, passing behavioral checks, a completed production build, and an
executed browser check rather than a browser-unavailable skip. Record any pre-existing
failures with the exact command/output. Keep check/build sequential because they share
generated SvelteKit files. Use the existing test framework and browser harness.

## Browser walkthrough

For an installed agent, open its production dashboard after building:

```sh
uv run kllm ui
```

For development against an existing local agent on port 18765:

```sh
npm --prefix dashboard run dev -- --host 127.0.0.1
```

Open the local URL printed by Vite; its existing configuration proxies `/v1` to that agent.
The automated browser tests provide synthetic node counts and failures; a manual local
session only demonstrates the actual connected hardware unless using that test harness.

### A. Screen occupancy and initial layout (SC-001, SC-002, SC-007)

1. Test 1280x720, 1440x900, and 1920x1080 with 1, 4, and 16 nodes. Reload before interacting.
   Workspace top must match actual header bottom and its bottom must match the viewport
   within 2px rounding tolerance. The graph fills the remaining space after compact chrome.
2. Verify no permanent side columns and no old 420px-height cap. One-/four-node normal
   fixtures are immediately readable and in view without clicking Fit. For sixteen nodes,
   all card bounds fit in the overview, and pan/zoom reaches readable detail.
3. Use real metric fixtures, including a two-GPU node, long identities, and a large GPU list.
   Cards do not overlap at inspection scale; resource text is not clipped. Verify that Fit
   contains actual enlarged card bounds, not just nominal positions.
4. Resize to 768x1024, 700px, 701px, and 390x844. Verify no page-wide horizontal overflow;
   the narrow layout is a full-width list with every node/device reachable by vertical scroll.
5. Pan/zoom manually, wait through at least two updates, select another node, open/close an
   inspector, and resize. Selection and manual transforms persist. Fit restores automatic
   fitting. Crossing list/graph breakpoints does not reset selected identity.
6. At 200% browser zoom and in short landscape view, controls and close buttons remain
   reachable. Open Models, Plan & Run, Connect, and Settings and confirm their content scrolls.

### B. Node-local values and states (SC-003, SC-004)

With all contextual views closed, verify both graph and list summaries using known values:

| Fixture | Expected result |
| --- | --- |
| Host CPU 25%, RAM 8/16 GiB; process CPU 3%, RSS 2 GiB | Card shows Host CPU 25%, Host RAM 8.0/16.0 GiB and 50%; process figures are separately scoped in Details. |
| GPU 0: 75%, 6/8 GiB; GPU 1: 0%, 0/4 GiB | Both IDs and each field are visible; numeric zero is not unavailable. |
| Null CPU, partial GPU sensors, missing/zero RAM total | Explicit field-level availability; no invalid percentage or fabricated zero. |
| Inventory-only GPU and reading-only GPU | Both remain represented; reported capacity is distinct from sample totals. |
| Empty GPU inventory on CPU vs unknown backend | Qualified no-GPUs-reported versus GPU-information-unavailable text. |
| Unified memory | Shared labeling; no host-plus-device double count. |
| Initializing, stale at ten seconds, disconnected | Text and sample age distinguish the states from a fresh measured zero. |
| No managed process / no reading | Unavailable measurements with reported capacity separately identified. |
| Failed live request or interrupted recorder | Cached data is qualified; error/last successful save remains evident with panels closed. |

Repeat loading, empty cluster, offline, and long/duplicate-name cases. Numeric equality is
checked against synthetic inputs, not assumed to validate physical sensors.

### C. On-demand history, keyboard, and controls (SC-006, SC-007)

1. Reload with history closed; count fake-API history calls in the browser regression test.
   Initial polling and node selection make zero history calls. Opening History loads the
   selected node's lifetimes and matching readings.
2. Use Tab/Enter to select a node and open Details/History. Verify visible focus, meaningful
   metric names, modal focus containment, and close-button/Escape dismissal with focus return.
   A screen-reader check must expose the metrics, not just the hostname.
3. Select a process lifetime, each 15m/1h/24h window, an exact UTC reading, and More pagination.
   Close/reopen and verify valid context persists. Change nodes and inject delayed responses;
   previous-node history must never appear under the current node's title. Retry a failed load.
4. Open Chat, then a contextual dialog. Escape closes only the active dialog. Chat remains
   available and its stream, model/sampling controls, and disable rules remain correct.
5. Exercise the existing idle/master/worker/busy control matrix through the fake API. Compact
   controls preserve all enable/disable rules, including join-key and worker-presence rules.
6. Enable reduced motion and inspect focus/text contrast against the recorded design. Status
   does not depend on color alone. Routine readings do not steal focus or announce every poll.

## Human and live evidence

For SC-005, give five operators the same four-node view with distinct CPU/RAM rankings. Ask
each to identify the highest-CPU and highest-RAM nodes without opening details; at least four
must answer both within 15 seconds. Record completion time and errors; an automated test is
not a substitute for this outcome.

A live smoke check confirms the dashboard presents existing readings from the actual node.
Record OS/backend/browser and unavailable sensors. Physical sensor accuracy and feature-002
hardware validation remain separate evidence; this UI refactor does not establish them.

Record implementation verification and revised OpenDesign references in
[docs/node-usage-monitor.md](../../docs/node-usage-monitor.md). This guide defines future
checks; no UI implementation or usability result is claimed by the planning artifacts.
