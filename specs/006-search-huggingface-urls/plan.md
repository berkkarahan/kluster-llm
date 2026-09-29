# Implementation Plan: Search Hugging Face URLs

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — MODELS search and shared Hugging Face URL resolution

**Branch**: `main` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/006-search-huggingface-urls/spec.md`

**Workflow status**: Phases 0 and 1 complete. The user supplied the fixed bar/modal brief
and selected Local Codex. The reference was generated, refined for source fidelity and
reviewed in Chrome at desktop/mobile sizes. See [design.md](design.md).

## Summary

Make the existing MODELS search resolve explicit Hugging Face repository, tree-folder,
revision, and file URLs into verified standalone GGUF options. Fix the shared Python
parser/metadata/download path rather than adding a second browser resolver. Preserve
revision and complete path in the selected source. Distinguish empty repositories from
failed lookups and never synthesize Hub options for missing files. Extend existing result
metadata additively and reuse the current download workflow and deterministic test tools.
The user's clarified brief fixes the current search bar and surrounding navigation in
place: Search continues opening the existing downloadable-results modal. Limit any visual
adaptation to that modal's result information and states; do not redesign the bar.

## Conventions captured at creation

- `python/kllm/models.py:parse_model_search_query`, `_parse_hf_url`, `_inspect_repo_ggufs`,
  and `search_hub_ggufs` own search classification and Hub lookup. `download_model` shares
  URL parsing but currently discards revision; fix both callers together.
- `python/kllm/agent/__init__.py:search_models` exposes POST `/v1/models/search`;
  `download_model` and `_download_job_runner` enqueue and execute selected downloads.
- `python/kllm/cli.py:model_download` reaches the same shared downloader; preserve CLI
  exact references, quant selection, progress, cancellation, and sidecars.
- `dashboard/src/routes/models/+page.svelte:runSearch`, `downloadRow` and
  `dashboard/src/lib/store.svelte.ts:searchModels`, `downloadModel` already pass query
  and selected source through. `types.ts:ModelSearchHit` is the wire-compatible row shape.
- Existing unknown size is numeric zero; `vram_fit` already returns `unknown`. Result
  basename and opaque source are existing contracts; add fields instead of repurposing them.
- `dashboard/DESIGN.md` and `dashboard/src/app.css` define the shared visual language.
  The requested design workflow must provide feature-specific guidance before UI changes.
- Concurrent feature `specs/005-dashboard-live-updates/` appeared while this plan was being
  prepared. This feature therefore uses 006. Keep store edits scoped and avoid changing
  global polling/refresh architecture or the other task's active feature pointer.

## Technical Context

**Language/Version**: Python 3.12 workspace (package >=3.10); TypeScript 5 and Svelte 5.

**Primary Dependencies**: Existing FastAPI, Pydantic, httpx, huggingface_hub (locked 1.32.0),
SvelteKit 2 and Vite 6; standard URL parsing and POSIX path handling. No new packages.

**Storage**: Existing master-local model directory and download sidecars. Search metadata
and browser results remain transient; no schema migration, database, or search cache.

**Testing**: Existing pytest/FastAPI TestClient, Node `node:test`/Vite store tests, and the
repository's Chrome/Edge CDP browser pattern. External Hub/HTTP and GPU metadata controlled
in automated checks; no real credentials, large model downloads, or GPUs required.

**Target Platform**: Existing Windows, Linux, and macOS agents and current desktop/mobile
browsers. Hub paths use POSIX semantics independently of the local OS.

**Project Type**: Static Svelte dashboard served by the Python cluster agent; shared CLI.

**Performance Goals**: One submission lists all eligible files for an explicit repository
or subtree without per-file network probing. One paste, one search, one download action.
Search performs metadata reads only and never starts model transfer. No new latency or
throughput guarantee beyond observed upstream availability is claimed.

**Constraints**: One in-flight browser search. Explicit metadata requests use a 15-second
transport timeout, not an asserted total deadline. Preserve keyword discovery caps (20
Hub candidates, inspect at most 8 repositories, return at most 40 rows); explicit repository
and folder lookups must not inherit that keyword file cap. Preserve existing error envelope,
download payload, source-string transport, token handling, and private cluster topology.

**Scale/Scope**: One existing page, its result contract, shared Python helpers, bounded
agent download-guard correction, regression checks, and existing operational docs. No
new route, new model formats, split-shard orchestration, cache, storage naming migration,
or inference/runtime changes.

## Constitution Check

| Principle | Pre-research gate | Post-technical-design gate |
|-----------|-------------------|----------------------------|
| I. Simple, consistent code | PASS: shared helper/caller flow inspected | PASS: existing modules, canonical URLs, no new dependencies or service |
| II. Behavior-focused tests | PASS: URL selection and error regressions identified | PASS: controlled resolver/API/store/browser acceptance checks specified |
| III. Integration and portability | PASS: API, CLI and cross-platform paths identified | PASS: real search-to-download boundary check and POSIX path cases specified; runtime tests remain to run |
| IV. Explicit contracts and failures | PASS: invalid inputs and credential boundaries identified | PASS: exact paths, validated selectors, sanitized errors, partial warnings, and lifecycle defined |
| V. Maintainable, reproducible changes | PASS: supported tools/docs identified | PASS: additive fields, existing doc updates and runnable validation guide; no packaging changes |

No constitutional exceptions are requested. These gates assess the technical plan, not
implemented behavior. **Design gate: PASS** — the Local Codex reference was generated,
refined, and checked against the real bar geometry and required modal interactions.
Production implementation and regression verification remain subsequent phases.

## Project Structure

### Documentation (this feature)

```text
plans/search-huggingface-urls.md          # repository plan snapshot and links
specs/006-search-huggingface-urls/
├── spec.md
├── checklists/requirements.md
├── plan.md
├── research.md
├── data-model.md
├── design.md                            # requested design workflow status/requirements
├── contracts/models-search.md
└── quickstart.md
```

`tasks.md` belongs to the subsequent `/speckit.tasks` phase and is not generated here.

### Source Code (repository root)

```text
python/kllm/models.py                    # parser, lookup, sources and downloader
python/kllm/agent/__init__.py             # error/selected-download boundary
python/kllm/cli.py                       # existing shared caller; regression coverage
dashboard/src/routes/models/+page.svelte # existing search/results surface
dashboard/src/lib/store.svelte.ts        # search lifecycle and unchanged source forwarding
dashboard/src/lib/types.ts               # additive row/response fields
tests/test_models.py
tests/test_dashboard_api.py
tests/test_model_management_api.py
dashboard/tests/store.test.mjs
dashboard/tests/models.test.mjs          # proposed focused browser check
docs/cli.md
docs/troubleshooting.md
```

**Structure Decision**: Reuse established modules and test patterns. The proposed browser
test can use the existing CDP approach from `dashboard/tests/usage-workspace.test.mjs`;
do not add a browser-test framework. Preserve the current bar and navigation. Any reviewed
UI adaptation is limited to the existing results modal and must reuse page/shell tokens.

## Phase 0 — Research outcome

See [research.md](research.md) for decisions, rationale, alternatives and source references.
All technical unknowns are resolved: library revision support, URL grammar, metadata
selection, error/partial semantics, wire compatibility, and verification approach.

## Phase 1 — Design and contracts

1. [data-model.md](data-model.md) defines transient target/option/outcome fields and states.
2. [contracts/models-search.md](contracts/models-search.md) fixes accepted input grammar,
   additive response fields, errors, and the selected-source download round trip.
3. [quickstart.md](quickstart.md) gives isolated validation scenarios and actual commands.
4. [design.md](design.md) records the confirmed constraints, generated local reference,
   source-fidelity refinement, real preview and desktop/mobile browser review evidence.

The eventual implementation sequence is shared parser/selection, download round trip,
additive response/store integration, reviewed UI adaptation, regression checks, and docs.
Avoid a parallel browser parser or speculative generalized search abstraction.

## Feature selection and setup evidence

The required `.venv/Scripts/python.exe .specify/scripts/python/setup_plan.py --json` ran
successfully from the repository root with process-scoped `SPECIFY_FEATURE_DIRECTORY`
set to `specs/006-search-huggingface-urls` and `SPECIFY_FEATURE_NO_PERSIST=1`.
The emitted FEATURE_SPEC, IMPL_PLAN, and FEATURE_DIR all point to this directory.
Its BRANCH value was `006-search-huggingface-urls`: this script derives that label from
the directory when `SPECIFY_FEATURE` is absent; `git branch --show-current` confirms the
actual branch is `main`. No branch was created or switched.

Use the same explicit directory and no-persist setting for downstream commands while
the other feature owns `.specify/feature.json`. Neither active feature selection nor
existing plan files should be overwritten to work around concurrent planning.

## Planning validation

- Nine feature/snapshot Markdown documents checked: relative links resolve, template
  placeholders are removed, code fences and whitespace are consistent, and the example
  response parses as JSON. All 16 specification-quality checklist items are satisfied.
- The required setup command succeeded. The actual branch remains `main`; the active
  feature pointer still belongs to concurrent feature 005.
- `.specify/extensions.yml` was absent at both pre-plan and post-plan checks; no hooks
  were registered to dispatch.
- No application code changed. Production regression suites/builds, model downloads and
  GPU checks were not run. Separate prototype browser checks verified bar fidelity and
  modal interactions; quickstart commands remain implementation gates.
- **Completed outcome:** The clarified brief and local reference have passed the design
  review documented in `design.md`. Phase 1 is complete and ready for `/speckit.tasks`.
