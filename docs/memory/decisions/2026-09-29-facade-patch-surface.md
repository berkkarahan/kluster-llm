### 2026-09-29 - Facade patch surface

**Status**
Active

**ID**
D1

**Why this is durable**
Agent and installer tests replace names on the module they import. Moving those names into a sibling makes the patch hit a different global, so the test no longer locks the behavior.

**What future mistake this prevents**
Moving `STATE`, route helpers, or installer functions such as `_download_once` and `_run` off `kllm.agent` or the loaded `scripts/install.py` module, which also breaks `kllm.agent:app`.

**Evidence**
`tests/test_install_script.py` loads `scripts/install.py` and patches its attributes. Agent tests assign `kllm.agent.STATE` and patch names on that module.

**Where to look next**
`.specify/memory/architecture_constitution.md` (Patch surface)

**Decision**
Refactors of the agent and the installer keep patched names on the module tests already import. Siblings receive those callables and values as arguments.

**Tradeoffs**
- Gained: existing monkeypatches stay valid while helpers move.
- Made harder: siblings cannot close over the facade globals.
- Reconsider: if tests stop patching module globals and use public seams only, the facade can shrink.
