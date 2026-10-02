# Memory Synthesis

## Current Scope

Add a first-class option to offload a MoE model's expert weights (then whole layers if needed) to remote cluster nodes when the model cannot fit in GPU memory, available in automatic, manual node-share, and partial GPU-offload paths. Raise the configurable batch-size ceiling (to 2048) and micro-batch ceiling (to 512) while keeping defaults at 512 / 128. Affected modules: `src/kllm/core/` (placement, memory estimation, placement models), `src/kllm/run_settings_resolution.py`, `src/kllm/run_launch.py`, `src/kllm/gguf_meta.py`, `src/kllm/extra_args.py`, `dashboard/src/routes/run/+page.svelte`, and `crates/kllm-native/` if expert cost modeling moves to the native planner.

## Relevant Decisions

- Keep test patch surfaces on facade modules (`kllm.agent`, `scripts/install.py` module) (Reason Included: architecture constitution patch-surface rule, Status: active, Source: decisions/2026-09-29-facade-patch-surface.md)
- Structural coding standards: no speculative abstractions; split long functions into same-module helpers; API/request models stay Pydantic, cluster state stays JSON (Reason Included: architecture constitution, Status: active, Source: architecture/2026-09-29-structural-standards.md)

## Active Architecture Constraints

- Responsibilities stay in `python/kllm/` (CLI/agent), `crates/kllm-native/` (native runtime), `dashboard/` (UI) (Source: constitution.md)
- llama.cpp pinned to stable `llama-release` tag `v0.5.0`; pin changes must document node sync via `python scripts/install.py --llama-only` (Source: constitution.md, AGENTS.md)
- RPC upstream is unauthenticated; port 50052 must stay private (Source: constitution.md)

## Accepted Deviations

- None recorded for this feature.

## Relevant Security Constraints

- Extra args must stay sanitized; kllm-managed flags (`--n-cpu-moe`, `-ot`, `-ngl`, etc.) cannot be overridden via `extra_args` — the new option must be a first-class setting, not an unblocked override (Source: extra_args.py BLOCKED_FLAGS).

## Related Historical Lessons

- `memory_estimation.py` already recognizes MoE architectures (`qwen2moe`, `qwen3moe`, `qwen35moe`) but models each layer as one contiguous cost unit — no expert-weight split yet.

## Conflict Warnings

- Soft: `--n-cpu-moe`/`-cmoe` offload MoE experts to the *host* CPU, not remote RPC nodes; remote expert placement requires tensor overrides (`-ot 'blk.N.ffn_*=RPC[endpoint]'`). The pinned `v0.5.0` support for `-ot` with RPC device patterns must be verified during implementation; fallback is whole-layer remote placement.

## Retrieval Notes

- Considered: constitution.md, architecture_constitution.md, docs/memory/INDEX.md, PROJECT_CONTEXT.md, decisions/2026-09-29-facade-patch-surface.md. MCP memory tools unavailable; used markdown-first fallback. Budget respected (no full durable-memory read).
