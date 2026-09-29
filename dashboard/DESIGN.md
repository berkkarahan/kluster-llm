# Kluster LLM Dashboard — Design System

> Generated from OpenDesign direction **tech-utility** (Datadog / GitHub density), adapted to a dark terminal-operator surface (exo-like TUI in the browser).
> OpenDesign daemon was unavailable (Node ABI mismatch); direction tokens pulled via `od tools directions --id tech-utility`.

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
