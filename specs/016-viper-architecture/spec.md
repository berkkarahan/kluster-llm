# Feature Specification: Feature Module Architecture

**Feature Branch**: `main` (no branch-creation hook configured)

**Created**: 2026-10-09

**Status**: Approved

**Input**: Restructure the Python package and the Svelte dashboard so each feature owns its entity, interactor, presenter, router, and view. Keep the listed external entry points. Patch tests on the owning module.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find one feature in one place (Priority: P1)

As a maintainer, I can open one feature directory and find that feature's data types, business logic, response shaping, routes, and CLI rendering together, so a change to the feature does not start in a shared grab-bag of handlers.

**Why this priority**: The move exists so later changes have one owner per feature.

**Independent Test**: For a feature that has HTTP and CLI surfaces, confirm its types, logic, presenters, and routers live under that feature directory, and that shared code on the path is a gateway passed in as an argument.

**Acceptance Scenarios**:

1. **Given** the Python package, **When** a maintainer opens `src/kllm/modules/<feature>/`, **Then** that directory holds `entities`, `interactors`, `presenters`, `router_http`, `router_cli`, and `view_cli`, omitting only a part the feature does not use.
2. **Given** a cross-module adapter (process, native, HTTP, SQLite, or OS), **When** an interactor uses it, **Then** the adapter lives in `src/kllm/shared/` and is a constructor argument.
3. **Given** a store only one feature uses, **When** that feature reads it, **Then** the store gateway stays with the feature.
4. **Given** the dashboard, **When** a maintainer opens `dashboard/src/lib/modules/<feature>/`, **Then** that directory holds the feature's entities, interactor, presenter, router, and view, omitting only a part the feature does not use.

---

### User Story 2 - Start the same entry points (Priority: P1)

As an operator, I can start the control app, the CLI, the update worker, and the installer the same way as before, so the restructure does not change how a node is launched or how scripts import the package.

**Why this priority**: The public paths are compatibility contracts. The move fails if any listed path stops importing.

**Independent Test**: Import or invoke each listed entry point and confirm it resolves. Confirm `scripts/install.py` is still at that path.

**Acceptance Scenarios**:

1. **Given** the installed package, **When** a process loads `kllm.agent:app`, `kllm.agent.create_app`, `kllm.agent.STATE`, `kllm.agent.legacy_v1_router`, `kllm.cli:main`, or `kllm.agent.__main__:main`, **Then** that name resolves.
2. **Given** the package, **When** a process runs `python -m kllm` or `python -m kllm.cluster_update`, **Then** that module entry still runs.
3. **Given** uvicorn's log config, docs, or `scripts/`, **When** they import `kllm.log_sink.PolicyFileHandler`, `kllm.models.*`, `kllm.native`, `kllm.runtimes.build.*`, `kllm.agent_skills`, `kllm.managed_network.platform.inspection`, `kllm._native`, or `kllm.__version__`, **Then** that import still succeeds.
4. **Given** `scripts/install.py`, **When** the tree is restructured, **Then** that file stays where it is.

---

### User Story 3 - Patch the module that owns the name (Priority: P1)

As a maintainer, I can patch a function on the module that defines it, so a test locks the behavior that actually runs.

**Why this priority**: A re-export kept only so an old patch path survives points tests at the wrong global.

**Independent Test**: Move one helper into its feature module, point its tests at that module, and confirm the patch changes the behavior under test. Confirm no new internal compatibility facade exists for the old path.

**Acceptance Scenarios**:

1. **Given** a name that moved into a feature module, **When** a test patches it, **Then** the patch target is the owning module, including string patch targets.
2. **Given** an interactor, **When** it needs cluster state or another module's result, **Then** that value is a constructor argument and the interactor does not import `kllm.agent`.
3. **Given** `kllm.agent.STATE`, **When** tests assign it, **Then** the assignment is still on `kllm.agent` and the same object is passed into interactors.
4. **Given** a test that reloads the module which computes home and paths, **When** it reloads after this move, **Then** the reload target is `kllm.shared.state`.

---

### User Story 4 - Keep dashboard features independent (Priority: P1)

As a maintainer, I can change one dashboard feature without importing a global store, so cross-feature data is an explicit input.

**Why this priority**: The dashboard half of the move is the same change set. A global store left in place recreates the coupling the split removes.

**Independent Test**: Render a feature view with its presenter passed in. Confirm a status update still schedules an estimate through a subscription the run interactor registers, and that saving node settings still clears the run plan through an event the run feature consumes.

**Acceptance Scenarios**:

1. **Given** a route, **When** it renders, **Then** the route shell builds nothing beyond its module and passes the presenter to the view.
2. **Given** `dashboard/src/lib/app.svelte.ts`, **When** the app starts, **Then** that module builds feature interactors once and wires the live feed.
3. **Given** a component that used to call the network itself, **When** the feature is split, **Then** that call lives in the feature interactor.
4. **Given** the old `store.svelte.ts` path, **When** dashboard tests load the app, **Then** they load `createApp()` from `dashboard/src/lib/app.svelte.ts` and the feature interactors.

### Edge Cases

- Dissolving `agent/__init__.py` must preserve threads, lifespan, background stop, and membership maintenance. The move does not change those behaviors.
- `legacy_v1_router` stays importable from `kllm.agent`. v1 paths are listed on each feature `router_http.py` and aggregated by `modules/legacy_v1.py`. v2 paths are aggregated by `modules/control_v2.py`.
- About 920 patch sites, including string targets, are rewritten in the same change. An unrewritten old path is a failure, not a facade to add.
- Dashboard status-to-estimate and shared plan-result updates stay observable after the global store is removed.
- `crates/kllm-native/` and `src/kllm/templates/` are not part of the move.
- CLI flags, HTTP paths, and payload shapes stay as they are. `docs/cli.md` and `docs/control-api-v2.md` need no behavior edits.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Python feature code MUST live at `src/kllm/modules/<feature>/` with `entities`, `interactors`, `presenters`, `router_http`, `router_cli`, and `view_cli`. A feature MUST omit a part it does not use.
- **FR-002**: Dashboard feature code MUST live at `dashboard/src/lib/modules/<feature>/` with entities, an interactor, a presenter, a router, and a view, omitting a part the feature does not use. Views MUST receive their presenter via props.
- **FR-003**: Cross-module process, native, HTTP, SQLite, and OS adapters MUST live in `src/kllm/shared/` and MUST be passed into interactors as constructor arguments. A store only one feature uses MUST stay with that feature.
- **FR-004**: Composition roots MUST be only `kllm.agent`, `kllm.cli`, and dashboard routes. `dashboard/src/lib/app.svelte.ts` MUST only build the interactors those routes render.
- **FR-005**: Test patch surfaces MUST be the owning module, including string targets. The change MUST NOT add a re-export whose only purpose is to keep an old patch path.
- **FR-006**: Facades MUST be kept only for `kllm.agent:app`, `kllm.agent.create_app`, `kllm.agent.STATE`, `kllm.agent.legacy_v1_router`, `kllm.cli:main`, `kllm.agent.__main__:main`, `python -m kllm`, `python -m kllm.cluster_update`, `kllm.log_sink.PolicyFileHandler`, the `kllm.models.*` facade, `kllm.native`, `kllm.runtimes.build.*`, `kllm.agent_skills`, `kllm.managed_network.platform.inspection`, `kllm._native`, `kllm.__version__`, and `scripts/install.py`. `scripts/install.py` MUST NOT be moved.
- **FR-007**: `STATE` MUST remain on `kllm.agent` and MUST be passed into interactor constructors. An interactor MUST NOT import `kllm.agent`.
- **FR-008**: The module that computes home and paths MUST be `kllm.shared.state`, and tests that reload that module MUST reload `kllm.shared.state`.
- **FR-009**: Observable CLI options, HTTP routes, payload shapes, installer flags, and native exports MUST stay compatible. `crates/kllm-native/` and `src/kllm/templates/` MUST stay in place.
- **FR-010**: Route handlers that today are inline on the agent MUST move into the owning interactor, with the HTTP or CLI shape in the presenter.
- **FR-011**: Governance for this layout MUST be the architecture constitution's Module shape section, decision D3, and architecture note A2. D1 remains as a superseded record.

### Key Entities *(include if feature involves data)*

- **Feature module**: One product area. It owns its types, logic, presentation, and routes.
- **Gateway**: A process, native, HTTP, SQLite, or OS adapter. Cross-module gateways live in `shared/` and are injected. A single-feature store stays in that feature.
- **Composition root**: The place that constructs the app and registers routers: `kllm.agent`, `kllm.cli`, and dashboard routes, with `app.svelte.ts` building dashboard interactors.
- **Facade**: A re-export kept only for an external entry point in FR-006.
- **Patch surface**: The module a test patches. It is the module that defines the name, or a facade from FR-006.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each Python and dashboard feature listed in the plan has its parts only under that feature directory, and `shared/` contains no feature business rules.
- **SC-002**: Every external entry point in FR-006 imports or runs. A name that moved is patched on its new module, and no new internal compatibility facade exists for the old path.
- **SC-003**: `uv run python -m pytest -q`, `uv run ruff check src tests`, `uv run mypy`, `npm --prefix dashboard test`, `npm --prefix dashboard run check`, `npm --prefix dashboard run build`, and `cargo test --manifest-path crates/kllm-native/Cargo.toml` pass after the move.
- **SC-004**: `kllm --help` and `uv run python -c "import kllm.agent as a; a.app"` succeed. `uv run python scripts/build.py` still loads the native extension.
- **SC-005**: Dashboard regressions for run planning, models, usage workspace, and managed network still pass with explicit cross-feature inputs.

## Assumptions

- This is one behavior-preserving change set. Code, tests, and the later doc/skill path updates land together.
- The governance documents in this folder are the structural contract. They do not themselves move code.
- CLI and HTTP docs stay as they are because those interfaces do not change.
- `AGENTS.md`, `README.md`, and the develop/dashboard skills are updated after the code lands, in this feature's last task.
- Installer tests keep patching the loaded `scripts/install.py` module, because that file is an external entry point and is not moved.
- `docs/memory/PROJECT_CONTEXT.md` is still an unfilled scaffold and is not a source of product constraints for this feature.
- The test rewrite is a one-off codemod. The codemod script is not committed.
