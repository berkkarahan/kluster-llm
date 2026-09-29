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

- Prefer editing existing modules under `python/kllm/` and `crates/kllm-native/`; keep changes scoped.
- RPC is unauthenticated upstream — never recommend exposing port **50052** publicly.
- Python is managed with **uv** (`.python-version`, `uv.lock`). After packaging changes, rebuild with `uv run python scripts/install.py` or `uv run python scripts/build.py` (maturin), not plain `pip install -e .` alone.
- llama.cpp is pinned in committed `llama-release` to a **stable** GitHub tag (e.g. `v0.5.0`, not `bNNNN` nightlies). Sync binaries on every node with `python scripts/install.py --llama-only`; mixed tags break RPC.
