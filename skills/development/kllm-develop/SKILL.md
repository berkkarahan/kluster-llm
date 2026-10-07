---
name: kllm-develop
description: "Implement or fix Kluster LLM repository code using its architecture, memory, and verification conventions. Use for Python/Rust/CLI/backend development, not unrelated projects or cluster operation alone."
---

# Develop Kluster LLM

Locate the checkout using `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`. Verify it contains `src/kllm`, `crates/kllm-native`, and `pyproject.toml`; do not apply these rules to an unrelated working repository.

1. Read `AGENTS.md` and `docs/agent-install.md`; inspect git status and preserve unrelated changes. Identify the requested behavior, public contracts, callers, and acceptance checks before editing.
2. Prepare project memory using configured Spec Kit memory MCP tools or the available prepare-context command. These may write synthesis files; in a read-only planning/review task use existing synthesis and the markdown fallback instead. Review `docs/memory/INDEX.md`, relevant synthesis, and available governance. Report missing referenced workflow/architecture files rather than inventing them or declaring them read. Do not capture or publish durable memory without the user's authorization.
3. If `.codegraph/` exists, use `codegraph_explore` or `codegraph explore` before grep/file reads for code discovery and affected call paths. Use its current source as the read; fall back for unindexed docs/configs, missing tools, or explicitly stale sections. Do not index a repository on the user's behalf.
4. Keep Python application code in `src/kllm`, native code in `crates/kllm-native`, and Svelte UI in `dashboard`. Respect current boundaries and public facades: tests patch names on `kllm.agent` and `scripts/install.py`; extracted helpers receive patched dependencies explicitly.
5. Implement the smallest complete change. Reuse current request models, state persistence, helpers and error contracts. Keep native/platform trust boundaries explicit and avoid arbitrary subprocess/elevation escape hatches. Preserve runtime identities, lifecycle ownership and source provenance when those flows are affected.
6. Add behavior-focused regression coverage for non-trivial logic. Use `kllm-test`; use `kllm-dashboard` for UI changes. Do not write tests that merely match instruction wording or reproduce an implementation.
7. Synchronize the relevant existing docs with interface or operational changes. After packaging/native changes rebuild through the supported tooling and verify import/native loading. Do not claim hardware or distributed validation from mocked tests.
8. Review the intended diff and `git diff --check`. Commit/push only when requested and keep unrelated changes out of the delivery.

Report what changed and why, verification results, compatibility impact, and unresolved acceptance boundaries. A plan/spec/task workflow does not itself authorize deployment or changes outside the task.
