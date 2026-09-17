# Quickstart Guide

This guide walks you through setting up a distributed LLM inference cluster in 10 minutes.

---

## Prerequisites Checklist

- [ ] macOS host (M1/M2/M3 series) with ≥16 GB unified memory
- [ ] Windows host with AMD GPU (RX 6000/7000 series recommended)
- [ ] Dedicated network link (Thunderbolt cable or 10GbE)
- [ ] Administrative access to both hosts
- [ ] Hugging Face account (for model downloads)

---

## Step 1: Network Setup (5 minutes)

### macOS Coordinator

```bash
# Set static IP on Thunderbolt interface
sudo networksetup -setstaticnetworksetup thunderbolt1 192.168.10.1 255.255.255.0 "" ""

# Disable DHCP
sudo networksetup -setdhcp thunderbolt1 off
```

### Windows Worker

```powershell
# Set static IP on Thunderbolt NIC
New-NetIPAddress -IPAddress 192.168.10.2 -PrefixLength 24 -InterfaceAlias "Ethernet1"

# Disable DHCP
Set-NetIPInterface -InterfaceAlias "Ethernet1" -Dhcp Disabled
```

### Verify Connection

```bash
# From macOS
ping -S 192.168.10.1 192.168.10.2 -c 5

# Expected: <0.2 ms latency, 0% packet loss
```

---

## Step 2: Worker Initialization (2 minutes)

On Windows:

```powershell
# Download Vulkan SDK and rpc-server.exe
.\worker-windows\setup_windows.ps1

# Launch worker (listens on 0.0.0.0:50052)
.\worker-windows\run_worker.ps1 -IP "192.168.10.2" -Port 50052
```

This opens TCP 50052 in Windows Firewall automatically.

---

## Step 3: Model Download (3 minutes)

On macOS:

```bash
# Download a quantized model (example: Llama 3.1 8B Q4_K_M)
curl -L -o /path/to/models/llama3.1-8b-q4_k_m.gguf \
  "https://huggingface.co/TheBloke/Llama-3.1-8B-Instruct-GGUF/resolve/main/llama-3.1-8b-instruct.Q4_K_M.gguf"
```

**Model size guide:**
| Model | Quant | Size |
|-------|-------|------|
| 8B | Q4_K_M | ~4.7 GB |
| 13B | Q5_K_M | ~9.1 GB |
| 70B | Q4_K_M | ~41 GB |

---

## Step 4: Launch Coordinator (1 minute)

On macOS:

```bash
cd kllm/coordinator-macos

# One-time setup (compile llama.cpp with Metal)
./setup_mac.sh

# Launch coordinator
./run_coordinator.sh \
  --model /path/to/llama3.1-8b-q4_k_m.gguf \
  --worker-ip 192.168.10.2 \
  --worker-port 50052
```

The script auto-calculates the layer split and launches with optimized settings.

---

## Alternative: Using Makefile

```bash
# Setup both hosts
make setup-coordinator setup-worker

# Calculate split ratio
make calculate-split

# Run coordinator
make run-coordinator MODEL=/path/to/model.gguf WORKER_IP=192.168.10.2
```

---

## Expected Output

```
=== Kluster LLM: Coordinator (macOS) ===

System: macOS 14.2
Memory: 24576 MB unified
CPU cores: 10
Metal: available

[INFO] No --tensor-split provided, auto-calculating...
Model size: ~4700 MB
Mac budget: ~19660 MB
Worker VRAM: ~8192 MB
Calculated split: Mac 0.706 : Worker 0.294

[INFO] Launching coordinator...
  Worker: 192.168.10.2:50052
  Model: /path/to/llama3.1-8b-q4_k_m.gguf
  Context: 4096 tokens
  Split mode: layer
  Tensor split: 0.706
  KV cache: q8_0 (8-bit quantized)
  Micro-batch: 2
  GPU layers (-ngl): 32

llama.cpp server listening on 0.0.0.0:8080
```

---

## Testing Inference

Once the coordinator is running, you can test with:

```bash
# Using curl
curl -X POST http://localhost:8080/v1/completions \
  -H "Content-Type: application/json" \
  -d '{"prompt": "The capital of France is", "n_predict": 50}'

# Using llama.cpp CLI
./llama-cli -m /path/to/model.gguf -c 4096 -p "The capital of France is" -n 50
```

---

## Performance Expectations

| Configuration | Tokens/sec | Latency |
|--------------|-----------|---------|
| 8B Q4_K_M (Mac + 8GB Worker) | 45-60 | <1 ms/token |
| 13B Q5_K_M (Mac + 12GB Worker) | 25-40 | <1.5 ms/token |
| 70B Q4_K_M (Mac + 24GB Worker) | 15-25 | 2-3 ms/token |

---

## Troubleshooting Quick Fixes

### "rpc-server.exe not found"
```powershell
.\worker-windows\setup_windows.ps1
```

### "llama-server not found"
```bash
cd coordinator-macos && ./setup_mac.sh
```

### "Connection timed out"
- Verify worker is running: `netstat -an | findstr 50052`
- Check Windows Firewall: `Get-NetFirewallRule -Name "kllm-RPC"`
- Test network: `ping 192.168.10.2`

### "OOM (Out of Memory)"
- Reduce context: `./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 --context 2048`
- Use smaller quantization (Q4_K_M instead of Q5_K_M)
- Increase tensor-split to move more layers to Mac

### "Slow inference"
- Check layer placement: look for `[Halda] Layer X → CPU` in logs
- Verify GPU is being used (Task Manager → Performance → GPU)
- Consider manual split with `--tensor-split` for better performance

---

## Next Steps

- Read [docs/memory-constraints.md](../docs/memory-constraints.md) for VRAM/RAM budgeting
- Read [docs/layer-split-calculator.md](../docs/layer-split-calculator.md) for split calculation
- Read [network-config/thunderbolt-bridging.md](../network-config/thunderbolt-bridging.md) for advanced network setup
- Explore [docs/prima-cpp-guide.md](../docs/prima-cpp-guide.md) for experimental tier
