# Kluster LLM dashboard design system

Regenerated **2026-10-06** by **OpenDesign / Local Codex** from the current implementation.
Source: this repository (`dashboard/DESIGN.md`).
This is a descriptive implementation reference, not a redesign or a proposal.
Links below are relative to this document's intended destination, `dashboard/DESIGN.md`.

## Authority and implementation

- The running product is **SvelteKit 2 / Svelte 5**, with TypeScript, Vite 6 and Tailwind CSS 4.
- [package.json](package.json) declares the dependencies; components use runes such as `$state`, `$derived` and `$effect`.
- [svelte.config.js](svelte.config.js) uses adapter-static, `build/` output and an `index.html` fallback.
- [src/routes/+layout.svelte](src/routes/+layout.svelte) owns the persistent shell and store lifecycle.
- [src/app.css](src/app.css) and [src/prototype.css](src/prototype.css) are both production stylesheets.
- [src/lib/store.svelte.ts](src/lib/store.svelte.ts) owns application state and commands.
- [src/lib/live-updates.ts](src/lib/live-updates.ts) owns shared live delivery, recovery and freshness.
- Route markup and scoped component CSS complete the design contract; stylesheet class existence does not prove current use.
- CodeGraph was consulted before implementation inspection; direct source reads filled CSS and elided-source gaps.
- The previous DESIGN.md and historical HTML prototypes were not used to infer current behavior.

## Design principles

1. **Dark operational surface.** Neutral near-black layers, restrained borders and green emphasis support repeated inspection.
2. **Evidence before decoration.** Render reported telemetry, timestamps and source coverage; preserve unavailable, stale and unmeasured states.
3. **Authority is visible.** Local settings, master commands, worker restrictions and next-deployment changes are distinct.
4. **Compact, readable hierarchy.** The shell uses a 16px base with larger page titles; technical details deliberately retain smaller text.
5. **Guided controls with expert disclosure.** Show common choices and effective values first, then advanced parameters and source details.
6. **IBM Plex Sans + Mono.** Sans handles navigation and prose; Mono distinguishes identifiers, measurements, commands and logs.
7. **State survives presentation changes.** Dismissing an error does not remove its validation failure; editing a draft does not alter a running model.

## CSS layering and effective cascade

The layout imports `app.css` first, then `prototype.css`.
`app.css` starts with `@import 'tailwindcss'`; Tailwind's normal utilities are layered.
`prototype.css` begins with the structural `@layer od-layout`, followed by unlayered product rules.
Normal unlayered author rules outrank normal layered utilities, even when a utility appears later in a class attribute.
Within the same layer, specificity and order still apply; Svelte adds specificity to scoped component rules.
Inline styles and inherited values must also be considered before describing an element's effective appearance.

| Earlier rule / alias | Current effective interpretation |
| --- | --- |
| `app.css` color tokens | Its shared palette already matches the current dark palette; do not invent a separate obsolete palette. |
| `--fg`, `--border`, `--surface-2`, `--warn` | Final aliases are `--text`, `--line`, `--raised`, `--warning`, respectively. |
| `--font-ui`, `--font-mono` | Final aliases point to `--font` and `--mono` in prototype.css. |
| Earlier `.btn` height 28px | The later base minimum is 44px; the retained height cannot shrink it below that minimum. |
| Earlier `.field` control height 28px, padding 0 8px | Final `input.field, select.field, textarea.field` use height auto, minimum 44px and padding 8px 12px. |
| Earlier outline-only `.btn-accent` | Final base treatment is solid accent with accent-ink text. |
| Earlier generic green links | Later global `a` inherits text color; `.text-button` explicitly restores accent. Earlier hover underline remains applicable. |
| Earlier compact `.nav-link` | Later sidebar links use 48px minimum height and 12px padding; the earlier 2px bottom-border rule still contributes. |
| `.page-title` 15px / `.page-description` 12px | These legacy classes are not the current route-heading contract: routes use `.page-heading` and `h1#page-title`. |
| Utility `text-[11px]` on an `h2` | Does not override unlayered global `h2` sizing by itself. Do not document utility text sizes as computed values. |
| `.app-shell`, `.usage-workspace`, `.dashboard-header` | Some old scaffolding remains; actual shell markup and scoped layout rules determine what is active. |

Tailwind's installed spacing base is 0.25rem; its utility values coexist with the product tokens below.
There is no universal component size or spacing scale beyond the named tokens.
For example, Deploy scoped rules use 18px/20px gaps and 13px field text; runtime settings use 48px controls.
The older yellow `--monitor-accent` belongs to `.usage-workspace`; the current Cluster route uses `.cluster-page`.

## Effective tokens

These global color values come from `prototype.css :root`, with compatibility aliases mapped at its end.

| Token | Value | Role |
| --- | --- | --- |
| `--bg` | `#0b0d10` | Page and recessed controls |
| `--surface` | `#12151a` | Panels and default buttons |
| `--raised` | `#181c22` | Raised rows, disclosures and hover fills |
| `--text` | `#e6edf3` | Primary text |
| `--muted` | `#a3adb8` | Supporting text |
| `--line` | `#2a3038` | Dividers and panel borders |
| `--control` | `#647181` | Input borders and stronger control edges |
| `--accent` | `#3dd68c` | Primary action, selected state, healthy state |
| `--accent-ink` | `#082116` | Text on solid accent |
| `--warning` | `#f2c76a` | Warning and incomplete state |
| `--danger` | `#ff8582` | Failure and destructive action |
| `--info` | `#82baff` | Informational and measurement accent |
| `--green-wash` | `#132b22` | Selected navigation and positive surface |
| `--blue-wash` | `#172330` | Defined auxiliary wash; not a universal panel fill |
| `--red-wash` | `#321d20` | Error surface |

Aliases resolve to the same hex values: `--fg=#e6edf3`, `--border=#2a3038`,
`--surface-2=#181c22`, and `--warn=#f2c76a`.
Status chips additionally use `color-mix()`: semantic color at 12% over transparent;
OK/info borders mix semantic color at 35% with the border token.

Component-specific literals are exceptions, not additional global theme tokens:
- Sidebar and shared data-table headers: `#101317`; log surface: `#0d1014`.
- Danger button/border treatment: `#79494d`; `.btn.primary` hover: `#66e6a5`.
- Shared warning notice: `#252219`; runtime pending-target strip: `#29251b`.
- Model emblem border: `#295c44`; runtime resolved-source border: `#2b5a44`.
- Shared code-block text: `#c4cdd7`; allocation reserve: `#d5dbe1`.
- ManagedNetwork's `.ready/.granted` uses `var(--success, #69b884)`; no global `--success` is defined here.
- Legacy `.usage-workspace` defines `--monitor-accent:#f5c653`; it is not the global green replacement.

### Typography

Local font files live in [static/fonts](static/fonts), including the Plex license files.
`Plex` loads Sans regular 400, medium 500 and semibold 600 TTFs.
`PlexMono` loads Mono regular 400; all four faces use `font-display:swap`.
Effective stacks: `--font:Plex,system-ui,sans-serif`; `--mono:PlexMono,ui-monospace,monospace`.
Mono uses tabular numerals. Other requested weights can be synthesized; no Mono medium/semibold face is declared.

| Element / token | Current sizing |
| --- | --- |
| `html`, body | 16px; body line-height 1.5 |
| `--text-xs/sm/md/lg/xl/2xl` | 12 / 14 / 16 / 20 / 24 / 32px |
| Global `h1` | `clamp(24px,2.6vw,32px)`, weight 600, line-height 1.25, tracking -0.8px |
| Global `h2` / `h3` | 20 / 16px, weight 600, line-height 1.25; h2 tracking -0.3px |
| Page subtitle | 14px muted, 8px top margin; paragraphs capped at 72ch |
| Sidebar link / breadcrumb | 15px weight 500 / 14px |
| Brand word / eyebrow | 24px weight 600 / 11px Mono uppercase, tracking 1.5px |
| `.stat-number` | `clamp(24px,2.5vw,32px)`, weight 500; unit 16px; responsive overrides below |
| Shared panel header h2 / Memory plan h2 | 16px |
| Runtime section / panel / dialog headings | 28 / 18 / 22px, component-scoped |
| Runtime running value / version name | 24px weight 500 / 16px |
| Deploy scoped h2 / h3 / field | 20px / 15px / 13px |
| ParameterControl label / helper / error | 13px weight 500 / 11px / 12px |
| ErrorMessage / chip | 11px Mono; chip uppercase with 0.04em tracking |

Small technical labels remain in the implementation; do not present the entire UI as 11–13px.
Conversely, a 16px base does not imply every control, status or table cell is 16px.

### Spacing, edges and motion

| Token / pattern | Values |
| --- | --- |
| `--s1/s2/s3/s4/s6/s8/s12/s16` | 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px |
| `--r1/r2` | 4 / 8px |
| `--enter/exit/transition` | 180 / 120 / 240ms |
| `--rail-width` | 224px; 192px at max-width 1023px |
| Base content / panel padding | 32px / 24px |
| Stack / stack-sm / action gap | 24 / 16 / 8px |
| OD row / grid / field-stat gap defaults | 8 / 12 / 2px |
| Panel / control borders | 1px; panel radius 8px, ordinary control radius 4px |

`od-stack`, `od-row`, `od-row-top`, `od-cluster`, `od-grid`, `od-field`, `od-stat` and `od-cell`
provide structural layout and child min-width protection; `--od-gap` and `--od-cols` parameterize them.
`od-fill` allows shrinking, `od-fixed` does not, and `od-nowrap` keeps numeric units together.
Shared `.stack/.stack-sm` explicitly use `minmax(0,1fr)` to contain wide descendants.
Do not normalize component exceptions: nodes have 20px padding, Deploy has 18px gaps,
ManagedNetwork has 6px radii, and allocation bars use a 3px gap.
Panel elevation is primarily background/border separation, not a global shadow system.
Buttons retain 120ms border/color/background transitions from app.css.
Page/toast entrances use 180ms opacity/translation; mobile sidebar uses a 240ms transform.
Reduced motion globally disables animations/transitions and restores automatic scrolling.

## Application shell and navigation

The current shell is a two-column application with a **permanent desktop sidebar**.
The outer shell and main shell are 100dvh; the main shell uses header + `minmax(0,1fr)` rows.
The route content scrolls in `main.app-main`; the sidebar has its own overflow handling.
Desktop sidebar: 224px, sticky, 24px 16px padding, right divider and 32px major gaps.
The topbar is 72px high with breadcrumb and wrapping health indicators.
Content is centered with a maximum width of 1800px.

| Navigation label | Route | Page heading |
| --- | --- | --- |
| Cluster | `/` | Local cluster |
| Models | `/models` | Model library |
| Deploy | `/run` | Deploy a model |
| Activity | `/logs` | Cluster activity |
| Connect | `/integrations` | Connect a harness |
| Settings | `/settings` | Node settings |

Links show icons, `aria-current="page"`, green selected fill and optional member/model counts.
The node/workspace card displays node initials, identity and role; it links to Settings.
Despite its picker appearance, it does not implement remote node switching.
Breadcrumb is “Local cluster › [section]”; the parent crumb hides below 768px.
Header chips separate online/offline, delivery health, per-area coverage and node role.
Download, model-load, worker-update and inference activity add their own progress/status indicators.
Sidebar connection text includes Live, Loading, Reconnecting, Periodic refresh, Updates paused,
Updates unavailable and Offline; these are not interchangeable with inference readiness.

Below 768px, the sidebar becomes a 260px off-canvas drawer with a dark scrim.
`button.mobile-menu` is explicitly hidden above this breakpoint to override the generic button display.
Closed mobile navigation is inert; route changes, Close and scrim clicks dismiss it.
Tab wraps among its enabled links/buttons. Escape closes it unless a native dialog is open and restores menu-toggle focus.
The skip link targets `#main-content`; desktop scoped CSS prevents residual drawer transforms.

## Responsive rules and containment

These are actual source thresholds, not a newly imposed breakpoint system.

| Threshold | Current behavior |
| --- | --- |
| min-width 1440px | Content padding 32px 40px; topbar horizontal padding 40px; shared split ratio 1.65:1. |
| max-width 1199px | Deploy summary column becomes 288px; Connect grid becomes two columns; node cards tighten. |
| max-width 1023px | Rail becomes 192px; content padding 24px; shared split/Deploy layout stack; plan summary loses sticky positioning. |
| max-width 1023px | Settings section navigation becomes a wrapping row; shared node cards use one column; resource grid stacks. |
| max-width 767px | Drawer shell, 64px topbar, 24px 16px content padding; two-column stats; shared forms and Connect grid stack. |
| max-width 767px | ClusterTopology hides the diagram and displays selectable node cards; shared table secondary/mobile classes can hide optional columns. |
| max-width 700px | Deploy scoped field grids finally stack, with 18px section padding; Activity rows reflow and their column header hides. |
| max-width 1200px, runtime only | Saved-version rows become two columns with actions below; target controls stack. |
| max-width 768px, runtime only | Target/running panels stack; target controls return to a row; primary panel padding becomes 20px. |
| max-width 520px, runtime only | Runtime controls, version rows and ref fields stack; dialog padding is 20px. |

At max-width 1023px, stat numbers become 24px (word values 20px);
at max-width 767px they become 28px (word values 24px).
Deploy's scoped two-column field grid overrides the shared mobile one-column rule until its own 700px breakpoint.
Topology stylesheet includes 1199px/1440px inspector rules, but the current component does not render that old inspector workspace.
Legacy 700px NodeMap/workspace CSS is not the current ClusterTopology mobile contract.
Tailwind responsive utilities also occur: NetworkSecurity's `sm:grid-cols-2` starts at 40rem (640px with a 16px initial size), as defined by the installed Tailwind theme.

Wide tables scroll **inside their containers**:
Models uses `.model-table-scroll` with a 560px minimum table width;
runtime readiness uses a 930px minimum and a focusable, named scroll region with horizontal overscroll containment.
Deploy review tables use local overflow and wrapping cells (maximum cell width 240px).
Long model names, repositories, paths, errors and commands wrap; they must not enlarge the page grid.

## Reusable interaction patterns

- **Buttons:** base minimum 44px, padding 8px 16px, 14px/500 text, 4px radius, no label wrapping.
  `.compact` is 36px with 13px text; icon buttons are 44px wide; toast dismiss controls are 32px.
  Primary aliases are `.btn.primary` and `.btn-accent`; `.btn-primary` is used by some markup but has no matching shared primary rule.
  Danger uses danger text and a darker red border; disabled buttons use opacity 0.45 and a not-allowed cursor.
  Hover behavior combines earlier app.css text color with later hover-capable prototype rules; primary variants are not identical on hover.
- **Fields:** `.field` on a label/div is a layout group. Only input/select/textarea.field receives control chrome.
  Final controls are block, full width, min-width 0, minimum 44px, 8px 12px padding and control-colored border.
  `.input/.select` explicitly use 14px; `.field` controls inherit their context's font.
  Textareas resize vertically; the shared 112px minimum has component exceptions such as Deploy's 90px.
  Checkboxes/radios are 20px with accent color; range tracks are 6px with 14px thumbs.
- **Tabs/disclosures:** Models uses tablist/tab roles and aria-selected; shared tabs are 48px minimum with a 2px active underline.
  Settings uses section-navigation buttons with aria-current, not route-level tabs.
  Deploy uses native details/summary with plus/minus markers; step navigation uses aria-current="step".
- **Chips/status:** chips are 20px high, 11px Mono, 0 6px padding and a 1px border.
  OK, warn, info, danger and muted variants supplement text; `.status` pills and runtime badges are distinct components.
  Loading indicators, stale text and explicit failure messages carry meaning independently of color.
- **Panels/tables:** shared panels use surface, line border and 8px radius; headers and padded bodies generally use 24px.
  Shared data tables use 14px body, 12px headers, 20px cells and 12px 20px header padding.
  Mobile shared table text becomes 13px and cell padding 16px; runtime and Deploy tables have their own rules.
- **Dialogs:** generic native dialog width is min(560px, viewport minus 32px), radius 8px, maximum height 100dvh minus 48px.
  Cluster inspector uses a 720px cap; runtime dialog a 680px cap; Jinja editor a 720px cap with 14px padding.
  Models search is a custom overlay/ARIA dialog with manual focus containment, Escape/backdrop close and focus restoration.
  Its base modal width is min(960px,100%), additionally constrained by its max-width utility; do not infer width from one class.
  Native dialogs use showModal; runtime dialogs explicitly restore trigger focus and abort source requests when closed.
- **Icons:** [Icon.svelte](src/lib/components/Icon.svelte) owns inline SVG paths, a 24×24 viewBox, currentColor,
  1.75px strokes, round joins/caps and no fill; rendered sizes are 20px or 16px.
  SVGs are aria-hidden; controls need text or accessible names. Existing × and arrow glyph buttons are local exceptions.

## Page patterns

### Cluster, topology and inspection

[src/routes/+page.svelte](src/routes/+page.svelte) combines membership controls, summary metrics, active run,
host CPU history, topology, private-network setup and recent activity.
Start/Join require idle state; Stop and Update workers are master actions; Leave is worker-specific.
Summary metrics distinguish host measured memory, inference state, member count and saved-plan/recording state.
The active-run panel shows reported tokens/requests and links “Open chat” to Connect.
Host CPU history starts unpopulated; throughput is explicitly unmeasured rather than synthesized.

[ClusterTopology.svelte](src/lib/components/ClusterTopology.svelte) derives master/worker paths from current nodes,
relationships and managed-network reports. Link states are Connected, Disconnected or Unmeasured.
Link speed is shown only when a participant reports link_speed_mbps; it is not an invented throughput/RTT/loss metric.
Selecting a node or link selects the worker/node; Details and History open separate native inspector dialogs.
Disconnected views retain old readings with stale/unavailable labels and clear current relationships.
A missing master heartbeat makes a worker view offline even if local requests still succeed.

[NodeUsageDetails.svelte](src/lib/components/NodeUsageDetails.svelte) separates host, process and device measurements,
observation/source metadata and memory backing pools; shared host RAM is counted once.
Assignment to inference is explicitly not a measurement of local computation.
[UsageHistory.svelte](src/lib/components/UsageHistory.svelte) loads on demand, selects process lifetimes and
15m/1h/24h windows, distinguishes gaps from measured zero and paginates exact UTC readings.
Planning JSON in the inspector is labeled as an estimate, not usage.
ManagedNetwork appears when available; its guided administrator/adapter/recovery flow retains explicit master authority.

### Models

[src/routes/models/+page.svelte](src/routes/models/+page.svelte) opens on **Discover**; **Local library** is the other tab.
Discover accepts Hugging Face queries/repositories/URLs and opens search results in a dialog.
Results identify source/revision/quantization and label fit as a **weight-size heuristic**, including unknown fit.
The page includes Hugging Face token controls and shared download/model-load feedback.
Local library filters by text and inferred family; rows show name, size, Ready/Loading/local status and Deploy/Delete.
Deploy selects that model and navigates to /run. Delete requests browser confirmation.
[DownloadPanel.svelte](src/lib/components/DownloadPanel.svelte) distinguishes running, complete, cancelled, error and unknown state;
Cancel download is an actual store action, and terminal state is tied to an operation identity.

### Deploy and effective configuration

[src/routes/run/+page.svelte](src/routes/run/+page.svelte) implements **Select → Configure → Review & deploy**.
Select chooses a local model, inference network and Automatic/Priority placement.
“Must use this node” means at least one model layer; unchecked nodes remain eligible.
Each node row shows that node's GPU names beside the node id (membership device names, or the plan's `node_gpu_names` when membership has none).
Priority has explicit up/down actions and removal for unavailable saved entries.
Configure begins with context, concurrent slots, response limit and thinking controls.
Disclosures group Capacity & performance, Memory & loading, Generation defaults,
Model features & templates, and Expert engine options.
[ParameterControl.svelte](src/lib/components/ParameterControl.svelte) uses descriptors, bounds, availability reasons,
inherit/automatic modes and effective values/sources/adjustments. Its scope label is “Next deployment”.
[ExpertArguments.svelte](src/lib/components/ExpertArguments.svelte) exposes installed-engine options and structured argv;
JSON arrays preserve literal values. Extra arguments can make the memory estimate incomplete.

[DeploymentPlan.svelte](src/lib/components/DeploymentPlan.svelte) keeps memory totals, components, usable budgets,
resolved settings, incomplete-estimate warnings and Refresh estimate beside the form.
Review lists requested/effective/source values, highlights differences from saved configuration,
shows device allocation and provides CLI/JSON export.
Saving model defaults, reverting, restoring defaults and saving after successful deployment are separate actions.
The editable draft, saved model configuration, plan effective_config and activeRunConfig are different state objects.
The active configuration comes from status.active_config/run results; draft changes do not rewrite it.
Plan requests debounce by 300ms, invalidate stale estimates and use remember:false.
Progression validates fields and attempts to focus the offending control; deploy requires a ready fit and runtime readiness.
An active model changes the final action to “Replace running model”; do not describe this as hot editing.
[JinjaEditor.svelte](src/lib/components/JinjaEditor.svelte) uses CodeMirror/Jinja/One Dark, server validation and a 65,536-byte limit;
syntax checks are explicitly not model-compatibility verification.

### Activity and Connect

[src/routes/logs/+page.svelte](src/routes/logs/+page.svelte) is the Activity log view; usage history remains under Cluster.
Rows are newest-first with UTC, node, source, tag and message; scrolling back preserves the visible anchor.
Filters are Node and Tag (UI/Inference/Unclassified); refresh choices are Live, 5s, 30s and 1m.
Master-only Stop/Resume logging controls cluster recording, separately from view delivery.
Clear affects only the display, not stored logs. Coverage, gaps, trimming and unavailable sources stay visible.
The scroll region is keyboard focusable with role=log and aria-live=off; status announcements are separate.
[log-trail.svelte.ts](src/lib/log-trail.svelte.ts) bounds the log buffer to 2,000 rows; tests cover trimming and continuity.

[src/routes/integrations/+page.svelte](src/routes/integrations/+page.svelte) offers Claude, OpenCode, Codex, Hermes, OpenClaw and Pi.
Shell choices are Bash / Zsh and PowerShell; copy actions include launch, --no-launch and token setup.
Workers use the master's endpoint and disable copying the worker token as an inference credential.
[ChatPanel.svelte](src/lib/components/ChatPanel.svelte) sends through the OpenAI-compatible proxy, supports streaming Stop,
and disables send until inference is up. Its default values come from the running model, not the deployment draft.
Temperature, thinking, response limit and text/JSON/schema output are session overrides.

## Settings and llama.cpp runtime management

[src/routes/settings/+page.svelte](src/routes/settings/+page.svelte) defaults to Hardware.
Section order is **Hardware, Network, Tokens, Software, llama.cpp, Paths**.
The llama section is directly addressable with `/settings?section=llama`.
Hardware uses node-local CPU/GPU/backend/device selection and RAM/VRAM budget controls; backend locks while live.
Network exposes RPC bind, LAN/WireGuard, join/master inputs, overlay status and warnings; live controls are locked.
Tokens masks/reveals/copies the API token and manages the Hugging Face token.
Software reports git HEAD, llama-release and ports, plus master-only worker updates.
Paths reports home/models/logs from /v2/node/home; its home fallback is ~/.kllm.
These are implemented in [src/lib/components](src/lib/components); llama-release is the bootstrap pin, not the only managed version.

### Source identity and the local library

[LlamaRuntimes.svelte](src/lib/components/LlamaRuntimes.svelte) contains Cluster target, Saved versions and Node readiness.
Saved rows show repository, ref type/ref, short commit, local backend, build status, disk usage and actions.
Disk is local MiB, not a cluster total; version details expose the full repository, commit and runtime_id.
Upstream is `https://github.com/ggml-org/llama.cpp.git`; public HTTPS forks are supported.
The add dialog has Display name, Public HTTPS Git repository, Browse releases and refs,
Release, Reference type (Tag/Branch/Commit), Resolve revision and Save version.
Releases select a tag; tags/branches resolve to a 40-character immutable commit before saving.
Saving persists the reviewed resolution; it does not silently re-resolve a moving branch.
Editing source fields invalidates the resolution; obsolete async responses cannot overwrite the current source selection.
Backend [runtime models](../src/kllm/runtimes/models.py) normalize repository URLs and validate identity.
runtime_id is SHA-256 of repository + newline + commit; a matching short SHA alone is not source equality.
Backends may differ per node while repository+commit identity remains identical.

### Target, running version and authority

The target picker is a draft until the master chooses **Set target & sync**.
“Enforced by master”, “Managed by master” and “Local selection” identify the current authority mode.
Running version is shown separately; a differing target produces a Pending target strip.
Preparation occurs beside active binaries. A new run uses the prepared target only after the old run fully stops.
[Runtime control](../src/kllm/agent/services/runtime_control.py) persists the target and reconciles local/connected worker preparation.
Its two-second reconciliation includes newly joined/returning supported workers; this is not purely event-driven backend scheduling.
Workers cannot set the target or add/remove versions in this UI; local target build/retry/log/cancel actions remain available.
Idle/local mode offers Use locally only for a built version with no running version.
Target requests carry an expected revision and request ID; failures remain visible rather than being presented as successful selection.

### Build, readiness and run gating

Build / Verify build acts on this node's configured backend; Logs opens output with an explicit Refresh log button.
Readiness columns are Node, Backend, Running SHA, Target SHA, Preparation and Actions.
Rows identify MASTER/WORKER, THIS NODE and REQUIRED where applicable.
Every node lists the full cluster from the membership store, so worker and master readiness match the cluster view.
A worker reports its own preparation locally; peer rows come from the store's liveness and capability, never a signed observation.
Preparation phases include queued, downloading, building, verifying, prepared, failed, cancelled and interrupted.
Additional reported phases include not_installed, waiting_for_target, offline and upgrade_required.
The UI displays underscores as spaces; “upgrade required” is the current capability-mismatch state, not a fabricated “incompatible” enum.
Running SHA is separate from preparation phase; a node can prepare another target while its old runtime runs.
Retry appears for failed/interrupted/cancelled jobs; active jobs expose Cancel and indeterminate progress.
Remote actions require master authority; offline log actions are disabled.
Remove opens a confirmation and protects running, selected, targeted and building versions, with backend enforcement too.
[RuntimeRegistry](../src/kllm/runtimes/registry.py) verifies build provenance/artifacts and retains isolated runtime builds.

[runtime-readiness.ts](src/lib/runtime-readiness.ts) uses the plan's runtime_required_nodes, falling back to the master.
Only the **master and selected run workers** gate deployment; unrelated offline children do not.
A changed plan runtime_id requires refreshing the plan; absent/offline/upgrade-required or wrong-identity required nodes block.
With no runtime-library payload, the UI preserves compatibility rather than claiming a successful readiness check.
[RPC run coordination](../src/kllm/agent/services/rpc_runs.py) checks selected workers and pins the runtime snapshot for execution/recovery.
Do not turn the “nodes prepared” summary into an all-children launch prerequisite.

## Data, feedback and accessibility

The application uses real store/API state. Keep delivery health, source availability, coverage,
measurement freshness, process activity and readiness as separate dimensions.
Shared live delivery uses an HTTP snapshot plus WebSocket `/v2/events` with validated batches and acknowledgements.
It rejects obsolete generations, manages per-area revisions/epochs, and ages measurements locally.
Usage interest is enabled on Cluster; changing usage/log interests currently reconnects the transport.
Hidden pages pause shared delivery; foreground resumes it. Socket failure uses labeled two-second snapshot fallback,
with a bounded jittered retry probe around 30 seconds. Denied topics are not bypassed through legacy routes.
Do not describe all traffic as WebSocket-only: Cluster recent logs poll every five seconds,
runtime build logs refresh on demand, and managed-network operations have explicit polling.

[ErrorMessage.svelte](src/lib/components/ErrorMessage.svelte) supplies role=alert, wrapping Mono text,
optional Details and an accessible Dismiss error button (24px square).
[errors.ts](src/lib/errors.ts) is presentation-only: source/identity matching suppresses repeated dismissed errors,
startup snapshots suppress historical failures, and generations reject stale asynchronous failures after clearing.
Dismissal can also hide a matching toast; authoritative validation and failure state remain intact.
The shell toast region is aria-live=polite. A dismissed diagnostic must never enable an unsafe action.

Keyboard behavior includes a skip link, visible focus outlines, native controls/dialogs and explicit mobile-menu containment.
Global focus is 2px accent; earlier specific rules retain 2px offsets on buttons/links/fields, while generic controls may use 4px.
Input.field focus also has a 1px accent rule; UsageHistory uses its own 2px foreground outline with zero offset.
There are intentional small controls, mixed modal implementations and locally styled helpers.
Models tabs have selection semantics but no custom arrow-key tab controller in their source.
The chat message input currently uses placeholder text without a persistent label; do not imply complete labeling coverage.
Reduced motion globally disables transitions/animations with important rules; focus, text and state remain available.
These mechanisms are evidence of implemented affordances, **not a claim of full WCAG conformance**.

## Verification evidence and maintenance

This regeneration inspected source and test code only. **No fresh browser test, screenshot capture,
computed-style measurement, contrast audit, hardware run or full test suite was performed.**
Existing test definitions describe coverage, not a new passing result.
Documentation checks verified all 43 local links and 15 palette values against the checkout;
`git diff --check` passed after applying the regenerated file.

| Evidence | What it exercises |
| --- | --- |
| [runtime-browser.test.mjs](tests/runtime-browser.test.mjs) | Fixture master/worker screens at 390/768/1440px; pending target, immutable fork save, expected revision, worker restrictions and page overflow. |
| [polish-browser.test.mjs](tests/polish-browser.test.mjs) | Six routes at 390/768/1440px, field containment, local-model overflow, error lifecycle, focus and a 640×360 layout exercise. |
| Same polish test | A targeted ≥4.5 contrast assertion for the selected refresh label; this is not a whole-product contrast audit. |
| [runtime-readiness.test.mjs](tests/runtime-readiness.test.mjs) | Unused offline children, mismatched source, stale plan and missing target/required-node behavior. |
| [configuration.test.mjs](tests/configuration.test.mjs) | Saved/draft isolation, argv round trips, explicit save, model-switch races and chat overrides. |
| [live-updates.test.mjs](tests/live-updates.test.mjs) | Frame validation, stale generations, hidden-page ownership, denied areas, fallback and measurement aging. |
| [logs.test.mjs](tests/logs.test.mjs) | Deduplication, continuity/gaps, display-only Clear, filters, cadence, recovery and bounded buffers. |
| [usage-workspace.test.mjs](tests/usage-workspace.test.mjs), [error-lifecycle.test.mjs](tests/error-lifecycle.test.mjs) | Telemetry/workspace behavior, deferred history and error-generation handling. |

Browser tests use fixtures and skip when a supported Chrome executable is unavailable.
Their screenshot-writing code is not evidence that screenshots were regenerated for this document.
Historical design inputs live under [design](design), including 003-responsive-node-workspace,
004-dashboard-polish and 005-llamacpp-settings; treat them as historical references only.

For future contributors:
- Start with current route markup, global import order and scoped styles; use CodeGraph before locating indexed code.
- Reuse active tokens, Icon and shared components; identify component exceptions rather than silently redefining the theme.
- Keep .field groups separate from control chrome and preserve min-width:0 / minmax(0,1fr) containment.
- Extend actual breakpoint behavior, including component-specific 700/768/1200px rules; do not assume one mobile threshold.
- Preserve role restrictions, immutable runtime identity, selected-participant gating and running-versus-pending distinctions.
- Keep unknown measurements unknown; do not substitute zero, membership or a saved plan for liveness.
- Preserve draft/saved/active configuration boundaries and error presentation generations.
- Update this reference alongside implementation changes; date the provenance and state precisely what validation was performed.
