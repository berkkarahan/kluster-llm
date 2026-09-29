### 2026-09-29 - Structural coding standards

**Status**
Active

**ID**
A1

**Why this is durable**
Every later specification is planned against the governance layer. These rules are the shape of Python, Rust, and installer changes, not a one-time refactor.

**What future mistake this prevents**
A pattern-driven rewrite, a new data library, or an import hoist that breaks a cycle or a test that reloads a module after patching.

**Evidence**
Behavior-preserving extraction in `specs/009-structural-coding-standards`, required by constitution 1.1.0.

**Where to look next**
`.specify/memory/architecture_constitution.md`

**Decision**
Split long functions into helpers that take arguments. Keep Pydantic request models and the current JSON cluster state. Keep imports at module top except for a documented cycle, an optional module, or a test reload. Do not treat `cfg`-gated platform functions as dead code. Leave public entry points (`scripts/install.py`, `kllm.agent:app`, native exports in `crates/kllm-native/src/lib.rs`) where they are.

**Tradeoffs**
- Gained: later plans have one structural standard.
- Made harder: a large move of a patched function requires a facade that still exposes the old name.
- Reconsider: if a second caller needs a shared pattern, add that type then and record why.
