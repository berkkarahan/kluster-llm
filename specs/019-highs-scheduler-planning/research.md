# Phase 0 Research: HiGHS Scheduler Integration for Prima.cpp Planning & Dashboard

**Feature**: `019-highs-scheduler-planning`  
**Date**: 2026-10-14  
**References**:
- Upstream Prima.cpp Halda scheduler: [`common/common.cpp`](file:///D:/dev/prima.cpp/common/common.cpp#L862-L1659) (`assign_layers_to_device` & `assign_layers_and_select_devices`)
- Kluster Prima planner & entities: [`entities.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py), [`planner.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/planner.py), [`distributed_planning.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/distributed_planning.py), [`measured_planning.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/measured_planning.py), [`segment_costs.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/segment_costs.py)
- Kluster graph reservation: [`prima_graph_reserve.py`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/interactors/prima_graph_reserve.py)

---

## R1: HiGHS MILP Formulation & Deterministic Python Execution

### Decision
Implement a dedicated interactor [`src/kllm/modules/primacpp/interactors/highs_scheduler.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/highs_scheduler.py) that constructs the exact Halda / HiGHS mixed-integer linear programming (`HighsModel`) representation in compressed sparse column (CSC) form (`col_cost`, `col_lower`, `col_upper`, `row_lower`, `row_upper`, `a_matrix`, `integrality`) and solves it deterministically via `highspy` (when installed) or an integrated exact integer solver over the identical `HighsModel` matrix and constraints.

### Rationale
1. **Structural Fidelity to Upstream**: In [`common/common.cpp`](file:///D:/dev/prima.cpp/common/common.cpp#L1035-L1275), `assign_layers_to_device(params, k)` formulates a $2M$-variable integer linear program for a fixed cycle count $k \mid L$ (where $W = L / k$ is the total layer window per cycle):
   - Columns $0 \dots M-1$: $w_m \in [0, W] \cap \mathbb{Z}$ (total CPU+GPU layers assigned to device $m$ per cycle). Note: when `just_enough_master` does not force $w_0 = 0$, $w_0 \ge 1$ ensures rank 0 participates.
   - Columns $M \dots 2M-1$: $n_m \in [0, W] \cap \mathbb{Z}$ (GPU layers assigned to device $m$ per cycle).
   - **Constraint 1 (Layer conservation)**: $\sum_{m=0}^{M-1} w_m = W$.
   - **Constraint 2 (GPU-to-window bound)**: For each device $m$, $n_m - w_m \le 0$ ($n_m \le w_m$).
   - **Constraint 3 (VRAM bound for $m \notin M_1 \cup M_2$, i.e. discrete GPU or $M_4$ resident)**: $k \cdot (b_{g,m} + d_{g,m}) \cdot n_m \le M_{\text{VRAM}, m}$.
   - **Constraint 4 (Host RAM / Unified Memory bound)**:
     - For $m \in M_3$ (discrete GPU with disk streaming on CPU): $k \cdot d_{c,m} \cdot w_m - k \cdot d_{c,m} \cdot n_m \le M_{\text{RAM}, m}$ (KV cache + activations of CPU layers must fit in host RAM).
     - For $m \in M_4$ with unified memory (`is_uma`): $k \cdot (b_{c,m} + d_{c,m}) \cdot w_m \le M_{\text{UMA}, m}$.
     - For $m \in M_4$ with discrete GPU (`not is_uma`): $k \cdot (b_{c,m} + d_{c,m}) \cdot w_m - k \cdot (b_{c,m} + d_{c,m}) \cdot n_m \le M_{\text{RAM}, m}$.
   - **Objective Function**: Minimize per-token decode step latency $T_{\text{step}}(w, n; k)$, expressed as linear column costs $c(w_m)$ and $c(n_m)$ summing compute latency ($t_{c,m}, t_{g,m}$), KV copy + GPU sync overhead ($s_{\text{copy},m} + s_{\text{up},m} + s_{\text{down},m}$), and disk page-fault reload time under `mmap` for $m \in M_1 \cup M_2 \cup M_3$:
     - $M_1$ (CPU-only / UMA without GPU offload, exceeding RAM): reload cost $\frac{k \cdot b_{c,m}}{v_{\text{seq},m}} \cdot w_m$ (offset by constant $-\frac{M_{\text{RAM},m} - k \cdot d_{c,m} \cdot w_m}{v_{\text{seq},m}}$).
     - $M_2$ (UMA with GPU offload, exceeding unified memory): reload cost $\frac{k \cdot b_{c,m}}{v_{\text{seq},m}} \cdot w_m$ on Linux/Windows or $\frac{k \cdot b_{g,m}}{v_{\text{rnd},m}} \cdot n_m + \frac{k \cdot b_{c,m}}{v_{\text{seq},m}} \cdot (w_m - n_m)$ on macOS (`Darwin`).
     - $M_3$ (Discrete GPU where GPU layers fit in VRAM, CPU layers stream from disk): reload cost $\frac{k \cdot b_{c,m}}{v_{\text{seq},m}} \cdot (w_m - n_m)$.
     - $M_4$ (Resident in RAM and VRAM): zero disk-reload penalty.
2. **Zero-Friction Cross-Platform Execution**: Because constraints 2, 3, and 4 decouple per node $m$ and only Constraint 1 ($\sum_{m=0}^{M-1} w_m = W$) couples nodes across the cluster, evaluating the exact `HighsModel` CSC matrix via either `highspy` or a built-in exact integer solver produces identical optimal integer solutions $(w_m^*, n_m^*)$ in `< 2ms` for $M \le 16, L \le 256$ without requiring native C++ HiGHS compilation during Python unit tests.

### Alternatives Considered
- **Calling `llama-cli` / C++ FFI for planning**: Rejected because dashboard plan previews (`/v2/inference/plans`) run on the master node before worker processes are spawned and must evaluate candidate topologies in-memory from cached `DeviceProfile` snapshots in `< 50ms`.
- **Heuristic greedy layer splitting only**: Rejected because greedy capacity splitting ignores disk-streaming vs. resident tradeoffs ($M_1 \dots M_4$), multi-cyclepipelining ($k > 1$), and weak-worker drag.

---

## R2: Cross-Platform & Vulkan Generalization of Memory-Pressure Sets ($M_1 \dots M_4$)

### Decision
Generalize the upstream device capability and memory-pressure classification in [`highs_scheduler.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/highs_scheduler.py) to support `Windows`, `Linux`, and `Darwin` nodes and Vulkan (`vulkan` / `metal+vulkan`) backends in addition to `metal` and `cuda`, and implement Option C from the specification clarifications (unrestricted Halda sets when `mmap` is enabled).

### Rationale
1. Upstream [`common/common.cpp`](file:///D:/dev/prima.cpp/common/common.cpp#L868-L905) contained `GGML_ASSERT(!params.devices_os[i].is_windows)` and checked `has_gpu = params.devices_gpu[i].metal || params.devices_gpu[i].cuda`. Kluster's Prima runtime supports Vulkan on Windows, Linux, and macOS (`Darwin` via MoltenVK).
2. In Kluster's [`DeviceProfile`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py#L99-L176):
   - A node has a usable GPU if `gpu_available(profile)` is true (positive `gpu_layer_ms` or `gpu_layer_ms_by_index` and usable VRAM / unified budget).
   - Per-layer weight bytes $b_{c,m}, b_{g,m}$ and KV+activation bytes $d_{c,m}, d_{g,m}$ are derived from the model's `weights_bytes_by_index` (or `model_bytes / n_layers`) and `kv_bytes_for_layers` + `compute_buffer_bytes`.
   - Initial set classification before MILP refinement:
     - If `mmap == "none"` or `disk_read_bytes_s < min_disk_read_speed_bytes_s` or the node is forced resident during iterative refinement: assign directly to $M_4$.
     - Otherwise (when `mmap` is enabled, which is the unrestricted Halda behavior per Clarification Q2):
       - If not `has_gpu`: $M_1$.
       - Else if `is_uma`: $M_2$.
       - Else (discrete GPU): $M_3$.
   - After solving for a given $k$, any device $m \in M_1 \cup M_2 \cup M_3$ whose assigned layers $(k \cdot w_m, k \cdot n_m)$ actually fit entirely within its available host RAM and VRAM (`overloaded_mem <= 0`) is reclassified as $M_4$ (resident) in the schedule report and objective evaluation, matching [`common/common.cpp`](file:///D:/dev/prima.cpp/common/common.cpp#L1185-L1208).

---

## R3: Multi-Cycle ($k \ge 1$) Translation to `Segment` Ranges & `prima_graph_counts`

### Decision
For every divisor $k \mid L$ (restricted to $k = 1$ when `"segmented_placement"` is not in `runtime_capabilities`), translate the solver's per-cycle window $(w_m, n_m)$ into concrete contiguous layer segments across the $k$ cycles and compute `prima_graphs` and `prima_graph_counts` via [`reserve_graph_visits()`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/interactors/prima_graph_reserve.py#L46-L70).

### Rationale
1. In Halda's cyclic ring assignment with cycle count $k$ and cycle window $W = L / k$:
   - In cycle $c \in \{0, \dots, k-1\}$, the ring traverses active devices $m = 0, \dots, M-1$ in order. Each active device $m$ with $w_m > 0$ is assigned a contiguous slice of $w_m$ layers within cycle $c$'s range $[c \cdot W, (c+1) \cdot W - 1]$.
   - When $k = 1$, each active device receives a single contiguous [`Segment(start_layer, end_layer, gpu_layers=n_m, cpu_layers=w_m - n_m)`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py#L64-L81), producing `prima_graphs == 1` per active device.
   - When $k > 1$ (and `"segmented_placement"` is supported by the runtime), each active device with $w_m > 0$ receives $k$ disjoint [`Segment`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py#L64-L81) entries (one per cycle), with total layers $k \cdot w_m$, total GPU layers $k \cdot n_m$, and total CPU layers $k \cdot (w_m - n_m)$.
   - Passing the ordered per-cycle device visits to [`reserve_graph_visits()`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/interactors/prima_graph_reserve.py#L46-L70) produces the exact `prima_graphs` and `prima_graph_counts` required so Vulkan backends do not reallocate compute graphs mid-decode.
2. **Exact Layer Indexing Accuracy**: After the MILP selects the optimal $(w_m, n_m)$ using average per-layer coefficients for cycle $k$, we also evaluate the exact segment cost using [`segment_compute_ms()`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/segment_costs.py#L49-L71) and [`segment_weight_bytes()`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/segment_costs.py#L74-L88) when heterogeneous per-layer arrays (`cpu_layer_ms_by_index`, `gpu_layer_ms_by_index`, `weights_bytes_by_index`) are present.

---

## R4: Iterative Weak-Worker Pruning, Slow-Disk Forcing & Operator Constraints

### Decision
Implement the full two-stage Halda outer loop (`assign_layers_and_select_devices`) in [`highs_scheduler.py`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/interactors/highs_scheduler.py) while honoring Kluster's operator constraints (`required_nodes`, `node_priority`, `just_enough_master`).

### Rationale
1. Upstream [`assign_layers_and_select_devices`](file:///D:/dev/prima.cpp/common/common.cpp#L1525-L1659) runs an outer `while True` loop around `assign_layers_to_device`:
   - **Step A (Weak-Worker Detection)**: Any worker $m > 0$ assigned $w_m \le 1$ and $n_m = 0$ is flagged as a weak device (`weak_devices`).
   - **Step B (Pruning with Operator Respect)**:
     - Master rank 0 ($m = 0$) and any node listed in `required_nodes` are **never** pruned (`FR-006`). If a required node has $w_m = 0$, the scheduler enforces $w_m \ge 1$ in the MILP column lower bounds (`col_lower[m] = 1.0`).
     - Optional workers in `weak_devices` are removed from the active candidate pool, recorded as [`HighsPrunedNode(node_id=..., reason="weak_device_w_le_1_ngl_0", last_w_m=..., last_n_m=...)`](file:///D:/dev/kluster-llm/specs/019-highs-scheduler-planning/data-model.md), and the inner loop re-runs on the remaining active devices.
   - **Step C (Slow-Disk / Overloaded-Disk Forcing into $M_4$)**:
     - If no weak devices remain, the scheduler checks if any active device $m \in M_1 \cup M_2 \cup M_3$ has overloaded disk-backed memory **and** slow sequential disk throughput (`disk_read_bytes_s < min_disk_read_speed_bytes_s`, default `500 MB/s` matching upstream's `500 * 1024 * 1024`).
     - Those slow-disk devices are forced into $M_4$ (`force_m4 = True`) and the MILP is re-solved. If forcing $M_4$ makes the MILP infeasible across all $k \mid L$, the scheduler rolls back to the best prior feasible schedule (`opt_k_backup`, `opt_t_backup`, `opt_w_backup`, `opt_n_backup`) so valid disk-streaming deployments are still permitted rather than failing hard when RAM+VRAM is smaller than the model.

---

## R5: Strategy Selection (`auto`, `capacity_first`, `priority`), Wire Digest Stability & Dashboard Surfacing

### Decision
1. **Strategy Selection (`FR-011`)**:
   - Support `placement_strategy: Literal["auto", "capacity_first", "priority"]` in [`InferenceParameters`](file:///D:/dev/kluster-llm/src/kllm/modules/inference/entities.py#L46-L154) and planning payloads (default `"auto"`).
   - Under `"auto"`, when `engine_id == "prima.cpp"` and complete `DeviceProfile` data exists for all candidate nodes, run the HiGHS scheduler (`scheduler_mode="highs_halda"`). When profile data is missing/incomplete, fall back to `"capacity_first"` and report `fallback_reason` in the schedule report.
   - When the operator explicitly selects `"capacity_first"` or `"priority"`, bypass HiGHS and run the corresponding deterministic allocator, reporting `scheduler_mode="capacity_first"` or `"priority"`.
   - Standard `llama.cpp` RPC deployments remain unchanged (`FR-001`).
2. **Wire & Digest Stability**:
   - Do **not** add new required fields to [`RankAllocation`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py#L14-L53) or [`ExecutionManifest`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py#L313-L343), because worker `/v2/node/runtimes/{id}/prima-prepare` endpoints validate `ExecutionManifest` with `extra="forbid"` (`Record`).
   - Instead, return the `scheduler: HighsScheduleReport` object alongside `profiling` in `/v2/inference/plans` and `/v2/node/runtimes/{id}/prima-plan` responses, and attach optional `scheduler_report: HighsScheduleReport | None = None` on [`PrimaPlan`](file:///D:/dev/kluster-llm/src/kllm/modules/primacpp/entities.py#L285-L310) (excluded from `PrimaPlan.digest()` unless explicitly requested, preserving 100% digest compatibility).
3. **Dashboard UX (`FR-009`, `FR-010`, `FR-011`)**:
   - Update [`RunPage.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/view/RunPage.svelte), [`DeploymentPlan.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/view/DeploymentPlan.svelte), and [`PrimaPreparation.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/runtimes/view/PrimaPreparation.svelte) to offer `Automatic (HiGHS / Capacity fallback)`, `Capacity-first`, and `Priority` placement modes and render the HiGHS schedule breakdown badge, cycle count $k$, cycle window $W$, per-node $(w_m, n_m)$ and $M_1 \dots M_4$ set badges, latency components (compute, disk reload, KV/link), and pruned weak workers.
