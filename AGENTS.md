# AGENTS.md

Instructions for LLM coding agents working in this repository.

## Install / bootstrap (read first)

Full machine setup (toolchains, `kllm`, llama.cpp `llama-server` + `rpc-server`, WireGuard, dashboard) is documented for agents here:

**→ [docs/agent-install.md](docs/agent-install.md)**

Primary command:

```bash
python scripts/install.py
```

Do not invent alternate install flows until that guide’s recovery tree is exhausted.

## Other docs

| Doc | Use when |
|-----|----------|
| [docs/cli.md](docs/cli.md) | `kllm` command reference |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Runtime / network failures |
| [docs/engine-decision.md](docs/engine-decision.md) | Why llama.cpp RPC; security posture |
| [README.md](README.md) | Human project overview |

### Spec Kit

You MUST follow the memory-first workflow defined in `.specify/memory/workflow.md`. Before planning, prepare context using the best available path: MCP tools if configured, or `/speckit.memory-md.prepare-context` if Spec Kit commands are available.

## Conventions

- Python application code uses the `src/kllm/` package; native code remains under `crates/kllm-native/`. Keep changes scoped to those boundaries.
- Structural coding standards for specs, plans, and refactors: [`.specify/memory/architecture_constitution.md`](.specify/memory/architecture_constitution.md).
- RPC is unauthenticated upstream — never recommend exposing port **50052** publicly.
- Python is managed with **uv** (`.python-version`, `uv.lock`). After packaging changes, rebuild with `uv run python scripts/install.py` or `uv run python scripts/build.py` (maturin), not plain `pip install -e .` alone.
- `llama-release` is the **stable** bootstrap default. Settings → llama.cpp supports explicit managed upstream/fork snapshots pinned to repository + commit. The master enforces one source identity for every run participant; backend builds may differ by node. Bootstrap installs still use `python scripts/install.py --llama-only`; managed selections use the isolated runtime library.
