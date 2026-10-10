# Architecture Constitution (Structural Coding Standard)

This file is the structural coding standard referenced by `constitution.md` and `AGENTS.md`.
It defines how modules, imports, helpers, and test patch surfaces are shaped. It adds no
product behavior; `constitution.md` holds the principles and quality gates.

Amended 2026-10-09 under constitution 1.3.0. Feature code is split by feature. Patch
surfaces live on the owning module. Facades remain only for the external entry points
listed under Module shape.

## Repository boundaries

- Python application code lives in `src/kllm/`: composition roots `agent/` and `cli.py`,
  gateways in `shared/`, and feature code in `modules/<feature>/`. Do not move code back
  into a top-level `python/` package. `src/kllm/templates/` stays put.
- Native code lives in `crates/kllm-native/`. Its public surface is `src/lib.rs`. It is
  not an application layer; Python reaches it through the native gateway.
- Dashboard code lives in `dashboard/` (Svelte 5 runes + TypeScript), with feature code
  in `dashboard/src/lib/modules/<feature>/` and route shells in `dashboard/src/routes/`.
- Installer and bootstrap scripts stay in `scripts/`. `scripts/install.py` is a public
  entry point and is not moved.
- External entry points that must keep working are listed under Module shape. Native
  exports stay in `crates/kllm-native/src/lib.rs`.

## Module shape

- Split long functions into helpers that take their dependencies as arguments. Keep helpers
  in the module that owns the behavior.
- A Python feature lives at `src/kllm/modules/<feature>/` with `entities`, `interactors`,
  `presenters`, `router_http`, `router_cli`, and `view_cli`. Omit a part the feature does
  not use. Cross-module gateways (process, native, HTTP, SQLite, OS adapters) live in
  `src/kllm/shared/` and are passed into interactors as constructor arguments. A feature
  may keep its own gateways for stores only that feature uses. A dashboard feature lives
  at `dashboard/src/lib/modules/<feature>/` with entities, an interactor, a presenter, a
  router, and a view. Composition roots are only `kllm.agent`, `kllm.cli`, and dashboard
  routes. `dashboard/src/lib/app.svelte.ts` builds the interactors those routes render; it
  is not a feature module.
- Patch surfaces live on the owning module. Tests patch the module that defines the name.
  Do not add a re-export to preserve an old patch path. Facades are kept only for these
  external entry points: `kllm.agent:app`, `kllm.agent.create_app`, `kllm.agent.STATE`,
  `kllm.agent.legacy_v1_router`, `kllm.cli:main`, `kllm.agent.__main__:main`,
  `python -m kllm`, `python -m kllm.cluster_update`, `kllm.log_sink.PolicyFileHandler`,
  the `kllm.models.*` facade, `kllm.native`, `kllm.runtimes.build.*`, `kllm.agent_skills`,
  `kllm.managed_network.platform.inspection`, `kllm._native`, `kllm.__version__`, and
  `scripts/install.py` (not moved).
- No interactor imports `kllm.agent`. `STATE` stays on `kllm.agent` and is passed into
  interactor constructors.
- Keep imports at module top, except for a documented import cycle, an optional module, or a
  test reload. Do not treat platform-gated (`cfg`/`sys.platform`) functions as dead code.
- Reuse existing helpers, Pydantic request models, JSON cluster state, and error contracts.
  New abstractions and dependencies must solve a current requirement with a second caller or
  a recorded justification.
- Keep native/platform trust boundaries explicit. No arbitrary subprocess or elevation
  escape hatches.

## Tests and verification

- Tests assert observable behavior or public contracts, not implementation details.
- Bug fixes need a regression check that fails before and passes after the fix.
- Tests are deterministic and isolated: temporary state, controlled external dependencies;
  no real credentials, public services, GPUs, or live clusters by default.
- Patch names on the owning module, including string patch targets. The external entry
  points listed under Module shape keep their facades. Shared contracts need boundary
  checks; mocked unit tests alone do not prove node communication.
- Platform-specific behavior is isolated and checked for the affected supported platforms.
  Hardware-dependent checks state their OS/backend prerequisites and stay explicitly
  unverified when hardware is unavailable.

## Documentation

- Interface and operational changes update the relevant existing docs in the same change:
  `docs/cli.md`, `docs/control-api-v2.md`, `docs/troubleshooting.md`, `docs/agent-install.md`,
  `docs/engine-decision.md`, `README.md`.
- Commands in documentation must match the implemented interface.
- Comments explain non-obvious constraints or decisions; deliberate shortcuts record the
  known limit and the condition that warrants replacing them.
