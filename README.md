# Kluster LLM

Distributed LLM inference across Windows, Linux, and macOS. Kluster LLM pools Metal, CUDA, ROCm, Vulkan, and CPU memory through llama.cpp RPC so one GGUF can span mixed machines.

The model file stays on the master. Workers add memory and compute through `rpc-server`. llama.cpp splits the model by layer between the master and those workers. [docs/engine-decision.md](docs/engine-decision.md) records why this is the supported engine.

**Repository:** https://github.com/berkkarahan/kluster-llm

## Private network

Run every node on a private network you control: a dedicated Ethernet or Thunderbolt link, or a trusted lab LAN.

- **50052** is llama.cpp RPC. Upstream RPC has no authentication. Reachability is enough to join inference. The HTTP API token does not protect this port.
- **18765** is the control plane and the dashboard. Keep it on that same private network.

Details and how to report a vulnerability are in [SECURITY.md](SECURITY.md).

## Platforms

| | Metal | CUDA | ROCm | Vulkan | CPU |
|--|-------|------|------|--------|-----|
| **macOS** | yes | — | — | yes (MoltenVK) | yes |
| **Linux** | — | yes | yes | yes (fallback) | yes |
| **Windows** | — | yes | not required | yes (AMD default) | yes |

On each node the planner prefers Metal, then NVIDIA CUDA, then AMD ROCm on Linux, then Vulkan, then CPU. One physical GPU is bound to a single backend.

## Run a cluster

Install on every node. The installer builds the CLI, native extension, and dashboard, and fetches or builds the pinned llama.cpp binaries:

```bash
uv run python scripts/install.py
# when uv is not yet available:
python scripts/install.py
```

Start the master, then join each worker with the key it prints:

```bash
uv run kllm start
uv run kllm join --key '<join-key-from-master>'
```

If mDNS discovery is unavailable, add `--master <master-lan-ip>`. Do not expose unauthenticated RPC port 50052 to shared or public networks.

Download and run a model on the master:

```bash
uv run kllm model download 'org/repo:Q4_K_M'
uv run kllm plan --model model.Q4_K_M.gguf
uv run kllm run --model model.Q4_K_M.gguf
uv run kllm ui
```

`kllm plan` shows the resolved placement before launch. The dashboard is at `http://127.0.0.1:18765/`. To point a coding agent at the local OpenAI-compatible API, run `uv run kllm connect claude`.

Every node must use the same pinned llama.cpp tag. A dedicated 10GbE or Thunderbolt link with stable addresses is the preferred path for multi-node inference; DHCP changes interrupt an active RPC session. Setup steps are in [network-config/10gbe-bridging-guide.md](network-config/10gbe-bridging-guide.md).

## What to expect

Automatic placement uses a master-local accelerator when the model fits, then a single remote accelerator, then pooled devices. Mixed backends use layer split. Planning is an estimate for unknown architectures and runtime buffers, so read its warnings before a tight fit. A large context can exhaust KV-cache memory after the weights fit; shorten the context or use a smaller quantization.

The dashboard and `kllm run` also cover context length, batch size, GPU selection, memory budgets, and mixture-of-experts expert offload. See [docs/memory-constraints.md](docs/memory-constraints.md), [docs/layer-split-calculator.md](docs/layer-split-calculator.md), and [docs/node-usage-monitor.md](docs/node-usage-monitor.md).

## Documentation

- [CLI reference](docs/cli.md)
- [Engine decision and RPC security](docs/engine-decision.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Agent installation guide](docs/agent-install.md)
- [Security policy](SECURITY.md)

## Contributing

Development setup and tests are in [CONTRIBUTING.md](CONTRIBUTING.md). Portable agent skills for operating and changing Kluster live in [skills/README.md](skills/README.md).

## Prima.cpp integration development

Prima source builds, topology previews and local profiling are available for
evaluation. Inference selection remains gated until compatibility, distributed
lifecycle and hardware performance qualification are complete. See
[Prima integration status and preparation interfaces](docs/prima-integration.md).

## License

[MIT](LICENSE)
