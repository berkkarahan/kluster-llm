# CLI — Kluster LLM router

Cross-platform entrypoint for starting/stopping a cluster, joining workers, downloading models, distributing them, and launching llama.cpp with a VRAM/RAM-aware layer split.

## Requirements

- Python 3.12 (pinned in `.python-version`; uv installs it if missing)
- One command installs the rest:

```bash
uv run python scripts/install.py
```

This creates `.venv` with **uv** (Python version from `.python-version`), installs Rust/Node if missing, builds the `kllm` package (maturin), builds the dashboard, installs/builds `llama-server` + `rpc-server` (RPC-enabled), and installs WireGuard for `--overlay wireguard`.

The committed [`llama-release`](../llama-release) is the stable bootstrap default. Settings → **llama.cpp** also stores upstream releases and public forks, resolved to exact repository/commit snapshots. The master can persist a target and synchronize source builds to children, including future joins/reconnections. Builds prepare beside active inference; new runs require the master and selected workers to match. Backend builds may differ by node. Bootstrap binaries can still be synchronized with:

```bash
python scripts/install.py --llama-only
python scripts/install.py --check
```

After install, run **`kllm …` from any directory** (shim in `~/.local/bin`, same as `uv run kllm …` for this repo). Without the shim: `uv run kllm …` or `uvx --from . kllm …` from the repo root. Use `python scripts/install.py --skip-path-shim` to omit the global launcher.

CLI/UI rebuild only: `uv run python scripts/build.py`. Optional: set `KLLM_LLAMA_SERVER` / `KLLM_RPC_SERVER` if binaries live outside `vendor/`.

**Agents:** step-by-step install + verify + recovery → [agent-install.md](agent-install.md).

## Commands

| Command | Description |
|---------|-------------|
| `kllm start [--key KEY] [--rpc-bind HOST] [--overlay none\|wireguard] [--gpus 0,1]` | Start as master, advertise via mDNS, print join key |
| `kllm join --key KEY [--master HOST] [--master-port PORT] [--rpc-bind HOST] [--overlay none\|wireguard] [--gpus 0,1]` | Join as worker (key-based mDNS discovery; `--master` skips discovery). `--port` is local agent only; `--master-port` is the remote control plane. On macOS Local Network / Zscaler blocks, auto-starts a localhost control-plane proxy (`cp_proxy_port` in status). |
| `kllm gpus` | List local GPUs (indices for `--gpus`) |
| `kllm config [--split-mode layer\|row\|tensor]` | Show local system settings or set the persistent split-mode default |
| `kllm flags [<name>] [--enable\|--disable]` | Show or change local dashboard feature flags. `private-network-setup` gates the Home → Private 10GbE network panel (disabled by default); omit `<name>` to list all flags |
| `kllm leave` | Worker leaves the cluster |
| `kllm stop` | Stop the local agent and any llama/rpc processes this machine started (tracked in `~/.kllm/.runtime.json`) |
| `kllm update [--port PORT]` | Local maintenance: stop this node, `git pull --ff-only` (requires a clean checkout), then full rebuild (`scripts/install.py --sync`). Node stays stopped; start or join again when it finishes. |
| `kllm cluster update` | Master: tell joined workers to `git pull --ff-only`, smart-rebuild (`install.py` if `llama-release` changed, else `build.py`), then rejoin. Requires a clean git checkout on each worker (`KLLM_REPO_ROOT` if needed). |
| `kllm status` | Role, members (same identities as Home topology), capacity, last plan, RPC bind / overlay, `apply_error` / `apply_warning` when set |
| `kllm logs [-f] [-n N] [--node ID] [--source agent\|llama\|rpc]` | Dump or follow cluster logs. Workers relay agent/llama/rpc output to the master journal so any node can debug from one stream. |
| `kllm model download <URL\|org/repo:file.gguf\|org/repo:Q4_K_M>` | Master downloads into `~/.kllm/models/` (progress on `kllm status` / dashboard). Workers do **not** need a local GGUF copy. A Hugging Face file URL keeps its revision and full path. Repository and tree page URLs are not downloads. |
| `kllm model delete <name>` | Delete a GGUF from the master model store |
| `kllm plan --model NAME …` | Dry-run plan with RUN SETTINGS + `fit_status` (`fits` / `tight` / `overflow`). RPC is used only when the model exceeds master-local capacity. |
| `kllm run --model NAME …` | Re-plan, start RPC on selected workers under master control, then launch `llama-server` (adds `--rpc` only when the plan needs workers) |
| `kllm rebuild` | Run full `scripts/install.py --sync` from the detected repo root (stages in order in one process; honors `KLLM_REPO_ROOT`) |
| `kllm ui [--no-open]` | Open the Svelte dashboard at `http://127.0.0.1:18765/` |
| `kllm connect <agent>` | Launch Claude/OpenCode/Codex/… with kllm API baked in |

Control plane default port: **18765**. Worker RPC default: **50052** (LAN-only; do not expose on the public internet). Inference HTTP: **8080** (also proxied on the control port as OpenAI `/v1/chat/completions` and Anthropic `/v1/messages`).

`kllm status` prints the same member map the dashboard Home topology uses on that node (`node_id`, `host`, `role`, capacity). When a shrink replan cannot apply (overflow) or applied with a tight fit, `apply_error` / `apply_warning` are printed; a non-zero exit is not required because the cluster is still up. RPC port **50052** stays on the private bind / overlay and is never published as a public service.

`kllm logs` dumps recent cluster log lines; `kllm logs -f` follows them. Workers relay agent, llama-server, and rpc-server output to the master (`~/.kllm/logs/cluster.jsonl`) so debugging is a single stream. On a worker the CLI proxies to the master and falls back to local files if the master is unreachable. Filter with `--node` and `--source agent|llama|rpc`.

### RPC bind and optional WireGuard

Joining establishes membership and networking and leaves the worker idle. The master starts `rpc-server` only when a model run allocates work to that node, using the saved GPU selection, private bind address, and RPC port. Startup best-effort prepares the host firewall for that endpoint. Wildcard binds are rejected by runtime control; choose one private interface address.

Stopping or replacing the model releases that run's RPC processes while the workers stay joined. Planning and previews start no RPC processes. The same lifecycle applies to LAN, WireGuard, and managed private networks: private-network setup verifies addresses, routes, and signed connectivity; runtime compatibility checks and RPC relay admission occur when inference starts.

Workers accept signed commands from their paired current master. Runtime ownership expires after 60 seconds without renewal (the master renews every 5 seconds). Worker heartbeat, software installation, and agent restart do not start RPC. If the local llama-server dies, or a worker renewal or private-network check fails on two consecutive samples, the master stops the affected run and reloads its saved settings with the same selected workers, trying at 1-, 2-, and 4-second intervals. One missed renewal does not reload the model. Stop cancels recovery. Three failed attempts leave inference stopped with an error; 60 seconds of healthy serving resets the retry budget. Interrupted inference requests are not replayed.

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

- **Default:** every discrete GPU, summed for planning. An integrated adapter (iGPU / APU graphics) shares host RAM and stays unselected while a discrete card is present; a machine whose only accelerator is integrated keeps it. On Apple Silicon, **reported** GPU VRAM is **80% of unified memory** (fixed probe convention; independent of the VRAM budget slider). Planning treats unified-memory nodes — Apple Silicon, and detected unified APUs such as AMD Strix Halo — as having **65% of total RAM** as effective VRAM before `vram_percent` / headroom. A machine that also has a discrete card is planned as a discrete node. Override that share with `KLLM_UNIFIED_VRAM_FRACTION` (clamped to 0.10–0.95). Set `unified_memory = true` or `false` in `~/.kllm/config.toml` to force this node's classification.
- **Interactive:** if multiple GPUs are present, `--gpus` is omitted, and stdin is a TTY, the CLI prompts for a comma-separated list (Enter = default selection; integrated adapters are labelled).
- **Dashboard:** Cluster and Plan & Run checkboxes share the same selection; Plan & Run also persists `gpu_ids` / percents via `PUT /v1/config`.
- Selection sets `CUDA_VISIBLE_DEVICES` (CUDA) or `HIP_VISIBLE_DEVICES` / `ROCR_VISIBLE_DEVICES` (ROCm) when launching `llama-server` / `rpc-server`. Metal has a single logical device and needs no env remap.

## Device preference (CPU / GPU)

Default is **GPU**. Preference is stored on the master in `~/.kllm/config.toml`:

```toml
device = "gpu"
gpu_ids = [0, 1]      # optional; omitted = all discrete local GPUs at Start/Join
vram_percent = 80     # used when device = gpu (scales selected VRAM)
ram_percent = 80      # used when device = cpu (scales total RAM)
accel_backend = "vulkan"  # optional; Cluster Backend selector (metal|cuda|rocm|vulkan)
split_mode = "layer"      # layer (pipeline), row (legacy), or tensor (experimental)
```

Precedence for the local engine preference on Start/Join: `KLLM_ACCEL_BACKEND` env → `accel_backend` in config → hardware probe. Omit `accel_backend` (or choose **auto** in the dashboard) to expose every device the installed llama.cpp binary lists. Each cluster node may use a different backend (Metal, Vulkan, CUDA, ROCm); RPC layer splits are designed for that mix. `kllm start --backend vulkan` and `kllm join --backend metal` write config before registering.

Precedence for `plan` / `run`: CLI `--device` (or dashboard/API request body) → master config → `gpu`. For local-only CLI/API clients, split mode follows explicit request `--split-mode` → master `split_mode` config → `layer`. The dashboard always requests `layer` for estimates, plans, and runs, even when an older config contains `row` or `tensor`; its next hardware-settings save also persists `layer`.

Memory budget for plan/run uses `vram_percent` or `ram_percent` from config (as planner `headroom`) unless the request passes an explicit `headroom` / `--vram-percent` / `--ram-percent`.

- **GPU:** VRAM-aware budgets scaled by `vram_percent`; GPU multi-select via `gpu_ids` / `--gpus`; `-ngl` = estimated layers when at least one node can use GPU/Metal.
- **CPU:** RAM-only budgets scaled by `ram_percent`; GPU picker ignored. Master CPU layers use the local prefix; worker CPU layers use RPC offload routing, so their upstream `ngl` can be positive while VRAM allocation is zero.
- **Fallback:** automatic GPU planning tries GPU placements before CPU RAM. Updated workers advertise a CPU RPC device to contribute RAM; older workers remain accelerator-only. Plan responses distinguish RAM/GPU allocations per node and count unified memory once.

Dashboard deployment controls affect the next deployment. Node Settings owns hardware selection and persistent RAM/VRAM safety caps. Optional deployment RAM and VRAM limits are independent; each node uses the lower of its safety cap and the deployment limit, bounded by available memory. Unified GPU capacity is shared RAM, not extra VRAM. The persistent memory summary reports separate components, incomplete estimates, warnings, and resolved settings throughout Select, Configure, and Review.

```bash
kllm plan --model NAME --device gpu --vram-percent 70
kllm run --model NAME --device cpu --ram-percent 50
```

### Plan / run RUN SETTINGS flags

`kllm plan` and `kllm run` share the same body as the dashboard Plan & Run panel. Overflow (`fit_status=overflow`) blocks **run** (exit code 1); **tight** prints a warning and still launches.

Commands load the model's saved configuration by default. Explicit flags override saved values; `--settings defaults` starts from Kluster defaults. The defaults below apply when no saved value exists. Preview and Plan do not save automatically. Save explicitly in the dashboard, or pass `--remember` to save after success. Restore defaults edits the draft; Save makes that reset persistent. There is one saved configuration per model.

| Flag | Default | Notes |
|------|---------|--------|
| `--context` / `--context-auto` | 4096 / off | Auto picks largest viable context under budget |
| `--cache-type-k`, `--cache-type-v` | `q8_0` | KV cache dtype |
| `--parallel`, `--batch-size`, `--micro-batch` | `auto` | Maps to llama-server parallel / batch / ubatch. Batch accepts up to 2048; micro-batch accepts up to 512 (defaults stay 512 / 128) |
| `--moe-offload N` | `auto` | Offload MoE expert weights to remote nodes when the model does not fit GPU memory; `auto` uses a heuristic, a count offloads the first N layers' experts, `0` disables |
| `--flash-attention` | `auto` | `auto`, `on`, or `off` |
| `--node-share NODE=FRACTION` (repeatable) | automatic | Manual GPU layer workload shares; give every current node, fractions totaling 1. Example: `--node-share worker=0.4 --node-share master=0.6`. No CPU spill in manual mode. |
| `--require-node NODE` (repeatable) | none | Require at least one model layer on each node in automatic layer placement. The planner chooses GPU/RAM amounts. Incompatible with manual shares and row/tensor modes. |
| `--chat-template` / `--chat-template-file` | `default` / — | Auto embedded GGUF, `chatml`, or a `preset:…` ID; file uploads custom Jinja text to the master. `kllm model templates --model NAME` lists choices. |
| `--vision`, `--mmproj` | off / — | Vision requires a usable mmproj on run |
| `--load-mode` | `auto` | mmap / mlock mapping |
| `--ctx-checkpoints`, `--cache-ram` | `auto` | Checkpoint / cache-RAM controls |
| `--threads` | min(8, available CPUs) | Bounded by the master's available logical CPUs |
| `--temperature` / `--top-p` / `--top-k` / `--min-p` | model default | Sampling overrides (omit = GGUF / Unsloth auto) |
| `--presence-penalty` / `--repeat-penalty` | model default | Penalty overrides |
| `--enable-thinking` / `--no-enable-thinking` | template default | Force thinking on/off for hybrid models |
| `--extra-arg` (repeatable) | — | Sanitized; cannot override `--host` / `--port` / `--rpc` |
| `--settings saved\|defaults` | saved | Saved model configuration or clean defaults |
| `--remember` / `--no-remember` | off | Explicitly save after successful plan/run |
| `--threads-batch` | physical cores, at least `--threads` | Prompt-processing threads on the master. Worker `rpc-server` CPU devices use the same count instead of the binary default of 8 |
| `--draft-max` | 3 | Maximum draft tokens when draft-model speculation is enabled |
| `--seed` | engine default | -1 random, or an unsigned 32-bit seed |
| `--max-output-tokens` | engine default | Positive response limit or -1 |
| `--frequency-penalty` | model / Kluster default | Generation penalty |
| `--stop` (repeatable) | inherited | Literal stop sequences |
| `--reasoning-effort`, `--reasoning-budget`, `--reasoning-format` | inherited | Template and installed-engine capability checks apply |
| `--chat-template-text` | — | Inline custom Jinja; also used in exported commands |
| `--vram-weight`, `--latency-weight` | 0.6 / 0.4 | Greedy placement score weights (VRAM capacity vs low latency); normalized by sum |
| `--split-mode` | system default | `layer`, `row`, or `tensor`; overrides the system setting for this plan/run |

No primary `--tensor-split` / `--tensor-parallel` (automatic layer/RPC placement: prefer master-local, then single remote, then VRAM-proportional pool — see [layer-split-calculator.md](layer-split-calculator.md)).

The dashboard uses the v2 schema and an installed `llama-server --help` catalog. Unknown fields, conflicting typed/extra options, managed flags, unsupported flags, and invalid arguments fail before the running model is stopped. API `extra_args` is a literal argv array; CLI `--extra-arg` strings are shell-tokenized once. The Expert editor accepts JSON argv for lossless paths and JSON values. Review exports both JSON and a POSIX-shell command. Legacy v1 retains permissive fields and historical remember behavior; old profiles are normalized on read.

Deployment records an active configuration tied to its process and run ID. Gateway requests inherit that snapshot, with explicit client values taking precedence. Chat overrides belong to the session and do not edit deployment drafts or saved settings. Responses, Chat Completions, and Messages use their protocol-specific output/stop fields. Messages cannot represent every sampling override; unsupported per-request fields return an error rather than being silently discarded. Deployment defaults still apply through the engine.

Automatic planning prefers GPU-only placements, then uses master and advertised
worker CPU RAM when needed. `--require-node master-id --require-node worker-id`
requires model layers on both nodes without imposing equal shares. The dashboard
provides the same option as **Must use this node** in Automatic placement. Rebuild
and rejoin workers to advertise CPU devices and refresh corrected macOS capacity.

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

### Native resource probes

Windows and macOS agents can collect resource readings through the Rust extension. Node
details distinguish host CPU/RAM, managed-process CPU/resident RAM, and device-wide memory.
Host/device readings continue without a managed child. CPU uses total logical processor
capacity as 100%; its first observation initializes a baseline instead of reporting zero.
Memory values are bytes at the native boundary and one-decimal GiB in the CLI and dashboard. Device capacity is the engine-reported `llama-server --list-devices` total for the selected GPUs. An integrated adapter that shares host RAM is listed separately (with an `integrated` flag) and is not added to the node's VRAM total while a discrete card is selected.

For an on-demand host/device snapshot from the repository environment:

```text
uv run python -c "import json; from kllm import native; print(json.dumps(native.probe_resources(node_id='local', managed_processes=[]), indent=2)); native.close_resource_probes()"
```

The agent supplies registered process identities automatically; the library rejects arbitrary
or mismatched process selections. Readings include observation times, scope, units and
`measured`, `initializing`, `unsupported` or `error` status. Private owned helpers isolate
blocked sources; no additional monitoring service or network port is installed.

| Platform | Memory semantics / validation |
|----------|-------------------------------|
| Windows WDDM | Adapter-wide dedicated/shared usage; dedicated free only for a compatible physical pool. Dedicated capacity shown with usage is the `--list-devices` total; the DXGI dedicated total is kept in the metric `detail` for reconciliation. Thirty paired used/free comparisons passed on an AMD RX 9070 XT (driver 32.0.31041.1004); other vendor/driver combinations require validation. |
| Windows RAM | Available includes reclaimable memory. True free is unsupported by the initial provider and is not replaced with available. |
| Apple Silicon | GPU memory shares host RAM and is counted once. Device-wide GPU used/free remain unsupported; the profiler's own Metal allocations and allocation budgets are not usage. |
| Intel macOS | Devices are classified using actual Metal metadata, including discrete memory. Native code cross-compiles; runtime/hardware acceptance remains pending. |
| Linux / older extension | Existing monitoring remains the compatibility path. New native counters are not implied by that fallback. |

Resource snapshots use the existing usage/live transport. Old masters receive legacy readings;
new masters negotiate the optional resource format and reject previous-session/replayed data.
Process history retains its existing format and limits; host-only live snapshots are transient.
Node capacity and model-placement budgets use that same `--list-devices` total. The registry or SMI sum remains only when the engine inventory cannot be read.

### Logs page

`/logs` shows one combined master + worker trail over the same log events as `kllm logs`. Choose **All nodes**, the master, or an individual worker. Entries carry a UTC timestamp, node, source, tag, and message. The tag is **UI** for kllm control-application logs, **Inference** for model serving and the activity that spreads that serving across machines, or **Unclassified** when the producer is unknown. Filter with **All tags**, **UI**, **Inference**, or **Unclassified**. A new visit starts on **All tags**. The filter changes only this visit's display and does not request a different log feed. `kllm logs --source` remains `agent`, `llama`, or `rpc`. There is no `--tag` flag. The refresh control offers **Live**, **5s**, **30s**, and **1m** and defaults to Live. Live uses the dashboard feed; timed modes use snapshot reads at the selected interval. **Clear** empties only this page's displayed buffer (including rows hidden by the node or tag filter) — it never deletes stored logs, does not affect other viewers, and keeps the selected node, tag, and refresh interval. Before the first successful load, Clear is a no-op so an in-flight result is not discarded.

The page reads a bounded recent tail (initially 200 rows, then incremental checkpoints) and renders at most 2,000 rows, disclosing older-row omissions. Each timed read has a five-second total deadline; a stalled read reports a timeout and retries on the next selected interval. If the master is unreachable from a worker-served dashboard the page labels the trail **local only**, and known retention/batch/reconnect gaps are labeled rather than presented as complete history. `follow` remains on `kllm logs -f`; the dashboard feed does not change the CLI stream contract.

## Connect harnesses (Unsloth-style)

`kllm start` remains “become cluster master”. Use **`kllm connect`** to wrap coding agents:

```bash
kllm run --model my-model.Q4_K_M.gguf
kllm connect claude
kllm connect opencode --model my-model.Q4_K_M.gguf
kllm connect hermes --url 'http://127.0.0.1:18765' --model 'Ornith-1.5-35B-A3B-Q6_K_L.gguf'
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

Flags: `--model`, `--api-key` / `KLLM_API_KEY`, `--url` / `KLLM_URL`, `--timeout` (readiness seconds; default 600), `--launch`/`--no-launch`, `--serve`/`--no-serve`, `--persist`, `--yolo`, `--context`. Extra args after the agent name are passed through to the harness.

`--kllm-skills all|operations|development|none` selects the provisioned Kluster
skill pack (default `all`). The [library](../skills/README.md) includes dedicated
Windows/macOS/Linux private Ethernet guidance (10GbE preferred). Selected copies live in isolated agent homes
or temporary session skill sources; ordinary profiles and other native skills
are not rewritten. Persistent sessions refresh only owned unchanged copies and
require review for local edits. `none` removes unchanged persistent session-owned
copies but does not disable native project/user skill discovery. Temporary copies
are cleaned on exit, launch failure and `--no-launch`. Missing library sources
produce an actionable note; malformed discovered libraries fail clearly.

CONNECT commands include the dashboard endpoint and selected model, quoted for Bash or PowerShell. Explicit loopback endpoints (`localhost`, IPv4 loopback addresses such as `127.0.0.1`, and `[::1]`) reuse the local token in `~/.kllm/api_token` (or the configured `KLLM_HOME`) when no key is supplied. This applies to both `--url` and `KLLM_URL`. `--api-key` takes precedence over `KLLM_API_KEY`, and both override the local token. Remote endpoints require `--api-key` or `KLLM_API_KEY` set to the master's token. A worker endpoint resolves to its master. Connection waits for the authenticated served catalog and uses the exact upstream model ID, including a path when llama.cpp advertises one. An already running different model is rejected; an idle master requires an explicit `--model`. Codex uses the native `/v1/responses` gateway; the other OpenAI-compatible clients use Chat Completions and Claude uses Messages. The pinned llama.cpp runtime implements these protocols. `--no-launch` performs the same readiness checks, prints a redacted plan, and removes temporary configuration directories.

OpenCode v2 starts a private server so the session provider configuration is inherited; an existing background service keeps its own settings. The Responses gateway combines only leading text developer/system messages into the instruction preamble for GGUF templates requiring one initial system turn. Tools and subsequent conversation input retain their native protocol representation. Installed-client smoke checks can be run with `KLLM_TEST_HARNESS_SMOKE=1 uv run python -m pytest -n 0 tests/test_connect_harness_smoke.py` while inference is already ready; these use temporary configurations and a private gateway. Claude, Hermes and OpenClaw require separate installed-client acceptance when available.

The master dashboard's **Logs → Stop logging** persists a cluster-wide policy. It stops agent, llama-server and rpc-server diagnostic file writes, collection, forwarding, journal writes and new live log delivery. Existing history remains readable; output generated while stopped is discarded and is never replayed on resume. Inference and RPC PIDs remain running across subsequent Stop/Resume toggles. New workers and restarted agents inherit the persisted setting. Nodes without the updated output helper report **unsupported**; unreachable or pending nodes keep the result **partial**, so Stop does not claim success for them. Upgrade and relaunch each existing agent/runtime once to attach the helpers. The policy controls diagnostic logs; telemetry history and explicit build/update operation reports have their own lifecycle.

Control API: `GET /v2/cluster/logging`; authenticated `PUT /v2/cluster/logging` with `{"enabled": false}` or `true`. The response includes the desired generation and each node's acknowledgement. Worker heartbeats reconcile missed policy changes.

Session-scoped env points agents at `http://127.0.0.1:18765` (token in `~/.kllm/api_token`). Hugging Face downloads use `~/.kllm/hf_token` (or `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN`) for gated repos and quant-tag listing.

The dashboard MODELS search accepts the same CLI sources plus keywords. It also accepts `huggingface.co` and `hf.co` repository, tree, and file links (`blob`, `resolve`, `raw`, and `show_file_info`). A revision that contains `/` must be percent-encoded in the URL (`release%2Fv2`); an unencoded `/tree/release/v2` means revision `release` and folder `v2`. Search lists standalone GGUF files and does not start a transfer. Download stays an explicit action in the results modal and stores the file under its basename. Unknown size stays unknown. Keyword search is dashboard-only; it is not a CLI source.

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
- Placement (`src/kllm/placement.py` + `tensor_split.py`) prefers master-local GPUs, then single-device RPC, then VRAM-proportional multi-node pooling. Split mode is always **layer**.
- GGUF lives on the **master** only. Workers contribute memory/compute via `rpc-server -c` (tensor cache). See [plans/rpc-memory-pooling.md](../plans/rpc-memory-pooling.md).

## Manual two-machine checklist

1. High-speed link up; firewall allows TCP 18765 and 50052 (keep 50052 private).
2. `kllm start` on master; copy join key.
3. `kllm join --key …` on worker; `kllm status` on master shows the member + device inventory.
4. Download model on master only (`~/.kllm/models`); do **not** copy GGUF to workers.
5. `kllm run --model …`; hit `http://<master>:8080` for inference.

### App-managed dedicated Ethernet

Home → Private 10GbE network guides setup from the master. The panel is hidden by default; show it with `kllm flags private-network-setup --enable` (hide it again with `--disable`). First install/update Kluster and join the intended cluster on every computer. Connect a dedicated physical Ethernet adapter negotiating at least 1 Gbps (10 Gbps preferred), keeping ordinary cluster management on a separate adapter.

The panel explains administrator access before requesting permission. On each computer, acknowledge that notice and click **Enable administrator access on this computer**. Approve the operating system prompt once. Kluster installs a protected networking helper; the agent and its credentials stay under your original account. The master can then handle the dedicated adapter settings for all authorized computers.

Both runtime binaries require a recorded TCP-only source build. On each participating node, use the supported installer and then restart its worker/agent:

```bash
uv run python scripts/install.py --llama-only --rpc-tcp-only
```

Return to the master, refresh the computer list, and click **Set up private network**. Initial node pairing, a sole safe adapter, private subnet and addresses are handled automatically. If multiple suitable adapters exist, choose one in the computer row. A slow or unknown link, missing authorization, unsupported runtime or unsafe configuration gets a readable status.

All participants prepare before address changes. Kluster saves dedicated DHCP settings, disables IPv4 DHCP where needed, and restores owned changes on stop or cancellation. Existing LAN/WireGuard management paths, DNS profiles and the original RPC child are preserved. Readiness requires signed dedicated path verification and a released valid RPC protocol probe. `RPC_BUSY` leaves the address configured with verification pending.

On Plan & Run, choose the dedicated network explicitly for that request. The default remains LAN/WireGuard. The selector is excluded from remembered model settings. Run diagnostics expose logical dedicated endpoints and the actual master loopback bridge endpoints. Stop inference before stopping or changing a dependent network. Retry/add/cancel and local cleanup review are available in the network panel. Temporary addresses survive only as long as the host OS retains them; they are not persistent adapter profiles. Keep upstream RPC port 50052 off public networks.

Administrator access can be revoked under **Advanced details** once the private network and cleanup are settled. That section retains manual identity pairing, adapter selection, subnet override and diagnostics. If ownership is lost after a restart or external changes, use **Review network recovery** locally and choose whether to keep the current settings or restore the reviewed Kluster changes. Recovery never silently deletes an ambiguous address.
