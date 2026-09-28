# Memory Constraints Guide

This guide covers VRAM and RAM budgeting for distributed LLM inference across heterogeneous hardware.

---

## Memory Budget Formula

For each device, the total memory budget is:

```
total_memory = model_weights + kv_cache + overhead
```

Where:
- **model_weights**: GGUF file size (see quantization table below)
- **kv_cache**: Scales with context length × batch size × model dimensions
- **overhead**: ~10-20% for runtime, temporary buffers, and system

### KV Cache Estimation (8-bit quantized)

With `--cache-type-k q8_0 --cache-type-v q8_0`:

```
kv_cache_gb ≈ (context_length × n_layers × n_heads × head_dim × 2 × 1 byte) / (1024^3)
```

Simplified rule of thumb:
```
kv_cache_gb ≈ context_tokens × n_layers × 0.0001  (for 7B models)
kv_cache_gb ≈ context_tokens × n_layers × 0.0002  (for 13B models)
kv_cache_gb ≈ context_tokens × n_layers × 0.001   (for 70B models)
```

**Example: 7B model, 4096 context, 32 layers, 8-bit KV cache:**
```
kv_cache_gb ≈ 4096 × 32 × 0.0001 = 13.1 GB
```

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
Can run: 70B Q4_K_M (48 GB) → YES (with split-mode layer)
```

For the 70B model:
- Mac holds ~68% of layers (48 GB × 0.68 = 32.6 GB)
- Worker holds ~32% of layers (48 GB × 0.32 = 15.4 GB)
- Worker spills 3.4 GB to RAM/CPU

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
