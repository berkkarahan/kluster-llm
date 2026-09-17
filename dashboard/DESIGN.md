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
- Three-pane home: cluster actions | topology/status | chat.
- Keyboard-first affordances; high contrast focus rings using `--accent`.

## Layout density

- Header ~40px; pane gutters 12px; row height ~28–32px.
- Prefer tables and lists over cards.
- Topology: compact node rows or simple SVG graph — not decorative.

## Motion

- Status/role transitions: 120ms opacity/color only.
- Chat stream: append tokens without layout thrash.
- No ambient glow, no parallax.
