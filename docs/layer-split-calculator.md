# Layer Split Calculator

This guide explains how to calculate the optimal tensor split between your macOS coordinator and Windows worker for pipeline-parallel inference.

---

## Why Split Mode Matters

**Always use `--split-mode layer`** (pipeline parallelism). Do NOT use tensor split mode across mixed backends.

Metal, CUDA, ROCm, and Vulkan use different precision / kernel paths. Tensor splits across those backends can cause output drift and degraded quality. Pipeline parallelism (layer splitting) avoids this by assigning entire transformer layers to each device. See [engine-decision.md](engine-decision.md).

---

## How Layer Splitting Works

In pipeline parallelism, the model's layers are divided sequentially:

```
Model Layers: [L0, L1, L2, ..., L(N-1)]

macOS Coordinator: [L0, L1, ..., L(K-1)]    ← First K layers
Windows Worker:    [L(K), L(K+1), ..., L(N-1)]  ← Remaining layers
```

The `--tensor-split` flag defines the **fraction of the model that resides on the Mac side**. For example:
- `--tensor-split 0.5` → 50% of layers on Mac, 50% on Windows
- `--tensor-split 0.6` → 60% of layers on Mac, 40% on Windows

---

## Calculation Method

### Step 1: Estimate Model Size

Use the GGUF file size as a proxy for model parameters:

```bash
# Model size in GB
du -gh /path/to/model.gguf
```

| Model Type | Approx. Size (Q4_K_M) |
|------------|----------------------|
| 8B params  | ~4.7 GB              |
| 13B params | ~7.8 GB              |
| 70B params | ~41 GB               |

### Step 2: Determine Available Memory

**macOS (coordinator):**
```bash
# Total unified memory in GB
sysctl -n hw.memsize | awk '{printf "%.0f", $1/1024/1024/1024}'
```

**Windows (worker):**
```powershell
# Total VRAM in GB (AMD GPU)
Get-WmiObject Win32_PnPEntity | Where-Object { $_.Name -match "AMD" } | ForEach-Object { $_.Name }
```

Or check in Device Manager → Display Adapters → AMD Radeon.

### Step 3: Calculate Split Ratio

Use this formula:

```
split_ratio = mac_memory_budget / (mac_memory_budget + worker_vram)
```

Where:
- `mac_memory_budget` = 80% of Mac unified memory (keep 20% headroom for KV cache and system)
- `worker_vram` = Windows GPU VRAM

**Example: Mac with 32 GB, Windows with 12 GB VRAM:**
```
mac_budget = 32 * 0.8 = 25.6 GB
worker_vram = 12 GB

split_ratio = 25.6 / (25.6 + 12) = 0.681
```

Use `--tensor-split 0.681` (Mac gets ~68% of layers).

### Step 4: Verify with Layer Count

For a 70B model with 80 layers:
```
Mac layers: 80 * 0.681 = 54 layers
Worker layers: 80 - 54 = 26 layers
```

---

## Auto-Calculation

The `run_coordinator.sh` script auto-calculates the split if you omit `--tensor-split`:

```bash
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2
```

It will:
1. Detect Mac unified memory via `sysctl`
2. Default to 8 GB worker VRAM (or prompt if dxdiag is available)
3. Calculate and display the split ratio
4. Pass it to llama.cpp

---

## RAM Offloading (When VRAM is Insufficient)

If the model exceeds total VRAM, layers spill to system RAM and CPU:

```bash
# Set -ngl to the number of layers that fit in VRAM
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 -ngl 32
```

- Layers 0–31 go to GPU (Mac + Windows)
- Layers 32–79 go to system RAM + CPU
- Performance will be slower (tokens/sec drops significantly)

The `-ngl` flag is **not** a split parameter—it controls how many total layers use GPU vs. RAM/CPU across both devices.

---

## Tips

- **Start with `--tensor-split 0.5`** and adjust based on memory usage
- **Monitor with `--verbose`** to see layer placement
- **If you see OOM errors**, reduce the split ratio (more layers on Mac)
- **For 70B+ models**, expect CPU fallback regardless of split
