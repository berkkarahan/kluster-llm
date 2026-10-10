### 2026-10-09 - Feature module layout

**Status**
Active

**ID**
A2

**Why this is durable**
Every later specification is planned against this layout. Feature code has one directory. Shared code is a gateway, not a second copy of the feature.

**What future mistake this prevents**
Putting a new HTTP route, CLI command, or dashboard fetch back on `kllm.agent`, `cli.py`, or a global dashboard store. Also treating A1's old facade tradeoff as permission to re-export a moved name.

**Evidence**
`specs/016-viper-architecture`, required by constitution 1.3.0. A1 still holds for helpers-as-arguments, imports, Pydantic models, and JSON cluster state.

**Where to look next**
`.specify/memory/architecture_constitution.md` (Module shape). D3 for patch surfaces.

**Decision**
Python features live at `src/kllm/modules/<feature>/{entities,interactors,presenters,router_http,router_cli,view_cli}`. Omit a part the feature does not use. Cross-module gateways live in `src/kllm/shared/` and are constructor arguments. A store only one feature uses stays in that feature (`gateways` on `usage`, `logs`, and `managed_network`).

Dashboard features live at `dashboard/src/lib/modules/<feature>/` with entities, an interactor, a presenter, a router, and a view. Omit a part the feature does not use. Views take the presenter as props.

Composition roots are only `kllm.agent`, `kllm.cli`, and dashboard routes. `dashboard/src/lib/app.svelte.ts` builds the interactors those routes render. No interactor imports `kllm.agent`. The `origin`, `lifecycle_bus`, and `schemas` implementations live in `kllm.shared` (`kllm.shared.origin`, `kllm.shared.lifecycle_bus`, `kllm.shared.schemas`); `kllm.agent.origin`, `kllm.agent.lifecycle_bus`, and `kllm.agent.schemas` re-export them. Feature modules must import those from `kllm.shared`, not from `kllm.agent`, so they do not load the composition root during their own import.

**Layout**

Python modules: `cluster`, `inference`, `models`, `runtimes`, `managed_network`, `usage`, `logs`, `updates`, `settings`, `live`, `connect`. HTTP paths for a feature are on its `router_http.py`. `endpoints.py` holds HTTP callables for cluster, inference, live, logs, models, runtimes, settings, updates, and usage. `managed_network` builds its router in `router_http.py`. `connect` is CLI only. `modules/legacy_v1.py` and `modules/control_v2.py` aggregate those tables; `http_routes.py` binds them; `http_deps.py` holds the key and token dependencies. `legacy_v1_router` stays on `kllm.agent`. Presenters and single-feature stores are packages (`presenters/`, `gateways/`) when the feature has more than one of those files. Some CLI commands live in sibling `*_cli.py` files beside `router_cli.py`.

`shared/` holds state (the home/path reload anchor is `kllm.shared.state`), config, native, HTTP clients, netsec, firewall, wireguard, persistence, platform, process registry, log sink, and the other cross-module adapters. It holds no feature business rules. Stable re-exports that remain at the package root are `kllm.models`, `kllm.native`, `kllm.log_sink`, `kllm.runtimes.build`, `kllm.cluster_update`, `kllm.agent_skills`, and `kllm.managed_network.platform.inspection`.

Dashboard modules: `cluster`, `usage`, `models`, `run`, `node-settings`, `network`, `runtimes`, `logs`, `integrations`. Shared UI, HTTP (`http.svelte.ts`), navigation, and formatters live in `dashboard/src/lib/shared/`. The live feed lives in `dashboard/src/lib/live/`. A feature omits a presenter or router file when it has none. `NodeMap` is a usage view. Managed-network screens are the `network` module. `run` takes cluster status and the model list as inputs and subscribes to the live interactor. `node-settings` emits the event that clears the run plan. The logs page is composed through `logs/controller.svelte.ts`.

`crates/kllm-native/` is reached only through the native gateway. `scripts/install.py` and `src/kllm/templates/` stay put.

**Tradeoffs**
- Gained: one directory per feature, and one structural rule for the next spec.
- Made harder: cross-feature dashboard data has to be an argument or a subscription.
- Reconsider: if a second feature needs a gateway that today sits inside one module, move that gateway to `shared/` and record why.
