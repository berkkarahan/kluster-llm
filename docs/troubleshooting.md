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

### `kllm ui` does not open or appears to hang

`kllm ui` starts the local agent before opening the browser. If startup fails, use the state and
log paths printed by the CLI; the agent log is normally `~/.kllm/logs/agent.log`. A corrupt
`cluster.json` now fails immediately instead of waiting for the health timeout. Do not expose or
paste its contents because it may contain cluster credentials.

If the CLI reports invalid persisted cluster state and no usable backup exists, stop the tracked
runtime and move the file aside explicitly. This creates a new node identity and clears saved
cluster membership/settings, but keeps downloaded models, logs, tokens, dashboard assets, and
runtime binaries.

PowerShell:

```powershell
uv run kllm stop
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Move-Item -LiteralPath "$env:USERPROFILE\.kllm\cluster.json" -Destination "$env:USERPROFILE\.kllm\cluster.json.corrupt-$stamp.bak"
uv run kllm ui --no-open
```

macOS/Linux:

```bash
uv run kllm stop
mv ~/.kllm/cluster.json ~/.kllm/cluster.json.corrupt-$(date +%Y%m%d-%H%M%S).bak
uv run kllm ui --no-open
```

Confirm `http://127.0.0.1:18765/health`, then run `uv run kllm ui`. Re-create or rejoin the
cluster as appropriate. Keep the renamed file until recovery is confirmed; the CLI never deletes
or silently restores corrupt state.

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

### Model search or Hugging Face link failed

Dashboard search and `kllm model download` share one resolver.

- An empty result means the repository or folder was read and contained no standalone GGUF. Documentation, projectors (`mmproj`), importance matrices (`imatrix`), and split shards are not offered as models.
- An error is a different outcome: unsupported page (dataset, Space, collection, profile), missing repository, revision, folder, or file, a file that is not a standalone GGUF, gated access, timeout, or rate limit. Correct the link or retry. Do not treat that error as an empty repository.
- A keyword search can return usable rows plus a warning when some candidate repositories could not be inspected. If every candidate fails, the search errors instead of showing an empty list.
- Gated or private repositories use the configured token (`kllm` token file, `HF_TOKEN`, or `HUGGING_FACE_HUB_TOKEN`). The dashboard does not print the token. A repository error does not prove that the repository does not exist.
- File links are exact and case-sensitive. Encode a slash inside a revision as `%2F`. Repository and tree pages must be searched, then downloaded from a result; they are not file downloads.
- CLI download still takes a file URL or `org/repo:file.gguf` / `org/repo:Q4_K_M`. Keyword search belongs to the dashboard, not the CLI.

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

The dashboard **Logs** page (`/logs`) reads the same stream for master and workers. **Stop logging** on the master stops diagnostic collection and file output across updated nodes while inference continues. **Resume logging** records new output; the stopped interval is discarded. A partial result lists pending, unreachable, unsupported or failed nodes. Upgrade/relaunch an unsupported node once to attach its independent output drain. Interpret its status text before assuming a problem:

- **Connected — no log entries** is a healthy empty poll, not an outage.
- **No entries for the selected tag** or **No entries match the selected node and tag** means this visit still holds other rows. Change **All tags**, **UI**, **Inference**, or **Unclassified**, or the node filter. That empty selection is not an outage, a cleared display, or a history gap.
- **Local only — master unreachable** means the page is showing only the serving worker's buffered records; the journal on the master is not being read. Confirm the control connection on port 18765.
- **Disconnected / cached rows** keeps the last received rows readable after a failure; they are not fresh.
- **Read error / timed out** keeps prior rows visible and reports the last successful read; a timed-out snapshot retries at the next selected interval. A stale warning clears when a later snapshot confirms recovery.
- **Master log request returned HTTP …** means the master responded but rejected or failed the request; this is not treated as network unreachability. Check the master version, authorization, and control service before relying on local-only fallback.
- **Cluster log journal is temporarily unavailable** means retained history could not be read. The master retries initialization; the logs endpoint reports 503 until the saved journal is readable. Check filesystem access and free disk space, then retry.
- **history gap — older entries omitted** or **trimmed N** means bounded retention or the 2,000-row display cap dropped older rows. Nothing was deleted from `~/.kllm/logs/cluster.jsonl`.
- **Log read timed out; retrying** means one snapshot read exceeded its five-second deadline; the page retries on the next selected interval. Repeated timeouts point at the control plane or an overloaded node.
- Clearing the display ("Display cleared — waiting for a new entry") is display-only. Stored logs and other dashboards are unaffected. After a service restart or a switch between cluster and local-only coverage, the page reports the recovery window it cannot backfill; entries generated during that window may not appear.

### Dashboard live updates

- **Live** means the dashboard WebSocket is receiving application updates. It does not certify that every source reading is fresh; check the node's own reading status and observation age.
- **Refresh** means socket delivery is interrupted. The dashboard retains the last accepted values, reads the aggregate snapshot every two seconds, and probes live delivery every 30 seconds. If ordinary reads also fail, the header shows **updates unavailable**.
- **reconnecting** means a replacement live connection is being initialized. A healthy connection with unchanged values should remain labeled **live**.
- An area labeled **denied** remains inaccessible; periodic refresh does not bypass access rules. The header shows its last successful update when available.
- On a worker, **mirrored** status/usage means the worker view includes upstream cluster information; load/download progress remains local. Logs can switch to explicitly labeled **local only** if the master cannot be reached. Check the worker's private control connection and `kllm status` before treating local-only logs as the cluster trail.
- A load/download outcome labeled **unknown** means the serving process no longer has a retained result for the operation. Check the model list and inference status; the dashboard will not infer success or replay the command.
- For Vite development, use `http://localhost:5173` or `http://127.0.0.1:5173`; the `/v1` proxy forwards WebSocket upgrades with the browser Origin intact. Do not rewrite Origin or expose worker services to make an upgrade succeed.

Inspect the snapshot without credentials when diagnosing the serving agent:

```bash
curl --get 'http://127.0.0.1:18765/v1/dashboard/live' \
  --data-urlencode 'topics=status,usage,load,download'
```

The endpoint is a dashboard read, not an authentication mechanism. Keep it on the existing private control plane. RPC port 50052 remains private and must never be exposed publicly.

### Native resource readings

macOS GPU utilization and GPU-used shared RAM come from IORegistry PerformanceStatistics,
matched to the Metal device's registry ID. Host available RAM describes the shared pool;
it is not dedicated VRAM or GPU-free memory. Windows utilization uses PDH GPU Engine
counters, summing process instances for each physical engine and reporting the busiest
engine. Device totals still use the reconciled llama.cpp capacity. Unknown driver counters
remain unsupported or failed with a source and reason in Details; zero is a measured value.
Resource schema 2 carries utilization. Updated workers negotiate schema 1 with older masters
and omit only the new field until the master is upgraded.

Run real device acceptance after rebuilding on each M1/M5/RX 9070 node:
`KLLM_TEST_HARDWARE=1 uv run python -m pytest -n 0 tests/test_native_usage.py`.
On PowerShell set `$env:KLLM_TEST_HARDWARE='1'` before running the pytest command.
The macOS reference test takes 30 paired IORegistry/native samples. A Windows compile check
does not establish RX 9070 counter accuracy; that requires its driver and hardware tests.

- **Initializing CPU** needs a second observation, normally two seconds later. Reset process
  identity, suspended/discontinuous timing, and CPU-count changes invalidate old baselines.
- **Unsupported** is not zero usage. Windows free RAM is distinct from available RAM; macOS
  GPU-wide usage is unavailable when the public interface cannot establish that scope.
- **GPU discovery error** with an empty list is not proof of a GPU-less machine. Check the
  display driver and counter provider; valid host/process readings remain usable.
- **Process identity unavailable** can occur for legacy saved records. The agent adopts an
  identity only if its saved start time matches the actual process; otherwise a normal
  managed-process relaunch is required before new process-attributed readings are trusted.
- **Timeout / source quarantined** indicates an isolated collector exceeded its deadline.
  The source is not replaced until its previous helper has exited; other sources can continue.
- If an updated Python wrapper cannot find the native collector, rebuild with
  `uv run python scripts/build.py --skip-ui`. Check the actual loaded file with
  `uv run python -c "from kllm import native; print(native.require_native().__file__)"`.
  Checkout tests use `src/` and can load an older in-tree `.pyd` instead of a rebuilt wheel.
  For that specific editable-build mismatch, `uv run maturin develop` refreshes the in-tree
  extension. Windows error 32 means a process still maps it: stop that owning agent safely
  before rebuilding; do not force-delete or overwrite a loaded DLL.
- Resource session conflicts require a fresh authenticated registration/reset handshake.
  Retrying an old generation or changing a sequence number alone does not reset its watermark.

### Information to include in reports

Runtime logs live under `~/.kllm/logs`. Include:

- Operating system, CPU, GPU, RAM, and VRAM for each node
- The committed `llama-release` value and `install.py --check` output
- `kllm status` and the relevant log excerpt
- Network type, addresses, latency, and whether WireGuard is enabled
- Model filename, quantization, context, and the `kllm plan` result

See [cli.md](cli.md), [engine-decision.md](engine-decision.md), and [memory-constraints.md](memory-constraints.md) for supported behavior.

### Managed private-network preparation and recovery

If adapter refresh reports HTTP 500 or a JSON parsing error, check `~/.kllm/logs/agent.log` for the failing `/v2/node/network-adapters` request. Older agents may abort macOS discovery on loopback or tunnel interfaces with no `networksetup` service; update and restart the agent to use discovery that skips uninspectable interfaces. The dashboard reports the request path and HTTP status when an error response is not JSON, and preserves structured prerequisite refusal codes.

Use Home → Private 10GbE network → Refresh computers. Enable administrator access locally on each computer after reading the notice and approving the OS prompt. Updated joined nodes pair automatically; a sole safe dedicated adapter is selected automatically. Multiple eligible adapters require a choice. Advanced details retains manual pairing and selection. `RPC_PROTOCOL_UNSUPPORTED` requires a matching recorded TCP-only source installation on both ends (`uv run python scripts/install.py --llama-only --rpc-tcp-only`) followed by an agent/worker restart. Installing over an already running RPC child does not prove that child's transport capabilities.

`RPC_BUSY` means the existing serial RPC server is occupied or its prior connection has not drained. Preserve that run, stop it through existing inference controls if appropriate, and retry verification. `STALE_REVISION` requires refreshed inventory/status. A degraded dedicated network refuses new selected runs; an existing managed stream keeps its frozen endpoints. Link failure never silently switches a running stream to LAN/WireGuard.

`INFERENCE_ACTIVE` blocks network stop/add/retry while a reservation, active run or unresolved admission owns its assignments. Stop inference first, then let the app obtain drain acknowledgements. Missing acknowledgements stay uncertain; they are not reported as successful cleanup. Cleanup pending on an unreachable worker is retained and retried after control connectivity returns.

After an abrupt agent restart, prior address observations and readiness are invalid. Present addresses with lost ownership remain untouched. Open that node's local cleanup review: inspect the current exact address and adapter snapshot, then either keep the address and release app ownership or explicitly confirm that exact delta for removal. A changed snapshot refuses removal and requires another review. Never delete all addresses from an adapter to resolve one journal. Graceful shutdown stops a matching dependent managed inference before closing bridges; unrelated legacy inference is identified separately.

Physical Windows/Linux/macOS and mixed-node acceptance remains required before treating this feature as validated on a particular deployment. Local controlled tests do not demonstrate 10GbE throughput or distributed model generation.

### Guided setup and administrator support

**Administrator access needed:** open Kluster on the named computer and use the local enable button. A remote master cannot open the OS approval prompt. Denial leaves the computer unconfigured; retry locally. The helper is installed by the supported build/install flow, while the agent remains in the original user account. A missing helper binary means update Kluster through [agent installation](agent-install.md).

**No eligible 10GbE connection:** check the cable, switch, adapter and negotiated speed. A 1 Gbps Ethernet connection is insufficient even when the adapter is advertised as 10GbE. Wi-Fi, virtual interfaces and adapters carrying the default or cluster-management connection are excluded. Unknown speed or DHCP/profile state requires fixing the OS configuration before setup.

**Restoration needs attention:** reconnect the named computer and refresh. If the agent or helper lost ownership continuity, open its local panel and choose **Review network recovery**. Keep current settings preserves them and releases Kluster's claim; restore removes only the exact reviewed address and restores its owned configuration. Changed snapshots require another review. Active or uncertain inference reservations block recovery.

**Linux configuration manager unsupported:** guided DHCP transitions support a readable NetworkManager connection or systemd-networkd network file. Custom DHCP plus static settings, ambiguous profiles or another configuration manager require manual review. Advanced/manual setup still supports already-configured dedicated interfaces.

**Windows private traffic blocked:** the helper adds an inbound TCP rule limited to the dedicated address, interface, signed private peers and network ports. Existing firewall rules remain. Group-policy restrictions or externally changed rules are reported; Kluster does not open public RPC access.
