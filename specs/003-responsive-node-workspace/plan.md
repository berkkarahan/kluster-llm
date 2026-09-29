# Implementation Plan: Responsive Node Workspace

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — responsive Home and node-local telemetry presentation

**Branch**: `main` (actual checkout) | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: `specs/003-responsive-node-workspace/spec.md`

**Setup target**: `003-responsive-node-workspace`. The setup script reports this feature
directory name as `BRANCH` when no feature override is set; it did not create a Git branch.

**Saved implementation sequence**: [plans/responsive-node-workspace.md](../../plans/responsive-node-workspace.md).
This file is the Spec Kit entry point for technical context, artifact discovery, and gates;
the repository's `plans/` document captures the source snapshot and execution sequence.

## Conventions captured at creation

- `dashboard/src/routes/+layout.svelte` owns the shell/header and store lifecycle. Home owns
  cluster controls, `displayNodes`, eager history loading, a permanent inspector, and Chat.
- `dashboard/src/app.css:.node-map-graph` caps graph height; `NodeMap.svelte:fit` is manual and
  height-only. `NodeMap.svelte` lives under `dashboard/src/lib/components/`.
- `dashboard/src/lib/types.ts:UsageNode` already supplies the required metrics. Existing
  `formatGib`, usage store callbacks, details/history components, and CDP tests are reusable.
- Feature-002 monitoring source is present in the working tree. Full concrete references
  and the inspected state are captured in the [saved plan](../../plans/responsive-node-workspace.md#conventions-captured-at-creation).

## Summary

Fill Home's available browser area with a responsive graph/list and show host CPU, host RAM,
and per-GPU utilization/memory directly on nodes. Use compact controls and native on-demand
Details/History, preserving existing Chat and cluster behavior. Correct sizing from shell
through graph, share the card/list summary, and fit real card bounds without overriding
manual navigation. Adapt the existing OpenDesign project before visual implementation.

## Technical Context

**Language/Version**: TypeScript 5.x; Svelte 5 / SvelteKit 2

**Primary Dependencies**: Existing SvelteKit, Tailwind CSS 4, Vite 6; browser CSS layout,
ResizeObserver, native details/dialog; no new runtime package

**Storage**: Existing agent-owned usage history from feature 002; new UI state is ephemeral

**Testing**: Existing Node test runner, Svelte rendering tests, and Chrome/Edge CDP browser
harness; Svelte diagnostics and production build, run sequentially

**Target Platform**: Modern desktop/mobile browsers serving the existing private dashboard;
supported agent OSes remain Windows, Linux, and macOS

**Project Type**: Existing Svelte web dashboard backed by a private Python agent

**Performance Goals**: Immediate window-filling layout and initial node framing; stable
navigation through existing two-second updates; four-of-five operators identify CPU/RAM
leaders within 15 seconds; no new sampling or inference-performance claim

**Constraints**: Existing metric scopes and ten-second staleness; no fabricated GPU readings;
keyboard/focus/contrast/reduced-motion support; no page-wide horizontal overflow; preserve
control availability and other routes' scrolling

**Scale/Scope**: Home presentation for 1-16 nodes, 390px and wider windows, per-device readings,
and contextual access to existing 15m/1h/24h history

## Constitution Check

**Pre-research gate — PASS (2026-09-29)**

| Principle | Design obligation and implementation evidence |
| --- | --- |
| I. Simple, consistent code | Fix the shared sizing chain; reuse existing models/store/components and native controls. Four primary UI files; no new package/service. |
| II. Behavior-focused tests | Extend existing tests with real metric fixtures, initial viewport/card bounds, availability states, deferred history, and keyboard behavior. |
| III. Integration and portability | Exercise real browser rendering against the fake private API and smoke other routes. Report browser/hardware evidence separately; no unrun GPU or multi-node claim. |
| IV. Explicit contracts and failures | Preserve scopes, missing values, stale/disconnected/recording failures, request identity, and disabled rules. Clean up size observers; modal dismissal does not stop workloads. |
| V. Reproducible changes | Record revised OpenDesign artifacts, update existing design/operator docs during implementation, and run documented sequential checks with actual results. |

No constitution exception is proposed. The specification checklist passes all 16 items.

## Project Structure

### Documentation (this feature)

```text
plans/responsive-node-workspace.md           # Saved implementation-sequence snapshot
specs/003-responsive-node-workspace/
  spec.md
  plan.md                                  # Spec Kit entry point
  research.md
  data-model.md
  quickstart.md
  checklists/requirements.md
  contracts/workspace-ui.md
```

### Source Code (repository root)

```text
dashboard/src/routes/+layout.svelte         # Intrinsic header and viewport shell
dashboard/src/routes/+page.svelte           # Controls, contextual views, history gating, Chat
dashboard/src/app.css                       # Fill layout, card/list sizing, responsive states
dashboard/src/lib/components/NodeMap.svelte  # Shared summaries, geometry, Fit and navigation
dashboard/src/lib/components/NodeUsageDetails.svelte  # Reused scoped diagnostics
dashboard/src/lib/components/UsageHistory.svelte      # Reused recorded readings
dashboard/src/lib/store.svelte.ts           # Reused selection and request ordering
dashboard/src/lib/types.ts                  # Existing telemetry contracts
dashboard/src/lib/format-memory.ts          # Existing GiB formatter
dashboard/tests/usage-live.test.mjs         # Scoped summary/availability checks
dashboard/tests/usage-workspace.test.mjs    # Real-browser layout and interaction checks
dashboard/tests/usage-history.test.mjs      # Existing history regression coverage
docs/node-usage-monitor.md                  # Operator guidance and artifact/check evidence
dashboard/DESIGN.md                         # Current layout conventions
```

**Structure Decision**: Keep the refactor in existing dashboard modules. A local summary
snippet serves graph/list; native disclosure/dialog supplies contextual controls. Reuse
diagnostics/history rather than building a replacement inspector or new data layer.

## Phase 0 — Research

[research.md](research.md) records layout, metric availability, geometry, contextual history,
OpenDesign, and testing decisions, including alternatives. Research resolves the technical
questions using the existing source and two focused read-only investigation passes.

## Phase 1 — Design and contracts

- [data-model.md](data-model.md) defines the existing-data projection and ephemeral view state.
- [contracts/workspace-ui.md](contracts/workspace-ui.md) defines visible interactions, reused
  endpoints, accessibility, and the concrete OpenDesign revision brief.
- [quickstart.md](quickstart.md) defines runnable checks and measurable acceptance evidence.
- [The saved implementation sequence](../../plans/responsive-node-workspace.md#implementation-sequence)
  orders design preparation, shell correction, node summaries/geometry, contextual views,
  and verification for subsequent task decomposition.

**Post-design constitution gate — PASS (2026-09-29):** Data and access contracts are preserved;
layout and interaction regression checks are specified; failure states and cleanup are
explicit. No technical clarification or exception remains. Feature-003 OpenDesign variants
are a required pre-visual-implementation deliverable; only the prior artifact's source has
been reviewed during planning. Implementation and validation outcomes are not claimed here.

Next phase: `/speckit.tasks`.
