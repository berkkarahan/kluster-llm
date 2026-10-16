# Phase 1 Data Model: HiGHS Scheduler Integration (`019-highs-scheduler-planning`)

**Feature**: `019-highs-scheduler-planning`  
**Date**: 2026-10-14  
**Module Boundaries**:
- Backend domain entities: [`src/kllm/modules/primacpp/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py), [`src/kllm/modules/inference/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/entities.py)
- Dashboard domain entities: [`dashboard/src/lib/modules/run/entities.ts`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/entities.ts), [`dashboard/src/lib/modules/runtimes/entities.ts`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/runtimes/entities.ts)

---

## 1. Enumerations & Literals

### `MemoryPressureSet`
Classifies each scheduled node's Halda memory-pressure regime for the selected cycle count $k$:
- `"M1"`: CPU-only or unified-memory node without GPU offload (`n_m == 0`) where assigned layer weights + KV/activation footprint exceed available RAM and stream from disk via `mmap`.
- `"M2"`: Unified-memory (`is_uma == True`) node with GPU offload (`n_m > 0`) where assigned layer weights + KV/activation footprint exceed unified memory budget and stream from disk via `mmap`.
- `"M3"`: Discrete-GPU (`is_uma == False`) node where GPU-assigned layers fit in VRAM (`k * (b_g + d_g) * n_m <= M_vram`), CPU KV/activations fit in host RAM (`k * d_c * (w_m - n_m) <= M_ram`), and CPU layer weights exceed host RAM and stream from disk via `mmap`.
- `"M4"`: Resident node where all assigned GPU and CPU layers (weights + KV cache + compute buffer) fit within available VRAM and host RAM without disk page-fault reloading.

### `PlacementStrategyMode`
Operator-selectable placement strategy (`placement_strategy` on [`InferenceParameters`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/entities.py#L46-L154) and planning payloads):
- `"auto"`: Default. Uses `"highs_halda"` optimization when `engine_id == "prima.cpp"` and complete `DeviceProfile` measurements exist for all candidate nodes; automatically falls back to `"capacity_first"` when profile measurements are missing or incomplete.
- `"capacity_first"`: Deterministic capacity-proportional / measured non-HiGHS allocation (`"capacity_first"`).
- `"priority"`: Deterministic operator-ordered priority allocation (`"priority"`).

### `SchedulerMode`
Indicates the concrete solver or fallback path that produced a Prima plan:
- `"highs_halda"`: Halda / HiGHS MILP solver produced the layer schedule.
- `"capacity_first"`: Capacity-first heuristic or fallback produced the schedule.
- `"priority"`: Operator priority ordering produced the schedule.

---

## 2. Backend Domain Entities (`src/kllm/modules/primacpp/entities.py`)

All new domain entities inherit from [`Record`](file:///D:/dev/kluster-llm/src/kllm/shared/entities.py) (`ConfigDict(extra="forbid")`).

### `HighsNodeAllocation`
Per-node decision variables, memory-pressure classification, and predicted per-token latency breakdown for an active rank in the selected schedule.

| Field | Type | Constraints / Defaults | Description |
|-------|------|------------------------|-------------|
| `node_id` | `str` | non-empty | Cluster node identifier |
| `rank` | `int` | `>= 0` | Ring rank index (`0` = master) |
| `w_m` | `int` | `>= 0` | Assigned total layers per cycle ($w_m$) |
| `n_m` | `int` | `0 <= n_m <= w_m` | Assigned GPU layers per cycle ($n_m$) |
| `cpu_layers_per_cycle` | `int` | `== w_m - n_m` | Assigned CPU layers per cycle ($w_m - n_m$) |
| `total_layers` | `int` | `== k * w_m` | Total layers assigned across all $k$ cycles |
| `total_gpu_layers` | `int` | `== k * n_m` | Total GPU layers assigned across all $k$ cycles (`ngl`) |
| `total_cpu_layers` | `int` | `== k * (w_m - n_m)` | Total CPU layers assigned across all $k$ cycles (`cpu_layers`) |
| `memory_pressure_set` | `Literal["M1", "M2", "M3", "M4"]` | required | Post-solve Halda memory-pressure set classification |
| `initial_memory_pressure_set` | `Literal["M1", "M2", "M3", "M4"]` | default `"M4"` | Initial set before post-solve residence check |
| `forced_m4` | `bool` | default `False` | `True` if forced into $M_4$ (`mmap == "none"` or slow disk `< min_disk_read_speed_bytes_s`) |
| `overloaded_memory_bytes` | `int` | `>= 0`, default `0` | Bytes exceeding physical RAM/UMA budget that stream from disk per step |
| `vram_used_bytes` | `int` | `>= 0`, default `0` | Estimated GPU memory consumed ($k \cdot (b_{g,m} + d_{g,m}) \cdot n_m$) |
| `ram_used_bytes` | `int` | `>= 0`, default `0` | Estimated host RAM / UMA memory consumed |
| `compute_ms` | `float` | `>= 0.0` | Predicted per-token compute time (GPU + CPU layers + output head) in ms |
| `disk_reload_ms` | `float` | `>= 0.0` | Predicted per-token `mmap` disk reload time in ms (`0.0` when `memory_pressure_set == "M4"`) |
| `kv_and_sync_ms` | `float` | `>= 0.0` | Predicted per-token GPU KV copy + upload/download sync overhead in ms |
| `network_ms` | `float` | `>= 0.0` | Predicted per-token ring network transfer latency from this rank in ms |
| `total_step_ms` | `float` | `>= 0.0` | Total per-token step latency contributed by this rank in ms |
| `prima_graphs` | `int` | `>= 1`, default `1` | Graph visits reserved for this rank (`reserve_graph_visits`) |

### `HighsPrunedNode`
Records a candidate worker excluded during iterative weak-worker pruning.

| Field | Type | Constraints / Defaults | Description |
|-------|------|------------------------|-------------|
| `node_id` | `str` | non-empty | Pruned worker node identifier |
| `reason` | `Literal["weak_device_w_le_1_ngl_0", "zero_layers_assigned"]` | required | Structured pruning reason |
| `iteration` | `int` | `>= 1`, default `1` | Outer Halda solver iteration in which the node was pruned |
| `last_w_m` | `int` | `>= 0`, default `0` | Window size $w_m$ assigned prior to pruning |
| `last_n_m` | `int` | `>= 0`, default `0` | GPU layers $n_m$ assigned prior to pruning |
| `detail` | `str` | default `""` | Human-readable explanation for dashboard display |

### `HighsCycleCandidate`
Summarizes the MILP evaluation for a single divisor cycle count $k \mid L$.

| Field | Type | Constraints / Defaults | Description |
|-------|------|------------------------|-------------|
| `cycle_count_k` | `int` | `>= 1` | Tested cycle count $k$ |
| `window_size_w` | `int` | `>= 1` | Tested window size $W = L / k$ |
| `feasible` | `bool` | required | Whether a feasible integer assignment existed for $k$ |
| `objective_step_ms` | `float | None` | `>= 0.0` when feasible | Predicted total per-token decode step latency (ms) |

### `HighsScheduleReport`
Complete provenance and telemetry artifact for a Prima placement schedule.

| Field | Type | Constraints / Defaults | Description |
|-------|------|------------------------|-------------|
| `scheduler_mode` | `Literal["highs_halda", "capacity_first", "priority"]` | required | Active scheduler mode that produced the plan |
| `requested_strategy` | `Literal["auto", "capacity_first", "priority"]` | default `"auto"` | Operator-requested strategy |
| `solver_backend` | `Literal["highspy", "highs_exact_milp", "heuristic_fallback"]` | default `"highs_exact_milp"` | Concrete MILP execution backend |
| `total_layers` | `int` | `>= 1` | Total model transformer layers $L$ |
| `cycle_count_k` | `int` | `>= 1`, default `1` | Selected optimal cycle count $k$ |
| `window_size_w` | `int` | `>= 1` | Selected cycle window $W = L / k$ |
| `segmented_placement_enabled` | `bool` | default `False` | Whether $k > 1$ multi-cycle segments were enabled by runtime capability |
| `mmap_mode` | `str` | default `"readahead"` | Effective `mmap` mode evaluated (`"none"`, `"prefetch"`, `"readahead"`) |
| `just_enough_master` | `bool` | default `False` | Whether coordinator-only / minimal master rank 0 mode was active |
| `objective_step_ms` | `float` | `>= 0.0`, default `0.0` | Total predicted per-token decode step latency (ms) |
| `compute_step_ms` | `float` | `>= 0.0`, default `0.0` | Aggregate per-token compute latency across active ranks (ms) |
| `disk_reload_step_ms` | `float` | `>= 0.0`, default `0.0` | Aggregate per-token disk reload latency across active ranks (ms) |
| `network_step_ms` | `float` | `>= 0.0`, default `0.0` | Aggregate per-token ring network latency across active ranks (ms) |
| `fallback_reason` | `str | None` | default `None` | Reason when `requested_strategy == "auto"` fell back to `capacity_first` |
| `missing_profile_nodes` | `list[str]` | default `[]` | Node IDs lacking complete `DeviceProfile` measurements |
| `forced_m4_rollback` | `bool` | default `False` | `True` if forcing slow-disk nodes into $M_4$ was infeasible and rolled back |
| `iterations` | `int` | `>= 1`, default `1` | Number of outer Halda refinement iterations executed |
| `node_allocations` | `list[HighsNodeAllocation]` | default `[]` | Ordered per-rank schedule allocations |
| `pruned_nodes` | `list[HighsPrunedNode]` | default `[]` | Weak workers pruned during optimization |
| `evaluated_cycles` | `list[HighsCycleCandidate]` | default `[]` | Candidate divisors $k \mid L$ evaluated in the final iteration |

---

## 3. Updates to Existing Entities

### `InferenceParameters` ([`src/kllm/modules/inference/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/entities.py#L46-L154))
- Add optional field:
  ```python
  placement_strategy: Literal["auto", "capacity_first", "priority"] | None = None
  ```
- Register `placement_strategy` in [`parameter_catalog.py`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/interactors/parameter_catalog.py) with default `"auto"`, options `("auto", "capacity_first", "priority")`, and `supports_auto=True`.

### `PrimaPlan` ([`src/kllm/modules/primacpp/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py#L285-L310))
- Add optional field:
  ```python
  scheduler_report: HighsScheduleReport | None = None
  ```
- **Digest Invariant**: Update `PrimaPlan.digest()` to exclude `scheduler_report` when `None` (or keep `digest()` scoped to execution-affecting fields `runtime_id`, `source_commit`, `recipe_digest`, `model_path`, `model_digest`, `topology`, `parameters`, `qualification`) so existing canonical plan digests and `ExecutionManifest.digest()` remain 100% unchanged.

---

## 4. State & Validation Invariants

1. **Layer Conservation (`FR-001`)**:
   - $\sum_{m=0}^{M_{\text{active}}-1} w_m = W = L / k$
   - $\sum_{m=0}^{M_{\text{active}}-1} \text{total\_layers}_m = \sum_{m=0}^{M_{\text{active}}-1} k \cdot w_m = L$
2. **GPU & CPU Bounds (`FR-001`, `FR-002`)**:
   - For every active rank $m$: $0 \le n_m \le w_m \le W$, $\text{total\_gpu\_layers}_m = k \cdot n_m$, $\text{total\_cpu\_layers}_m = k \cdot (w_m - n_m)$.
3. **Memory-Pressure Consistency (`FR-003`)**:
   - When `mmap == "none"`: all active ranks must have `memory_pressure_set == "M4"` and `disk_reload_ms == 0.0`.
   - When `memory_pressure_set == "M4"`: `overloaded_memory_bytes == 0` and `disk_reload_ms == 0.0`.
4. **Pruning Invariant (`FR-006`, `FR-007`)**:
   - Rank 0 (master) and any `node_id` in `required_nodes` must never appear in `pruned_nodes`.
5. **Segmented Graph Reservation (`FR-004`, `FR-005`)**:
   - When `cycle_count_k > 1`, `"segmented_placement"` must be present in `runtime_capabilities`, each active rank with $w_m > 0$ has $k$ segments in `RankAllocation.segments`, and `parameters.prima_graph_counts` matches `reserve_graph_visits`.
