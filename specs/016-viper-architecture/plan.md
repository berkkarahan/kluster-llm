# Implementation Plan: Feature Module Architecture

**Branch**: `main` | **Feature identifier**: `016-viper-architecture` | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

## Summary

Move `src/kllm/` and `dashboard/src/` into per-feature modules in one change set. Each feature owns its types, logic, presentation, and routes. Cross-module adapters live in `shared/` and are constructor arguments. Composition roots stay `kllm.agent`, `kllm.cli`, and dashboard routes. Tests patch the owning module. The only re-export facades are the external entry points in FR-006.

`crates/kllm-native/`, `scripts/install.py`, and `src/kllm/templates/` stay where they are. CLI flags and HTTP payloads do not change.

## Amendment record (constitution 1.3.0)

Authorized by the approved restructuring. Merge review still applies. This slice does not move code.

| Item | Record |
|---|---|
| Previous | 1.2.0, last amended 2026-10-06 |
| New | 1.3.0, last amended 2026-10-09 |
| Ratified | 2026-09-28, unchanged |
| Bump | MINOR. Adds the feature-module layout and redefines the patch-surface rule. Principles I–V are unchanged. Same class of bump as 1.2.0, which redefined a repository constraint without a major version. |
| Reason | A whole-package move cannot keep a re-export for every historical patch path. |
| Impact | Later specs patch the owning module. The FR-006 entry points stay importable. |
| Files | `.specify/memory/constitution.md` states the reason and impact on the architecture-constitution constraint. `.specify/memory/architecture_constitution.md` holds the layout and facade list. |

Principle text was not copied into templates, commands, or `AGENTS.md`. `constitution.md` does not require that sync. Its amendment rule is: update the constitution, explain reason and impact, bump the version, keep the ratification date, and review dependents for conflicts and follow-ups. `.specify/templates/` do not restate patch surfaces. `extensions.yml` has no constitution hook.

Follow-ups from the governance slice:

- T018 updated `AGENTS.md`, the README layout, and the develop/dashboard skills in `skills/development/`, `.agents/skills/`, and `.opencode/skills/`. Those files name `modules/<feature>/` and `shared/`, and they patch the owning module. `kllm.agent` stays the patch surface for `app`, `create_app`, `STATE`, and `legacy_v1_router`.
- `constitution.md` Repository Constraints still says Python code lives in `python/kllm/`. That predates this amendment and conflicts with `src/kllm/`. Not corrected in 1.3.0.
- `plans/resilience/` still describes facade patch surfaces. Those notes are not the structural standard.

## Constitution Check

*GATE: Passed for this governance slice. Re-check after the code move.*

| Principle / boundary | Assessment |
|---|---|
| I. Simple, consistent code | Pass for this slice: one layout rule, no new dependency. The code move must stay a move. |
| II. Behavior-focused tests | Pass for this slice: documentation-only governance adds no test. The code move must re-point existing tests and keep them green. |
| III. Integration and portability | Pass: no contract or native change. Smoke imports are SC-004. Hardware claims are out of scope. |
| IV. Explicit contracts | Pass: FR-006 and FR-009 are the compatibility list. |
| V. Reproducible changes | Pass: this plan records the amendment, the follow-ups, and the verification commands. |
| Architecture constitution | This change is the 1.3.0 amendment of that file. |
| Durable memory | Authorized. D3 supersedes D1. A2 records the layout. `PROJECT_CONTEXT.md` stays an unfilled scaffold. |

## Role map

- **Entity**: Pydantic or dataclass models, or the dashboard `types` for that feature.
- **Interactor**: business logic. It receives gateways and `STATE` as constructor arguments. It does not import `kllm.agent`.
- **Presenter**: response, table, and view-model shaping.
- **Router**: FastAPI `APIRouter` (`router_http`), Typer commands (`router_cli`), or a SvelteKit route plus a typed navigation helper.
- **View**: Rich console rendering (`view_cli`), or `.svelte` components. Dashboard views take their presenter as props.
- **Composition root**: builds the app and includes routers. Python: `kllm.agent` and `kllm.cli`. Dashboard: `dashboard/src/routes/` shells, wired by `dashboard/src/lib/app.svelte.ts`.

Router calls presenter. Presenter calls interactor and shapes the view. Interactor uses entities and gateways.

## Module map

### Python

`src/kllm/agent/` keeps `app`, `STATE`, `create_app`, lifespan, and exception handlers, and includes module routers. `src/kllm/cli.py` only registers Typer routers. `src/kllm/__init__.py`, `__main__.py`, `agent/__main__.py`, `application_lifecycle.py`, `lifecycle_bus.py`, and `origin.py` stay at those paths.

`src/kllm/shared/` holds cross-module gateways and the state store: `state.py` (cluster state, node info, load/save/paths; reload anchor `kllm.shared.state`), `config.py`, `native.py`, `http_clients.py`, `cp_proxy.py`, `rpc_cache.py`, `netsec.py`, `firewall.py`, `wireguard_config.py`, `wireguard_runtime.py`, `atomic_io.py`, `auth.py`, `hf_token.py`, `process_registry.py`, `log_sink.py`, `llama_pack.py`, `accel.py`, `exceptions.py`, `persistence/`, and `platform/`. No feature business rules. `state_ref.py` publishes the agent `STATE` accessor.

| Module | Owns |
|---|---|
| `cluster` | Start/join/register bodies, discovery models; membership, join, lifecycle, heartbeat, discovery; status and heartbeat presenters; HTTP router; CLI start/stop/join/leave/status/gpus |
| `inference` | Plan/run/settings bodies, placement, engine parameters; planning, launch, placement, RPC, templates; run response and error envelopes; HTTP plans/runs/model-settings/capabilities and v1 chat proxies; CLI plan/run |
| `models` | Model schemas; catalog, query, downloads, GGUF metadata; HTTP router; CLI template/download/delete |
| `runtimes` | Runtime models; registry, build, packs, selection, source, control; HTTP router; CLI rebuild |
| `managed_network` | Network models; allocation, bootstrap, identity, guided setup, admission, recovery; feature store and transport gateways; HTTP router |
| `usage` | Usage schema; sessions, collector, overview, publisher; usage store and probe gateways; HTTP router |
| `logs` | Cluster log, relay, live tail; log store, tail, and forwarder gateways; HTTP router; CLI logs |
| `updates` | Cluster update, progress, reconnect, worker; HTTP router; CLI update |
| `settings` | Config body; node config, devices, GPU visibility, credentials; HTTP node/config/devices/home/credentials; CLI config and flags |
| `live` | Live feed; live payloads and websocket signature; events HTTP and websocket |
| `connect` | Connect plan, agent skills; print plan; CLI connect |

v1 and v2 route tables leave `agent/routes/`. Each feature lists its paths on `router_http.py`. `modules/legacy_v1.py` and `modules/control_v2.py` aggregate them; `http_routes.py` binds handlers; `http_deps.py` holds the key and token dependencies. `legacy_v1_router` stays on `kllm.agent`. HTTP callables live in the feature's `endpoints.py` where that file exists.

After the move, delete `agent/routes`, `agent/handlers`, `agent/services`, `commands/`, `core/`, and the top-level modules that moved. Do not delete a path that is on the facade list.

### Dashboard

`dashboard/src/routes/` are thin shells: build the module, render its view. Prerender and SSR settings stay. `dashboard/src/lib/app.svelte.ts` builds interactors once and wires the live feed. Delete `store.svelte.ts`.

`dashboard/src/lib/shared/` holds Icon, ErrorMessage, SliderField, AutoEditField, `errors.ts`, `toast.ts`, `format-memory.ts`, `format-transfer.ts`, `http.svelte.ts`, `navigation.ts`, and `entities.ts`. `dashboard/src/lib/live/` holds the shared live interactor (`interactor.svelte.ts`, `updates.ts`, `entities.ts`).

| Module | Owns |
|---|---|
| `cluster` | Status, lifecycle posts, fast subnet; topology view-model; cluster page and topology |
| `usage` | Live usage, lifetimes, series, paging; node map, node usage, history, inference activity |
| `models` | Catalog, search, downloads, HF token; select navigates to `/run`; download panel |
| `run` | Settings load/save, plan, estimate, run/stop; effective config and blockers; deployment plan and parameter controls. Takes cluster status and the model list as inputs |
| `node-settings` | Node config, devices, credentials; hardware, tokens, software, network security |
| `network` | Managed network and advanced network requests; those views |
| `runtimes` | Runtime types, refresh, actions, paths |
| `logs` | Log trail |
| `integrations` | Chat streaming, connection snapshot, connect command |

`run` subscribes to the live interactor for estimate refresh. `node-settings` emits the event that clears `planResult` on `run`. Components do not import a global store.

### Facades

Implementation moves to the owning module or to `shared/`. These paths stay as the stable import or launch surface:

| Kept path | Why it stays |
|---|---|
| `kllm.agent:app`, `create_app`, `STATE`, `legacy_v1_router` | App composition root and existing test anchors |
| `kllm.cli:main` | CLI composition root |
| `kllm.agent.__main__:main`, `python -m kllm` | Package entry |
| `python -m kllm.cluster_update` | Update worker. Thin `cluster_update.py` re-export |
| `kllm.log_sink.PolicyFileHandler` | String in uvicorn log config. Re-export of `shared` |
| `kllm.models.*` | Imported by docs and `scripts/` |
| `kllm.native` | Re-export of the shared native gateway |
| `kllm.runtimes.build.*` | Lazy import from `scripts/install.py` |
| `kllm.agent_skills` | `scripts/install_skills.py` |
| `kllm.managed_network.platform.inspection` | `scripts/configure_10gbe.py` |
| `kllm._native` | Maturin module name |
| `kllm.__version__` | Package version |
| `scripts/install.py` | Public installer. Not application code. Not moved |

## Verification

From the repository root, after the code and test move:

- `uv run python -m pytest -q`
- `uv run ruff check src tests`
- `uv run mypy`
- `npm --prefix dashboard test`
- `npm --prefix dashboard run check`
- `npm --prefix dashboard run build`
- `cargo test --manifest-path crates/kllm-native/Cargo.toml`
- `kllm --help`
- `uv run python -c "import kllm.agent as a; a.app"`
- `uv run python scripts/build.py`

This governance slice checks internal consistency of the spec, the constitution amendment, and the memory index. It does not claim those commands have been run.

`mypy` is not part of `make test`. The split still has to leave `uv run mypy` green.

## Risks

- `agent/__init__.py` is where lifespan, threads, background stop, and membership maintenance can drift. Move endpoint groups with their existing tests. Do not edit behavior in the same step.
- Reloading any module other than `kllm.shared.state` for home and paths leaks stale paths between tests.
- Dashboard cross-feature updates must be explicit subscriptions. The run-planning, models, usage-workspace, and managed-network checks are the regression net.
- A missed string patch (`patch("kllm.membership...")`, `patch("kllm.state.repo_root")`, and the same pattern elsewhere) fails closed only if the codemod map is complete. Do not paper over a miss with a facade.

## Execution order

1. This spec, the constitution amendment, and durable memory. Done in this slice.
2. Python `shared/`, then leaf modules (`usage`, `logs`, `updates`, `models`, `runtimes`, `managed_network`), then `cluster`, `inference`, `settings`, `live`, `connect`. Dissolve inline agent endpoints last. Delete the old packages. Keep the facade list.
3. Re-point tests. Pytest, ruff, and mypy green.
4. Dashboard `shared/` and `live/`, then modules, then `app.svelte.ts` and thin routes. Delete `store.svelte.ts`. Re-point the 22 Node tests. npm test, check, and build green.
5. Update `AGENTS.md`, `README.md`, and the develop/dashboard skills to the new paths. Full `make test` and the smoke commands.
