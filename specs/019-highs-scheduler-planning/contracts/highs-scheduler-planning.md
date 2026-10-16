# Contracts: HiGHS Scheduler Planning & Dashboard Integration (`019-highs-scheduler-planning`)

**Feature**: `019-highs-scheduler-planning`  
**Date**: 2026-10-14  
**Scope**:
1. Master inference plan preview endpoint (`POST /v2/inference/plans`)
2. Runtime-specific Prima plan preview endpoint (`POST /v2/node/runtimes/{runtime_id}/prima-plan`)
3. Python interactor contract (`solve_highs_halda_schedule` in `src/kllm/modules/primacpp/interactors/highs_scheduler.py`)
4. Dashboard TypeScript state & view contracts (`dashboard/src/lib/modules/run/` and `dashboard/src/lib/modules/runtimes/`)

---

## 1. Master Inference Plan Preview (`POST /v2/inference/plans`)

### Request Payload Extension (`InferencePlanBody`)
Located in [`src/kllm/shared/schemas.py`](file:///D:/dev/kluster-llm/src/kllm/shared/schemas.py) and [`src/kllm/modules/inference/entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/entities.py):

```json
{
  "model_path": "/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf",
  "runtime_id": "prima-vulkan-win",
  "engine_id": "prima.cpp",
  "cluster_enabled": true,
  "parameters": {
    "placement_strategy": "auto",
    "mmap": "readahead",
    "just_enough_master": false,
    "node_priority": ["node-master", "node-fast", "node-weak"],
    "required_nodes": ["node-master"]
  }
}
```

- `parameters.placement_strategy` (optional, default `"auto"`): `"auto" | "capacity_first" | "priority"`.
- Backward-compatible with dashboard requests that also pass top-level or nested `placement_strategy` or `auto_fields`.

### Response Payload Extension (`PrimaPlanner.preview`)
Returned by [`PrimaPlanner.preview`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/planner.py#L469-L561):

```json
{
  "status": "ok",
  "engine_id": "prima.cpp",
  "runtime_id": "prima-vulkan-win",
  "fit": true,
  "distributed": true,
  "placement_mode": "highs_halda",
  "placement_strategy": "auto",
  "topology": {
    "mode": "distributed",
    "transport": "tcp_ring",
    "world_size": 2,
    "ranks": [
      {
        "rank": 0,
        "node_id": "node-master",
        "role": "master",
        "host": "10.0.0.1",
        "port": 51050,
        "next_host": "10.0.0.2",
        "next_port": 51050,
        "layers": [0, 1, 2, 3, 4, 5, 6, 7, 16, 17, 18, 19, 20, 21, 22, 23],
        "ngl": 16,
        "cpu_layers": 0,
        "prima_graphs": 2,
        "backend": "vulkan",
        "segments": [
          {"start_layer": 0, "end_layer": 7, "gpu_layers": 8, "cpu_layers": 0},
          {"start_layer": 16, "end_layer": 23, "gpu_layers": 8, "cpu_layers": 0}
        ]
      }
    ]
  },
  "scheduler": {
    "scheduler_mode": "highs_halda",
    "requested_strategy": "auto",
    "solver_backend": "highs_exact_milp",
    "total_layers": 32,
    "cycle_count_k": 2,
    "window_size_w": 16,
    "segmented_placement_enabled": true,
    "mmap_mode": "readahead",
    "just_enough_master": false,
    "objective_step_ms": 41.25,
    "compute_step_ms": 34.8,
    "disk_reload_step_ms": 3.2,
    "network_step_ms": 3.25,
    "fallback_reason": null,
    "missing_profile_nodes": [],
    "forced_m4_rollback": false,
    "iterations": 2,
    "node_allocations": [
      {
        "node_id": "node-master",
        "rank": 0,
        "w_m": 8,
        "n_m": 8,
        "cpu_layers_per_cycle": 0,
        "total_layers": 16,
        "total_gpu_layers": 16,
        "total_cpu_layers": 0,
        "memory_pressure_set": "M4",
        "initial_memory_pressure_set": "M3",
        "forced_m4": false,
        "overloaded_memory_bytes": 0,
        "vram_used_bytes": 2147483648,
        "ram_used_bytes": 134217728,
        "compute_ms": 16.0,
        "disk_reload_ms": 0.0,
        "kv_and_sync_ms": 1.4,
        "network_ms": 1.625,
        "total_step_ms": 19.025,
        "prima_graphs": 2
      },
      {
        "node_id": "node-fast",
        "rank": 1,
        "w_m": 8,
        "n_m": 6,
        "cpu_layers_per_cycle": 2,
        "total_layers": 16,
        "total_gpu_layers": 12,
        "total_cpu_layers": 4,
        "memory_pressure_set": "M3",
        "initial_memory_pressure_set": "M3",
        "forced_m4": false,
        "overloaded_memory_bytes": 536870912,
        "vram_used_bytes": 1610612736,
        "ram_used_bytes": 268435456,
        "compute_ms": 18.8,
        "disk_reload_ms": 3.2,
        "kv_and_sync_ms": 1.1,
        "network_ms": 1.625,
        "total_step_ms": 22.225,
        "prima_graphs": 2
      }
    ],
    "pruned_nodes": [
      {
        "node_id": "node-weak",
        "reason": "weak_device_w_le_1_ngl_0",
        "iteration": 1,
        "last_w_m": 1,
        "last_n_m": 0,
        "detail": "Pruned weak worker node-weak (w_m=1, n_m=0)"
      }
    ],
    "evaluated_cycles": [
      {"cycle_count_k": 1, "window_size_w": 32, "feasible": true, "objective_step_ms": 46.8},
      {"cycle_count_k": 2, "window_size_w": 16, "feasible": true, "objective_step_ms": 41.25}
    ]
  },
  "profiling": {
    "profiled": true,
    "mode": "measured_distributed_ring",
    "scheduler_mode": "highs_halda",
    "predicted_step_ms": 41.25,
    "predicted_tok_s": 24.24
  }
}
```

---

## 2. Runtime Prima Plan Endpoint (`POST /v2/node/runtimes/{runtime_id}/prima-plan`)

Handled by [`PrimaControlService.plan`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/control.py):
- Accepts `placement_strategy` inside `parameters` or top-level payload.
- Returns `scheduler` (`HighsScheduleReport.model_dump()`) in both preview and materialized plan responses.
- Preserves existing `ExecutionManifest` wire format for `POST /v2/node/runtimes/{runtime_id}/prima-prepare` so worker nodes validate `ExecutionManifest` with zero schema breakage.

---

## 3. Python Interactor Contract (`highs_scheduler.py`)

```python
def solve_highs_halda_schedule(
    *,
    members: Sequence[Mapping[str, Any]],
    profiles: Mapping[str, DeviceProfile],
    total_layers: int,
    model_bytes: int,
    ctx_size: int,
    parameters: InferenceParameters,
    runtime_capabilities: Sequence[str] = (),
    requested_strategy: Literal["auto", "capacity_first", "priority"] = "auto",
    min_disk_read_speed_bytes_s: float = 500.0 * 1024.0 * 1024.0,
) -> HighsScheduleSolution:
    ...
```

Where `HighsScheduleSolution` provides:
- `report: HighsScheduleReport`
- `active_members: list[dict[str, Any]]` (ordered active members after weak-worker pruning)
- `device_allocations: list[int]` (total layers $k \cdot w_m$ per active member)
- `gpu_layers_by_node: dict[str, int]` (total GPU layers $k \cdot n_m$ per active member)
- `cpu_layers_by_node: dict[str, int]` (total CPU layers $k \cdot (w_m - n_m)$ per active member)
- `segments_by_node: dict[str, list[Segment]]` (ordered contiguous segments across $k$ cycles)
- `prima_graphs_by_node: dict[str, int]` (reserved graph count per active member)
- `prima_graph_counts: str` (comma-separated string for `InferenceParameters.prima_graph_counts`)

---

## 4. Dashboard TypeScript Contracts

### `dashboard/src/lib/modules/run/entities.ts` & `dashboard/src/lib/modules/runtimes/entities.ts`

```typescript
export type PlacementStrategyMode = 'auto' | 'capacity_first' | 'priority';
export type SchedulerMode = 'highs_halda' | 'capacity_first' | 'priority';
export type MemoryPressureSet = 'M1' | 'M2' | 'M3' | 'M4';

export interface HighsNodeAllocation {
  node_id: string;
  rank: number;
  w_m: number;
  n_m: number;
  cpu_layers_per_cycle: number;
  total_layers: number;
  total_gpu_layers: number;
  total_cpu_layers: number;
  memory_pressure_set: MemoryPressureSet;
  initial_memory_pressure_set?: MemoryPressureSet;
  forced_m4?: boolean;
  overloaded_memory_bytes: number;
  vram_used_bytes: number;
  ram_used_bytes: number;
  compute_ms: number;
  disk_reload_ms: number;
  kv_and_sync_ms: number;
  network_ms: number;
  total_step_ms: number;
  prima_graphs: number;
}

export interface HighsPrunedNode {
  node_id: string;
  reason: string;
  iteration: number;
  last_w_m: number;
  last_n_m: number;
  detail?: string;
}

export interface HighsScheduleReport {
  scheduler_mode: SchedulerMode;
  requested_strategy: PlacementStrategyMode;
  solver_backend: string;
  total_layers: number;
  cycle_count_k: number;
  window_size_w: number;
  segmented_placement_enabled: boolean;
  mmap_mode: string;
  just_enough_master: boolean;
  objective_step_ms: number;
  compute_step_ms: number;
  disk_reload_step_ms: number;
  network_step_ms: number;
  fallback_reason?: string | null;
  missing_profile_nodes?: string[];
  forced_m4_rollback?: boolean;
  iterations: number;
  node_allocations: HighsNodeAllocation[];
  pruned_nodes: HighsPrunedNode[];
}
```
