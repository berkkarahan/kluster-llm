# Windows implementation verification — 2026-09-20

Implemented and rebuilt through `uv run python scripts/build.py --skip-npm-install`.
The Windows control agent was restarted; the existing Qwen inference process (PID
7384) remained running. The Mac was not changed and no SSH was used, as requested.

## Checks

- Python: **256 passed**, with two third-party deprecation warnings.
- Native: **28 passed**; Windows build has an existing unused Metal enum warning.
- Dashboard: production build passed; `npm run check` passed with 0 errors and 11
  existing accessibility warnings.
- Installer `--check`: **b11026** at the time of this run (repo now pins stable **v0.5.0**); both llama-server and rpc-server present.
- Agent install guide §0: venv/package/native/dashboard/binaries/pin/WireGuard/agent
  checks passed on Windows. Both cluster members are present and inference is healthy.
- The Windows binary reports `Vulkan0`, AMD Radeon RX 9070 XT, build 11026.
- Tests cover device ordering and multiple GPUs, output offload, master CPU spill,
  unified RAM accounting, attributable replacement RAM, Qwen hybrid memory, GGUF
  tensor spans, settings preservation, concurrent probes, piece retries/seed failure,
  malformed ranges/lengths, interrupted and corrupt resume, changed manifests,
  legacy pulls, streaming disconnect/cancellation and unavailable upstreams.

The Windows file lock on the active native extension was resolved by stopping only
the control agent for the final rebuild. Package RECORD and native import were
verified afterward. Settings backups are under
`C:\Users\karah\.kllm\rollbacks\20260920-232242` and `20260920-233057`.

## Five-request comparison

Identical 64-token streamed requests through the Windows gateway, with saved model,
quantization, context, cache precision and reasoning settings retained. The inference
engine was not restarted. The repeatable command is
`python scripts/compare_inference.py --output <result.json>`; write outputs to a local
path (for example `artifacts/` or any file under `.gitignore`) — do not commit
machine-specific timing JSON to the repo. The table below is the durable record.

| Metric | Before median (range) | After median (range) |
|---|---:|---:|
| First token, seconds | 1.591 (0.380–47.120) | 0.371 (0.349–35.014) |
| Generation, tokens/sec | 11.987 (8.260–14.588) | 15.162 (7.157–16.005) |
| Complete request, seconds | 6.527 (4.700–53.000) | 4.940 (4.287–39.180) |

These are gateway smoke measurements. The large first-request and generation
outliers make them insufficient to establish a repeatable 5% regression bound or
attribute the entire change to gateway work. Tests verify that forwarding invokes
no synchronous diagnostic subprocess or health preflight.

## Deferred until the Mac update

The old Mac agent still reports its legacy Vulkan label and has no device inventory.
New placement deliberately requires that inventory; the already running model remains
available. After the manual Mac pull/build/rejoin, verify Metal and build 11026, run
Plan with the preserved settings, and compare actual runtime layer assignments.

Live validation of new Vulkan–Metal placement, five cold/warm engine startups,
clean/interrupted cross-machine distribution throughput, and the final 5% acceptance
criterion remain pending. Transfer correctness is covered by automated tests; this
report does not claim measured cross-machine transfer performance. CUDA/ROCm hardware
combinations also remain unverified.

See [operation and rollout details](inference-distribution.md).
