# System Settings UI and HTTP contract

## Dashboard surfaces

Both `dashboard/src/routes/settings/+page.svelte` and `dashboard/src/routes/run/+page.svelte` use `HardwareSettings.svelte`. Neither exposes a split-mode selector or instructions to choose Row/Tensor. Local GPU inventory capacity, selected discrete GPU total, and planned memory quantities display to one decimal in **GiB**, with unavailable values shown as `—` rather than an apparently complete sum. CPU-only quantities say RAM/memory; unified GPU quantities say shared. Changing selected devices/budget refreshes the applicable display. Capacity and usable budget are visibly distinct.

The Run-page summary labels top-level plan `estimated_bytes` and `budget_bytes` **total memory** (not VRAM). GPU-only consumption uses `vram_alloc_bytes`; CPU consumption uses `cpu_alloc_bytes`. Per-device plan `estimated_bytes / budget_bytes` labels its order as estimated / usable budget. A plan routing to fewer than all eligible devices must not re-label its budget as the entire inventory's physical capacity.

## Existing HTTP boundaries (no schema changes)

| Interface | Request or response fields | Required behavior |
| --- | --- | --- |
| `GET /v1/gpus` | `gpus[]` with `index`, `vram_bytes`, optional `free_bytes`, plus backend metadata | Source of truth for local reported bytes; optional/invalid measurements do not imply advertised size. |
| `GET/PUT /v1/config` | `device`, `gpu_ids`, `vram_percent`, `ram_percent`, `accel_backend`, `split_mode` | Existing Row/Tensor values remain readable for compatibility. Dashboard hardware saves persist `split_mode: "layer"` without changing other clients' explicit choices. |
| `POST /v1/plan` | Existing run-settings body with `split_mode: "layer"` for **all dashboard** estimate and Plan calls | Explicit Layer overrides legacy config. Response retains byte fields `estimated_bytes`, `budget_bytes`, `device_allocations`, `vram_alloc_bytes`, `cpu_alloc_bytes`, and `split_mode`; fit checks remain server-side. |
| `POST /v1/run` | Same dashboard request field `split_mode: "layer"` | Launch and its returned plan use Layer. Existing local-only callers may explicitly request Row/Tensor; RPC validation still rejects those modes. |

Measurements and request percentages retain their original byte and integer representations on the wire. The GiB conversion is display-only. Legacy preferences never require a config rewrite simply to open the dashboard.
