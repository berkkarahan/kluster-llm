# Feature Specification: Structural Coding Standards

**Feature Branch**: `main` (current branch; no branch-creation hook registered)

**Created**: 2026-09-29

**Status**: Implemented

**Input**: Record the behavior-preserving structure used for the agent, installer, CLI, and native runtime so later specifications follow the same rules.

## Requirements

### Requirement 1 - Later specs follow the architecture constitution (Priority: P1)

Specifications, plans, tasks, and reviews comply with `.specify/memory/architecture_constitution.md`. The project constitution points at that file. `AGENTS.md` points at it for agents that open the repo guide first.

**Independent Test**: Open a new specification and confirm its plan does not introduce a pattern, data library, or moved public entry point that the architecture constitution forbids.

**Acceptance Scenarios**:

1. **Given** a new specification, **When** it is planned, **Then** it keeps API models as the existing Pydantic types and cluster state as the current JSON shape.
2. **Given** a long function, **When** a change splits it, **Then** helpers live in the same module or take their inputs as arguments.
3. **Given** a module tests patch, **When** work moves into a sibling, **Then** the patched name stays on `kllm.agent` or the loaded `scripts/install.py` module.

### Requirement 2 - This refactor keeps external behavior (Priority: P1)

The extraction in the agent, installer, CLI, and native planner does not change HTTP routes, CLI options, install flags, argv flags, or plan results.

**Independent Test**: Existing agent, install, and native tests pass without new assertions that encode the helper layout.

**Acceptance Scenarios**:

1. **Given** the installer, **When** tests patch `_is_windows`, `_download_once`, or `_run` on the loaded `scripts/install.py` module, **Then** those patches still affect the code under test.
2. **Given** the agent, **When** tests assign `kllm.agent.STATE` or patch `ensure_rpc_peers_reachable` on `kllm.agent`, **Then** plan and run still observe that patch.
3. **Given** `kllm.agent:app` and `python scripts/install.py`, **When** a process starts them, **Then** the entry points are unchanged.

## Out of scope

- Dashboard UI.
- Restyling tests.
- Retargeting `.specify/feature.json` away from `specs/008-add-10gbe-link`.
