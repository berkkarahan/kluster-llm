# Troubleshooting Guide

This guide covers common issues and their solutions for the Kluster LLM project.

---

## Setup Issues

### Preferred installer

```bash
python scripts/install.py
```

Installs Rust/Node (if missing), `kllm`, dashboard, `llama-server` + `rpc-server` at the tag in `llama-release`, and WireGuard. Use `--backend vulkan|cuda|metal|cpu` as needed.

llama.cpp only (run on **every** node; pin is committed `llama-release`):

```bash
python scripts/setup-llama.py
python scripts/setup-llama.py --check
```

Agent-oriented checklist (success criteria, flags, recovery): [agent-install.md](agent-install.md).

### "setup_mac.sh: command not found"

**Solution:** Prefer `python scripts/install.py --backend metal`, or:

```bash
chmod +x coordinator-macos/setup_mac.sh
./coordinator-macos/setup_mac.sh
```

### "setup_windows.ps1: cannot be loaded because running scripts is disabled"

**Solution:** Prefer `python scripts\install.py`, or:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
powershell -ExecutionPolicy Bypass -File worker-windows\setup_windows.ps1
```

### "Xcode Command Line Tools not installed"

**Cause:** Missing CLT on macOS.

**Solution:**
```bash
xcode-select --install
# Follow installer prompts, then: python scripts/install.py --backend metal
```

---

## Compilation Issues

### "cmake: command not found"

**Cause:** CMake not installed or not in PATH.

**Solution:**
```bash
brew install cmake
# Re-run setup_mac.sh
```

### "LLAMA_METAL=1: unrecognized option"

**Cause:** Outdated llama.cpp version.

**Solution:**
```bash
cd coordinator-macos/../vendor/llama.cpp
git pull --rebase
cmake -B build -DGGML_METAL=1 -DCMAKE_BUILD_TYPE=Release
cmake --build build --target llama-server -j$(sysctl -n hw.ncpu)
```

### "clang: error: unsupported option '-fopenmp'"

**Cause:** Incompatible compiler version.

**Solution:**
```bash
brew install llvm
export CC=/opt/homebrew/opt/llvm/bin/clang
export CXX=/opt/homebrew/opt/llvm/bin/clang++
# Re-run compilation
```

---

## Runtime Issues

### RPC must stay on a private LAN

**Cause:** llama.cpp RPC is unauthenticated (upstream documents it as PoC / insecure on open networks; no in-app auth planned).

**Solution:**
- Default `kllm join` binds `rpc-server` to a **private interface IP** (check `kllm status` → `rpc_bind`), not `0.0.0.0`.
- Prefer a dedicated 10GbE / Thunderbolt interface with **static IPs**.
- Firewall TCP **50052** to cluster members only; never publish it to the internet. `kllm` best-effort creates/updates the `kllm-RPC` firewall rule.
- Optional: `kllm start --overlay wireguard` / `kllm join --overlay wireguard` (WireGuard must be installed) so only join-key holders reach RPC on `10.66.87.0/24`.
- Prefer the worker tensor cache (`-c`, enabled by `kllm` worker launch) so reconnects do not re-ship large tensors.
- See [engine-decision.md](engine-decision.md).

### WireGuard overlay fails on start/join

**Cause:** `wg` / `wg-quick` (or WireGuard for Windows) not installed, or overlay mismatch (master `none`, worker `wireguard`).

**Solution:**
- Install WireGuard from https://www.wireguard.com/install/ and ensure `wg` is on `PATH` (or set `KLLM_WG`).
- Use the same `--overlay` on master and workers.
- Control plane stays on LAN — only RPC moves to the overlay. Verify with `kllm status` (`wg_address`, `overlay_status`).

### macOS worker on a Vulkan master (Windows/Linux)

**Cause:** Default macOS install uses **Metal** (`hw` probe + `install.py --backend auto`). A Vulkan master cluster expects workers to run **Vulkan** `rpc-server` binaries and to register `backend=vulkan` for layer split / distribute planning. Metal workers in a Vulkan-first cluster mis-report capacity and backend.

**Solution:**
```bash
uv run python scripts/install.py --backend vulkan
uv run python scripts/setup-llama.py --backend vulkan
export KLLM_ACCEL_BACKEND=vulkan   # add to shell profile for kllm-agent
uv run kllm join --key '<key>' --master <windows-lan-ip>
```
Confirm on the master: `kllm status` shows the Mac member with `backend: vulkan`. GitHub macOS packs are Metal-only but still match the pinned RPC protocol. A Vulkan `rpc-server` source build needs Vulkan SDK / MoltenVK.

### llama.cpp RPC HELLO / "Remote RPC server crashed or returned malformed response"

**Cause:** Master and worker are on different llama.cpp builds. RPC HELLO (`RPC_CMD_HELLO`) is unversioned across nightlies; a Windows `b11026` `llama-server` talking to a Mac HEAD/`git pull` `rpc-server` closes the socket.

**Solution:**
1. Confirm the committed pin: `cat llama-release` (must be the same git revision on every node).
2. On **every** node: `python scripts/setup-llama.py` then `python scripts/setup-llama.py --check`.
3. Do not TCP-probe port 50052 (a connect+close can hang or kill `rpc-server`).
4. After binaries match: `kllm leave` on the worker, then `kllm join --key … --rpc-bind 0.0.0.0` if the master's LAN IP cannot bind the worker interface.
5. Re-run `kllm run`. If HELLO still fails, `--force` the setup on both sides and confirm `vendor/bin/llama-release.txt` shows the same `tag=`.

Do **not** mix `vendor/llama.cpp` HEAD with a GitHub nightly on the other machine.

### "rpc-server.exe not found"

**Cause:** Worker setup not completed.

**Solution:**
```powershell
.\worker-windows\setup_windows.ps1
```

### "llama-server not found"

**Cause:** Coordinator setup not completed.

**Solution:**
```bash
cd coordinator-macos && ./setup_mac.sh
```

### "ERROR: --model <path> is required"

**Cause:** Missing required argument.

**Solution:**
```bash
./run_coordinator.sh --model /path/to/model.gguf --worker-ip 192.168.10.2
```

### "ERROR: Model file not found"

**Cause:** Model path is incorrect or file doesn't exist.

**Solution:**
```bash
# Verify model exists
ls -lh /path/to/model.gguf

# Use absolute path
./run_coordinator.sh --model /absolute/path/to/model.gguf --worker-ip 192.168.10.2
```

---

## Network Issues

### "Connection timed out"

**Cause:** Worker not running, firewall blocking, or wrong IP.

**Solution:**
```bash
# Check worker is running (Windows)
netstat -an | findstr 50052

# Check firewall (Windows)
Get-NetFirewallRule -Name "kllm-RPC" | Format-Table DisplayName, Enabled

# Test connectivity
ping -S 192.168.10.1 192.168.10.2
```

### "Access is denied" creating `kllm-RPC` firewall rule

**Cause:** `New-NetFirewallRule` requires an elevated (Administrator) PowerShell. The agent runs without elevation and only best-effort creates the rule.

**Solution:** Open **PowerShell as Administrator** once and run:
```powershell
New-NetFirewallRule -Name "kllm-RPC" -DisplayName "Kluster LLM RPC Worker" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 50052 -Profile Any
```
Then restart/join again. After the rule exists, non-elevated starts reuse it without prompting.
### "No route to host" / join fails but `curl` works (macOS + Zscaler)

**Cause:** The uv-managed Python running `kllm-agent` cannot open LAN sockets (`errno 65`: Local Network TCC and/or Zscaler), while `curl` / Homebrew Python can. The same restriction also hides mDNS, so Join-by-key with an empty Master host used to 404 even when the master was advertising.

**Solution:** Join automatically (1) browses mDNS under a helper interpreter when the agent Python sees no masters, then (2) starts a localhost control-plane TCP proxy for register/heartbeat. Status shows `cp_proxy_port` and a `netsec_warnings` line. RPC still uses your real LAN `--rpc-bind`. Dashboard Join with only the join key should work; fill **Master host** only if discovery still fails.

```bash
uv run kllm join --key '<key>'
# if discovery still fails:
uv run kllm join --key '<key>' --master <windows-lan-ip>
# optional override:
export KLLM_CP_PROXY_PYTHON=/opt/homebrew/bin/python3
```

Also grant **System Settings → Privacy & Security → Local Network** to Python/Cursor when prompted. Use `--master-port` for the remote control port — `--port` is only the **local** agent.

### Cluster members show mismatched backends (Metal vs Vulkan)

**Cause:** Each node reports its accelerator via probe, optional `accel_backend` in `~/.kllm/config.toml`, or `KLLM_ACCEL_BACKEND`. Mixed Metal/Vulkan (or CUDA/Vulkan) labels are intentional when you pick **Backend** on the Cluster panel before Start/Join.

**Solution:** Set the same Backend on every node (dashboard selector or `accel_backend = "vulkan"` / `export KLLM_ACCEL_BACKEND=vulkan`), then Stop/Start or Leave/Join.

### "No route to host" (wrong subnet)

**Cause:** IPs not in same subnet or interface disabled.

**Solution:**
```bash
# Verify IPs (macOS)
ifconfig thunderbolt1 | grep "inet "

# Verify IPs (Windows)
Get-NetIPAddress -InterfaceAlias "Ethernet1" | Format-Table IPAddress
```

### "High latency (>1 ms)"

**Cause:** Using wrong network interface (Wi-Fi instead of Thunderbolt).

**Solution:**
```bash
# Disable other interfaces (macOS)
sudo networksetup -setairportpower airport off

# Verify you're using Thunderbolt interface
ping -S 192.168.10.1 192.168.10.2
```

### "DHCP reassignment mid-inference"

**Cause:** DHCP lease renewal changed IP.

**Solution:**
```bash
# Set static IPs (macOS)
sudo networksetup -setstaticnetworksetup thunderbolt1 192.168.10.1 255.255.255.0 "" ""
sudo networksetup -setdhcp thunderbolt1 off

# Set static IPs (Windows)
New-NetIPAddress -IPAddress 192.168.10.2 -PrefixLength 24 -InterfaceAlias "Ethernet1"
Set-NetIPInterface -InterfaceAlias "Ethernet1" -Dhcp Disabled
```

---

## Memory Issues

### "CUDA out of memory" or "Mach kernel reported"

**Cause:** Model too large for available VRAM/RAM.

**Solution:**
```bash
# Reduce context length
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 --context 2048

# Use smaller quantization (Q4_K_M instead of Q5_K_M)

# Increase tensor-split to move more layers to Mac
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 --tensor-split 0.75
```

### "OOM: Out of Memory"

**Cause:** System RAM exceeded.

**Solution:**
```bash
# Reduce micro-batch size
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 --micro-batch 1

# Close other applications to free RAM
```

### "Memory pressure exceeded threshold" (Prima.cpp)

**Cause:** mmap threshold too low for model size.

**Solution:**
```bash
# Increase mmap threshold
./run_coordinator_prima.sh --model model.gguf --worker-ip 192.168.10.2 --mmap-threshold 0.15

# Or switch to llama.cpp for manual control
```

---

## Performance Issues

### "Slow inference"

**Cause:** Layers spilling to CPU/RAM.

**Solution:**
```bash
# Check layer placement (look for "→ CPU" in logs)
kllm logs -f --source llama

# Put more layers on GPU (cluster already up via kllm start / kllm join)
kllm run --model model.gguf --device gpu --vram-percent 90
# Or force a layer count: kllm run --model model.gguf --extra-arg "-ngl 40"

# Use smaller model or smaller quantization
```

### "Tokens/sec much lower than expected"

**Cause:** Not using GPU, or network bottleneck.

**Solution:**
```bash
# Verify GPU utilization (Windows)
# Task Manager → Performance → GPU

# Verify network bandwidth
iperf3 -c 192.168.10.1 -t 10

# Check for other network traffic
# Disable other interfaces
```

### "Inference speed drops after few minutes"

**Cause:** Thermal throttling or memory pressure.

**Solution:**
```bash
# Monitor temperature (macOS)
sudo powermetrics -samplers thermal

# Reduce context length
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 --context 2048

# Ensure adequate cooling
```

---

## Precision Issues

### "Output quality degraded" or "precision drift"

**Cause:** Using tensor-split mode instead of layer-split mode.

**Solution:**
```bash
# Use layer-split mode (required for kllmgeneous hardware)
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 --split-mode layer
```

### "Garbage output" or "nonsensical responses"

**Cause:** Incorrect tensor-split ratio or model mismatch.

**Solution:**
```bash
# Recalculate split ratio
./calculate_split.sh --mac-memory 24 --worker-vram 12

# Use calculated ratio
./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2 --tensor-split 0.68
```

---

## Prima.cpp Issues

### "Halda scheduler failed to initialize"

**Cause:** Prima.cpp not compiled or worker unreachable.

**Solution:**
```bash
# Recompile Prima.cpp
cd coordinator-macos/../vendor/prima.cpp
cmake -B build -DGGML_METAL=1 -DCMAKE_BUILD_TYPE=Release
cmake --build build --target prima-server -j$(sysctl -n hw.ncpu)

# Verify worker is accessible
ping 192.168.10.2
```

### "Variable performance" or "latency spikes"

**Cause:** Automatic scheduling overhead.

**Solution:**
```bash
# Enable verbose logging to see scheduler decisions
./run_coordinator_prima.sh --model model.gguf --worker-ip 192.168.10.2 --verbose

# Consider switching to llama.cpp for predictable performance
```

---

## Windows-Specific Issues

### "rpc-server.exe access denied"

**Cause:** Antivirus or Windows Defender blocking execution.

**Solution:**
```powershell
# Add exception to Windows Defender
Add-MpPreference -ExclusionPath "C:\path\to\worker-windows"

# Or temporarily disable real-time protection
Set-MpPreference -MapSchedulerEnabled $false
```

### "Firewall rule already exists"

**Cause:** Rule created previously.

**Solution:**
```powershell
# Remove existing rule
Remove-NetFirewallRule -Name "kllm-RPC"

# Re-create
New-NetFirewallRule -Name "kllm-RPC" -DisplayName "Kluster LLM RPC Worker" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 50052 -Profile Any
```

---

## macOS-Specific Issues

### "Permission denied" on network setup

**Cause:** Need sudo for network configuration.

**Solution:**
```bash
sudo ./run_coordinator.sh --model model.gguf --worker-ip 192.168.10.2
```

### "Thunderbolt interface not found"

**Cause:** Interface name different on your system.

**Solution:**
```bash
# List all interfaces
networksetup -listnetworkserviceorder

# Use correct interface name
sudo networksetup -setstaticnetworksetup thunderbolt2 192.168.10.1 255.255.255.0 "" ""
```

---

## General Debugging Tips

### Enable verbose logging

```bash
# On the master (or any joined node — workers proxy to the master)
kllm logs -f
kllm logs -n 200 --source rpc
```

Logs are stored under `~/.kllm/logs/` (`agent.log`, `llama-server.log`, `rpc-server.log`, and `cluster.jsonl` on the master).

### Check system logs

```bash
# macOS
log show --predicate 'process == "llama-server"' --last 10m

# Windows
Get-Content C:\Windows\System32\LogFiles\WMI\*.log | Select-String "rpc-server"
```

### Verify hardware detection

```bash
# macOS
sysctl -n hw.memsize
sysctl -n hw.ncpu
sysctl -n hw.machine

# Windows
Get-WmiObject Win32_PnPEntity | Where-Object { $_.Name -match "AMD" } | ForEach-Object { $_.Name }
Get-WmiObject Win32_Processor | Select-Object Name, NumberOfLogicalProcessors
```

### Test network independently

```bash
# From macOS
iperf3 -s -B 192.168.10.1

# From Windows (another terminal)
iperf3 -c 192.168.10.1 -t 10
```

---

## Getting Help

If issues persist:

1. **Check logs**: Run `kllm logs -f` on the master (or any joined node) and review output
2. **Verify prerequisites**: Ensure all hardware and software requirements are met
3. **Test components separately**: Run worker and coordinator independently
4. **Check documentation**: Review [docs/layer-split-calculator.md](layer-split-calculator.md) and [docs/memory-constraints.md](memory-constraints.md)
5. **File an issue**: Provide full error output and system info
