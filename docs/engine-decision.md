# Engine decision: llama.cpp RPC

> **Date:** 2026-09-17  
> **Status:** Accepted  
> **Scope:** Kluster LLM (`kllm`) production inference backend

## Decision

**Keep llama.cpp (`llama-server` + `rpc-server`) with RPC layer-pipeline splits as the Stable production engine.** Do not replace it with vLLM, SGLang, TensorRT-LLM, ExLlama, exo, or mesh-llm for the default path.

## Why

Kluster targets:

| Dimension | Requirement |
|-----------|-------------|
| OS | **Windows + Linux + macOS** (mixed-OS clusters) |
| GPU backends | **Metal + CUDA + ROCm + Vulkan** (Vulkan for AMD and NVIDIA fallback) |
| Distribution | One model **sharded** across nodes (weights + KV), not only request routing |

llama.cpp is the only mainstream stack that exposes those backends as GGML devices and joins them over RPC so a single GGUF can span mixed OS/vendor nodes. Throughput-oriented engines optimize a different problem (homogeneous Linux CUDA/ROCm serving) and drop Windows and/or macOS from the distributed path.

## Rejected alternatives (as primary engine)

| Engine | Why not primary |
|--------|-----------------|
| vLLM / SGLang / TensorRT-LLM | Linux-first; no native Windows; no Metal↔CUDA/ROCm/Vulkan weight shard |
| ExLlamaV2/V3 | CUDA-only |
| exo | macOS/MLX-first; Windows unsupported |
| mesh-llm | Same llama.cpp-family kernels; control-plane product swap, not a speed upgrade |
| Prima.cpp | Remains Experimental (Halda / mmap) |

## OS × backend support matrix

Supported cells to ship and test:

| | Metal | CUDA | ROCm | Vulkan | CPU |
|--|-------|------|------|--------|-----|
| **macOS** | yes | — | — | yes (MoltenVK; use for **Vulkan master** clusters) | yes |
| **Linux** | — | yes | yes | yes (fallback) | yes |
| **Windows** | — | yes | not required | yes (AMD default; NVIDIA fallback) | yes |

**Planner preference (per node):**

1. macOS → `metal`
2. NVIDIA → `cuda`, else `vulkan`
3. AMD → `rocm` (Linux), else `vulkan` (Windows or ROCm-less)
4. else → `cpu`

Do not double-bind one physical GPU (e.g. both CUDA and Vulkan for the same NVIDIA card).

**Binary flavors:** ship/build per cell (Metal macOS; CUDA Linux/Windows; ROCm Linux; Vulkan Windows/Linux), not one universal binary.

## Split policy

Always use `--split-mode layer` when devices span different backends (Metal / CUDA / ROCm / Vulkan). Tensor/row splits risk precision drift across backends. See [layer-split-calculator.md](layer-split-calculator.md).

## RPC operational rules

- **Default:** bind `rpc-server` to a **private LAN / 10GbE / Thunderbolt** interface IP (not `0.0.0.0`). `kllm` picks a private IPv4 unless `--rpc-bind` overrides. DHCP mid-run can still collapse the RPC state machine — prefer static IPs.
- Treat `rpc-server` as **LAN-only**. Upstream llama.cpp RPC has **no auth/TLS** and maintainers do not plan to add it ([discussion #14932](https://github.com/ggml-org/llama.cpp/discussions/14932)); HTTP `--api-key` on `llama-server` does **not** protect port **50052**. Firewall TCP 50052; never expose on the public internet. Reachability equals compromise (see e.g. CVE-2026-34159).
- **Optional:** `--overlay wireguard` (CLI/dashboard) builds a join-key-derived WireGuard mesh and rebinds RPC to overlay IPs (`10.66.87.0/24`). Requires system WireGuard tooling (`wg` / `wg-quick` or WireGuard for Windows). Control-plane HTTP stays on the LAN.
- Prefer worker **tensor cache** (`rpc-server -c`) to avoid re-shipping large tensors on reconnect.
- Optional RDMA only when both ends support it (typically Linux RoCE); do not require it for baseline support.

## When to re-evaluate

Revisit replacing llama.cpp only if product requirements change to one of:

- Drop **Windows** or **macOS/Metal**, and target **Linux-only homogeneous CUDA or ROCm** → then evaluate vLLM/SGLang for multi-user throughput.
- All-Apple Thunderbolt cluster with no Windows/AMD → then exo may fit.
- Willing to abandon custom `kllm` orchestration for an off-the-shelf mesh product → then mesh-llm (still llama.cpp-class kernels).

## Related code

- Process argv / enforced layer split: `crates/kllm-native/src/process.rs`
- Capacity probe / backend hint: `crates/kllm-native/src/hw.rs`
- Layer budget planner: `crates/kllm-native/src/planner.rs`
- RPC bind / optional WireGuard overlay: `python/kllm/netsec.py`

## Experimental: MPI activation collectives

`python/kllm/mpi_comm.py` and `cpp/kllm_mpi/` are an optional MPI wrapper for activation buffers. They do not replace `llama-server` / `rpc-server`, and they do not tensor-split Metal with ROCm or CUDA.

- The `mpi` object on a run plan describes llama.cpp v0.5.0 layer owners (`upper_bound` on cumulative f32 splits of the 4-decimal `--tensor-split`) when the GGUF `block_count` is known. `estimated_layers` is a file-size bucket (32/48/80) and is not that count. The object does not start MPI and does not move tensors; RPC remains the runtime.
- Weights and KV stay on the rank that owns the layer. The wire format is a 96-byte GGML header plus the activation bytes (token ids, hidden states, logits). The activation flag is the sender's claim. A payload above 4 GiB is refused, so a weight tensor cannot cross by wearing that flag.
- `balanced_bcast` validates the header (flags, overflow-safe `ne`, contiguous `nb`, `nbytes`) on every rank and agrees on success before anyone allocates or broadcasts the payload. An allocation failure is reduced the same way, so one rank cannot leave the others blocked in `MPI_Bcast`.
- `balanced_allreduce` gathers counts, then gathers the reduction kind and weights. Equal f32 partial sums use `MPI_Allreduce`. Unequal column shards use `MPI_Allgatherv`. A mismatch or a `plan` failure is reduced before either payload collective, and unequal slices are not padded into an allreduce.
- `split_graph` is the experimental harmonic planner. Its layer cap is weights plus KV (`context * 128 * (k + v) * slots` per layer). Column shards reserve the full KV on every rank, then a slice of the weights. Tensor-parallel column shards are opt-in and only inside one backend.
- MPI is unauthenticated. Keep it on the private fabric, the same rule as port 50052.
