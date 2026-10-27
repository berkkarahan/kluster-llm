# Implementation Plan: Settings Feature Flags Admin View

**Branch**: `022-settings-feature-flags` | **Date**: 2026-10-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/022-settings-feature-flags/spec.md`

## Summary

Add a dedicated **Feature flags** section (`?section=flags`) to the dashboard Settings page (`dashboard/src/lib/modules/node-settings/`) and corresponding HTTP endpoints (`GET /v2/node/flags`, `PUT /v2/node/flags`) in `src/kllm/modules/settings/` so operators can view and flip feature flags (`private-network-setup`, `prima-auto-deploy`) on the go. Toggling a flag persists immediately to the node's unified SQLite database (`~/.kllm/managed-network.sqlite` via `kllm.shared.config`) and updates the live dashboard state without requiring a page reload or node restart.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript / Svelte 5 (dashboard frontend)

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy Core / SQLite (backend); Svelte 5 runes (`$state`, `$derived`, `$props`) (frontend)

**Storage**: Unified application SQLite database (`~/.kllm/managed-network.sqlite`, `feature_flags` table via `kllm.shared.persistence.feature_flags` and `kllm.shared.config`)

**Testing**: `uv run pytest -q` (Python backend), `npm --prefix dashboard run check` & `node dashboard/tests/run-tests.mjs` (Svelte 5 dashboard)

**Target Platform**: Windows, macOS, Linux (`kllm-agent` local/cluster control plane and web dashboard)

**Project Type**: Distributed LLM cluster agent + Svelte 5 web dashboard

**Performance Goals**: Sub-second (<1s) end-to-end toggle persistence and live UI reflection across gated views

**Constraints**: Strict Clean Architecture / VIPER boundaries (`.specify/memory/architecture_constitution.md`); no `kllm.agent` imports in `src/kllm/modules/`; preserve install boundary (`tests/test_install_boundary.py`)

**Scale/Scope**: 2 registered feature flags (`private-network-setup`, `prima-auto-deploy`), extensible via `FEATURE_FLAGS`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Simple, Consistent Code**: PASS — Reuses `kllm.shared.config` (`feature_flags()`, `set_feature_flag()`) and existing `SettingsHandlers` / `NodeSettingsInteractor` patterns without introducing new dependencies or abstractions.
- **II. Behavior-Focused Regression Tests**: PASS — Adds targeted backend API tests in `tests/test_dashboard_api.py` and frontend component/interactor tests in `dashboard/tests/settings-feature-flags.test.mjs`.
- **III. Verified Integration and Portability**: PASS — Uses platform-agnostic HTTP and SQLite persistence paths already verified across Windows, macOS, and Linux.
- **IV. Explicit Contracts and Failures**: PASS — Validates flag names and strict booleans via `FeatureFlagBody` and `kllm.shared.config.set_feature_flag`, returning HTTP 400 on unknown flags and surfacing errors in the UI while preserving confirmed toggle state.
- **V. Maintainable, Reproducible Changes**: PASS — Complies with `.specify/memory/architecture_constitution.md` module ownership rules (`src/kllm/modules/settings/` and `dashboard/src/lib/modules/node-settings/`).

## Project Structure

### Documentation (this feature)

```text
specs/022-settings-feature-flags/
├── plan.md              # This file (/speckit-plan command output)
├── memory.md            # Feature scratch memory
├── memory-synthesis.md  # Synthesized governance & architecture context
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── settings-flags.md
└── tasks.md             # Phase 2 output (/speckit-tasks command)
```

### Source Code (repository root)

```text
src/kllm/
├── shared/
│   └── schemas.py                                # Add FeatureFlagBody schema
├── modules/
│   └── settings/
│       ├── router_http.py                        # Add get_feature_flags / save_feature_flag to SettingsHandlers & put_feature_flag to route tables
│       └── endpoints.py                          # Expose get_feature_flags / put_feature_flag & routes in create_settings_router
└── agent/
    └── runtime.py                                # Wire get_feature_flags & put_feature_flag in _route_handlers

dashboard/src/lib/modules/node-settings/
├── entities.ts                                   # Add FeatureFlagDescriptor & FEATURE_FLAG_CATALOG
├── interactor.svelte.ts                          # Add featureFlags, pendingFlags, flagError, loadFeatureFlags, setFeatureFlag
└── view/
    ├── FeatureFlags.svelte                       # New compact Feature Flags admin panel component
    └── SettingsPage.svelte                       # Add 'flags' section to nav & render FeatureFlags panel

tests/
└── test_dashboard_api.py                         # Add GET/PUT /v2/node/flags & error boundary tests

dashboard/tests/
└── settings-feature-flags.test.mjs               # Add Settings Feature Flags UI & live reactivity tests
```

**Structure Decision**: Extends the existing `src/kllm/modules/settings/` backend module and `dashboard/src/lib/modules/node-settings/` frontend module in place, adhering to the VIPER module structure in `.specify/memory/architecture_constitution.md`.

## Complexity Tracking

No constitution violations.
