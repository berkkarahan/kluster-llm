# Node and process usage monitor

The agent now samples only its tracked managed child every two seconds while running, independently of browser tabs. The master stores readings in `KLLM_HOME/usage.sqlite3` (24-hour rolling window; 256 MiB physical database and SQLite sidecar allowance). Worker readings reach the master through the existing private join-key control plane. Unsupported device sensors are unavailable, not measured zero. The home page offers node inspection and bounded 15m/1h/24h history, including retained departed-node lifetimes. Port 50052 remains private.

On the Home page, the node workspace fills the area beneath navigation. Each node card (or its
stacked list entry on a narrow screen) shows its identity, role, connection/freshness state and
measurement age plus Host CPU, Host RAM used/total/percent and one row per reported GPU before
selection. Cluster controls sit in a collapsed disclosure so no permanent sidebar reserves node
space. Detectable process, host and device readings and a 15-minute, one-hour or 24-hour history
window open on demand from the Details and History actions in the native inspector dialog, where
a process lifetime, plotted bucket or timestamped exact reading reveals its values. Focus returns
to the trigger when the dialog closes, and Escape closes the dialog without closing Chat. Retained
history stays available on Home even after stopping the master; those nodes are labeled
disconnected, not live. The node map frames one and four nodes on load, fills the remaining width
and height, and supports Fit, zoom and arrow-button pan; none of those controls changes cluster
membership or inference. Chat opens from Home's Chat button; Plan & Run stays on `/run`.

Fit shows the full registered master/worker map; cards retain a bounded last-known hardware and model-assignment summary after departure. History shows CPU alongside process RSS, plus available scoped host/device readings without filling missing intervals. Point pagination uses signed, window-bound cursors that stay valid across monitoring-agent restarts. A failed worker ingest is visible from a worker-served dashboard without exposing its join key. If the master becomes unreachable, the worker page reports interrupted recording with the last known successful save (if available) and marks cached measurements stale.

Process CPU is a percent of total machine logical-processor capacity. Resident process memory, host RAM, device-wide memory/capacity and planner estimates have separate labels and are shown in one-decimal binary GiB. A shared device memory pool is counted once. Optional GPU sensor fields require supported device tooling; `unavailable` does not mean measured zero. Readings turn `stale` after ten seconds, while a departed node remains selectable as `disconnected` for its retained history. `unrecorded` denotes time before collection; history gaps are not connected as zero samples. Failed recording shows its error and last successful save without stopping inference; verify free space and the private master control connection when history is interrupted.

On Windows Vulkan/ROCm nodes, the agent attempts to read device-wide **dedicated** GPU memory used from the Windows `GPU Adapter Memory(*)\Dedicated Usage` performance counter. It matches the counter's adapter LUID to the DirectX registry adapter description and the selected inventory GPU; an ambiguous match, missing counter, or measurement above reported capacity stays unavailable rather than attributing another GPU's activity. GPU utilization, temperature and power are independent optional sensors and may still be unavailable. To verify on the affected Windows worker, compare `GET /v1/usage/live` → that node's `reading.devices[0].used_bytes` (bytes) against Task Manager → Performance → Radeon → Dedicated GPU memory; `Get-Counter -Counter '\GPU Adapter Memory(*)\Dedicated Usage'` can diagnose missing counters. The Apple unified-memory pool is shared with host RAM: reported GPU capacity is not additional VRAM, and host RAM use is not claimed to be GPU-only use.

Private same-origin API: `GET /v1/usage/live`, `GET /v1/usage/lifetimes?node_id=…`, `GET /v1/usage/series?node_id=…&lifetime_id=…&window=15m`, and `GET /v1/usage/readings?node_id=…&lifetime_id=…&window=15m&limit=500&cursor=…`. Internal worker→master ingestion is `POST /v1/usage/ingest` with the existing `X-Kllm-Key`. History is never stored in the browser.

## Reviewed OpenDesign direction

On 2026-09-28, the local launcher was corrected outside this repository to use OpenDesign's packaged Electron/Node runtime (ABI 145). Its existing `better-sqlite3` binary loads and successfully queries SQLite under that runtime; the old launcher selected Node 26 (ABI 147). The user confirmed a polished responsive web prototype with connected live-node and history flows. The generated artifact was reviewed via OpenDesign's complete artifact bundle and its served preview:

- **Project:** Kluster LLM Node Usage Monitor (`kluster-llm-node-usage-monitor-307e`).
- **Artifact:** `kluster-node-usage-monitor.html` (run completed successfully; preview HTTP 200).
- **Preview (local daemon):** http://127.0.0.1:7456/api/projects/kluster-llm-node-usage-monitor-307e/raw/kluster-node-usage-monitor.html
- **Reviewed decisions:** near-black graphite panels and cool hairline borders; feature-local high-contrast amber for selected cards, telemetry traces and keyboard focus; IBM Plex Sans/Mono; ~212px left control rail, node-centered master→worker map and adjacent ~356px detail/history inspector at desktop; switch to a real stacked node list on narrow screens; chat stays reachable through a drawer. Preserve the existing app's actual controls/routes and measured data semantics; the prototype's fixture data, demo-only extra navigation, and invented RDMA/TCP link labels are **not** production data. Missing/stale/disconnected/gap/recording-failed states must remain textually distinct. Keep zoom/Fit, keyboard-exact history points and reduced-motion.
- **Review notes:** Preview returns 200; OpenDesign lint has 0 P0 issues, one tracking (P1) and one section-anchor (P2) suggestion. Implement with tracked all-caps labels and semantic section anchors rather than copying those minor misses.


## Responsive workspace refactor (feature 003, 2026-09-29)

Feature `003-responsive-node-workspace` changed presentation only; collection, retention, the
private API and stored history are unchanged from feature 002.

- **Shell**: `dashboard/src/routes/+layout.svelte` and `dashboard/src/app.css` use a
  `100dvh` grid (`auto minmax(0, 1fr)`) so the header is intrinsic and `main` owns the
  remaining height and scrolling. The old fixed 40px offset is gone.
- **Home**: the 212px/356px permanent rails were replaced by one compact toolbar (collapsed
  native cluster-controls disclosure, live status, Details/History/Chat actions), a
  remaining-height topology, and one native `<dialog>` inspector. The graph no longer has the
  `min(52vh, 420px)` height cap. The closed Chat drawer is clipped by the workspace so it
  cannot add scrollable overflow.
- **Nodes**: `dashboard/src/lib/components/NodeMap.svelte` renders one shared summary snippet in
  both the graph cards and the narrow-screen list, using only existing `UsageNode`/`UsageReading`
  fields and `formatGib`. It shows Host CPU, Host RAM used/total/percent, and one row per device
  from the union of node inventory and reading devices (matched by `String(index)`/`device_id`).
  Measured zero stays numeric; `null`/invalid shows availability text; inventory-only devices
  show reported capacity; empty inventory says "CPU backend · no GPUs reported" or "GPU
  information unavailable"; shared memory is labeled and never summed with host RAM. Card
  heights are measured after mount so row spacing and Fit follow real content.
- **Navigation**: a deterministic master-first world-space grid replaces viewport-percentage
  positions. Fit runs automatically until the user pans/zooms; manual transforms survive
  polling, selection and resizing; explicit Fit restores automatic mode. Fit can use the
  calculated scale below ordinary zoom levels to contain the full map even in short landscape;
  zoom-out continues to decrease from that scale.
- **History on demand**: selecting a node with the inspector closed makes no history request.
  Opening History loads lifetimes/series/readings for the selected node; reopening the same
  node refreshes the retained lifetime/window; changing the selected node while open
  reinitializes the context.
- **Operational status while collapsed** (convergence T029): the compact toolbar shows the
  recording state and last save plus `usageError`, `apply_error`, `apply_warning`, any
  top-level or per-worker `update` error, every `netsec_warnings` entry and a non-empty
  `overlay_status`, so a collapsed cluster-controls disclosure does not hide an operational
  failure. Overlay state is informational/muted; warning color is reserved for actual warnings
  and errors.
- **Long labels** (convergence T028): device labels are shrinkable/wrap-capable with a 60%
  width cap, so an overlong GPU name wraps instead of pushing the measured value outside the
  card; `dashboard/tests/usage-workspace.test.mjs` asserts every `.node-metric .v` stays within
  its card's bounds.

### Design reference

- **Artifact**: [`dashboard/design/003-responsive-node-workspace.html`](../dashboard/design/003-responsive-node-workspace.html)
  — desktop 1280×720 and 1920×1080 variants plus the 390×844 stacked list, showing node-local
  Host CPU/RAM/multi-GPU rows, the collapsed cluster controls, the on-demand inspector and the
  stale/disconnected/unavailable/recording-failure states.
- **Direction**: reuses the existing OpenDesign `tech-utility` direction (graphite panels,
  hairline borders, amber selection/focus, IBM Plex Sans/Mono) and removes the permanent rails.
- **Lint**: `od lint dashboard/design/003-responsive-node-workspace.html --fail-on p1` → 0 P0,
  0 P1, 0 P2 (`ok: true`). One initial P1 (`all-caps-no-tracking` on `nav span`) was fixed by
  raising letter-spacing to 0.08em.
- **OpenDesign registration (2026-09-29)**: the artifact is registered in an OpenDesign
  project created through the OpenDesign MCP server.
  - **Project**: `Kluster LLM Responsive Node Workspace` — id
    `kluster-llm-responsive-node-workspace-d8d4`.
  - **Artifact**: `003-responsive-node-workspace.html` (20,368 bytes, version
    `cf84d5b5-c511-4464-8a23-f3fd7ffc324a`); SHA256 of the stored copy matches the repository
    file (`57b8ab8135c9b3413af8e8f67659ccd5da9809e116857f3ede3e0154e99291e9`).
  - **MCP config**: the launcher config passes `ELECTRON_RUN_AS_NODE=1` and runs
    `<en>\af58b746af3495a9\Open Design.exe` with the `0.24.1` `daemon-cli.mjs mcp`; the
    stdio handshake, `tools/list` (22 tools) and `create_project`/`create_artifact` all
    succeed with empty stderr. Project creation uses the `create_project` tool and artifact
    registration the `create_artifact` tool.
  - **Preview/studio URLs are daemon-session scoped** and stop responding when that MCP
    session exits; retrieve a live one with the `get_project` tool or by opening the project
    in the OpenDesign UI.
  - **`od artifacts create` diagnosis**: the `unknown option: ;` failure is a stray literal
    `;` argv token rejected by the wrapper's `parseOptions`, not a version defect — quote a
    multi-word `--project` value and drop the `;`. The `od` CLI and the OpenDesign MCP follow
    the running desktop daemon through `OD_SIDECAR_CLIENT_ENDPOINT` (0.24.1, namespace data
    directory). Do not pin `--daemon-url` to `http://127.0.0.1:7456`; that port was a leftover
    0.22.2 daemon with a separate project store.
  - Human visual sign-off against this artifact remains part of the open walkthrough task; the
    prior feature-002 artifact reference is in
    [the Reviewed OpenDesign direction](#reviewed-opendesign-direction) above.

### Verification run (2026-09-29)

Executed from the repository root, sequentially:

- `npm --prefix dashboard run check` → 0 errors, 0 warnings.
- `node --test --test-concurrency=1 dashboard/tests/*.test.mjs` → 12 passed, 0 failed, 0 skipped
  (the Chrome/Edge CDP browser test executed, not skipped; includes the concurrent log-trail tests).
- `npm --prefix dashboard run build` → completed; `build/index.html` fallback written.

Browser and rendering evidence captured by `dashboard/tests/usage-workspace.test.mjs` at
1280×720, 1440×900 and 1920×1080 with 16 fixture nodes: workspace top equals header bottom and
bottom equals the viewport (≤2px), the graph spans the full width with no permanent sidebars,
graph height is 530px at 720p (past the old 420px cap), `documentElement.scrollWidth <=
innerWidth`, and all card bounds fit the graph on initial load and after Fit. At 390×844 the
graph is hidden, the list is a scrolling grid with all 16 entries and no horizontal overflow,
and the whole flow (list selection, History open, exact UTC reading, dismissal with focus
return, Chat) is keyboard-only. At 768×1024 there is no horizontal overflow. Pan survives two
polling cycles; zoom-out reduces scale; selection is unchanged through polling, pan, zoom and
Fit; keyboard selection makes zero history requests until History is opened. A node with an
overlong GPU name keeps every resource value inside its card. `/models` still scrolls inside
`main` with no horizontal overflow. The browser checks also emulate `prefers-reduced-motion`,
a 640px CSS viewport (200% zoom-equivalent), and 1280×480 short landscape; Fit contains all 16
cards at short landscape, and zoom-out reduces the fitted scale. `dashboard/tests/usage-live.test.mjs`
covers scoped host versus process values, duplicate identities, GPU union, measured zero versus
unavailable, inventory-only capacity, shared memory and partial sensor failures.

Convergence passes (2026-09-29) completed T026–T032 of `tasks.md`: the 1440×900/1920×1080
viewport assertions, the mobile keyboard flow, the long-label clipping regression, the compact
operational-status indicators, selection stability, reduced-motion/zoom/short-landscape checks
and healthy-overlay color semantics all pass. The T028 regression was confirmed to fail against
the previous CSS before its fix. T032 also removed the Fit minimum-scale floor after its
short-landscape regression showed that the floor could leave cards outside the graph; zoom-out
now continues decreasing from a very small fitted scale. Healthy/disabled `overlay_status` is
muted, while `netsec_warnings` and update failures retain warning/error styling.

Not run in this pass (do not treat as passing):

1. Real screen-reader inspection at desktop and mobile sizes.
2. A live node's actual GPU/accelerator sensors; all metric fixtures are synthetic.
3. SC-005 with five real operators, which requires people rather than automated checks.

The user later directed T024 and T025 to be marked done. They are closed in `tasks.md` as
**waived, not executed**; screen-reader/live-hardware inspection and the five-operator study are
not claimed as passing.

## Checks run on Windows (2026-09-28)

- `uv run pytest -q tests/test_usage.py tests/test_dashboard_api.py tests/test_membership.py`: 71 passed, 2 third-party deprecation warnings.
- `node --test dashboard/tests/*.test.mjs`: 8 passed with installed Chrome/Edge on Windows; the browser test also runs with Chrome/Chromium on Linux/macOS or `CHROME_BIN` set (skips with a reason when unavailable). Concurrent Vite tests printed a WebSocket port-in-use diagnostic while exiting successfully.
- `npm --prefix dashboard run check`: 0 errors; 7 existing a11y-label warnings in `AutoEditField.svelte` and `/run`.
- `npm --prefix dashboard run build`: passed.
- `uv run pytest -q`: 352 passed, 6 failed. Three join discovery tests in `tests/test_agent_auth.py` mock `browse_masters` without the pre-existing `port` keyword passed by `_join_cluster`; three macOS CMake-cache expectations in `tests/test_install_script.py` fail on Windows. The join call already existed before this feature. Neither failure belongs to this feature's targeted suite.

## Browser and accessibility verification

On 2026-09-28, Chrome headless on Windows served the sequential production build behind an isolated, in-memory fake control API (no real cluster). At **1280×720**, the graph was visible, node selection worked with keyboard Enter, and `documentElement.scrollWidth=1280` equaled the viewport. At **390×844**, the graph switched to the stacked node list, there was no page-wide horizontal overflow (`scrollWidth=375 <= innerWidth=390`), and keyboard Enter selected a worker, inspected an exact UTC history reading, opened Chat, and Escape closed it. Empty and offline states showed explicit text; `prefers-reduced-motion: reduce` disabled the drawer transition. Chrome reported no runtime exceptions. The sequential rebuild was necessary because running `npm check` and `npm build` concurrently gave SvelteKit's fallback page and client bundle different app IDs; keep those checks sequential.

Computed WCAG contrast ratios against `#12151a` panel: foreground `#e6edf3` **15.48:1**, muted text `#8b949e` **5.95:1**, feature amber `#f5c653` **11.40:1**, warning `#d29922` **7.25:1** (all >= AA text target 4.5:1). Browser interaction used synthetic node and history data; it does not establish live hardware timing or GPU sensor accuracy.

## Available-platform process check

On Windows x64 with 16 logical CPUs and a controlled, explicitly tracked CPU-bound Python child (not a llama.cpp process), the first `psutil.Process.cpu_percent()` reading was unavailable; the second two seconds later read **6.3% of whole-machine CPU** (~one saturated logical processor / 16 = 6.25%). `psutil.Process.cpu_times()` advanced from 0 to ~2.02 seconds, RSS was 10,424,320 bytes, host physical RAM was 33,883,607,040 bytes, and the observed PID/start identity matched the OS process. A first attempt that sampled the uv venv's Python launcher rather than its CPU-bound child yielded 0% CPU, so the check was corrected to start the real interpreter. That exercise also exposed and fixed a sampler bug: it had constructed a fresh `psutil.Process` on each tick, resetting the CPU delta and reporting false zero; `tests/test_usage.py` now covers distinct psutil objects and the retained per-lifetime CPU baseline.

Available-machine validation also simulates **150 two-second readings** across five minutes with no browser, then reopens the SQLite store to verify all 150 exact points, bounded buckets and explicit gaps. A separate 96 KiB allowance check verifies oldest-first pruning and physical database-plus-sidecar size; an aged-out storage-truncation flag clears. HTTP tests verify private ingestion, process identity, stopped-process history, chunked oversized payload rejection, and a corrupt saved row returning 503 while another node's records remain readable.

The convergence checks also cover batch rollback on a later write failure, worker→master ingest followed by authenticated worker-proxied read through the same private API, forged cursor rejection, missing tracked-child exit, bounded optional GPU probing including timeout/partial results, persisted departed-node metadata, and a real Chrome/Edge interaction check for Fit on 16 nodes, keyboard history, pan/zoom, narrow-screen layout, empty/offline states and selection stability through polling. These are deterministic local checks, **not** multi-node hardware or inference-performance measurements.

## Deferred cluster/hardware evidence (user waived for this implementation pass)

No runnable multi-node cluster or appropriately matched pre-feature/current inference workload was available for this pass. The existing `benchmark-before.json`/`benchmark-after.json` have no identified feature-002 baseline and **are not** evidence of monitoring overhead. The following outcomes remain **unverified**, not passed:

1. On Windows, Linux and macOS, start a real Kluster-managed `llama-server`/`rpc-server`, confirm tracked PID/create-time, process CPU and RSS against the OS, then check each available accelerator/backend's device utilization, memory, temperature and power. Log unsupported sensors as unavailable and their OS/backend. Port 50052 stays private.
2. On a private master plus workers, repeat [the quickstart walkthrough](../specs/002-node-usage-monitor/quickstart.md) for one, four and sixteen nodes: collect for 30 minutes (including five minutes without dashboard tabs), record per-node two-second coverage, 95th-percentile live-change latency (<5 seconds), 95th-percentile interaction latency (<2 seconds), disconnect/rejoin times, oldest-first 24-hour and 256 MiB physical-file coverage including sidecars. Keep the workload and results with OS/network/hardware details.
3. On a model that **fits both revisions on identical hardware**, run `uv run python scripts/compare_inference.py --output artifacts/usage-baseline.json` at the pre-feature revision and `uv run python scripts/compare_inference.py --output artifacts/usage-monitored.json` with monitoring enabled, after creating the ignored `artifacts/` directory. Each invocation sends five identical streaming requests. Record model, backend, input/settings, OS/hardware, each run's generated tokens/second, medians and `(baseline - monitored) / baseline * 100`; require regression <=5%. Never substitute existing unrelated benchmark files.
4. Repeat keyboard and reading-state inspection with a screen reader and five real operators at 1280×720 and 390×844 for SC-001/SC-006. The automated Chrome check establishes browser behavior and contrast only, not human usability.
