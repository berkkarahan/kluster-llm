# Troubleshooting Guide

Use the Python installer and `kllm` CLI for every supported platform. Avoid manual binary copies or alternate launch scripts until the recovery steps in [agent-install.md](agent-install.md) are exhausted.

## Installation

### Re-run the supported installer

From the repository root:

```bash
uv run python scripts/install.py
# If uv is not installed yet:
python scripts/install.py
```

The installer stages `llama-server`, `rpc-server`, and their shared libraries under `vendor/bin`.

### Verify an installation

```bash
uv run python scripts/install.py --check
uv run python -c "import kllm; from kllm import native; assert native.native_available()"
uv run kllm status
```

On Windows, verify the staged executables with:

```powershell
Get-ChildItem vendor\bin\llama-server.exe, vendor\bin\rpc-server.exe
```

On Unix:

```bash
ls -l vendor/bin/llama-server vendor/bin/rpc-server
```

### llama.cpp binary missing or wrong version

Every node must use the tag committed in `llama-release`. Repair and recheck it on every node:

```bash
uv run python scripts/install.py --llama-only --force
uv run python scripts/install.py --check
```

If a release archive omits `rpc-server`, build the pinned source with RPC enabled:

```bash
uv run python scripts/install.py --llama-only --from-source --force
```

Do not copy binaries between operating systems or mix tags. Override a deliberate external installation with `KLLM_LLAMA_SERVER` and `KLLM_RPC_SERVER`.

### Build tools are missing

Allow the installer to provision its supported toolchains. If a package manager requests a shell restart, open a new terminal and rerun the same installer command. Useful checks are:

```bash
cmake --version
cargo --version
node --version
npm --version
```

On macOS, install Xcode Command Line Tools when prompted:

```bash
xcode-select --install
uv run python scripts/install.py --backend metal
```

### macOS Metal or Vulkan build failure

The default macOS build must provide Metal and Vulkan through MoltenVK. Run the installer without `--prefer-prebuilt`:

```bash
uv run python scripts/install.py --llama-only --backend metal --from-source --force
```

If Homebrew upgraded OpenSSL, MoltenVK, glslc, or SPIR-V headers, rerun the command so the installer can invalidate stale CMake paths. Use `--backend cpu` only when a CPU-only installation is intentional.

## Cluster startup

### Start a master and join a worker

```bash
# Master
uv run kllm start

# Worker
uv run kllm join --key '<join-key>'
```

If mDNS discovery fails, specify the private master address:

```bash
uv run kllm join --key '<join-key>' --master 192.168.10.1
```

Run the full installer on every node before joining. A worker does not need a GGUF; it receives tensors over RPC.

### No matching master or join returns 401

- Confirm the master is still running and copy its current join key.
- Use `--master <private-ip>` to bypass mDNS.
- Ensure the control port, normally 18765, is reachable between the nodes.
- Do not reuse a key from a previous `kllm start` session.

### RPC server is unreachable

Inspect the selected bind address and member state:

```bash
uv run kllm status
uv run python scripts/install.py --check
```

RPC port 50052 must be reachable over a private LAN or WireGuard overlay. Test it from the master:

```bash
# Unix
nc -vz <worker-ip> 50052
```

```powershell
# Windows
Test-NetConnection <worker-ip> -Port 50052
```

If Windows could not create the firewall rule automatically, run this once in an Administrator PowerShell session:

```powershell
New-NetFirewallRule -Name "kllm-RPC" -DisplayName "Kluster LLM RPC Worker" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 50052 -Profile Any
```

Never open RPC port 50052 to the public internet.

### RPC handshake or malformed-response errors

This usually means nodes have different llama.cpp builds. On every node:

```bash
uv run python scripts/install.py --llama-only --force
uv run python scripts/install.py --check
```

Restart the cluster only after every node reports the same tag.

### WireGuard overlay fails

Confirm WireGuard is installed on every node:

```bash
wg --version
```

On Windows, also check `C:\Program Files\WireGuard\wg.exe`. Start and join with `--overlay wireguard` consistently; do not mix overlay and plain-LAN nodes in the same session.

## Planning and inference

### Model not found

List or download models through the CLI:

```bash
uv run kllm model list
uv run kllm model download 'ORG/REPO:Q4_K_M'
```

Pass a listed model name or an absolute GGUF path to both planning and execution:

```bash
uv run kllm plan --model /absolute/path/model.gguf
uv run kllm run --model /absolute/path/model.gguf
```

### Out of memory

Inspect the plan before launching:

```bash
uv run kllm plan --model model.gguf --context 2048 --micro-batch 1
```

Then apply the same safe settings to the run:

```bash
uv run kllm run --model model.gguf --context 2048 --micro-batch 1
```

If the model still does not fit, use a smaller quantization or model, free memory on participating nodes, or lower the configured memory headroom. Placement and layer allocation are automatic; do not supply a manual `--tensor-split` override.

### No GPU is available

Check what the installed llama.cpp binary reports:

```bash
vendor/bin/llama-server --list-devices
```

On Windows use `vendor\bin\llama-server.exe`. A saved backend preference cannot add a backend that was not compiled into the binary. Reinstall with the required backend, then restart the node.

### Slow inference

- Run `kllm plan` and inspect CPU spill, selected devices, and warnings.
- Confirm the high-speed interface is carrying RPC traffic.
- Check GPU utilization and thermal throttling on every node.
- Reduce context or choose a smaller quantization when memory pressure causes spill.
- Prefer the default `layer` split for heterogeneous clusters.

### Output quality or precision drift

Use `layer` split mode across mixed Metal, CUDA, ROCm, or Vulkan backends:

```bash
uv run kllm config --split-mode layer
uv run kllm run --model model.gguf
```

Row and tensor modes are only appropriate where the planner accepts the selected homogeneous/local backend combination.

## Network diagnosis

Use stable private addresses on dedicated links. Verify both directions with `ping`, then verify the control and RPC ports separately. High latency, packet loss, or DHCP address changes can interrupt inference even when cluster discovery succeeds.

For 10GbE and Thunderbolt IP setup, see [../network-config/10gbe-bridging-guide.md](../network-config/10gbe-bridging-guide.md).

## Logs and support

Use the cluster log command rather than launching child processes manually:

```bash
uv run kllm logs
uv run kllm status
```

The dashboard **Logs** page (`/logs`) reads the same stream for master and workers. Interpret its status text before assuming a problem:

- **Connected — no log entries** is a healthy empty poll, not an outage.
- **Local only — master unreachable** means the page is showing only the serving worker's buffered records; the journal on the master is not being read. Confirm the control connection on port 18765.
- **Disconnected / cached rows** keeps the last received rows readable after a failure; they are not fresh.
- **Read error / timed out** keeps prior rows visible and reports the last successful read; a timed-out snapshot retries at the next selected interval. A stale warning clears when a later snapshot confirms recovery.
- **Master log request returned HTTP …** means the master responded but rejected or failed the request; this is not treated as network unreachability. Check the master version, authorization, and control service before relying on local-only fallback.
- **Cluster log journal is temporarily unavailable** means retained history could not be read. The master retries initialization; the logs endpoint reports 503 until the saved journal is readable. Check filesystem access and free disk space, then retry.
- **history gap — older entries omitted** or **trimmed N** means bounded retention or the 2,000-row display cap dropped older rows. Nothing was deleted from `~/.kllm/logs/cluster.jsonl`.
- **Log read timed out; retrying** means one snapshot read exceeded its five-second deadline; the page retries on the next selected interval. Repeated timeouts point at the control plane or an overloaded node.
- Clearing the display ("Display cleared — waiting for a new entry") is display-only. Stored logs and other dashboards are unaffected. After a service restart or a switch between cluster and local-only coverage, the page reports the recovery window it cannot backfill; entries generated during that window may not appear.

Runtime logs live under `~/.kllm/logs`. When reporting a problem, include:

- Operating system, CPU, GPU, RAM, and VRAM for each node
- The committed `llama-release` value and `install.py --check` output
- `kllm status` and the relevant log excerpt
- Network type, addresses, latency, and whether WireGuard is enabled
- Model filename, quantization, context, and the `kllm plan` result

See [cli.md](cli.md), [engine-decision.md](engine-decision.md), and [memory-constraints.md](memory-constraints.md) for supported behavior.
