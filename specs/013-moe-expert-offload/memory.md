# Feature Memory: MoE Expert Offload to Scattered Nodes

Feature-local notes for `specs/013-moe-expert-offload`. Not durable project memory.

## Constraints inherited from governance

- RPC upstream is unauthenticated; port 50052 must stay on private LAN/WireGuard (Constitution).
- Placement/estimation logic lives in `src/kllm/core/placement.py` and `src/kllm/core/memory_estimation.py`; run normalization in `src/kllm/run_settings_resolution.py`. Keep changes in these boundaries.
- llama.cpp is pinned to a stable tag (v0.5.0 era); new flags must match what that build supports.

## Relevant current behavior

- MoE architectures are recognized in `memory_estimation.py` (`qwen2moe`, `qwen3moe`, `qwen35moe`, `deepseek2/3` MLA, etc.) but expert weights are NOT split separately from whole layers; each transformer layer is one contiguous unit.
- `extra_args.py` currently BLOCKS `--cpu-moe`/`-cmoe`/`--n-cpu-moe`/`-ncmoe`/`--n-cpu-ffn`/`-ncffn`, so upstream MoE expert offload cannot be reached via extra args. The feature must expose a first-class option instead.
- Batch/micro-batch resolution in `run_settings_resolution.py`:
  - defaults `batch_size=512`, `micro_batch=128` (when `"auto"`).
  - `batch_size = min(batch, max(512, context))`, `micro_batch = min(micro_batch, batch)`.
  - Dashboard inputs cap batch at `max=4096` and micro-batch at `max=batch`.
- llama.cpp upstream defaults: `--batch-size` 2048, `--ubatch-size` 512.

## Open questions / watchpoints

- Confirm the exact llama.cpp flag semantics on the pinned tag for expert-only offload vs whole-layer `ngl` interaction.
- Confirm how "partial/manual" offload should combine expert placement with `node_shares`; the spec assumes expert weights follow the operator's share intent.
- MoE expert weights may need a separate cost model (expert count × expert size) rather than the existing per-layer cost list.

## Notes

- Hardware validation (multi-node MoE inference) must be run before claiming full validation; deterministic local checks cover the contract.
