### 2026-10-09 - Owning-module patch surface

**Status**
Active

**ID**
D3

**Why this is durable**
Tests replace names on the module they import. After a feature move, that module is the one that defines the name. A re-export kept for the old path patches a different global.

**What future mistake this prevents**
Adding a compatibility facade so `patch("kllm.membership...")` or `patch("kllm.state.repo_root")` can stay unchanged, or importing `kllm.agent` from an interactor to reach `STATE`.

**Evidence**
`specs/016-viper-architecture`. Constitution 1.3.0. `.specify/memory/architecture_constitution.md` Module shape. Supersedes D1 (`2026-09-29-facade-patch-surface.md`).

**Where to look next**
`docs/memory/architecture/2026-10-09-viper-modules.md` (A2)

**Decision**
Patch surfaces live on the owning module, including string targets. Interactors take dependencies as constructor arguments and do not import `kllm.agent`.

Facades stay only for external entry points: `kllm.agent:app`, `kllm.agent.create_app`, `kllm.agent.STATE`, `kllm.agent.legacy_v1_router`, `kllm.cli:main`, `kllm.agent.__main__:main`, `python -m kllm`, `python -m kllm.cluster_update`, `kllm.log_sink.PolicyFileHandler`, the `kllm.models.*` facade, `kllm.native`, `kllm.runtimes.build.*`, `kllm.agent_skills`, `kllm.managed_network.platform.inspection`, `kllm._native`, `kllm.__version__`, and `scripts/install.py` (not moved). `STATE` remains on `kllm.agent` and is passed in.

**Tradeoffs**
- Gained: one patch path per name, on the module that runs.
- Made harder: existing patch sites are rewritten in the same change. There is no shim for a missed path.
- Reconsider: if an entry point on the list is removed, delete its facade in that change.
