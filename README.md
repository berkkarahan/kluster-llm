# Kluster LLM

**Repository:** https://github.com/berkkarahan/kluster-llm

Distributed LLM inference across **Windows, Linux, and macOS**, pooling Metal / CUDA / ROCm / Vulkan (and CPU spill) so a single GGUF can span heterogeneous nodes.

---

## Engine Tiers

| Tier | Engine | Status | Architecture |
|------|--------|--------|--------------|
| Stable | `llama.cpp` RPC | Production | Layer-pipeline split via coordinator + `rpc-server` workers |
| Experimental | `Prima.cpp` (Halda scheduler) | Alpha | Automatic workload distribution across mixed CPUs/GPUs, mmap offload, <10% memory pressure |

**Decision:** llama.cpp RPC remains the production engine for the full OS × backend matrix. See [docs/engine-decision.md](docs/engine-decision.md).

### Supported platforms

| | Metal | CUDA | ROCm | Vulkan | CPU |
|--|-------|------|------|--------|-----|
| **macOS** | yes | — | — | — | yes |
| **Linux** | — | yes | yes | fallback | yes |
| **Windows** | — | yes | not required | yes (AMD default) | yes |

Preference per node: NVIDIA → CUDA then Vulkan; AMD → ROCm (Linux) then Vulkan; Apple → Metal.

---

## Repository Structure

```
kllm/
├── python/kllm/              # Cross-platform CLI + agent (Python)
├── crates/kllm-native/       # HW probe, planner, process supervisor (Rust)
├── coordinator-macos/          # Legacy macOS scripts (fallback)
├── worker-windows/             # Legacy Windows scripts (fallback)
├── network-config/             # High-speed link setup guides
├── docs/                       # Guides (includes docs/cli.md)
├── pyproject.toml
├── .gitignore
├── LICENSE
└── README.md
```

---

## Quick Start

### Cluster CLI (recommended)

One-shot install (Python 3.10+). Pulls Rust/Node if missing, builds `kllm`, fetches/builds `llama-server` + `rpc-server`, and installs WireGuard for optional overlay:

```bash
python scripts/install.py
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

# Master
kllm start
kllm model download 'org/repo:model.Q4_K_M.gguf'

# Worker (another machine — run install.py there too)
kllm join --key '<join-key-from-master>'

# Master
kllm model distribute model.Q4_K_M.gguf
kllm run --model model.Q4_K_M.gguf
kllm ui
# optional: coding harness against the cluster API
kllm connect claude
# optional RPC overlay (WireGuard installed by install.py):
# kllm start --overlay wireguard
```

CLI/UI-only rebuild: `python scripts/build.py`. Flags: `--backend vulkan|cuda|metal|cpu`, `--skip-wireguard`, `--prefer-prebuilt`.

Dashboard is built by `install.py`; or manually `cd dashboard && npm install && npm run build`, then `kllm ui` → `http://127.0.0.1:18765/`.

See [docs/cli.md](docs/cli.md) for the full command reference. **LLM agents:** follow [docs/agent-install.md](docs/agent-install.md) (also linked from [AGENTS.md](AGENTS.md)) for the full install checklist. Low-level shell scripts below remain available as a fallback.

### Prerequisites

- **OS**: Windows, Linux, and/or macOS nodes in one cluster (mixed OS supported)
- **macOS**: Apple Silicon with Metal (≥16 GB unified memory recommended)
- **Linux**: NVIDIA (CUDA preferred) and/or AMD (ROCm preferred, Vulkan fallback)
- **Windows**: NVIDIA (CUDA preferred) and/or AMD (**Vulkan** default); ROCm not required
- **Network**: Dedicated 10GbE link or Thunderbolt IP bridge (sub-millisecond latency); keep RPC on a private LAN
- **Model**: GGUF quantized LLM (Q4_K_M or Q5_K_M recommended)

---

### Step 1 — Network Setup

Set static IPs on your dedicated high-speed link. DHCP reassignments during long inference runs can collapse the RPC state machine.

**macOS (dedicated interface `enX`):**
```bash
sudo networksetup -setstaticnetworksetup enX 192.168.10.1 255.255.255.0 "" ""
```

**Windows (dedicated NIC `Ethernet1`):**
```powershell
New-NetIPAddress -IPAddress 192.168.10.2 -PrefixLength 24 -InterfaceAlias "Ethernet1"
```

See [network-config/10gbe-bridging-guide.md](network-config/10gbe-bridging-guide.md) for full Thunderbolt bridging and 10GbE instructions.

---

### Step 2 — Worker Initialization (Windows)

On the Windows host:

```powershell
# One-time setup
.\worker-windows\setup_windows.ps1

# Launch worker (legacy script; prefer `kllm join` which binds a private IP)
.\worker-windows\run_worker.ps1 -IP "192.168.10.2" -Port 50052
```

This downloads the Vulkan/ROCm runtime, fetches the pre-compiled `rpc-server.exe`, and opens TCP 50052 in the Windows Firewall.

---

### Step 3 — Model Acquisition

Download a quantized GGUF model (example: Llama 3.1 8B Q4_K_M):

```bash
# From the coordinator host
cd /path/to/models
curl -L -o llama3.1-8b-q4_k_m.gguf \
  "https://huggingface.co/TheBloke/Llama-3.1-8B-Instruct-GGUF/resolve/main/llama-3.1-8b-instruct.Q4_K_M.gguf"
```

Model size budget (rough guide):
| Quant | 8B params | 13B params | 70B params |
|-------|-----------|------------|------------|
| Q4_K_M | ~4.7 GB   | ~7.8 GB    | ~41 GB     |
| Q5_K_M | ~5.5 GB   | ~9.1 GB    | ~48 GB     |

---

### Step 4 — Coordinator Execution (macOS)

#### Stable Tier (llama.cpp RPC)

```bash
cd coordinator-macos
chmod +x setup_mac.sh run_coordinator.sh
./setup_mac.sh

# Launch coordinator (example: 8B model, worker at 192.168.10.2)
./run_coordinator.sh \
  --model /path/to/llama3.1-8b-q4_k_m.gguf \
  --worker-ip 192.168.10.2 \
  --worker-port 50052
```

The script auto-calculates layer splits based on your Mac's unified memory and the worker's VRAM.

#### Experimental Tier (Prima.cpp / Halda)

```bash
./run_coordinator_prima.sh \
  --model /path/to/llama3.1-8b-q4_k_m.gguf \
  --worker-ip 192.168.10.2 \
  --worker-port 50052
```

Halda scheduler automatically distributes layers across Mac CPU+GPU and Windows GPU with mmap disk offload. No manual split calculation needed.

---

## Configuration Reference

### Key Flags (Stable Tier — `run_coordinator.sh`)

| Flag | Default | Description |
|------|---------|-------------|
| `--split-mode layer` | enforced | Pipeline parallelism — prevents precision drift between Apple Silicon and AMD |
| `--tensor-split` | auto-calculated | Ratio of layers on Mac vs Windows (see [docs/layer-split-calculator.md](docs/layer-split-calculator.md)) |
| `--cache-type-k q8_0` | enforced | 8-bit KV cache key quantization — halves KV memory footprint |
| `--cache-type-v q8_0` | enforced | 8-bit KV cache value quantization — halves KV memory footprint |
| `-ngl` | auto-calculated | GPU layers. Excess layers spill to system RAM + CPU |
| `-t` (threads) | `sysctl -n hw.perflevel_sustained.performance` | CPU threads for RAM-spilled layers |
| `-c` (context) | `4096` | Context window in tokens |
| `-ub` (micro-batch) | `2` | Micro-batch size — kept small to avoid Apple GPU driver timeouts |

### Key Flags (Experimental Tier — `run_coordinator_prima.sh`)

| Flag | Default | Description |
|------|---------|-------------|
| `--scheduler halda` | enforced | Automatic workload distribution |
| `--mmap-threshold 0.10` | enforced | Keep memory pressure below 10% via mmap offload |
| `--worker-ip` | required | Windows worker address |
| `--worker-port` | `50052` | Windows worker port |

---

## Documentation

- **[docs/engine-decision.md](docs/engine-decision.md)** — Why llama.cpp RPC; OS × backend matrix; when to revisit
- **[docs/cli.md](docs/cli.md)** — Cross-platform `kllm` CLI (start/join, models, planner)
- **[docs/layer-split-calculator.md](docs/layer-split-calculator.md)** — How to calculate tensor splits between nodes
- **[docs/memory-constraints.md](docs/memory-constraints.md)** — VRAM/RAM budgeting for common model sizes
- **[network-config/10gbe-bridging-guide.md](network-config/10gbe-bridging-guide.md)** — Full network setup for 10GbE and Thunderbolt IP bridges
- **[docs/troubleshooting.md](docs/troubleshooting.md)** — Common failures and RPC notes

---

## Architecture Overview

```
┌─────────────────────┐          10GbE / Thunderbolt          ┌─────────────────────┐
│  Coordinator (any OS)│ ◄────────── llama.cpp RPC ────────── │  Worker (any OS)     │
│                     │                                       │                     │
│  Metal / CUDA /     │   Pipeline-parallel layer split       │  CUDA / ROCm /      │
│  ROCm / Vulkan / CPU│  (split-mode: layer)                  │  Vulkan / CPU       │
└─────────────────────┘                                       └─────────────────────┘
```

Layers are split by pipeline parallelism across mixed backends. KV cache is 8-bit quantized on both sides. See [docs/engine-decision.md](docs/engine-decision.md).

---

## Known Limitations

- **Split-mode `layer` is required** across mixed backends (Metal / CUDA / ROCm / Vulkan) — do not use tensor split mode; precision handling differs between backends and can cause output drift.
- **RPC is LAN-only by default** — upstream llama.cpp RPC has no auth; `kllm` binds a private interface IP and firewalls 50052. Optional `--overlay wireguard` (CLI/dashboard) for join-key gated mesh. Never expose 50052 publicly.
- **Thunderbolt bridging requires `networksetup` privileges** — run coordinator scripts with `sudo` if your network interface is locked.
- **Prima.cpp is experimental** — the Halda scheduler may not handle all model architectures; test with Q4_K_M or Q5_K_M quantizations first.
- **Context length > 8192** with 8-bit KV cache may still exceed memory on smaller systems; reduce `-c` or use a smaller quantization.

---

## License

See [LICENSE](LICENSE) for details.
