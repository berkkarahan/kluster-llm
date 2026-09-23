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

## Experimental: MPI tensor parallelism

MPI is an explicit, admission-gated path. RPC stays the default. The production
provider is the pinned pure-Rust fork under `crates/kllm-mpi-provider`; the old
native-MPI CMake branch is no longer a production option.

- Each selected physical GPU receives one deterministic rank. All-Mac clusters
  prefer Metal, all-NVIDIA clusters prefer CUDA, all-AMD clusters prefer ROCm,
  and mixed or unknown families require Vulkan. Every rank must verify the same
  engine, operation profile, provider protocol, and physical-device mapping.
- The structured rank path binds only fixed private addresses in ports
  20000–20999 and mutually authenticates peers with a per-job secret and fresh
  nonces. KLLM worker endpoints check the cluster key on worker roles. Commands,
  executable paths, raw environments, and user ports are absent from the launch
  grammar.
- `python/kllm/mpi_manifest.py` implements the pinned b11026 dense-Llama split
  axes, layer rotation, quantization alignment, and exact rank weight bytes.
  Planning, future loading, and engine verification share its identity.
- The first engine surface is dense Llama text, one sequence, explicit context,
  f16 KV, and Flash Attention. Unsupported settings block before launch.
- Provider collectives, the panic-contained C ABI, process ownership, and local
  rank-loss behavior have software tests. The distributed llama.cpp graph patch
  and mandatory mixed/native GPU runs remain release gates, so `/v1/run`
  currently returns 503 for MPI and never substitutes RPC.

See [mpi-runtime-validation.md](mpi-runtime-validation.md) for exact evidence and
unpassed hardware gates.
