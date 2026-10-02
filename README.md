# Kluster LLM

**Repository:** https://github.com/berkkarahan/kluster-llm

Distributed LLM inference across Windows, Linux, and macOS. Kluster LLM pools Metal, CUDA, ROCm, Vulkan, and CPU memory through llama.cpp RPC so a GGUF model can span heterogeneous nodes.

llama.cpp RPC is the supported inference engine. It uses pipeline layer splitting between a master and one or more `rpc-server` workers. See [docs/engine-decision.md](docs/engine-decision.md) for the engine and security rationale.

## Supported platforms

| | Metal | CUDA | ROCm | Vulkan | CPU |
|--|-------|------|------|--------|-----|
| **macOS** | yes | — | — | — | yes |
| **Linux** | — | yes | yes | fallback | yes |
| **Windows** | — | yes | not required | yes (AMD default) | yes |

Backend preference per node is NVIDIA CUDA then Vulkan, AMD ROCm on Linux then Vulkan, and Apple Metal.

## Repository structure

```text
kllm/
├── src/kllm/                 # Cross-platform CLI and control agent
├── crates/kllm-native/       # Hardware probe, planner, and process supervisor
├── scripts/                  # Python installer, builder, and validation tools
├── dashboard/                # Svelte dashboard
├── network-config/           # High-speed link setup guides
├── docs/                     # Operations and CLI documentation
├── tests/                    # Python test suite
├── pyproject.toml
└── llama-release             # Pinned stable llama.cpp release
```

## Quick start

Run the installer on every node. It installs the CLI, builds the native extension and dashboard, fetches or builds the pinned llama.cpp binaries, and optionally installs WireGuard:

```bash
uv run python scripts/install.py
# Or, when uv is not yet available:
python scripts/install.py
```

The installer stages `llama-server`, `rpc-server`, and their shared libraries in `vendor/bin`. To rebuild only the CLI and dashboard, run `uv run python scripts/build.py`.

Start the master:

```bash
uv run kllm start
```

Install the project on each worker, then join it using the key printed by the master:

```bash
uv run kllm join --key '<join-key-from-master>'
```

If mDNS discovery is unavailable, add `--master <master-lan-ip>`. For shared networks, start and join with `--overlay wireguard` on every node.

Download and run a model on the master:

```bash
uv run kllm model download 'org/repo:Q4_K_M'
uv run kllm plan --model model.Q4_K_M.gguf
uv run kllm run --model model.Q4_K_M.gguf
uv run kllm ui
```

The GGUF remains on the master. Workers contribute memory and compute through `rpc-server`; tensors are transferred automatically. The dashboard is served at `http://127.0.0.1:18765/`.

On Home, select a node to inspect measured process/host/device usage and recorded 15-minute, one-hour or 24-hour history. See [node usage monitor](docs/node-usage-monitor.md) for retention, availability and private-cluster access details.

Optional coding-harness integration:

```bash
uv run kllm connect claude
```

See [docs/cli.md](docs/cli.md) for the complete command reference. Automated agents should follow [docs/agent-install.md](docs/agent-install.md).

## Network requirements

- Keep RPC port 50052 on a private LAN or WireGuard overlay. Upstream llama.cpp RPC has no authentication.
- Prefer a dedicated 10GbE or Thunderbolt IP link for multi-node inference.
- Use stable IP addresses on dedicated links; DHCP changes interrupt active RPC sessions.
- Never expose port 50052 publicly.

Example static addresses for a dedicated link:

```bash
# macOS; replace enX with the dedicated interface
sudo networksetup -setmanual "Thunderbolt Bridge" 192.168.10.1 255.255.255.0 ""
```

```powershell
# Windows; replace Ethernet1 with the dedicated interface
New-NetIPAddress -IPAddress 192.168.10.2 -PrefixLength 24 -InterfaceAlias "Ethernet1"
```

See [network-config/10gbe-bridging-guide.md](network-config/10gbe-bridging-guide.md) for detailed network setup.

## Runtime behavior

Automatic placement prefers master-local accelerators when the model fits, then a single remote accelerator, then pooled devices. Layer counts, GPU offload, and RPC endpoints are calculated from probed capacity and GGUF metadata. Use `kllm plan` to inspect the resolved placement before launching.

Common controls are available through `kllm run` and the dashboard:

- Context length and KV-cache precision
- CPU threads, batch size, and micro-batch size
- MoE expert offload (`--moe-offload`): scatter a mixture-of-experts model's expert weights across nodes when it cannot fit in GPU memory, keeping attention layers local
- Accelerator backend and GPU selection
- Memory budgets and placement weights
- Additional llama.cpp arguments with validation of planner-owned flags

The dashboard always uses `layer` split for RPC workloads; explicit local-only CLI/API requests may use `row` or `tensor` where supported. System Settings reports measured GPU capacity and planner budgets/estimates in GiB, distinguishing GPU memory, CPU RAM, and total memory. See [docs/layer-split-calculator.md](docs/layer-split-calculator.md) and [docs/memory-constraints.md](docs/memory-constraints.md) for placement and memory details.

## Development

The Makefile is a small convenience wrapper around the maintained workflows:

```bash
make install
make build
make test
```

Equivalent direct commands are preferred on systems without Make:

```bash
npm --prefix dashboard test
uv run python -m pytest -q
PYO3_PYTHON="$(uv run python -c 'import sys; print(sys.executable)')" cargo test --manifest-path crates/kllm-native/Cargo.toml
npm --prefix dashboard run check
```

Python tests use four pytest-xdist workers by default and keep each file on one worker. Run
them serially with `uv run python -m pytest -n 0`. Dashboard tests run non-browser files with four Node
workers and the two Chrome-backed files serially to isolate their browser profiles and shared
SvelteKit state; compare the full suite serially with `npm --prefix dashboard run test:serial`.
`make test` runs the test suites sequentially before the Rust suite and dashboard check.

Python is managed with uv. After packaging changes, rebuild with `uv run python scripts/build.py` or the full installer rather than relying on `pip install -e .` alone.

## Documentation

- [Control API v2](docs/control-api-v2.md)

- [CLI reference](docs/cli.md)
- [Agent installation guide](docs/agent-install.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Engine decision and RPC security](docs/engine-decision.md)
- [Placement and inference](docs/inference-distribution.md)
- [Memory constraints](docs/memory-constraints.md)
- [Network configuration](network-config/10gbe-bridging-guide.md)

## Known limitations

- Every node must use the same pinned llama.cpp tag; mixed tags break the RPC handshake.
- RPC is unauthenticated upstream and must remain private.
- Planning is an estimate for unknown architectures and runtime execution buffers; inspect warnings before launching a tight fit.
- Large contexts can exhaust KV-cache memory even when model weights fit. Reduce context or use a smaller quantization when needed.

## License

See [LICENSE](LICENSE).
