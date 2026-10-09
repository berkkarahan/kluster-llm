# Contract: MoE Expert Offload Option

## Field

- **Name**: `moe_offload`
- **Type**: `"auto" | integer ≥ 0`
- **Default**: `"auto"`
- **Surfaces**: CLI `--moe-offload VALUE`, API request body `moe_offload`, dashboard Run panel, `~/.kllm` remembered settings.

## Semantics

| Value | Behavior |
|-------|----------|
| `"auto"` (default) | Heuristic chooses strategy (`experts` → `layers` → `mix`) and count per model/fit |
| `0` | Offload disabled; identical to current behavior |
| `n > 0` | Offload the expert weights of the first `n` layers off the master GPU |

## Resolution order

1. Explicit request value (CLI/API/dashboard) wins.
2. Otherwise `"auto"` heuristic.

## Placement contract (applies to MoE architectures only)

1. Shared/attention weights stay on the master's fast local memory.
2. Expert weights are distributed to remote nodes — **GPU memory first, then system RAM**.
3. Whole layers are offloaded only if expert-only placement is still over capacity.
4. Non-MoE architectures: the option has no effect and no error.

## Reporting contract

A plan/estimate MUST report, in addition to existing fields:
- `expert_offloaded` (bool)
- `expert_strategy` (`none | experts | layers | mix`)
- `expert_offload_count` (int | null)

Per-node allocation totals (`node_vram_alloc_bytes`, `node_ram_alloc_bytes`, `estimated_bytes`) MUST include offloaded expert bytes so `fit_status` and the reported plan match the eventual launch.

## Failure contract

- Model still overflows after expert offload → actionable `fit_status=overflow` with a hint naming the shortfall; `run` is blocked (exit 1).
- Remote nodes lack expert-receiving capability → degrade to current behavior and report why offload was not used.

## Sanitization contract

`--n-cpu-moe`, `--cpu-moe`, `-cmoe`, `--n-cpu-ffn`, `-ncffn`, `--override-tensor`, `-ot` remain blocked in user `extra_args`. The planner emits these flags itself from `moe_offload` resolution.
