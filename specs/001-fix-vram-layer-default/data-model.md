# Data model: settings memory and split mode

No new persisted entity or database schema is needed. The following are existing byte fields plus derived display values; see [contracts/system-settings.md](contracts/system-settings.md) for their external meaning.

| Entity | Fields / source | Relationships and validation |
| --- | --- | --- |
| GPU inventory device | `index`, `device`, `backend`, `name`, `vram_bytes`, `free_bytes` from `/v1/gpus` or member `devices` | Indices identify local selection; memory is measured in bytes, not decimal GB. Missing/non-finite/negative capacity is unknown, not zero or guessed size. Shared/unified devices refer to RAM-backed capacity. |
| Local selected capacity | Unique `selectedGpuIds` matched against local eligible `gpus`; derived display in GiB | Sum discrete device capacities once. If any included capacity is unknown, the total is unavailable. Changes with selection/backend/inventory; no aggregate `member.vram_bytes` addition. Shared pool is labeled shared and not added twice to combined memory. |
| Effective device budget | Plan `device_allocations[].budget_bytes` | The planner caps capacity by headroom (`vram_percent`/100), available plus reclaimable bytes, and unified RAM constraints. It reports budgets for planned routing, which may differ from all selected physical capacity. 16 GiB fully free at 95% → 15.2 GiB. |
| Estimated consumption | Plan `device_allocations[].estimated_bytes`, `vram_alloc_bytes`, `cpu_alloc_bytes`/`ram_alloc_bytes`, `estimated_bytes` | Per-device and GPU-only allocation excludes CPU. CPU allocation is RAM. Top-level `estimated_bytes` is combined GPU + CPU consumption and must be labeled total memory; no re-computation for fit in UI. |
| Combined budget | Plan `budget_bytes` / `total_budget_bytes` | Includes applicable CPU RAM plus planned GPU budgets, with unified local memory counted once. Label total memory, never VRAM capacity. |
| Dashboard effective split mode | Request `split_mode: "layer"`; legacy config `split_mode` may be `layer`, `row`, `tensor` | Dashboard estimate/plan/run always request Layer. Fresh default is Layer; loading a legacy value does not change effective requests; dashboard hardware saves normalize it to Layer. Explicit non-dashboard local-only CLI/API requests remain subject to existing validation. |

## State transitions

1. On page load, inventory/config populate local selection and persisted preferences. Legacy split value can be read, but the effective dashboard request mode remains Layer without a write on load.
2. On device or GPU selection change, capacity is recomputed from the current selected unique devices and a fresh plan recalculates applicable budgets/estimates.
3. On a dashboard hardware save, persist Layer with other settings. Model Save/Reset and subsequent reloads continue to request Layer.
4. On plan/run, the backend validates the requested split and computes byte budgets; the UI formats for display without changing bytes or fit decisions. Unknown measurements render unavailable.
