# Feature Memory - 016-viper-architecture

## Scope Notes

- One behavior-preserving move of `src/kllm/` and `dashboard/src/` into per-feature modules.
- Governance for the layout is constitution 1.3.0 and `.specify/memory/architecture_constitution.md`.
- This file is scratch context for the feature. Durable rules are D3 and A2.

## Relevant Durable Memory

- A1 structural standards. Still in force for helpers-as-arguments, import placement, Pydantic models, and JSON cluster state. Its facade tradeoff is not the current patch rule.
- A2 feature module layout.
- D1 facade patch surface. Superseded on 2026-10-09 by D3. Do not apply D1 to moved names.
- D3 owning-module patch surface. Facades only for the FR-006 entry points.
- D2 MoE expert offload. Unrelated to this move; placement code moves with `modules/inference` without a behavior change.
- `PROJECT_CONTEXT.md` is an unfilled scaffold. It added no product facts.

## Open Questions

- None for the layout. The `python/kllm/` sentence in `constitution.md` is a known stale path and was left out of the 1.3.0 text on purpose.

## Watchlist

- Do not add an internal compatibility facade when a patch site is missed. Fix the patch site.
- Reload home and paths through `kllm.shared.state` only.
- No interactor imports `kllm.agent`. `STATE` is passed in and stays on `kllm.agent`.
- `scripts/install.py` is not moved. Installer patches stay on the loaded script module.
- `crates/kllm-native/` and `src/kllm/templates/` stay put.
- T018 names module paths in `AGENTS.md`, the README layout, and the develop/dashboard skills.
- `docs/cli.md` and `docs/control-api-v2.md` need no behavior edits.
- Dissolving `agent/__init__.py` must keep lifespan, threads, background stop, and membership maintenance.

## Never Store Here

- permanent project decisions
- general bug patterns unless they are directly reused
- implementation history after the feature ships
