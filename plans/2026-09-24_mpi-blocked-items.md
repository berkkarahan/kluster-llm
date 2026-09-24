# MPI Integration — Blocked Items Plan

**Created:** 2026-09-24  
**Status:** BLOCKED at infrastructure and engine patch gates  
**Goal:** Complete MPI tensor-parallel inference with distributed GGML execution

---

## Current State (2026-09-24, macOS arm64)

**PASS:**
- Open MPI 5.0.11 installed (Homebrew)
- C++ smoke test: 3/3 tests pass (broadcast, allreduce, allgatherv, invalid-header rejection)
- MPI provider build: `vendor/mpi/metal/bin/` contains `kllm-mpi-provider-check`, `kllm-mpi-rank`, `libmpi.dylib`
- MPI Python tests: 69 passed
- Cluster heuristic (Task 5): 47 tests pass
- Transport settings (Task 4): PASS
- Fail-closed guard (Task 4b): PASS (evolved to real dispatch)

**BLOCKED:**
1. Cross-OS communicator (R2)
2. Backend flavors (R3)
3. Real engine (R4)
4. Mixed acceptance

---

## Blocked Item 1: Cross-OS Communicator (R2)

**Gate criterion:** One authenticated world on Windows AMD, macOS, Linux NVIDIA through KLLM control plane; forced rank loss reaps all owned processes.

**Blockers:**
- Windows AMD host (`192.168.68.59`) unreachable (2s timeout)
- macOS host (`192.168.68.61`) unreachable (2s timeout)
- Linux NVIDIA host (`192.168.68.51`) unreachable (2s timeout)
- These addresses are a persisted snapshot, not proof of live peers

**Prerequisites:**
- Reachable and authorized Windows AMD, macOS, and Linux NVIDIA hosts
- Private fabric for provider interop and rank-loss cleanup
- Live KLLM agents on each host

**Required evidence:**
- `mpiexec` launches 3 ranks across Windows/macOS/Linux
- HMAC peer authentication succeeds
- Allcollectives (broadcast, allreduce, allgatherv) pass
- Forced rank loss reaps all owned processes within bounded timeout
- No public listeners; actual data-plane interfaces documented

**Next action:** Re-probe host reachability; establish private fabric; validate R1 production provider on macOS/Linux before cross-OS launch.

---

## Blocked Item 2: Backend Flavors (R3)

**Gate criterion:** Probe pinned MPI Vulkan, Metal, CUDA, and supported Linux ROCm on their intended GPUs.

**Blockers:**
- No MPI GPU engine flavor staged on any host
- `vendor/mpi/metal/bin/install.json` records `engine.available=false`
- Windows AMD physical identity detected but no Metal/Vulkan engine
- macOS Metal inference not yet verified with real GGUF
- CUDA, ROCm probes remain hardware gates

**Prerequisites:**
- Separately pinned and probed MPI engine flavors for selected hardware
- Reviewed `llama.cpp b11026` distributed graph patch
- Shard manifest contract consumed unchanged by engine patch

**Required evidence per backend:**
- `--list-devices` shows actual backend/device and physical identity (not `allowed_backends`)
- Tiny dense GGUF inference runs on selected lane's actual backend/device
- Logs show execution on the selected backend/device
- For macOS Vulkan: actual MoltenVK/loader/SDK versions validated
- For macOS Metal: validate actual Metal device and tensor-parallel operations

**Next action:** Re-run `uv run python scripts/install.py --mpi-only --backend metal --force` to rebuild provider; verify engine readiness; probe Metal inference on this Mac.

---

## Blocked Item 3: Real Engine (R4)

**Gate criterion:** Rank-local tensor allocation and graph execution, collective traffic, distributed warmup, reference-logit agreement.

**Blockers:**
- No distributed GGML allocation/execution patch
- No GPU warmup, logits, streaming HTTP, or cancellation evidence
- MPI Plan reports graph engine blocker
- MPI Run returns 503 without entering RPC

**Prerequisites:**
- Distributed GGML patch for `llama.cpp b11026`
- Rank-local implementation of tensor-split graph
- Collective-backed warmup/decode path
- C ABI bridge (Rust ↔ C++/llama.cpp) already exists and tested

**Engine surface (initial implementation limits):**
- Dense `llama` architecture
- Text generation
- One concurrent sequence
- Explicit context
- `f16` KV, flash attention enabled
- No draft model, no vision, no LoRA/hot reload, no automatic context shrinking

**Next action:** Expand engine work packages into reviewed, source-accurate patches; verify with 2–5 minute RED/GREEN steps; do not guess the missing patch or substitute a no-op backend.

---

## Blocked Item 4: Mixed and Native Acceptance

**Gate criterion:** Mixed Windows AMD + Mac + Linux NVIDIA Vulkan job and homogeneous Metal, CUDA, ROCm jobs; cleanup and second clean launch.

**Blockers:**
- Depends on R1, R2, backend flavors, and real engine
- No HTTP/logit/performance claim made

**Required evidence:**
- Mixed cluster (Windows AMD + Mac + Linux NVIDIA) runs Vulkan job end-to-end
- Homogeneous Metal job (all-Mac) runs end-to-end
- Homogeneous CUDA job (all-NVIDIA) runs end-to-end
- Homogeneous ROCm job (all-AMD, Linux) runs end-to-end
- Cleanup after forced rank loss
- Second clean launch after cleanup
- Reference logit agreement across backends

**Next action:** After R2, R3, and R4 pass, execute acceptance tests for each backend lane.

---

## Dependency Graph

```
R0 (local provider) ──PASS──→ R1 (production provider) ──PASS──→ R2 (cross-OS)
                                                              ↓ BLOCKED (hosts unreachable)
R3 (backend flavors) ──BLOCKED (no engine flavor)────────────→ R4 (real engine)
                                                              ↓ BLOCKED (no GGML patch)
Mixed acceptance ──BLOCKED (depends on R2, R3, R4)
```

---

## Open Questions

1. **Cross-OS provider choice:** MS-MPI + Open MPI interoperability not demonstrated. Candidate: same pinned Open MPI release on Linux/macOS and Cygwin build on Windows. Do not default to mixing MS-MPI and Open MPI.

2. **ROCm on Windows:** ROCm installation supported on Linux only. Windows AMD + macOS + Linux NVIDIA requires Vulkan. Do not assume Windows AMD can use ROCm.

3. **Engine patch scope:** The distributed GGML patch is a gated engineering specification, not a fabricated ready-to-apply upstream patch. After the communicator/backend gates, the implementer must expand the engine work packages into reviewed, source-accurate patches and their own 2–5-minute RED/GREEN steps before implementing them.

4. **Backend verification:** Metal/CUDA/ROCm selection does not require Vulkan to be installed. A changed membership, selection, binary, capability fingerprint, model, or operation profile invalidates the decision and the shard manifest.

---

## References

- Plan: `.hermes/plans/2026-09-22_194613-mpi-vulkan-ui-cli.md`
- Validation ledger: `docs/mpi-runtime-validation.md`
- C++ smoke test: `cpp/kllm_mpi/mpi_smoke_test.cpp`
- CMake option: `cpp/kllm_mpi/CMakeLists.txt` (KLLM_WITH_MPI)
- Rust provider: `crates/kllm-mpi-provider/`
- Python runtime: `python/kllm/mpi_runtime.py`
- Cluster heuristic: `python/kllm/mpi_policy.py`

---

**Last updated:** 2026-09-24  
**Next review:** After R1 macOS/Linux validation and host reachability re-probe
