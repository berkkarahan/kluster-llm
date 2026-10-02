# Research: MoE Expert Offload to Scattered Nodes

## 1. How llama.cpp offloads MoE experts

**Decision**: Model MoE expert weights as separately-priced units and distribute them across remote nodes using llama.cpp tensor overrides, with `--n-cpu-moe` as the host-CPU spill fallback.

**Rationale**: llama.cpp exposes three relevant mechanisms on the pinned `v0.5.0` line:
- `--cpu-moe` / `-cmoe` — keep all MoE expert weights on the host CPU.
- `--n-cpu-moe N` / `-ncmoe` — keep the MoE expert weights of the first `N` layers on the host CPU (implemented as generated tensor overrides).
- `--override-tensor` / `-ot 'pattern=device'` — place specific tensors on a named device, including remote `RPC[host:port]` devices (confirmed to work in `llama-server`; performance is interconnect-bound).

None of these offload experts to *remote* nodes except `-ot` with an `RPC[...]` device pattern. `--n-cpu-moe` targets only the master host CPU. Therefore "scatter experts across nodes" requires emitting `-ot` tensor overrides that assign expert tensors (`blk.N.ffn_gate*`, `blk.N.ffn_up*`, `blk.N.ffn_down*`, `blk.N.ffn_gate_inp*`) to specific RPC devices, while shared attention tensors stay on the master GPU. `--n-cpu-moe` covers any experts kept on the master CPU.

**Alternatives considered**:
- Unblock `--n-cpu-moe`/`-ot` in `extra_args.py` — rejected: violates the sanitization/override boundary (Constitution IV) and gives no memory-aware placement.
- Whole-layer remote placement only — rejected as the sole mechanism because it does not uniquely help MoE (keeps attention + experts together) and is already available; retained as fallback when a node cannot take expert-only tensors.

## 2. Detecting MoE expert sizes from GGUF metadata

**Decision**: Extend `gguf_meta.py` to recognize MoE architectures (already detected as `qwen2moe`, `qwen3moe`, `qwen35moe`, and dense-vs-expert FFN tensor names) and bucket per-layer bytes into a shared portion (attention + gate + shared/routed expert) and expert portions (`ffn_up`/`ffn_down`/`ffn_gate` per expert).

**Rationale**: `read_model_layout` already walks `blk.N.*` tensors into per-layer byte totals. Splitting the expert tensors out per layer is a small, same-module extension. The number of experts is available from tensor naming (`ffn_up.1.weight`, …) and/or `{arch}.expert_count` metadata.

**Alternatives considered**: Add expert counts to the native GGUF parser — rejected as redundant until the native planner needs the split; Python already reads tensors.

## 3. Cost model and placement

**Decision**: In `memory_estimation.py`, produce per-layer `shared_cost` and `expert_cost` so the planner can allocate experts independently. In `placement.py`, after the existing GPU/local/single-remote/pool attempt fails for a MoE model, attempt expert-level distribution: place shared portions on the master GPU and expert portions across remote nodes (GPU memory first, then system RAM), then fall back to whole layers, then CPU spill, then overflow.

**Rationale**: This directly implements FR-002 (experts first, then whole layers), FR-002b (remote GPU then remote RAM), and the adaptive "automatic" strategy. It reuses the existing contiguous-allocation machinery by treating expert groups as additional cost units.

**Alternatives considered**: A separate allocation pass isolated from `plan_devices` — rejected as duplication; the existing pass already yields per-node allocations, routing mode, and fit status that the plan report must preserve (FR-006).

## 4. Offload option surface

**Decision**: Add a single run-setting field (e.g. `moe_offload`) with value `"auto"` (default) or a non-negative integer (explicit count of expert groups/layers to offload). Resolve in `run_settings_resolution.py`, expose in CLI (`--moe-offload`), API body, and dashboard. Emit the resulting llama.cpp flags from `run_launch.py` (`-ot` patterns for remote experts, `--n-cpu-moe` for host-CPU experts).

**Rationale**: Mirrors the existing `AutoInt` pattern (`parallel_slots`, `batch_size`, `checkpoints`) used across the codebase, satisfying FR-002a (automatic default + explicit count override) and Constitution IV (validated, compatible field).

**Alternatives considered**: Boolean toggle only — rejected; the clarified requirement specifies an explicit count override. Separate "strategy" enum — rejected as speculative; the heuristic covers strategy selection.

## 5. Batch and micro-batch ceilings

**Decision**: Keep defaults (`batch_size` 512, `micro_batch` 128). Raise the batch ceiling floor from 512 to 2048 and introduce a 512 micro-batch ceiling, preserving the context coupling so no currently-valid configuration is rejected.

**Rationale**: Upstream llama.cpp defaults are `--batch-size` 2048 and `--ubatch-size` 512. Current kllm resolution is `batch = min(requested, max(512, context))` and `micro = min(requested, batch)`. Raising the batch floor to 2048 (`min(requested, max(2048, context))`) lets operators request larger logical batches at moderate contexts without breaking larger-context batches. Capping micro-batch at 512 aligns with the upstream physical maximum while keeping the `micro ≤ batch` invariant.

**Alternatives considered**:
- Hard-reject values above 2048/512 — rejected: would reject currently-valid large-context batch sizes (backward incompatibility).
- Leave ceilings untouched — rejected: does not satisfy the request.

## 6. Launch-argument emission and sanitization

**Decision**: `run_launch.py` emits `--override-tensor` (`-ot`) patterns for remote expert placement and `--n-cpu-moe` for host-CPU experts, computed from the plan result. `extra_args.py` keeps `-ot`, `--n-cpu-moe`, `--cpu-moe`, `-ngl`, etc. blocked for user override — these become first-class, planner-owned outputs.

**Rationale**: Prevents users from bypassing the memory-aware planner while letting the planner itself use the flags. Preserves the sanitization boundary.

## 7. Open verification item (flagged for implementation)

**Verification needed**: Confirm the pinned `llama-release` build (`v0.5.0`) accepts `-ot 'blk.N.ffn_*=RPC[endpoint]'` patterns and `--n-cpu-moe`, and that remote expert tensors actually land on the target RPC node's memory (GPU vs RAM). If `-ot` RPC patterns are unsupported on the pinned build, fall back to whole-layer remote placement and document the limitation. This is a hardware/integration check (Constitution III) that must not be reported as passed without evidence.
