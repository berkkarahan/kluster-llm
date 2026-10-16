# Placement, inference, and transfers

Every node must use llama.cpp **v0.5.0** (stable pin in `llama-release`). Use each node's installed accelerator:
Metal on Apple Silicon, CUDA on supported NVIDIA installations, ROCm on supported
Linux AMD installations, and Vulkan where native support is unavailable. RPC layer
splitting supports mixed backends. Keep RPC port 50052 on a trusted private LAN or the dedicated fast Ethernet network.

`KLLM_ACCEL_BACKEND` and the saved backend selection express a preference. Status
reports that request separately from the backend and device names returned by the
installed `llama-server --list-devices`. Selecting another backend requires installing
a compatible binary; an override cannot change a Metal binary into Vulkan.

Plan resolves launch arguments before calculating memory. Automatic settings are one
slot, batch 512, micro-batch 128 (capped by batch), and Flash Attention `auto`. Saved
explicit values take precedence, including micro-batch 512 and attention `on`.
Automatic checkpoints and host prompt-cache limits resolve to the pin's defaults
(32 checkpoints and 8192 MiB), instead of passing an unlimited `-1` for host cache.
Overlapping advanced batch/context/cache arguments are normalized before both stages.
Device, layer-split, model, and tensor-placement overrides are rejected because they
would invalidate the plan.

## Memory-pooling routing

GGUF metadata provides the block count, tensor spans, attention dimensions and Qwen
hybrid recurrent state dimensions. Input tensors and the CPU prefix belong to the
master; workers can also host layer segments on explicitly advertised RPC CPU
devices. The output layer is included in upstream offload counts.

Routing policy (`src/kllm/tensor_split.py` + `placement.py`):

1. Prefer **master-local GPUs** when they can hold the model without CPU spill.
2. Else prefer a **single remote GPU** that alone fits.
3. Else **pool GPUs** across nodes with contiguous allocation.
4. If GPU placement cannot fit or satisfy required nodes, use master/worker CPU RAM,
   minimizing CPU bytes, RPC endpoints, then favoring the existing device scores.

`required_nodes` (repeatable `--require-node NODE`) overrides local-only shortcuts:
each listed node must host at least one model layer. The planner chooses appropriate
shares; it never forces equal allocations or ignores an infeasible requirement.

`--tensor-split` values are layer counts from that allocation (with a tiny boundary
bias). Unified RAM is one shared pool on Apple Silicon and on other detected
unified-memory devices (for example AMD Strix Halo). Planning uses 65% of that
node's total RAM as effective VRAM (`KLLM_UNIFIED_VRAM_FRACTION`), then applies
the usual VRAM percent. Reported macOS VRAM remains 80% of RAM and is not the
planning budget.
Measured RSS of the tracked process
may be reclaimed when replacing it. Discrete GPU memory without an attributable
allocation measurement is conservatively treated as occupied. Execution buffers and
unknown architectures remain estimates, labeled in plan results.

Workers must publish device inventory before a new placement can be launched.
Updated workers expose selected GPUs followed by CPU in an explicit runtime order.
`rpc_devices` tracks that full order so skipping a CPU device does not shift another
worker's RPC index. Rebuild and rejoin workers to enable RAM pooling; older workers
remain accelerator-only. CPU-mode plans can pool advertised worker CPU devices.
Control RTT uses pooled HTTP connections; it measures control-plane responsiveness,
not RPC throughput or GPU performance.

Inference forwards directly to llama-server using an application-owned connection
pool. Connection failures return 503 before stream headers. Disconnects close the
upstream response. Set `KLLM_DEBUG_HARDWARE=1` before starting the agent to retain
the existing diagnostic sampling in a background task; it is off by default.

## Models and RPC cache

Inference needs the GGUF on the **master** only. Workers do not receive a physical
`.gguf` copy. `rpc-server -c` retains its tensor cache across restarts. Status reports
`rpc_cache` including the actual platform default location (or `LLAMA_CACHE/rpc`).
Master downloads land in `~/.kllm/models/` (that directory may also cache llama.cpp
**release archives** for install — not GGUF sync).

## Rollout and comparison

Build with `uv run python scripts/build.py`, then restart agents. On each node,
`python scripts/install.py --llama-only` when the `llama-release` pin changes.
Run Plan before Run, inspect `routing_mode`, device ranges, and estimate confidence,
then compare actual llama-server placement logs with the plan. Do not accept an
overflowing plan.

Pinned upstream references: [server arguments](https://github.com/ggml-org/llama.cpp/blob/v0.5.0/tools/server/README.md),
[device assignment](https://github.com/ggml-org/llama.cpp/blob/v0.5.0/src/llama-model.cpp),
[RPC registration/cache](https://github.com/ggml-org/llama.cpp/blob/v0.5.0/ggml/src/ggml-rpc/ggml-rpc.cpp).

Design write-up: [plans/rpc-memory-pooling.md](../plans/rpc-memory-pooling.md).
