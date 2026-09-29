# CLI — Kluster LLM router

Cross-platform entrypoint for starting/stopping a cluster, joining workers, downloading models, distributing them, and launching llama.cpp with a VRAM/RAM-aware layer split.

## Requirements

- Python 3.12 (pinned in `.python-version`; uv installs it if missing)
- One command installs the rest:

```bash
uv run python scripts/install.py
```

This creates `.venv` with **uv** (Python version from `.python-version`), installs Rust/Node if missing, builds the `kllm` package (maturin), builds the dashboard, installs/builds `llama-server` + `rpc-server` (RPC-enabled), and installs WireGuard for `--overlay wireguard`.

llama.cpp is pinned in committed [`llama-release`](../llama-release) to a stable tag (same role as `.python-version`). Every node must run the same tag or RPC HELLO fails. Sync binaries only:

```bash
python scripts/install.py --llama-only
python scripts/install.py --check
```

Run the CLI without activating the venv: `uv run kllm …` or `uvx --from . kllm …` from the repo root.

CLI/UI rebuild only: `uv run python scripts/build.py`. Optional: set `KLLM_LLAMA_SERVER` / `KLLM_RPC_SERVER` if binaries live outside `vendor/`.

**Agents:** step-by-step install + verify + recovery → [agent-install.md](agent-install.md).

## Commands

| Command | Description |
|---------|-------------|
| `kllm start [--key KEY] [--rpc-bind HOST] [--overlay none\|wireguard] [--gpus 0,1]` | Start as master, advertise via mDNS, print join key |
| `kllm join --key KEY [--master HOST] [--master-port PORT] [--rpc-bind HOST] [--overlay none\|wireguard] [--gpus 0,1]` | Join as worker (key-based mDNS discovery; `--master` skips discovery). `--port` is local agent only; `--master-port` is the remote control plane. On macOS Local Network / Zscaler blocks, auto-starts a localhost control-plane proxy (`cp_proxy_port` in status). |
| `kllm gpus` | List local GPUs (indices for `--gpus`) |
| `kllm config [--split-mode layer\|row\|tensor]` | Show local system settings or set the persistent split-mode default |
| `kllm leave` | Worker leaves the cluster |
| `kllm stop` | Stop the local agent and any llama/rpc processes this machine started (tracked in `~/.kllm/.runtime.json`) |
| `kllm cluster update` | Master: tell joined workers to `git pull --ff-only`, smart-rebuild (`install.py` if `llama-release` changed, else `build.py`), then rejoin. Requires a clean git checkout on each worker (`KLLM_REPO_ROOT` if needed). |
| `kllm status` | Role, members (same identities as Home topology), capacity, last plan, RPC bind / overlay, `apply_error` / `apply_warning` when set |
| `kllm logs [-f] [-n N] [--node ID] [--source agent\|llama\|rpc]` | Dump or follow cluster logs. Workers relay agent/llama/rpc output to the master journal so any node can debug from one stream. |
| `kllm model download <URL\|org/repo:file.gguf\|org/repo:Q4_K_M>` | Master downloads into `~/.kllm/models/` (progress on `kllm status` / dashboard). Workers do **not** need a local GGUF copy. |
| `kllm model delete <name>` | Delete a GGUF from the master model store |
| `kllm plan --model NAME …` | Dry-run plan with RUN SETTINGS + `fit_status` (`fits` / `tight` / `overflow`). RPC is used only when the model exceeds master-local capacity. |
| `kllm run --model NAME …` | Re-plan, gate on overflow, launch `llama-server` (adds `--rpc` only when the plan needs workers) |
| `kllm ui [--no-open]` | Open the Svelte dashboard at `http://127.0.0.1:18765/` |
| `kllm connect <agent>` | Launch Claude/OpenCode/Codex/… with kllm API baked in |

Control plane default port: **18765**. Worker RPC default: **50052** (LAN-only; do not expose on the public internet). Inference HTTP: **8080** (also proxied on the control port as OpenAI `/v1/chat/completions` and Anthropic `/v1/messages`).

`kllm status` prints the same member map the dashboard Home topology uses on that node (`node_id`, `host`, `role`, capacity). When a shrink replan cannot apply (overflow) or applied with a tight fit, `apply_error` / `apply_warning` are printed; a non-zero exit is not required because the cluster is still up. RPC port **50052** stays on the private bind / overlay and is never published as a public service.

`kllm logs` dumps recent cluster log lines; `kllm logs -f` follows them. Workers relay agent, llama-server, and rpc-server output to the master (`~/.kllm/logs/cluster.jsonl`) so debugging is a single stream. On a worker the CLI proxies to the master and falls back to local files if the master is unreachable. Filter with `--node` and `--source agent|llama|rpc`.

### RPC bind and optional WireGuard

By default workers bind `rpc-server` to a **private interface IP** (not `0.0.0.0`) and `kllm` best-effort opens the host firewall for TCP 50052. Pass `--rpc-bind 0.0.0.0` only for debugging.

Optional overlay (must match on master and workers):

```bash
kllm start --overlay wireguard
kllm join --key <key> --overlay wireguard
```

Requires system WireGuard (`wg` on PATH, plus `wg-quick` on Linux/macOS or WireGuard for Windows). PSK is derived from the join key; RPC moves to `10.66.87.0/24` while control-plane HTTP stays on the LAN. Dashboard Cluster panel exposes the same **RPC bind** and **Overlay** controls.

Masters advertise a non-secret join-key fingerprint (`kid`) over mDNS. Workers join with only `--key`; the agent matches that fingerprint on the LAN (falling back to a helper Python when the agent interpreter cannot see mDNS). Pass `--master` only when discovery is unavailable.

## GPU selection (multi-GPU hosts)

Before `start` / `join`, pick which local GPUs this node contributes:

```bash
kllm gpus                    # list indices / names / VRAM
kllm start --gpus 0,1
kllm join --key <key> --gpus 0
```

- **Default:** all discovered GPUs (summed VRAM for planning). On Apple Silicon, reported GPU VRAM is **80% of unified memory** (fixed convention; independent of the VRAM budget slider).
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
accel_backend = "vulkan"  # optional; Cluster Backend selector (metal|cuda|rocm|vulkan)
split_mode = "layer"      # layer (pipeline), row (legacy), or tensor (experimental)
```

Precedence for the local engine preference on Start/Join: `KLLM_ACCEL_BACKEND` env → `accel_backend` in config → hardware probe. Omit `accel_backend` (or choose **auto** in the dashboard) to expose every device the installed llama.cpp binary lists. Each cluster node may use a different backend (Metal, Vulkan, CUDA, ROCm); RPC layer splits are designed for that mix. `kllm start --backend vulkan` and `kllm join --backend metal` write config before registering.

Precedence for `plan` / `run`: CLI `--device` (or dashboard/API request body) → master config → `gpu`. For local-only CLI/API clients, split mode follows explicit request `--split-mode` → master `split_mode` config → `layer`. The dashboard always requests `layer` for estimates, plans, and runs, even when an older config contains `row` or `tensor`; its next hardware-settings save also persists `layer`.

Memory budget for plan/run uses `vram_percent` or `ram_percent` from config (as planner `headroom`) unless the request passes an explicit `headroom` / `--vram-percent` / `--ram-percent`.

- **GPU:** VRAM-aware budgets scaled by `vram_percent`; GPU multi-select via `gpu_ids` / `--gpus`; `-ngl` = estimated layers when at least one node can use GPU/Metal.
- **CPU:** RAM-only budgets scaled by `ram_percent`; `-ngl 0` (GPU picker ignored).
- **Fallback:** if GPU is preferred but a node has unknown/zero VRAM (and is not Metal), that node falls back to RAM/CPU silently. Plan responses include `device_requested`, `device_effective`, and per-node `node_devices`.

Dashboard Plan & Run exposes device-gated controls (GPUs + VRAM % vs RAM %) and persists via `PUT /v1/config`. System Settings reports measured local discrete GPU capacity in one-decimal GiB, counting each selected GPU once; missing measurements show as unavailable. Plans show usable per-device budgets limited by headroom and available memory, GPU-only and CPU RAM allocations separately, and explicitly labeled total-memory estimates/budgets. Unified GPU capacity is shared RAM, not extra VRAM.

```bash
kllm plan --model NAME --device gpu --vram-percent 70
kllm run --model NAME --device cpu --ram-percent 50
```

### Plan / run RUN SETTINGS flags

`kllm plan` and `kllm run` share the same body as the dashboard Plan & Run panel. Overflow (`fit_status=overflow`) blocks **run** (exit code 1); **tight** prints a warning and still launches.

| Flag | Default | Notes |
|------|---------|--------|
| `--context` / `--context-auto` | 4096 / off | Auto picks largest viable context under budget |
| `--cache-type-k`, `--cache-type-v` | `q8_0` | KV cache dtype |
| `--parallel`, `--batch-size`, `--micro-batch` | `auto` | Maps to llama-server parallel / batch / ubatch |
| `--flash-attention` | `auto` | `auto`, `on`, or `off` |
| `--node-share NODE=FRACTION` (repeatable) | automatic | Manual GPU layer workload shares; give every current node, fractions totaling 1. Example: `--node-share worker=0.4 --node-share master=0.6`. No CPU spill in manual mode. |
| `--chat-template` / `--chat-template-file` | `default` / — | Auto embedded GGUF, `chatml`, or a `preset:…` ID; file uploads custom Jinja text to the master. `kllm model templates --model NAME` lists choices. |
| `--vision`, `--mmproj` | off / — | Vision requires a usable mmproj on run |
| `--load-mode` | `auto` | mmap / mlock mapping |
| `--ctx-checkpoints`, `--cache-ram` | `auto` | Checkpoint / cache-RAM controls |
| `--threads` | min(8, available CPUs) | Bounded by the master's available logical CPUs |
| `--temperature` / `--top-p` / `--top-k` / `--min-p` | model default | Sampling overrides (omit = GGUF / Unsloth auto) |
| `--presence-penalty` / `--repeat-penalty` | model default | Penalty overrides |
| `--enable-thinking` / `--no-enable-thinking` | template default | Force thinking on/off for hybrid models |
| `--extra-arg` (repeatable) | — | Sanitized; cannot override `--host` / `--port` / `--rpc` |
| `--remember` / `--no-remember` | remember on | Upserts `~/.kllm/model-settings.json` after successful plan/run |
| `--vram-weight`, `--latency-weight` | 0.6 / 0.4 | Greedy placement score weights (VRAM capacity vs low latency); normalized by sum |
| `--split-mode` | system default | `layer`, `row`, or `tensor`; overrides the system setting for this plan/run |

No primary `--tensor-split` / `--tensor-parallel` (automatic layer/RPC placement: prefer master-local, then single remote, then VRAM-proportional pool — see [layer-split-calculator.md](layer-split-calculator.md)).

Manual node shares are rounded to contiguous full layers; Plan reports requested and actual bytes per node and blocks Run if the requested distribution cannot fit. Pinned preset assets come from [llama.cpp v0.5.0](https://github.com/ggml-org/llama.cpp/tree/v0.5.0/models/templates); select the version that matches your model, rather than guessing from its family name. Custom Jinja can be smoke-checked in the dashboard editor, but that check does not establish model compatibility.

### Split modes

- `layer` is the default pipeline mode and is the only mode supported across mixed backends and all RPC clusters.
- `row` is llama.cpp's legacy row-split path. It requires local backend split-buffer support and cannot use kllm RPC workers.
- `tensor` is llama.cpp's experimental tensor-parallel path. It requires local devices from one backend, Flash Attention, and `f32`, `f16`, or `bf16` KV cache types. Validate a model and hardware combination before production use.

## Dashboard

Build once, then serve via the agent:

```bash
cd dashboard && npm install && npm run build
kllm ui
```

The dashboard mirrors CLI actions (start/join/leave/stop, models, plan/run, chat) plus a Connect page for harness commands. Design tokens live in [`dashboard/DESIGN.md`](../dashboard/DESIGN.md).

### Dashboard updates

The open dashboard uses one same-origin WebSocket at `/v1/dashboard/live` for status and operation progress. Home subscribes to usage, and Logs subscribes to log events only while its refresh mode is **Live**. Initial state and recovery use the matching versioned snapshot route; explicit commands remain ordinary single requests.

The header labels delivery as **live**, **refresh**, **updates unavailable**, or **reconnecting**. If a socket closes or becomes silent, the dashboard retains the last accepted values, refreshes the aggregate snapshot every two seconds, and probes for live delivery every 30 seconds. A healthy but unchanged socket remains live. Area errors and coverage are distinct from delivery health; source readings may be stale even while delivery is live. On a worker, local-plus-mirrored status/usage is labeled as mirrored coverage; load/download progress remains local. A denied area is shown as unavailable with its safe error and last successful update when known.

Download and load operations carry source-assigned IDs. Completion effects are deduplicated during the visit; an operation that disappears before a recoverable outcome is available is shown as unknown rather than treated as success. Reconnection does not repeat commands.

### Logs page

`/logs` shows one combined master + worker trail over the same log events as `kllm logs`. Choose **All nodes**, the master, or an individual worker; entries carry a UTC timestamp, node, source, and message. The refresh control offers **Live**, **5s**, **30s**, and **1m** and defaults to Live. Live uses the dashboard feed; timed modes use snapshot reads at the selected interval. **Clear** empties only this page's displayed buffer (including rows hidden by the node filter) — it never deletes stored logs, does not affect other viewers, and keeps the selected node and refresh interval. Before the first successful load, Clear is a no-op so an in-flight result is not discarded.

The page reads a bounded recent tail (initially 200 rows, then incremental checkpoints) and renders at most 2,000 rows, disclosing older-row omissions. Each timed read has a five-second total deadline; a stalled read reports a timeout and retries on the next selected interval. If the master is unreachable from a worker-served dashboard the page labels the trail **local only**, and known retention/batch/reconnect gaps are labeled rather than presented as complete history. `follow` remains on `kllm logs -f`; the dashboard feed does not change the CLI stream contract.

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

Session-scoped env points agents at `http://127.0.0.1:18765` (token in `~/.kllm/api_token`). Hugging Face downloads use `~/.kllm/hf_token` (or `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN`) for gated repos and quant-tag listing.

## Typical flow

```bash
# Machine A (master)
kllm start
kllm model download 'TheBloke/Llama-3.1-8B-Instruct-GGUF:Q4_K_M'
# equivalent: …:llama-3.1-8b-instruct.Q4_K_M.gguf
# note the printed join key

# Machine B (worker) — no model copy; rpc-server only
kllm join --key '<join-key>'
# if mDNS fails: kllm join --key '<join-key>' --master 192.168.10.1

# Machine A
kllm plan --model llama-3.1-8b-instruct.Q4_K_M.gguf
kllm run --model llama-3.1-8b-instruct.Q4_K_M.gguf

# Later: after master has pulled/rebuilt, update workers' checkouts
kllm cluster update
```

## Architecture notes

- **Python** owns CLI, FastAPI agent, mDNS, membership, and master-local model download.
- **Rust (`kllm_native`)** owns HW capacity probe and process supervision.
- Placement (`python/kllm/placement.py` + `tensor_split.py`) prefers master-local GPUs, then single-device RPC, then VRAM-proportional multi-node pooling. Split mode is always **layer**.
- GGUF lives on the **master** only. Workers contribute memory/compute via `rpc-server -c` (tensor cache). See [plans/rpc-memory-pooling.md](../plans/rpc-memory-pooling.md).

## Manual two-machine checklist

1. High-speed link up; firewall allows TCP 18765 and 50052 (keep 50052 private).
2. `kllm start` on master; copy join key.
3. `kllm join --key …` on worker; `kllm status` on master shows the member + device inventory.
4. Download model on master only (`~/.kllm/models`); do **not** copy GGUF to workers.
5. `kllm run --model …`; hit `http://<master>:8080` for inference.
