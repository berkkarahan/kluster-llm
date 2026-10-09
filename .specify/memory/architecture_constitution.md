# Architecture Constitution (Structural Coding Standard)

This file is the structural coding standard referenced by `constitution.md` and `AGENTS.md`.
It defines how modules, imports, helpers, and test patch surfaces are shaped. It adds no
product behavior; `constitution.md` holds the principles and quality gates.

## Repository boundaries

- Python application code lives in `src/kllm/` (CLI, agent, services, runtimes,
  managed network). Do not move code back into a top-level `python/` package.
- Native code lives in `crates/kllm-native/`. Its public surface is `src/lib.rs`.
- Dashboard code lives in `dashboard/` (Svelte 5 runes + TypeScript).
- Installer and bootstrap scripts stay in `scripts/`; `scripts/install.py` is a public
  entry point.
- Public entry points stay where they are: `scripts/install.py`, `kllm.agent:app`, and the
  native exports in `crates/kllm-native/src/lib.rs`.

## Module shape

- Split long functions into helpers that take their dependencies as arguments. Keep helpers
  in the module that owns the behavior.
- Extracted services receive patched dependencies explicitly through their constructors;
  tests patch names on façade modules (`kllm.agent`, `scripts/install.py`), so a move of a
  patched function requires a façade that still exposes the old name.
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
- Keep test patch surfaces on façades. Shared contracts need boundary checks; mocked unit
  tests alone do not prove node communication.
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
