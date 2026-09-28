# Implementation Plan: Correct VRAM Reporting and Default to Layer

> **Created:** 2026-09-28
> **Status:** Planned
> **Repo / scope:** kluster-llm — dashboard hardware settings and System Settings planning

**Branch**: `001-fix-vram-layer-default` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-fix-vram-layer-default/spec.md`

## Conventions captured at creation

- `dashboard/src/lib/components/HardwareSettings.svelte` is reused by `dashboard/src/routes/settings/+page.svelte` and `dashboard/src/routes/run/+page.svelte`. Local GPU inventory and selected IDs live in `dashboard/src/lib/store.svelte.ts`; hardware preferences use `GET/PUT /v1/config`.
- `dashboard/src/lib/format-transfer.ts:formatGb` divides by decimal `1e9` for transfer labels; `HardwareSettings.svelte` and the run page also use it for hardware. The inventory bytes from `python/kllm/accel.py:parse_devices` originate in MiB and are converted with `1024**2`.
- `dashboard/src/lib/store.svelte.ts:planPayload` feeds `refreshEstimate`, `plan`, and `run`. `python/kllm/run_planning.py:resolve_split_mode` chooses an explicit request mode before persisted config. `python/kllm/placement.py:plan_devices` computes effective per-device budgets and returns separate GPU, CPU, and combined allocation fields.
- Existing local CLI and API clients may explicitly select other split modes; `python/kllm/run_planning.py:validate_split_mode` rejects Row/Tensor with RPC. Python uses uv and pytest; the dashboard uses Svelte 5/TypeScript with `npm --prefix dashboard run check` and `npm --prefix dashboard run build`.

## Summary

Render measured hardware capacity and planner budgets/allocations in one-decimal GiB with accurate GPU/RAM/total-memory labels. Count selected local discrete GPUs once, report invalid measurements as unavailable, and rely on planner-provided budgets where free/shared memory constrains fit. Remove split-mode selection from both dashboard settings surfaces and explicitly request Layer for estimates, plans, and launches, overriding legacy saved dashboard preferences while preserving explicit local-only CLI/API options.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: TypeScript (Svelte 5/SvelteKit 2); Python >=3.10 (existing FastAPI boundary)

**Primary Dependencies**: Existing Svelte/Vite dashboard, FastAPI agent, Python placement planner; no new dependency

**Storage**: Existing `~/.kllm/config.toml` settings; no schema migration

**Testing**: `npm --prefix dashboard run check`, `npm --prefix dashboard run build`, uv/pytest for plan/config boundaries; small runnable behavioral regression check for new display logic plus browser scenarios

**Target Platform**: Browser dashboard against Windows, Linux, or macOS cluster nodes; hardware smoke on an available supported backend

**Project Type**: Dashboard web UI with existing Python HTTP planning service

**Performance Goals**: No performance change; formatting is linear in the small local GPU list

**Constraints**: Keep authoritative bytes unchanged; avoid double counting shared memory; preserve explicit local-only CLI/API modes and unauthenticated RPC's private-network boundary

**Scale/Scope**: Two settings pages using one component, shared dashboard store/formatter, existing `/v1/gpus`, `/v1/config`, `/v1/plan`, `/v1/run` contracts; relevant docs/tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Pre-research gate — PASS (2026-09-28)**

| Constitution requirement | Design gate |
| --- | --- |
| I. Scope and root cause | Reuse shared hardware component and common payload; correct displayed unit/label rather than mutating source bytes or every caller. |
| II. Behavior regression | Require an automated display check for 8/16/24 GiB, invalid input, selection and 95% budget, plus an HTTP boundary regression for legacy modes; check observable output rather than source text. |
| III. Integration and portability | Check dashboard build/types and plan/run HTTP payloads; specify hardware smoke and OS/backend prerequisites if live validation is available. Do not claim unrun hardware validation. |
| IV. Contracts and failures | Preserve existing byte fields and explicit CLI/API split modes; unknown measurements render unavailable; no new resources or credential exposure. |
| V. Reproducibility | Update relevant existing dashboard/CLI usage docs for GiB and automatic Layer; use existing uv/npm checks, record actual results during implementation. |

No justified exceptions or unresolved clarifications. See [research.md](research.md) for scoped decisions.

## Project Structure

### Documentation (this feature)

```text
specs/001-fix-vram-layer-default/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/system-settings.md
```

### Source Code (repository root)
```text
dashboard/src/lib/components/HardwareSettings.svelte
dashboard/src/lib/store.svelte.ts
dashboard/src/lib/types.ts
dashboard/src/lib/format-transfer.ts
dashboard/src/routes/settings/+page.svelte
dashboard/src/routes/run/+page.svelte
python/kllm/agent/__init__.py
python/kllm/run_planning.py
python/kllm/placement.py
tests/test_plan_run_params.py
tests/test_gpus.py
docs/cli.md
```

**Structure Decision**: Implement at the shared UI and request-payload boundaries; Python files above document existing contracts and are changed only if boundary checks reveal a backend defect. Keep behavior-focused checks alongside existing test conventions.

## Phase 0 — Research

Resolved display units, inventory ownership, budget provenance, shared-memory behavior, config persistence, and split-mode precedence in [research.md](research.md). No `NEEDS CLARIFICATION` remains.

## Phase 1 — Design and contracts

1. Define memory semantics and effective split-mode state in [data-model.md](data-model.md); specify dashboard and HTTP behavior in [contracts/system-settings.md](contracts/system-settings.md).
2. Format capacity, budget and estimate for the settings workflow in GiB. Use reported local selected GPU values for capacity, planner allocation data for effective budgets, and distinct total-memory and CPU/GPU labels. Show unavailable if an included capacity is unknown; never synthesize advertised size or a partial total.
3. Remove the selector/guidance in the shared hardware component, request `split_mode: "layer"` through the shared estimate/plan/run payload, and ensure dashboard config saves persist Layer rather than a stale legacy preference. Loading a legacy preference must not alter effective dashboard mode; do not rewrite configuration merely by opening a page.
4. Add the smallest observable regression check for formatting/selection and plan/run with a saved Row/Tensor preference; exercise save, reset, reload and both settings pages. Update existing user-facing docs and follow [quickstart.md](quickstart.md) for validation.

**Post-design constitution gate — PASS (2026-09-28):** Design keeps authoritative bytes and external field names, preserves CLI compatibility, uses planner budgets for free/shared constraints, checks legacy-mode behavior at the HTTP boundary, and explicitly reserves GPU/OS validation for hardware execution. No exception or unresolved clarification.
