# Memory Constraints Guide

This guide covers VRAM and RAM budgeting for distributed LLM inference across heterogeneous hardware.

---

## Memory Budget Formula

PLAN & RUN uses the Python planner behind `/v1/plan` as its canonical estimator.
Its component total is:

```
estimated_bytes = weights_estimate_bytes + auxiliary_weights_bytes
                + kv_estimate_bytes + recurrent_state_bytes
                + checkpoint_estimate_bytes + compute_estimate_bytes
                + auxiliary_runtime_bytes + execution_overhead_bytes
```

The same total equals `cpu_alloc_bytes + vram_alloc_bytes` and the sum of
`node_alloc_bytes`. Unified memory is one shared capacity; RAM and VRAM are not
added twice. GGUF tensor spans determine resident weights, including any tied
embedding copy needed for offloaded output. GGUF metadata/header bytes are not
weights. The legacy native planner without resolved launch settings remains a
file-size approximation and reports an incomplete estimate.

`cpu_alloc_bytes`/`ram_alloc_bytes` include master and RPC worker CPU allocations.
`node_ram_alloc_bytes` and `node_vram_alloc_bytes` show the per-node breakdown;
`node_ram_budget_bytes` reports usable CPU budgets. RPC CPU layers contribute to
upstream `ngl` and `tensor_split`, but are never labeled as VRAM.

Compute is a conservative graph reserve based on physical microbatch, sequence
outputs, embedding/FFN dimensions, attention masks, Flash Attention, cache
dequantization and row/tensor split scratch. Each active device and the master
receive a compute reserve and a 256 MiB execution reserve. Backend allocator
reuse and hardware-specific workspaces are approximate; this is not a measured
peak. Row/tensor placement is also approximate and reports a warning.

Recurrent architectures reserve float32 convolution/state buffers per slot and
host checkpoints per slot and checkpoint count. Draft models include resident
weights, their own KV/state/compute reserve and speculative rollback buffers.
Local projector weights include a 512 MiB runtime reserve per slot. Local LoRA
and control-vector assets contribute their file sizes. Auxiliary reserves are
currently budgeted on master RAM; hardware-specific auxiliary offload residency
needs validation on the deployment backend.

`estimate_complete` means every recognized input has been priced with the
documented approximations. It does not guarantee a measured peak or successful
backend allocation. Missing metadata/assets, unknown extra-argument memory
effects and unsupported speculative architectures set it to false and populate
`estimate_warnings`. Missing or remote assets are not silently priced at zero
with a complete estimate.

### KV Cache Estimation (8-bit quantized)

With `--cache-type-k q8_0 --cache-type-v q8_0`:

```
kv_bytes = sum_over_attention_layers(cache_tokens ×
           (GGML_row_bytes(K_dimensions, K_type) + GGML_row_bytes(V_dimensions, V_type)))
```

K and V types are independent. `q8_0` uses 34 bytes per 32 elements, `q4_0`
uses 18; both include block scales. GQA uses KV heads rather than all query
heads. Modern MLA caches the compressed latent and rotary dimensions in K
without allocating a separate V cache. Recurrent layers have state rather than
standard KV. Sliding-window layers can reserve a smaller padded cache unless
`--swa-full` is supplied.

Context is total cache capacity, not automatically multiplied by slots. The
pinned runtime pads it to 256 tokens, and separate slot streams also pad their
individual capacities. Consequently an aligned slot-count change keeps standard
KV bytes constant while compute/output or recurrent buffers may grow.

For example, 4096 tokens, 32 attention layers, 8 KV heads and 128 dimensions for
each of K/V with `q8_0` reserve 272 MiB of KV, before compute and weights.

### Controls and preview behavior

| Controls | Memory effect |
|----------|---------------|
| Context, independent K/V types | KV capacity/storage; attention scratch |
| Logical batch | Capped by resolved context; affects memory when it changes effective microbatch |
| Physical microbatch | Capped by effective batch; graph/attention scratch |
| Parallel slots | Output/compute, recurrent state and checkpoints; cache stream padding |
| Flash Attention, SWA, checkpoints | Attention workspaces, sliding-window KV, host state snapshots |
| Draft/projector assets, adapters, speculative mode, split mode | Auxiliary weights/runtime, rollback and transfer scratch |
| Node shares, GPU selection, device mode, headroom, placement weights | Budgets and placement; active device count can change workspace reserves |
| Sampling, templates/thinking, threads | No reserved model-memory effect |
| Cache RAM | Server cache limit, not an eagerly reserved model allocation |
| mmap/mlock | Primarily host residency rather than model VRAM |

All relevant edits request a new preview. A logical batch edit can legitimately
leave memory unchanged when effective microbatch stays constant; the dashboard
reports that it recalculated and explains this case. Aggregate memory retains
one-decimal GiB formatting, while small components display MiB/KiB so changes
below 0.1 GiB remain visible. Equivalent allowed extra arguments are resolved
before both planning and launch. Disabling Flash Attention with quantized V is
rejected; auto selects on when quantized V requires it. For float V, auto is
priced conservatively using the non-Flash Attention graph.

The formulas are independently implemented against
[llama.cpp v0.5.0 context semantics](https://github.com/ggml-org/llama.cpp/blob/v0.5.0/src/llama-context.cpp),
[KV storage](https://github.com/ggml-org/llama.cpp/blob/v0.5.0/src/llama-kv-cache.cpp) and
[sliding-window storage](https://github.com/ggml-org/llama.cpp/blob/v0.5.0/src/llama-kv-cache-iswa.cpp).

Run deterministic regressions with `uv run python -m pytest -q tests/test_memory_estimation.py tests/test_plan_run_params.py`
and `node --test dashboard/tests/memory-estimation.test.mjs`.
The optional local Qwen3.5 regression uses
`KLLM_TEST_QWEN_GGUF=/path/to/Qwen3.5-122B.gguf uv run python -m pytest -q tests/test_memory_estimation.py -k optional_local_qwen`.
It reads metadata without
loading weights. Backend peak-memory and live RPC validation still require the
target hardware and a real model run.

For a fixed context and Flash Attention setting, quantization changes only KV
storage and associated scratch. For the Qwen hybrid model at context 200192,
changing both K and V from `q8_0` to `q4_0` reduces KV from about 6.49 to 3.44 GiB.
It does not reduce model weights, float32 recurrent state, or host checkpoints.
Auto context may grow after quantization, consuming some of the memory savings.
The preview displays resolved K/V types and reports KV savings between edits.

### Automatic placement and required nodes

Automatic planning tries local GPUs, a single remote GPU, and pooled GPUs before
CPU RAM fallback. Worker CPU RAM is usable only when the running worker advertises
its CPU device in `rpc_devices`; rebuild and rejoin older workers to enable it.
On macOS, available RAM uses free plus inactive pages rather than subtracting
compressed pages again. Metal and Vulkan on Apple Silicon share that same pool.

Select **Must use this node** in Automatic placement, or repeat `--require-node NODE`
on `kllm plan`/`kllm run`. API requests use `required_nodes: ["node-id", ...]`.
Each required node must receive a transformer or recurrent model layer; host-only
allocations and an output-only layer do not satisfy the requirement. The planner
chooses the shares and budgets all runtime reserves. Unavailable IDs are rejected;
insufficient capacity returns overflow rather than silently omitting a required node.
Requirements are incompatible with manual shares and row/tensor modes.

Select **Priority** to order nodes with the up/down controls. The planner maximizes
workload on the first node, then the next, while respecting capacity, contiguous
layers and required nodes. GPU-only solutions take precedence over CPU fallback.
This replaces the dashboard's fixed-share sliders; stored manual shares migrate to
priority order. API requests use `node_priority: ["preferred-node", "next-node"]`.
Unlisted current nodes remain available as fallback; unavailable priority IDs must
be removed. Priority order is remembered with model settings and applied to previews
and launches.

---

## Quantization Size Reference

| Model Size | Q2_K | Q3_K_M | Q4_K_M | Q5_K_M | Q6_K | Q8_0 |
|------------|------|--------|--------|--------|------|------|
| 7B         | 3.1 GB | 4.4 GB | 4.7 GB | 5.5 GB | 6.3 GB | 7.8 GB |
| 8B         | 3.5 GB | 5.0 GB | 5.4 GB | 6.3 GB | 7.2 GB | 9.0 GB |
| 13B        | 5.8 GB | 8.2 GB | 8.9 GB | 10.3 GB | 11.8 GB | 14.7 GB |
| 70B        | 31 GB | 44 GB | 48 GB | 55 GB | 63 GB | 76 GB |

---

## Hardware Budget Calculator

### macOS Coordinator (Apple Silicon)

```bash
# Get unified memory in GB
TOTAL_MEM_GB=$(sysctl -n hw.memsize | awk '{printf "%.0f", $1/1024/1024/1024}')

# Budget: 80% for model + KV cache
BUDGET_GB=$(( TOTAL_MEM_GB * 80 / 100 ))

echo "Total unified memory: ${TOTAL_MEM_GB} GB"
echo "Model + KV budget: ${BUDGET_GB} GB"
```

**Example: M2 Pro with 24 GB:**
```
Budget: 24 * 0.8 = 19.2 GB
Can run: 70B Q4_K_M (48 GB) → NO (exceeds budget)
Can run: 13B Q5_K_M (10.3 GB) → YES (fits with room for KV cache)
```

### Windows Worker (AMD GPU)

Check VRAM:
```powershell
# Get VRAM in GB
Get-WmiObject Win32_PnPEntity | Where-Object { $_.Name -match "AMD" } | ForEach-Object { $_.Name }
```

Or check in Settings → Display → Advanced settings → Adapter information.

**Example: RX 6700 XT with 12 GB VRAM:**
```
VRAM: 12 GB
Can run: 7B Q5_K_M (6.3 GB) → YES
Can run: 13B Q5_K_M (10.3 GB) → YES (with small KV cache)
Can run: 70B Q4_K_M (48 GB) → NO (exceeds VRAM, will spill to RAM)
```

---

## Distributed Budget (macOS + Windows)

When combining both devices:

```
total_budget = mac_budget + worker_vram
```

**Example: Mac (24 GB) + Windows (12 GB VRAM):**
```
Mac budget: 24 * 0.8 = 19.2 GB
Worker VRAM: 12 GB
Total: 31.2 GB

Can run: 13B Q5_K_M (10.3 GB) → YES (plenty of room)
Can run: 70B Q4_K_M (48 GB) → NO (weights alone exceed the combined budget)
```

For the 70B model, add capacity or use a smaller quantization. The Python planner
can spill a contiguous prefix onto master CPU RAM when that RAM budget permits;
updated GPU workers can contribute RAM through their advertised RPC CPU devices.
Manual shares remain GPU-only.

---

## RAM Offloading Performance

When layers spill to RAM/CPU, expect:

| Layer Location | Tokens/sec (7B, 4096 ctx) | Tokens/sec (70B, 4096 ctx) |
|----------------|--------------------------|--------------------------|
| All GPU        | 45-60                    | 15-25                    |
| 50% GPU / 50% RAM | 20-30                 | 5-10                     |
| All RAM/CPU    | 8-15                     | 2-5                      |

**Tips:**
- Minimize RAM-spilled layers for best performance
- Use smaller context windows to reduce KV cache pressure
- Q4_K_M or Q5_K_M quantizations offer the best speed/quality tradeoff
- For 70B+ models, expect significant slowdowns regardless of split

---

## Monitoring Memory Usage

### macOS
```bash
# Real-time memory pressure
memory_pressure -s

# Process memory usage
top -pid $(pgrep llama-server)
```

### Windows
```powershell
# GPU memory (AMD)
nvidia-smi  # If using NVIDIA GPU
# Or check in Task Manager → Performance → GPU

# Process memory
Get-Process rpc-server | Select-Object CPU, WorkingSet
```

---

## Common Issues

### OOM (Out of Memory)
**Symptom:** Server crashes with "CUDA out of memory" or "Mach kernel reported"
**Fix:** Reduce context length, use a smaller quantization, or adjust the node memory budgets and let `kllm plan` recompute placement.

### Slow Inference
**Symptom:** Tokens/sec much lower than expected
**Fix:** Check if layers are spilling to RAM (use `--verbose` to see layer placement). Reduce `-ngl` if needed, or use a smaller model.

### Precision Drift
**Symptom:** Output quality degrades with tensor-split mode
**Fix:** Switch to `--split-mode layer` (pipeline parallelism). This is the only safe split mode for kllmgeneous hardware.
