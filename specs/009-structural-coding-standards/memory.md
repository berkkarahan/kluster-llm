# Feature Memory - 009-structural-coding-standards

## Scope Notes

- Structural rules for later specifications live in `.specify/memory/architecture_constitution.md`.
- This feature extracts helpers from the agent, installer, CLI, and native planner without changing external behavior.

## Relevant Durable Memory

- A1 structural standards.
- D1 facade patch surface for `kllm.agent` and `scripts/install.py`.
- Project constitution 1.1.0 requires the architecture constitution.

## Open Questions

- None.

## Watchlist

- Do not retarget `.specify/feature.json` away from `specs/008-add-10gbe-link`.
- Do not hoist imports inside tests. Those imports reload modules after a patch.
- Do not move a name that tests patch off `kllm.agent` or the loaded `scripts/install.py` module.
- Leave `kllm._dbg_a1ff77` imports inside the hardware-debug flag. The module is optional.

## Never Store Here

- permanent project decisions
- general bug patterns unless they are directly reused
- implementation history after the feature ships
