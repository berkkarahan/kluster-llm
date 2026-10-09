### 2026-10-02 - MoE expert offload targets remote RPC via tensor overrides

**Status**
Active

**ID**
D2

**Why this is durable**
llama.cpp's `--n-cpu-moe`/`--cpu-moe` offload MoE expert weights to the *host* CPU only; they do not reach remote RPC nodes. Scattering experts across nodes requires `--override-tensor`/`-ot` patterns like `blk.N.ffn_*=RPC[host:port]`. This distinction is non-obvious and affects any future distributed-MoE work.

**What future mistake this prevents**
Emitting `--n-cpu-moe` to "offload to workers" and silently getting host-CPU spill, or unblocking planner-owned MoE flags as user `extra_args` instead of emitting them from the planner.

**Evidence**
`src/kllm/core/placement.py` (`_expert_offload`, `_expert_offload_manual`) emit `-ot` patterns; `src/kllm/run_launch.py` appends `--override-tensor`; `src/kllm/extra_args.py` still blocks `--n-cpu-moe`/`-ot`; specs/013-moe-expert-offload/research.md §1 and §7.

**Where to look next**
`src/kllm/core/placement.py`, `src/kllm/run_launch.py`, specs/013-moe-expert-offload/research.md

**Decision**
MoE expert weights are modeled as per-layer expert costs and placed on remote nodes (GPU first, then RAM) using `-ot` tensor overrides; `--n-cpu-moe` is reserved for host-CPU spill. The strategy defaults to a heuristic ("auto") with an explicit count override.

**Tradeoffs**
- Gained: experts and shared attention can live on different nodes, so large MoE models fit past a single node's VRAM.
- Made harder: byte-accurate within-layer expert splitting is not implemented (each layer's experts are assigned atomically to one node).
- Reconsider: if a workload needs a single layer's experts spread across nodes, add per-expert cost units to the allocation DP.
