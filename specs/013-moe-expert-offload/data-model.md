# Data Model: MoE Expert Offload

## 1. MoE model layout (extension of existing `ModelLayout`)

The existing layout already carries per-layer byte totals and architecture metadata. This feature adds an expert-aware split.

- **Shared portion per layer** (`shared_cost`): attention weights plus gate/routed components that must stay with the layer on the master.
- **Expert portion per layer** (`expert_cost`): the sum of `ffn_up`, `ffn_down`, `ffn_gate` (and `ffn_gate_inp`) expert tensor bytes for that layer.
- **Expert count** (`expert_count`): number of routed experts per layer, derived from tensor naming or `{arch}.expert_count`.

Validation: expert costs must be non-negative and sum (with shared costs and input/output bytes) to the model's total weight bytes, matching the existing `weights_bytes` invariant.

## 2. Run setting: MoE offload option

Field added to `RunSettingsFields` and its wire format (`normalized_auto_fields`):

- `moe_offload`: `"auto"` (default) or a non-negative integer `N` (the number of layers whose expert weights are offloaded to remote nodes, matching llama.cpp's per-layer expert-offload semantics).

Validation rules:
- `"auto"` → heuristic decides strategy and count.
- integer `n ≥ 0` → explicit; `n = 0` disables expert offload (equivalent to current behavior).
- negative or non-integer → validation error (Constitution IV).

## 3. Batch / micro-batch (revised ceilings)

- `batch_size`: `"auto"` (resolves to 512) or positive integer. Effective value `min(requested, max(2048, context))`.
- `micro_batch`: `"auto"` (resolves to 128) or positive integer. Effective value `min(requested, batch_size, 512)`.

Invariants preserved: `micro_batch ≤ batch_size`; defaults unchanged (512 / 128); non-positive values produce a clear error, while above-ceiling values are capped.

## 4. Plan result additions

`DevicePlacementPlan` gains fields to report expert distribution without breaking existing consumers:

- `expert_offloaded: bool` — whether expert offload participated.
- `expert_strategy: "none" | "experts" | "layers" | "mix"` — the strategy the heuristic (or override) chose.
- `expert_offload_count: int | None` — number of expert groups/layers offloaded.
- Per-node expert allocation, folded into the existing `device_allocations` / `node_vram_alloc_bytes` / `node_ram_alloc_bytes` so `estimated_bytes` and `fit_status` remain consistent.

## 5. Launch record additions

`build_master_launch` output gains planner-owned argument entries (not user `extra_args`):

- `tensor_overrides`: list of `-ot` patterns mapping expert tensors to `RPC[...]`/CPU devices.
- `n_cpu_moe`: host-CPU expert count (or `null` when unused).

## 6. Entity relationships

- A **MoE model** has many **layers**; each layer has one **shared portion** and one **expert portion**.
- An **offload placement** maps expert portions (or whole layers, under the `layers` strategy) to **nodes**, preferring each node's GPU memory over its system RAM.
- **Run settings** (including `moe_offload`, `batch_size`, `micro_batch`) resolve to a single **launch record** consumed by the native process adapter.
