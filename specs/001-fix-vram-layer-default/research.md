# Research: VRAM reporting and Layer default

## Displayed hardware capacity

- **Decision:** Treat `vram_bytes` from `/v1/gpus` as measured bytes and format settings memory as one-decimal GiB (`bytes / 1024**3`). Missing, negative, non-finite or otherwise invalid quantities display as unavailable; do not round a short measurement up to a marketed capacity.
- **Rationale:** `python/kllm/accel.py:parse_devices` converts llama.cpp MiB to bytes with `1024**2`, but `dashboard/src/lib/format-transfer.ts:formatGb` divides by `1e9`: 16 GiB becomes 17.2 GB. Planner fit calculations already use bytes.
- **Alternatives considered:** Scaling inventory bytes would corrupt fit calculations; globally changing `formatGb` would silently alter unrelated download/transfer labels. Prefer a scoped hardware-memory formatter.

## Capacity, budgets, and estimates

- **Decision:** Show capacity from unique selected local discrete GPU inventory entries. When any included measurement is invalid, mark the total unavailable rather than present a complete-looking partial total. Derive effective budgets from `/v1/plan` device allocations, and label `estimated_bytes`/`budget_bytes` as **total memory** when showing top-level values; use `vram_alloc_bytes` for GPU-only consumption and `cpu_alloc_bytes`/`ram_alloc_bytes` for CPU RAM. In CPU-only mode use RAM/memory labels. Identify unified capacity as shared rather than additive discrete VRAM.
- **Rationale:** `python/kllm/agent/__init__.py:_resolve_gpu_selection` already aggregates selected devices; summing the aggregate and device list double counts. `python/kllm/placement.py:plan_devices` caps per-device budget by configured headroom, free plus reclaimable bytes, and RAM on unified systems; it may route over only part of the inventory. Top-level plan budget includes CPU and eligible GPU budgets and is not a GPU capacity. A fully available 16 GiB discrete device at 95% headroom has a 15.2 GiB device budget.
- **Alternatives considered:** `capacity × slider` ignores available/shared-memory constraints. Summing plan allocations to report physical capacity ignores idle devices; summing every cluster member for the System Settings local GPU selector confuses local selection with remote inventory. A separate total-capacity endpoint is unnecessary for the specified local settings view.

## Layer split with saved preferences

- **Decision:** Remove the shared dashboard split selector and obsolete mode guidance; send `split_mode: "layer"` explicitly in `dashboard/src/lib/store.svelte.ts:planPayload` for estimates, plans and launches. Dashboard hardware config saves also write Layer so subsequent reloads no longer preserve old dashboard Row/Tensor preferences; loading old config must still leave dashboard requests effective Layer. Do not change the CLI/API acceptance of explicitly requested Row/Tensor for local-only execution.
- **Rationale:** `python/kllm/run_planning.py:resolve_split_mode` gives requests precedence over persisted TOML; omitted dashboard field currently inherits old preferences and `validate_split_mode` rejects Row/Tensor for RPC. The shared component covers both settings pages; shared payload covers all three request paths, including after model save/reset/reload.
- **Alternatives considered:** Hiding only the selector leaves a broken legacy setting. Globally coercing `config.get_split_mode` breaks supported explicit local-only settings. Writing TOML on page load adds a surprising side effect; effective Layer plus normalization at the next dashboard save covers the requirement.

## Integration and verification

- **Decision:** Keep `/v1/gpus`, `/v1/config`, `/v1/plan`, and `/v1/run` byte fields unchanged; specify behavioral regression checks for formatted capacity/missing data and request precedence, then run existing uv/pytest and dashboard check/build commands. A real node/backend smoke check remains necessary before claiming GPU or multi-node verification.
- **Rationale:** Frontend is Svelte 5/TypeScript with no existing dashboard behavioral test runner (`dashboard/package.json`); backend uses pytest and already tests device parsing, selection and plan boundaries. Node 22 in this workspace supports a small built-in test runner without adding a dependency.
- **Alternatives considered:** Adding a full browser E2E framework for one display path has no demonstrated need; static source assertions do not prove behavior. Existing backend-only tests cannot verify GiB rendering.
