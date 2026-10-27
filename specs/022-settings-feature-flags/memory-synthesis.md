# Memory Synthesis

## Current Scope
Feature 022 adds a dedicated **Feature flags** section (`?section=flags`) in the dashboard Settings navigation (`dashboard/src/lib/modules/node-settings/`) and HTTP endpoints in `src/kllm/modules/settings/` (`GET /v2/node/flags`, `PUT /v2/node/flags`) backed by the unified SQLite `feature_flags` table via `kllm.shared.config`.

## Relevant Decisions
- D3: Patch surfaces live on the owning module (Reason Included: Settings HTTP handlers and interactors own `/v2/node/flags`, Status: active, Source: `decisions/2026-10-09-viper-owning-module-patch-surface.md`)

## Active Architecture Constraints
- A1: Structural coding standards (Reason Included: Enforces explicit schemas and module boundaries, Source: `architecture/2026-09-29-structural-standards.md`)
- A2: Feature module layout (Reason Included: Python code stays in `src/kllm/modules/settings/` with zero `kllm.agent` imports; Svelte 5 UI stays in `dashboard/src/lib/modules/node-settings/`, Source: `architecture/2026-10-09-viper-modules.md`)

## Accepted Deviations
- None

## Relevant Security Constraints
- Control plane authentication applies automatically to `/v2/node/*` routes via the agent middleware; unknown flag names or non-boolean payloads must be rejected at the trust boundary with HTTP 400.

## Related Historical Lessons
- Live status synchronization: Dashboard views (`PrimaRunPage.svelte`, network setup views) read `cluster.status?.feature_flags`, so updating `cluster.status.feature_flags` and triggering `live.refresh()` immediately reflects flag changes across the UI without page reloads.

## Conflict Warnings
- None. No conflicts between the requested feature and existing durable memory or constitution rules.

## Retrieval Notes
- Index entries considered: 5 (`A1`, `A2`, `D1`, `D2`, `D3`)
- Source sections read: `.specify/memory/constitution.md`, `.specify/memory/workflow.md`, `specs/022-settings-feature-flags/memory.md`
- Budget status: Within 900-word synthesis limit (~260 words)
