# Memory-Pooling RPC Strategy

> **Created:** 2026-09-25
> **Status:** Realized
> **Repo / scope:** kluster-llm — heterogeneous RPC placement & model distribution removal

## Conventions captured at creation

- Live placement is Python `python/kllm/placement.py` `plan_devices` (via `native.plan_split` when `launch_settings` is set). Rust `crates/kllm-native/src/planner.rs` is fallback-only.
- Local-fit short-circuit already exists at `placement.py:108-111` (one master-local accelerator only).
- `--tensor-split` values today are **layer counts** from DP (`placement.py:148-151`), not raw GB ratios; launch always uses `--split-mode layer` (`crates/kllm-native/src/process.rs`).
- Workers join via `native.start_worker` → `rpc-server --host/--port -c` **without** `--model` (`agent/__init__.py`, `process.rs` `worker_from_pydict`).
- Physical GGUF fan-out is a full LAN Range-pull tree in `models.py` (`distribute_to_workers`, `pull_from_seed`, …) + agent routes + dashboard/CLI — optional today; inference docs already say GGUF is master-only (`docs/inference-distribution.md`).
- Operator docs still mandate distribute: `README.md`, `docs/cli.md`, `docs/agent-install.md`.

---

## 1. Codebase audit (baseline)

### What already matches the new rules

| Rule | Current state |
|------|----------------|
| RPC = memory pooling | Product intent in `docs/engine-decision.md`; layer-pipeline shards weights+KV |
| No GGUF required on workers for inference | True at runtime; `rpc-server -c` receives tensors over RPC |
| Skip RPC when one local GPU fits | Implemented in `plan_devices` |

### What conflicts or is leftover

| Area | Finding | Action |
|------|---------|--------|
| **Physical distribute** | Full stack: `distribute_to_workers`, `/v1/models/{distribute,blob,pull,manifest,file}`, CLI `kllm model distribute`, dashboard Distribute UI, piece manifests | **Remove / gut** (keep master-local download) |
| **Docs UX** | README/cli/agent-install require distribute before run | Rewrite to master-only GGUF |
| **RPC over-trigger** | Short-circuit is **single** local device; high-scoring remotes can still enter DP when multi-GPU master would fit alone | Prefer **all master-local devices** before any remote |
| **“Route to best node”** | Master always runs `llama-server`; cannot run GGUF-less on a worker without RPC | Adopt policy below (no GGUF move) |
| **Proportional split docs** | `docs/layer-split-calculator.md` is manual Mac/Windows FAQ; live code uses layer-count DP | Replace with TensorSplitCalculator + point docs at auto path |
| **Rust planner** | Greedy fill-all-nodes; no local-prefer | Align or document as dead for launch |
| **Worker lifecycle** | `rpc-server` always started on join | Keep (cheap); unused unless plan emits endpoints |

### Default routing policy (locked)

Because GGUF stays on the master and workers never receive `.gguf` copies:

- **No `--rpc`** only when master-local capacity (one or more local GPUs, DP over locals only) holds the model.
- **Single-device RPC** when one remote accelerator alone fits and master does not.
- **Multi-node RPC** only when `model_need > max(single_device_budget across cluster)`.

---

## 2. Architectural update — Memory-Pooling First

1. Master downloads/holds the only `.gguf`.
2. Workers = `rpc-server` memory/compute endpoints on `:50052` + capacity registration.
3. Planner decides **local | single-RPC | pool** using device budgets (free VRAM + reclaimable, headroom).
4. Pooling uses contiguous layer assignment with splits proportional to each device’s budget (heterogeneous-safe), never even split.
5. Network is a cost to avoid when capacity allows — not a throughput scaler.

---

## 3. TensorSplitCalculator

Module: `python/kllm/tensor_split.py`, wired into `placement.py`.

- Prefer master-local devices before any RPC.
- Prefer single remote device when it alone fits.
- Pool with VRAM-proportional ordering + existing contiguous DP allocator.
- Launch always honors `plan_result["rpc_endpoints"]` (empty list = no `--rpc`).

---

## 4. Eradicate physical model distribution

Remove distribute/pull/manifest/blob fan-out; keep master-local download and `rpc-server -c` tensor cache.

## 5. Worker provisioning

1. `python scripts/install.py` on worker (same `llama-release` pin).
2. `kllm join --key …`
3. Agent registers VRAM + starts `rpc-server` on private IP:50052 with `-c`.
4. Master: `kllm model download` → `kllm plan` / `kllm run` (no model copy).

Never expose port **50052** publicly.
