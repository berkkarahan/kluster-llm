# CLI — Kluster LLM router

Cross-platform entrypoint for starting/stopping a cluster, joining workers, downloading models, distributing them, and launching llama.cpp with a VRAM/RAM-aware layer split.

## Requirements

- Python 3.12 (pinned in `.python-version`; uv installs it if missing)
- One command installs the rest:

```bash
uv run python scripts/install.py
```

This creates `.venv` with **uv** (Python version from `.python-version`), installs Rust/Node if missing, builds the `kllm` package (maturin), builds the dashboard, installs/builds `llama-server` + `rpc-server` (RPC-enabled), and installs WireGuard for `--overlay wireguard`.

Run the CLI without activating the venv: `uv run kllm …` or `uvx --from . kllm …` from the repo root.

CLI/UI rebuild only: `uv run python scripts/build.py`. Optional: set `KLLM_LLAMA_SERVER` / `KLLM_RPC_SERVER` if binaries live outside `vendor/`.

**Agents:** step-by-step install + verify + recovery → [agent-install.md](agent-install.md).

## Commands

| Command | Description |
|---------|-------------|
| `kllm start [--key KEY] [--rpc-bind HOST] [--overlay none\|wireguard] [--gpus 0,1]` | Start as master, advertise via mDNS, print join key |
| `kllm join --key KEY [--master HOST] [--rpc-bind HOST] [--overlay none\|wireguard] [--gpus 0,1]` | Join as worker (key-based mDNS discovery; `--master` skips discovery) |
| `kllm gpus` | List local GPUs (indices for `--gpus`) |
| `kllm leave` | Worker leaves the cluster |
| `kllm stop` | Stop the local agent and any llama/rpc processes this machine started (tracked in `~/.kllm/.runtime.json`) |
| `kllm status` | Role, members, capacity, last plan, RPC bind / overlay |
| `kllm model download <URL\|org/repo:file.gguf>` | Master downloads into `~/.kllm/models/` |
| `kllm model distribute <name>` | Master pushes GGUF to all workers (SHA-256 verified) |
| `kllm plan --model NAME [--device cpu\|gpu]` | Dry-run capacity-aware `--tensor-split` |
| `kllm run --model NAME [--device cpu\|gpu]` | Plan + launch `llama-server` with `--rpc` workers |
| `kllm ui [--no-open]` | Open the Svelte dashboard at `http://127.0.0.1:18765/` |
| `kllm connect <agent>` | Launch Claude/OpenCode/Codex/… with kllm API baked in |

Control plane default port: **18765**. Worker RPC default: **50052**. Inference HTTP: **8080** (also proxied on the control port as OpenAI `/v1/chat/completions` and Anthropic `/v1/messages`).

### RPC bind and optional WireGuard

By default workers bind `rpc-server` to a **private interface IP** (not `0.0.0.0`) and `kllm` best-effort opens the host firewall for TCP 50052. Pass `--rpc-bind 0.0.0.0` only for debugging.

Optional overlay (must match on master and workers):

```bash
kllm start --overlay wireguard
kllm join --key <key> --overlay wireguard
```

Requires system WireGuard (`wg` on PATH, plus `wg-quick` on Linux/macOS or WireGuard for Windows). PSK is derived from the join key; RPC moves to `10.66.87.0/24` while control-plane HTTP stays on the LAN. Dashboard Cluster panel exposes the same **RPC bind** and **Overlay** controls.

Masters advertise a non-secret join-key fingerprint (`kid`) over mDNS. Workers join with only `--key`; the agent matches that fingerprint on the LAN. Pass `--master` only when mDNS is unavailable.

## GPU selection (multi-GPU hosts)

Before `start` / `join`, pick which local GPUs this node contributes:

```bash
kllm gpus                    # list indices / names / VRAM
kllm start --gpus 0,1
kllm join --key <key> --gpus 0
```

- **Default:** all discovered GPUs (summed VRAM for planning).
- **Interactive:** if multiple GPUs are present, `--gpus` is omitted, and stdin is a TTY, the CLI prompts for a comma-separated list (Enter = all).
- **Dashboard:** Cluster and Plan & Run checkboxes share the same selection; Plan & Run also persists `gpu_ids` / percents via `PUT /v1/config`.
- Selection sets `CUDA_VISIBLE_DEVICES` (CUDA) or `HIP_VISIBLE_DEVICES` / `ROCR_VISIBLE_DEVICES` (ROCm) when launching `llama-server` / `rpc-server`. Metal has a single logical device and needs no env remap.

## Device preference (CPU / GPU)

Default is **GPU**. Preference is stored on the master in `~/.kllm/config.toml`:

```toml
device = "gpu"
gpu_ids = [0, 1]      # optional; omitted = all local GPUs at Start/Join
vram_percent = 80     # used when device = gpu (scales selected VRAM)
ram_percent = 80      # used when device = cpu (scales total RAM)
```

Precedence for `plan` / `run`: CLI/dashboard `--device` (or request body) → master config → `gpu`.

Memory budget for plan/run uses `vram_percent` or `ram_percent` from config (as planner `headroom`) unless the request passes an explicit `headroom` / `--vram-percent` / `--ram-percent`.

- **GPU:** VRAM-aware budgets scaled by `vram_percent`; GPU multi-select via `gpu_ids` / `--gpus`; `-ngl` = estimated layers when at least one node can use GPU/Metal.
- **CPU:** RAM-only budgets scaled by `ram_percent`; `-ngl 0` (GPU picker ignored).
- **Fallback:** if GPU is preferred but a node has unknown/zero VRAM (and is not Metal), that node falls back to RAM/CPU silently. Plan responses include `device_requested`, `device_effective`, and per-node `node_devices`.

Dashboard Plan & Run exposes device-gated controls (GPUs + VRAM % vs RAM %) and persists via `PUT /v1/config`.

```bash
kllm plan --model NAME --device gpu --vram-percent 70
kllm run --model NAME --device cpu --ram-percent 50
```

## Dashboard

Build once, then serve via the agent:

```bash
cd dashboard && npm install && npm run build
kllm ui
```

The dashboard mirrors CLI actions (start/join/leave/stop, models, plan/run, chat) plus a Connect page for harness commands. Design tokens live in [`dashboard/DESIGN.md`](../dashboard/DESIGN.md).

## Connect harnesses (Unsloth-style)

`kllm start` remains “become cluster master”. Use **`kllm connect`** to wrap coding agents:

```bash
kllm run --model my-model.Q4_K_M.gguf
kllm connect claude
kllm connect opencode --model my-model.Q4_K_M.gguf
kllm connect claude --no-launch   # print env + argv only
```

| Agent | Command |
|-------|---------|
| Claude Code | `kllm connect claude` |
| OpenCode | `kllm connect opencode` |
| OpenAI Codex | `kllm connect codex` |
| Hermes | `kllm connect hermes` |
| OpenClaw | `kllm connect openclaw` |
| Pi | `kllm connect pi` |

Flags: `--model`, `--api-key` / `KLLM_API_KEY`, `--launch`/`--no-launch`, `--serve`/`--no-serve`, `--persist`, `--yolo`, `--context`. Extra args after the agent name are passed through to the harness.

Session-scoped env points agents at `http://127.0.0.1:18765` (token in `~/.kllm/api_token`).

## Typical flow

```bash
# Machine A (master)
kllm start
kllm model download 'TheBloke/Llama-3.1-8B-Instruct-GGUF:llama-3.1-8b-instruct.Q4_K_M.gguf'
# note the printed join key

# Machine B (worker)
kllm join --key '<join-key>'
# if mDNS fails: kllm join --key '<join-key>' --master 192.168.10.1

# Machine A
kllm model distribute llama-3.1-8b-instruct.Q4_K_M.gguf
kllm plan --model llama-3.1-8b-instruct.Q4_K_M.gguf
kllm run --model llama-3.1-8b-instruct.Q4_K_M.gguf
```

## Architecture notes

- **Python** owns CLI, FastAPI agent, mDNS, membership, and model transfer.
- **Rust (`kllm_native`)** owns HW capacity probe, layer-split planner, and process supervision (generalized from the old `.sh` / `.ps1` scripts).
- Split mode is always **layer** (required for Metal + AMD mixed clusters).

## Manual two-machine checklist

1. High-speed link up; firewall allows TCP 18765 and 50052.
2. `kllm start` on master; copy join key.
3. `kllm join --key …` on worker; `kllm status` on master shows the member.
4. Download + distribute model; confirm file under `~/.kllm/models` on both.
5. `kllm run --model …`; hit `http://<master>:8080` for inference.
