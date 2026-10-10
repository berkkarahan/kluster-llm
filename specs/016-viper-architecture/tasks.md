# Tasks: Feature Module Architecture

**Input**: [spec.md](spec.md) and [plan.md](plan.md)

**Tests**: Existing suites are the regression net. Re-point them to the owning module. Add no assertions whose only purpose is to lock the new directory layout. This governance slice adds no test: constitution II treats documentation-only changes as exempt.

**Organization**: Governance first, then Python, then tests, then dashboard, then the doc and skill paths that must wait until the code exists. One change set. A later phase assumes the earlier phase is importable.

## Path Conventions

Python stays under `src/kllm/`. Dashboard stays under `dashboard/`. Tests stay under `tests/` and `dashboard/tests/`. Native code stays under `crates/kllm-native/`. `scripts/install.py` stays put.

## Phase 1: Governance

- [x] T001 Write `specs/016-viper-architecture/spec.md`, `plan.md`, `tasks.md`, and `memory.md` for the feature-module layout, the facade list, and the module map.
- [x] T002 Amend `.specify/memory/architecture_constitution.md` Module shape and the tests patch-surface sentence. Bump `.specify/memory/constitution.md` to 1.3.0 (ratified 2026-09-28, last amended 2026-10-09) with the reason and impact on the architecture-constitution constraint (depends on T001).
- [x] T003 Add `docs/memory/decisions/2026-10-09-viper-owning-module-patch-surface.md` (D3) and `docs/memory/architecture/2026-10-09-viper-modules.md` (A2). Mark D1 superseded. Route both new entries from `docs/memory/INDEX.md` (depends on T002).
- [x] T004 Record the amendment-review follow-ups in `specs/016-viper-architecture/plan.md`: skill and README path updates wait for T018; the stale `python/kllm/` constraint and `plans/resilience/` facade notes are identified and not edited (depends on T002).

**Checkpoint**: Structural standard and durable memory match the approved layout. No application code has moved.

## Phase 2: Python shared gateways

- [ ] T005 Move cross-module gateways and state into `src/kllm/shared/` (`state.py`, `config.py`, `native.py`, `http_clients.py`, `cp_proxy.py`, `rpc_cache.py`, `netsec.py`, `firewall.py`, `wireguard_config.py`, `wireguard_runtime.py`, `atomic_io.py`, `auth.py`, `hf_token.py`, `process_registry.py`, `log_sink.py`, `llama_pack.py`, `accel.py`, `exceptions.py`, `persistence/`, `platform/`) and codemod imports (depends on T004). Leave FR-006 re-exports in place where a kept path now points at `shared/`.

**Checkpoint**: `kllm.shared.state` is the single home/path module. Feature business rules are not in `shared/`.

## Phase 3: Python feature modules

- [ ] T006 Create `src/kllm/modules/{usage,logs,updates,models,runtimes,managed_network}/` from the current top-level, `agent/services`, `agent/handlers`, and `commands` code, using the plan's module map (depends on T005).
- [ ] T007 Create `src/kllm/modules/{cluster,inference,settings,live,connect}/`. Split `agent/routes` v1/v2 tables into each module's `router_http.py`. Keep `legacy_v1_router` on `kllm.agent` via `modules/legacy_v1.py` (depends on T006).
- [ ] T008 Move inline endpoint callables out of `agent/__init__.py` into the owning interactor, with HTTP shape in the presenter. Pass `STATE` in. No interactor imports `kllm.agent` (depends on T007).
- [ ] T009 Reduce `agent/__init__.py` to app, `STATE`, `create_app`, lifespan, and router mounting. Reduce `cli.py` to Typer registration. Delete `agent/routes`, `agent/handlers`, `agent/services`, `commands/`, `core/`, and moved top-level files. Keep every FR-006 path (depends on T008).

**Checkpoint**: The package imports. Listed entry points resolve. Old internal paths are gone.

## Phase 4: Python tests

- [ ] T010 Re-point patch sites and imports in `tests/`, including string targets, `conftest.py` (`isolated_control_pool` to `kllm.shared.http_clients`, `native_inventory_counts` to `kllm.shared.native`), and `importlib.reload` anchors to `kllm.shared.state` (depends on T009).
- [ ] T011 Run `uv run python -m pytest -q`, `uv run ruff check src tests`, and `uv run mypy`. Fix misses in the owning module. Do not add a compatibility facade (depends on T010).

**Checkpoint**: SC-002 and the Python half of SC-003 pass.

## Phase 5: Dashboard

- [ ] T012 Add `dashboard/src/lib/shared/` and `dashboard/src/lib/live/` as in the plan (depends on T011).
- [ ] T013 Split the dashboard store into `dashboard/src/lib/modules/{logs,integrations,node-settings,runtimes,network,usage,models,cluster,run}/` with explicit cross-module inputs. Move component fetches into interactors (depends on T012).
- [ ] T014 Add `dashboard/src/lib/app.svelte.ts`. Make `dashboard/src/routes/**` thin shells. Delete `store.svelte.ts`. Re-point the Node tests that load the store (depends on T013).
- [ ] T015 Run `npm --prefix dashboard test`, `npm --prefix dashboard run check`, and `npm --prefix dashboard run build` (depends on T014).

**Checkpoint**: SC-005's dashboard flows pass against `createApp()`.

## Phase 6: Docs and full verification

- [ ] T016 Run `kllm --help`, `uv run python -c "import kllm.agent as a; a.app"`, and `uv run python scripts/build.py` (depends on T011, T015).
- [ ] T017 Run `cargo test --manifest-path crates/kllm-native/Cargo.toml` and full `make test` (depends on T016).
- [x] T018 Update `AGENTS.md`, `README.md`, `.agents/skills/kllm-develop`, `.agents/skills/kllm-dashboard`, and the matching develop/dashboard skill copies so they name module paths and the owning-module patch rule (depends on T009, T014). Do not change `docs/cli.md` or `docs/control-api-v2.md` for behavior.

**Checkpoint**: SC-003 and SC-004 are recorded from the commands above. Guidance no longer tells maintainers to patch a moved name on `kllm.agent`.
