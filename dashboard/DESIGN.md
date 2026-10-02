# Kluster LLM Dashboard — Design System

> Generated from OpenDesign direction **tech-utility** (Datadog / GitHub density), adapted to a dark terminal-operator surface (exo-like TUI in the browser).
> OpenDesign daemon was unavailable (Node ABI mismatch); direction tokens pulled via `od tools directions --id tech-utility`.

## Dashboard polish reference (2026-10-02)

The later polish used Open Design's `impeccable-design-polish` workflow with Local Codex in
a separate project. The completed, self-contained six-page reference is retained in
[design/004-dashboard-polish/index.html](design/004-dashboard-polish/index.html), with its
[audit and translation guidance](design/004-dashboard-polish/AUDIT.md). Current Svelte source
remains authoritative. The earlier [responsive workspace reference](design/003-responsive-node-workspace.html)
and before screenshots informed the brief.

Adapted refinements preserve the existing palette, IBM Plex fonts, 13px root, square panels,
horizontal navigation and page structure: 15px page titles, consistent helper text, compact
spacing, a green active-navigation underline with `aria-current`, visible keyboard focus,
pressed/disabled controls, and red destructive hover states. Long names and paths wrap;
wide tables scroll inside their containers. Existing backgrounds and Home's measured graph,
pan/zoom, telemetry, mobile list, inspector and chat remain part of the production design.

### Error presentation

All routes use the same compact error treatment with an accessible **Dismiss error** button.
The session-local controller in `src/lib/errors.ts` owns visibility and acknowledgment;
components and the store retain authoritative validation, failures and availability state.
Acknowledged text stays hidden across repeated snapshots, route changes and soft refreshes.
Changed failures, a new operation, explicit failed retries and recurrence after recovery can
produce a new message. Duplicate inline/toast messages dismiss together.

Startup and hard refresh acknowledge each source's initial historical failure, including
sources first loaded on later navigation. Stop Run, Stop Cluster and Leave clear every error
before dispatch; failed actions can show a fresh error. Confirmed external termination,
membership loss and connection loss also clear once per transition. Reset generations reject
earlier request failures and component-local operation callbacks. No presentation state is
persisted. Status indicators, backend diagnostics, validation and historical logs remain
accurate; the Logs clear action still clears only the displayed buffer.

Verification captures all six routes at 390/768/1440px, Home at 1280×720, and a 640×360 CSS
viewport representing 200% layout scaling. Browser tests cover overflow, keyboard focus,
reduced-motion preference, new errors, dismissal, retained validation, hard refresh and
lifecycle failures; store/controller tests cover repeated snapshots, recovery, lazy sources,
external termination and stale responses. Before/after captures are generated under
`artifacts/dashboard-polish/` by `tests/polish-browser.test.mjs`; baseline mode uses
`KLLM_POLISH_BASELINE` to point at an archived original dashboard.

## Mood

Data-dense, monospace-friendly, dark + hairline grid. Built for cluster operators — information per square inch, not marketing vibes.

## Typography

| Role | Stack |
|------|--------|
| UI / labels | `"IBM Plex Sans", "Segoe UI", system-ui, sans-serif` |
| Body | same as UI |
| Mono / IDs / hashes / join keys | `"IBM Plex Mono", "JetBrains Mono", ui-monospace, Menlo, monospace` |

Use tabular numerics for RAM/VRAM, tok/s, ports.

## Palette (CSS variables)

```css
:root {
  --bg: #0b0d10;
  --surface: #12151a;
  --surface-2: #181c22;
  --fg: #e6edf3;
  --muted: #8b949e;
  --border: #2a3038;
  --accent: #3dd68c; /* oklch(58% 0.16 145) → dark-surface green */
  --warn: #d29922;
  --danger: #f85149;
  --info: #58a6ff;
}
```

## Posture

- One sans family + mono for code/IDs — utility over editorial.
- Dense panels with hairline borders; no card shadows, no rounded-full pills.
- Inline status chips: success / warn / danger with restrained tinted backgrounds.
- No hero imagery, oversized headlines, or marketing copy.
- Viewport-filling home: the app shell is a `100dvh` grid (intrinsic header + remaining-height
  `main`). Home has one compact toolbar (collapsed cluster-controls disclosure, live status,
  Details/History/Chat actions), a full-area topology graph/list, and a native modal inspector
  opened on demand. There are no permanent side rails; on-demand panels reserve no width.
- Node cards carry their own telemetry (Host CPU, Host RAM used/total/percent, one row per GPU)
  and grow to fit it; the graph measures real card bounds for row spacing and Fit.
- Keyboard-first affordances; high contrast focus rings using `--accent`.

## Layout density

- Header intrinsic height (~33px at the 13px root font; wraps below 700px); pane gutters 12px; row height ~28–32px.
- Prefer tables and lists over cards.
- Topology: compact node rows or simple SVG graph — not decorative. Below 700px the graph is
  replaced by an equivalent full-width scrolling node list; above it the graph fills the area.

## Motion

- Status/role transitions: 120ms opacity/color only.
- Chat stream: append tokens without layout thrash.
- No ambient glow, no parallax.
