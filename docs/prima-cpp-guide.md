# Prima.cpp Experimental Tier Guide

This guide covers the Experimental Tier using Prima.cpp with the Halda scheduler for automatic workload distribution across heterogeneous hardware.

---

## Overview

The Experimental Tier uses **Prima.cpp** instead of llama.cpp, with the **Halda scheduler** that automatically distributes workloads across mixed CPUs and GPUs. Key features:

- **Automatic layer placement** — no manual tensor-split calculation needed
- **mmap disk offload** — keeps memory pressure below 10%
- **Mixed CPU/GPU scheduling** — Halda decides optimal placement per layer
- **Reduced network dependency** — less data movement between coordinator and worker

---

## When to Use Prima.cpp

| Use Case | llama.cpp (Stable) | Prima.cpp (Experimental) |
|----------|-------------------|-------------------------|
| Manual control over layer placement | ✓ | ✗ |
| Predictable performance | ✓ | ✗ (variable) |
| Highly constrained consumer hardware | ✗ | ✓ |
| Automatic memory optimization | ✗ | ✓ |
| Production reliability | ✓ | ✗ |
| Mixed CPU/GPU scheduling | ✗ | ✓ |

**Choose llama.cpp for production.** Use Prima.cpp for experimentation, constrained hardware, or when you want automatic memory optimization.

---

## Prerequisites

1. **Prima.cpp repository**: Clone from https://github.com/your-org/prima.cpp
2. **macOS host**: Apple Silicon Mac with ≥16 GB unified memory
3. **Windows host**: Any GPU (AMD, NVIDIA, or Intel) with Vulkan support
4. **Network**: Standard Ethernet or Wi-Fi is acceptable (less critical than llama.cpp)

---

## Setup Steps

### 1. Clone Prima.cpp

```bash
cd /path/to/kllm/coordinator-macos
git clone https://github.com/your-org/prima.cpp ../vendor/prima.cpp
```

### 2. Compile Prima.cpp

```bash
cd ../vendor/prima.cpp
cmake -B build -DGGML_METAL=1 -DCMAKE_BUILD_TYPE=Release
cmake --build build --target prima-server -j$(sysctl -n hw.ncpu)
```

### 3. Verify Installation

```bash
ls -la build/release/bin/prima-server
# Expected: -rwxr-xr-x 1 user staff ... build/release/bin/prima-server
```

---

## Running the Coordinator

### Basic Command

```bash
cd coordinator-macos
./run_coordinator_prima.sh \
  --model /path/to/model.gguf \
  --worker-ip 192.168.10.2 \
  --worker-port 50052
```

### Advanced Options

```bash
./run_coordinator_prima.sh \
  --model /path/to/model.gguf \
  --worker-ip 192.168.10.2 \
  --worker-port 50052 \
  --context 8192 \
  --mmap-threshold 0.05 \
  --micro-batch 4
```

| Flag | Default | Description |
|------|---------|-------------|
| `--scheduler halda` | enforced | Automatic workload distribution |
| `--mmap-threshold 0.10` | enforced | Keep memory pressure below 10% via mmap offload |
| `--context 4096` | 4096 | Context window in tokens |
| `--micro-batch 2` | 2 | Micro-batch size |
| `--worker-ip` | required | Windows worker address |
| `--worker-port 50052` | 50052 | Windows worker port |

---

## How Halda Scheduler Works

The Halda scheduler automatically decides:

1. **Which layers go to GPU** — based on available VRAM and compute
2. **Which layers go to CPU** — based on available RAM and CPU cores
3. **Which layers go to disk (mmap)** — when memory pressure exceeds threshold
4. **Data movement between devices** — minimizes network traffic

### Memory Pressure Management

```
mmap-threshold 0.10 → Keep <10% of total memory on RAM/disk
```

When memory pressure exceeds 10%, Halda:
- Moves least-accessed layers to disk via mmap
- Prefers GPU over CPU for active layers
- Balances memory across coordinator and worker

### Performance Characteristics

| Model Size | Layer Count | Expected Tokens/sec (7B class) | Expected Tokens/sec (70B class) |
|------------|-------------|-------------------------------|-------------------------------|
| 7B Q4_K_M | ~32 layers | 35-50 | N/A |
| 13B Q5_K_M | ~48 layers | 15-25 | N/A |
| 70B Q4_K_M | ~80 layers | N/A | 8-15 |

**Note:** Performance is variable due to automatic scheduling. Expect 20-40% slower than manual llama.cpp split.

---

## Worker Configuration (Windows)

The Windows worker for Prima.cpp uses the same `run_worker.ps1` script:

```powershell
.\worker-windows\run_worker.ps1 -IP "192.168.10.2" -Port 50052
```

**Key difference:** Prima.cpp can work with any GPU backend (AMD, NVIDIA, Intel), not just AMD ROCm/Vulkan.

---

## Monitoring Prima.cpp

### Check Scheduler Activity

```bash
# Enable verbose logging
./run_coordinator_prima.sh --model model.gguf --worker-ip 192.168.10.2 --verbose

# Look for scheduler decisions in output:
# [Halda] Layer 0-15 → GPU (Apple Silicon)
# [Halda] Layer 16-31 → CPU (system RAM)
# [Halda] Layer 32-47 → GPU (Windows AMD)
# [Halda] Layer 48-63 → Disk (mmap offload)
```

### Memory Pressure

```bash
# Real-time memory pressure
memory_pressure -s

# Process memory
top -pid $(pgrep prima-server)
```

---

## Limitations

1. **Experimental status** — May not handle all model architectures
2. **Variable performance** — Automatic scheduling can cause latency spikes
3. **No manual control** — Cannot override layer placement decisions
4. **Testing required** — Always test with Q4_K_M or Q5_K_M quantizations first
5. **Network less critical** — Still benefits from dedicated link, but not required

---

## Troubleshooting

### "Halda scheduler failed to initialize"
- Verify Prima.cpp is compiled with Metal support (`-DGGML_METAL=1`)
- Check that worker is accessible: `ping 192.168.10.2`

### "Memory pressure exceeded threshold"
- Reduce `--mmap-threshold` (e.g., `--mmap-threshold 0.05`)
- Use smaller context window (`--context 2048`)
- Switch to llama.cpp for manual control

### "Slow inference"
- Monitor layer placement: `[Halda] Layer X → CPU` indicates CPU fallback
- Consider manual split with llama.cpp for better performance
- Check if worker GPU is being utilized (Task Manager → Performance → GPU)

---

## Comparison: llama.cpp vs Prima.cpp

| Feature | llama.cpp | Prima.cpp |
|---------|-----------|-----------|
| Layer placement | Manual (`--tensor-split`) | Automatic (Halda) |
| Memory optimization | Manual tuning | Automatic mmap offload |
| Performance predictability | High | Variable |
| Hardware support | Apple Metal, AMD ROCm/Vulkan | Apple Metal, AMD, NVIDIA, Intel |
| Network dependency | High (sub-ms latency critical) | Moderate (standard Ethernet OK) |
| Production readiness | ✓ | ✗ |
| Ease of use | Medium (manual split calculation) | Easy (automatic) |

**Recommendation:** Use llama.cpp for production, Prima.cpp for experimentation or constrained hardware.
