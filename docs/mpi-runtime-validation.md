# MPI runtime validation ledger

This ledger records gate criteria and observed results. A local provider test
does not establish a production communicator or tensor-parallel inference.

## 2026-09-23 — Windows workspace

| Gate | Pass criterion | Result and evidence |
|---|---|---|
| Python baseline | Existing suite passes before MPI changes | **PASS after test isolation repair.** `test_control_client_keeps_idle_sockets` and the membership HTTP-client test isolate their pools. The final full regression result is 349 passed and 1 skipped. |
| R0 local provider | Pinned fork builds with `derive`; all local tests and three-rank self-test pass | **PASS on Windows only.** `cargo test --manifest-path crates/kllm-mpi-provider/Cargo.toml --all-targets --features derive`: 24 library + 3 provider-check + 2 structured-rank + 50 integration tests passed. The local `mpi-derive` path is used. |
| R1 production provider | Authenticated structured rank launch, fixed private bind/port, bounded collectives, and whole-job cleanup on each OS | **SOFTWARE PATH PASS on this Windows host; OS matrix BLOCKED.** `MpiJobSpec`, HMAC peer authentication with a fresh server challenge, nonce/address checks, 20000–20999 private binds, bounded bootstrap and descriptor agreement, broadcast/all-reduce/all-gatherv, rank-loss timeout, panic-contained C ABI, stdin-only rank launch, Windows Job Object/Unix session ownership, and authenticated worker endpoints are implemented. Local three-rank production collectives, missing-rank timeout, forced rank loss, and singleton process launch pass. macOS/Linux process-loss evidence is still required. |
| R2 cross-OS provider | One authenticated world on Windows AMD, macOS, Linux NVIDIA through KLLM control plane; forced rank loss reaps all owned processes | **BLOCKED.** This node's `kllm status` is `role=idle`. Read-only `/v1/status` requests to the configured private `192.168.68.59`, `.61`, and `.51` control endpoints each exceeded a two-second timeout. Those addresses are a persisted snapshot, not proof of live peers. No cross-host process was launched. |
| Backend flavors | Probe pinned MPI Vulkan, Metal, CUDA, and supported Linux ROCm on their intended GPUs | **BLOCKED.** `--mpi-only --backend vulkan` now stages the verified provider separately on this host, and its install manifest truthfully records `engine.available=false`. The Windows AMD physical identity is detected from its PNP ID. No MPI GPU engine flavor is claimed. Metal, CUDA, ROCm, and real Vulkan probes remain hardware gates. |
| Real engine | Rank-local tensor allocation and graph execution, collective traffic, distributed warmup, reference-logit agreement | **BLOCKED at the graph patch.** The Rust C ABI and a deterministic dense-Llama b11026 manifest now exist, including quantization-aligned unequal shards, explicit rotated axis offsets, and exact rank weight/KV bytes. No distributed GGML allocation/execution patch, GPU warmup, logits, streaming HTTP, or cancellation evidence exists. MPI Plan reports the graph engine blocker and MPI Run returns 503 without entering RPC. |
| Mixed and native acceptance | Mixed Windows AMD + Mac + Linux NVIDIA Vulkan job and homogeneous Metal, CUDA, ROCm jobs; cleanup and second clean launch | **BLOCKED.** Depends on R1, R2, backend flavors, and real engine. No HTTP/logit/performance claim is made. |

### Provider identity observed on this host

- Upstream revision: `99be8ac11c04f5419452428a486ae0eee1f5552e`
- Local source digest: `sha256:d9a1e691293d0225fb7e921daf576d2e480aa1f90a6f5c30ca02c98839cec09b`.
- Provider-check binary: `sha256:2a0520451a4da4e3f858803bd8b9165bf9c2a507854f4f4d9d304faa0f750ff7`;
  structured-rank binary: `sha256:3333a044e1847ad27d556c9f375ce20b79f32466058697f2095daeca7af6d0db`.
- Staged C ABI header:
  `sha256:6e00dd02ed3f34e96dfe75d00ad73c73dd8ef6ff9ffcfff7efabcdab84276bbe`.
- Installed capability fingerprint:
  `sha256:b983b514bf4ec78c06ffa041be6cecbc68e86f58505532d0f486cf347889fb1a`.
  Rerun `--mpi-only --force` after any provider source change before collecting
  hardware evidence.
- Protocol: `1`; target: `x86_64-pc-windows-msvc`; packaged features: `["derive"]`.
- The `derive` cargo test uses the local path dependency `mpi-derive`; Linux and macOS builds and hashes remain unverified.

### Independent software verification

- `uv run python -m pytest tests -q`: 349 passed, 1 skipped after provider control, manifest, physical identity, installer, settings, policy, API, CLI, UI, and membership changes.
- `cargo test --manifest-path crates/kllm-native/Cargo.toml`: 28 passed.
- `npm test`: five Vitest dashboard store tests passed, including stale estimate and stale model-settings races.
- `npm run check`: zero errors, eleven existing accessibility warnings.
- `npm run build`: passed; the same eleven warnings were emitted.
- CMake/MSVC build and CTest: two of two tests passed (local activation
  loopback and the C++ compile check for the Rust provider header). This does
  not exercise a GPU graph.
- `uv run python scripts/install.py --mpi-only --backend vulkan --force`
  rebuilt the isolated package; the same command without `--force` then
  verified and reused the complete source, binary, rank, header, backend, and
  llama revision identity without touching the RPC package.

## Remaining dependencies

1. Reachable and authorized Windows AMD, macOS, and Linux NVIDIA hosts with
   live KLLM agents and a private fabric for provider interop and rank-loss
   cleanup.
2. The implemented production `MpiJobSpec` and provider data plane validated
   for process loss and cleanup on macOS and Linux, then as one three-host world.
3. Separately pinned and probed MPI engine flavors for the selected hardware,
   plus the reviewed `llama.cpp b11026` distributed graph patch. The shard
   manifest contract is implemented and must be consumed unchanged by it.
4. Real distributed model and HTTP acceptance evidence for the mixed Vulkan
   lane and native Metal, CUDA, and supported Linux ROCm lanes.
