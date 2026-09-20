# Placement, inference, and transfers

Every node must use llama.cpp **b11026**. Use each node's installed accelerator:
Metal on Apple Silicon, CUDA on supported NVIDIA installations, ROCm on supported
Linux AMD installations, and Vulkan where native support is unavailable. RPC layer
splitting supports mixed backends. Keep RPC port 50052 private to LAN/WireGuard.

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

GGUF metadata provides the block count, tensor spans, attention dimensions and Qwen
hybrid recurrent state dimensions. Input tensors and CPU spill belong to the master;
the output layer is included in GPU-layer counts. Device order, contiguous layer
ranges, split values, and launch arguments are generated together. Unified RAM is a
shared pool, independently of backend names. Measured RSS of the tracked process may
be reclaimed when replacing it; unrelated RAM use is unavailable. Discrete GPU memory
without an attributable allocation measurement is conservatively treated as occupied.
Execution buffers and unknown architectures remain estimates, labeled in plan results.

Workers must publish the new device inventory before a new placement can be launched.
Legacy workers may keep serving an already running model while they await an update.
Control RTT uses pooled HTTP connections; it measures control-plane responsiveness,
not RPC throughput or GPU performance.

Inference forwards directly to llama-server using an application-owned connection
pool. Connection failures return 503 before stream headers. Disconnects close the
upstream response. Set `KLLM_DEBUG_HARDWARE=1` before starting the agent to retain
the existing diagnostic sampling in a background task; it is off by default.

Distribution uses a cached manifest with source identity, whole-file SHA-256 and
8 MiB piece hashes. Identity includes size, nanosecond timestamps and file identity.
Changes invalidate the cache, even with the same name and size. Worker partial files
have an atomically saved `.resume.json` bound to this content. Recorded pieces are
hashed again before reuse. Failed pieces get up to three retries and may use another
completed, verified seed. Range, length and piece hashes are checked before recording
completion; the whole file is verified before atomic publication.

Eight concurrent probes feed the existing tree scheduler as they complete. A worker
uses up to four concurrent 8 MiB pieces. Completed workers become seeds. The
authenticated `/v1/models/manifest` endpoint exposes the manifest. `/v1/models/pull`
accepts optional `manifest` and `sources`; legacy `source` requests remain supported,
with locally hashed resume pieces and final whole-file verification. Old unbound
bitmap files are not trusted for resume.

Worker GGUF replication is separate from **RPC tensor caching**. Inference needs the
GGUF on the master; it does not require a complete GGUF on every worker. `rpc-server
-c` retains its tensor cache across restarts. Status reports `rpc_cache` including
the actual platform default location (or `LLAMA_CACHE/rpc`). The cache is never cleared
by model distribution or an ordinary restart.

## Rollout and comparison

Build with `uv run python scripts/build.py`, then restart the Windows agent. Save the
previous runtime/configuration files before changing a running deployment. On the Mac,
pull and build manually, use `python scripts/install.py --llama-only --backend metal`,
and restart/rejoin to publish the device inventory. Preserve all saved model settings.
Run Plan before Run, inspect device ranges and estimate confidence, then compare actual
llama-server placement logs with the plan. Do not accept an overflowing plan.

`python scripts/compare_inference.py --output before.json` makes five identical,
64-token streamed requests without changing saved model, quantization, context,
sampling or reasoning settings. Repeat after the controlled restart with `after.json`.
It reports median/minimum/maximum TTFT and generation rate. Run clean and interrupted
distribution comparisons, plus cold/warm loads, after both nodes are updated. Cache
warmth, paging, and background load must be comparable before applying the 5% regression
criterion. CUDA/ROCm combinations remain unverified until tested on that hardware.

Pinned upstream references: [server arguments](https://github.com/ggml-org/llama.cpp/blob/b11026/tools/server/README.md),
[device assignment](https://github.com/ggml-org/llama.cpp/blob/b11026/src/llama-model.cpp),
[RPC registration/cache](https://github.com/ggml-org/llama.cpp/blob/b11026/ggml/src/ggml-rpc/ggml-rpc.cpp).
