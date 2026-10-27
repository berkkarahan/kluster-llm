# Feature Specification: HiGHS Scheduler Integration for Planning and Dashboard

**Feature Branch**: `019-highs-scheduler-planning`

**Created**: 2026-10-14

**Status**: Draft

**Input**: User description: "implement and integrate HiGHS scheduler from primacpp into kluster-llm's dashboard and planning"

## Clarifications

### Session 2026-10-14

- Q: Should the HiGHS scheduler optimize placements only for Prima.cpp runs, or also for standard llama.cpp RPC deployments? → A: Prima.cpp only — run the HiGHS scheduler for Prima.cpp planning and preparation; standard llama.cpp RPC keeps existing capacity-first/priority planning.
- Q: How should the HiGHS scheduler treat models or node allocations that exceed usable RAM/VRAM budgets when memory-mapped disk streaming (`mmap`) is enabled? → A: Unrestricted Halda sets — always allow the solver to trade off disk-streaming sets ($M_1..M_3$) against resident memory ($M_4$) whenever `mmap` is enabled.
- Q: How should the operator select between HiGHS latency-optimized scheduling and capacity-first placement in the dashboard and planning requests? → A: Auto uses HiGHS + explicit Capacity-first option — "Automatic" runs HiGHS when profiles exist (falling back to capacity-first if unmeasured), and operators can also explicitly select "Capacity-first" or "Priority".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Latency-Optimal Layer, GPU, and Cycle Scheduling in Planning (Priority: P1)

A cluster operator preparing a distributed or local model run wants the planner to compute the fastest layer and GPU allocation across participating machines using Prima's mathematical optimization scheduler (the HiGHS/Halda solver) rather than relying solely on proportional memory-capacity splitting. When verified hardware, storage, and link profiles are available, the planner solves for each node's per-cycle layer window, GPU-offloaded layer count, memory-pressure regime, and ring cycle count to minimize predicted token generation latency while respecting every node's memory limits.

**Why this priority**: This is the foundational scheduling capability. Without solver-driven placement in the planner, Kluster can only score pre-generated capacity-first splits and cannot discover optimal layer/GPU distributions or multi-cycle ring schedules that balance compute speed, disk read bandwidth, host-to-GPU transfer overhead, and network latency.

**Independent Test**: Provide a model layout and complete participant/link profiles for a heterogeneous cluster, request a deployment plan, and verify that the planner returns a valid, solver-optimized placement with per-node layer windows, GPU layer counts, cycle count, memory-pressure classifications, and predicted latency breakdown.

**Acceptance Scenarios**:

1. **Given** a cluster with complete, fresh device and link profiles across participating nodes (on Windows, Linux, or macOS), **When** an operator requests a plan with optimization enabled, **Then** the planner solves for the per-node layer window ($w_m$), GPU layer count ($n_m$), and cycle count ($k$) that minimize predicted decode latency, and returns a valid placement topology covering 100% of model layers.
2. **Given** a cluster where a runtime supports segmented (multi-visit) execution and a multi-cycle schedule ($k > 1$) yields lower predicted latency under memory pressure than a single-cycle schedule ($k = 1$), **When** the planner runs the scheduler, **Then** the resulting plan selects the optimal divisor cycle count $k$, expands it into exact contiguous layer segments per visit, and reserves the required per-node execution graphs.
3. **Given** a cluster where the runtime does not advertise segmented execution support (or the operator constrains the cycle count to 1), **When** the planner runs the scheduler, **Then** the solver restricts candidate schedules to a single cycle ($k = 1$) so the resulting placement is executable on that runtime.
4. **Given** a cluster where device profiles, storage measurements, or private link measurements are missing, stale, or incomplete, **When** an operator requests a plan, **Then** the planner explicitly falls back to deterministic capacity-first placement and reports the exact reason why solver optimization was not applied, without inventing measurements.

---

### User Story 2 - Weak-Worker Pruning and Constraint-Aware Participant Selection (Priority: P2)

A cluster operator has multiple worker nodes of varying capability enrolled in the cluster. Some workers may have slow CPUs, no usable GPU memory, slow disk throughput, or high network latency such that assigning layers to them slows down the entire inference ring. When the operator plans a deployment without marking those slow workers as required, the scheduler identifies and prunes weak workers and re-optimizes the layer and GPU schedule across the remaining participants while strictly honoring all operator-specified node constraints.

**Why this priority**: A single slow or memory-starved worker in a ring pipeline bottlenecks every token step. Automatic weak-worker pruning paired with hard operator constraints (`required_nodes`, `node_priority`, master priority, and memory caps) ensures optimal cluster utilization without overriding explicit user intent.

**Independent Test**: Request a plan on a cluster containing a fast GPU node and an optional weak CPU-only worker; confirm the scheduler prunes the weak worker and reassigns all layers to the remaining capable nodes. Repeat with the weak worker marked as required and confirm the worker is retained.

**Acceptance Scenarios**:

1. **Given** an enrolled optional worker that the solver assigns at most 1 CPU layer and 0 GPU layers (indicating a weak device that degrades ring latency), **When** the planner runs the scheduler, **Then** the weak worker is pruned from the active participant set, the excluded node and reason are recorded in the plan summary, and layers are re-solved across the remaining participants.
2. **Given** an enrolled worker that the operator has explicitly marked as a required node, **When** the planner runs the scheduler, **Then** the scheduler never prunes that required worker even if it would otherwise qualify as a weak device.
3. **Given** an operator-configured master priority weight or "Just Enough Master" preference, **When** the planner runs the scheduler, **Then** the solver incorporates the head-node cost weighting and memory bounds so the master allocation reflects the operator's preference.
4. **Given** a model and context size whose memory footprint exceeds both resident memory ($M_4$) and feasible disk-streaming constraints ($M_1..M_3$, or when `mmap` is disabled and resident memory is exceeded) under every valid cycle count, **When** the planner runs the scheduler, **Then** the solver reports infeasibility with an actionable diagnostic and the plan is marked as overflowing or falls back with an explicit explanation.

---

### User Story 3 - Dashboard Schedule Inspection and Strategy Controls (Priority: P3)

An operator using the Kluster dashboard (in the Deploy workflow and the Prima Preparation panel) wants to choose the scheduling strategy, configure solver preferences (such as cycle count or automatic optimization vs. capacity-first), and inspect the resulting HiGHS schedule details before preparing files or launching inference.

**Why this priority**: Visibility and control in the dashboard make the mathematical scheduler understandable and actionable for operators, allowing them to see why specific nodes received specific CPU/GPU layer counts, whether any node is under memory/swap pressure, and which workers were pruned.

**Independent Test**: Open the Deploy page and Prima Preparation panel in the dashboard with a planned model, toggle scheduling options, and verify that the UI renders the solver status, cycle count ($k$), per-node window/GPU allocations ($w_m, n_m$), memory-pressure regime ($M_1..M_4$), pruned nodes, and predicted latency breakdown.

**Acceptance Scenarios**:

1. **Given** an operator on the Deploy page or Prima Preparation panel, **When** a plan is computed using the HiGHS scheduler, **Then** the dashboard displays the scheduler status (e.g., optimal schedule vs. capacity-first fallback), the selected cycle count ($k$) and window size ($W$), each participating node's assigned layer window ($w_m$), GPU layer count ($n_m$), and memory-pressure classification ($M_1..M_4$), and any pruned weak workers.
2. **Given** a solver-optimized plan with valid profile evidence, **When** the operator views the plan summary in the dashboard, **Then** the dashboard displays the predicted single-token latency and its component breakdown (compute, host/GPU handoff, disk/memory-pressure read, and network transfer).
3. **Given** an operator who changes a scheduling preference (such as switching between automatic solver scheduling and capacity-first/priority placement, or adjusting cycle preferences), **When** the setting changes, **Then** the dashboard invalidates stale preparation state, refreshes the plan preview, and requires a matching plan digest before preparation or launch.
4. **Given** a plan where solver optimization fell back to capacity-first placement due to missing profiles or solver infeasibility, **When** the operator views the dashboard, **Then** the fallback status and exact diagnostic reason are clearly shown — never a fabricated zero latency or silent error.

---

### Edge Cases

- **Prime or indivisible layer counts**: What happens when a model's total transformer layer count $L$ has limited divisors or cannot be evenly divided by the sum of initial layer windows $W$? The scheduler adjusts initial window estimates so $L \bmod W = 0$ and evaluates all valid divisor cycle counts $k$ (including $k = 1$ where $W = L$), ensuring at least $k = 1$ is always evaluated.
- **Coordinator-only CPU master**: How does the system handle a distributed cluster where the master node acts as a zero-layer CPU coordinator while workers hold all transformer layers? The planner preserves support for coordinator-only masters when required by capacity/priority rules and accurately prices rank-zero input/output overhead ($\kappa$) without crashing on missing master GPU capabilities.
- **Iterative memory-set oscillation**: What happens if the iterative memory-pressure set assignment ($M_1, M_2, M_3, M_4$) does not converge or encounters an infeasible step after forcing slow-disk devices into the in-memory set ($M_4$)? The scheduler bounds iteration count, rolls back to the last valid optimal solution found, or cleanly reports infeasibility if no valid solution exists.
- **Cross-platform and unified-memory nodes**: How does the scheduler treat Windows, Linux, and macOS nodes (including unified-memory Apple Silicon with MoltenVK/Metal vs. discrete-VRAM Windows/Linux Vulkan GPUs)? Each node is classified into the appropriate memory-pressure set using its actual reported OS, unified vs. discrete memory architecture, and usable RAM/VRAM budgets, with zero refusals caused solely by operating system.
- **Read-only plan previews**: What happens when the dashboard or CLI requests a plan preview repeatedly? Running the HiGHS solver during preview is purely computational and read-only; it never initiates remote profiling jobs, transfers model assets, or mutates cached profiles.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: For Prima.cpp planning and preparation, the planning system MUST integrate Prima's HiGHS mixed-integer linear programming (MILP) scheduler to compute per-node layer windows ($w_m$), GPU-offloaded layer counts ($n_m$), and ring cycle counts ($k$) that minimize predicted token decode latency subject to per-node RAM and GPU memory constraints; standard llama.cpp RPC deployments MUST retain existing capacity-first and priority planning.
- **FR-002**: The scheduler MUST support heterogeneous clusters across Windows, Linux, and macOS and across supported GPU backends (including Vulkan, MoltenVK, Metal, and CUDA) as well as CPU-only participants, eliminating legacy restrictions that blocked Windows or Vulkan devices from solver scheduling.
- **FR-003**: Whenever `mmap` is enabled, the scheduler MUST allow the solver to freely trade off Halda disk-streaming memory-pressure sets ($M_1, M_2, M_3$) against resident memory ($M_4$) based on whether the node uses unified or discrete GPU memory, whether assigned weights, KV cache, and compute buffers fit within usable RAM/VRAM budgets, and whether disk read bandwidth meets the minimum viable threshold (forcing slow-disk or non-`mmap` nodes into $M_4$), iteratively refining the classification until convergence or bounded rollback.
- **FR-004**: The scheduler MUST evaluate valid cycle counts $k$ (where $k$ divides the model's total layer count $L$, or matches an explicit cycle constraint) and MUST restrict $k = 1$ whenever the target runtime does not advertise segmented placement support.
- **FR-005**: When a multi-cycle schedule ($k > 1$) is selected, the planner MUST translate the cyclic window allocation $(w_m, n_m, k)$ into exact absolute layer segments per rank visit, compute accurate per-rank graph visit counts, and enforce graph reservation requirements before admitting the plan.
- **FR-006**: The scheduler MUST iteratively identify and prune optional weak worker nodes (workers assigned $w_m \le 1$ and $n_m = 0$) and re-solve layer allocation across the remaining nodes, while NEVER pruning the master node, any operator-specified `required_nodes`, or the last remaining worker in a network-bound distributed run.
- **FR-007**: The scheduler MUST respect operator-configured placement constraints, including node priority ordering, master priority weighting, "Just Enough Master" allocation, explicit device selections, and node VRAM/RAM percentage safety caps.
- **FR-008**: Solver-driven latency optimization MUST be gated on complete, fresh, verified model metadata, device profiles, storage evidence, and directed private-link measurements (for distributed runs); when any required evidence is missing, stale, or mismatched, the planner MUST fall back to deterministic capacity-first placement and record the specific fallback reason.
- **FR-009**: Every plan response produced for a Prima runtime MUST include structured scheduler metadata detailing the scheduler mode/status, selected cycle count ($k$), per-cycle window total ($W$), per-node window and GPU layer assignments ($w_m, n_m$), memory-pressure set assignments ($M_1..M_4$), pruned nodes with reasons, and the objective latency breakdown when measured.
- **FR-010**: Adding scheduler metadata to plan responses MUST preserve backward compatibility and canonical digest stability for execution manifests and rank wire payloads sent to workers.
- **FR-011**: The dashboard (Deploy page and Prima Preparation panel) MUST display the HiGHS scheduler status, cycle and window summary, per-node allocation and memory-pressure details, pruned weak workers, and fallback or infeasibility diagnostics, and MUST allow the operator to select between "Automatic" (uses HiGHS optimization when profiles exist, with automatic capacity-first fallback when unmeasured), "Capacity-first" (forces capacity-based placement even when profiles exist), and "Priority" placement modes.
- **FR-012**: Plan preview operations invoking the HiGHS scheduler MUST remain fast, deterministic, and strictly read-only, without spawning remote measurement jobs, staging model files, or modifying persistent caches.

### Key Entities

- **HiGHS Schedule Solution**: The output of the MILP scheduler for a candidate participant set, containing solver status (`optimal`, `fallback`, `infeasible`), cycle count ($k$), window sum ($W = L / k$), objective decode latency (ms), per-node allocations ($w_m, n_m$), memory-pressure set memberships ($M_1..M_4$), and any pruned weak worker records.
- **Memory-Pressure Set ($M_1..M_4$)**: The per-node classification used by the Halda cost model to price disk/swap overhead vs. resident memory execution:
  - $M_1$: Unified-memory/CPU-only node whose assigned footprint exceeds available physical RAM.
  - $M_2$: Unified-memory GPU node whose assigned footprint (weights, KV, CPU+GPU buffers) exceeds available unified memory.
  - $M_3$: Discrete-GPU or CPU node whose host RAM footprint (CPU layers, KV, and buffers) exceeds available physical RAM.
  - $M_4$: Node whose assigned CPU and GPU footprints fit entirely within available physical RAM and VRAM budgets (or that is forced into resident constraints due to slow disk I/O).
- **Scheduler Strategy Preference**: The operator's selected planning mode (`auto`, `capacity_first`, or `priority`) determining whether the planner applies HiGHS latency optimization by default when evidence permits (`auto`), forces capacity-first placement (`capacity_first`), or enforces manual node priority ordering (`priority`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any cluster with complete profile and link evidence across Windows, Linux, and macOS nodes, the planner produces a valid, solver-verified placement covering 100% of model layers in under 1 second per plan preview request.
- **SC-002**: In 100% of test scenarios where a multi-node cluster includes an optional weak worker ($w_m \le 1, n_m = 0$), the scheduler prunes the weak worker and produces a lower or equal predicted decode latency unless that worker is marked as required by the operator.
- **SC-003**: In 100% of test scenarios where profile or link evidence is missing, stale, or incomplete, the planner deterministically falls back to capacity-first placement and surfaces the exact fallback reason in both the plan response and dashboard UI, with zero fabricated latency values.
- **SC-004**: When a multi-cycle schedule ($k > 1$) is selected for a segmented-capable runtime, 100% of generated plans pass layer coverage and per-visit graph reservation validation without gaps or overlaps.
- **SC-005**: Operators can view the scheduler status, cycle count, per-node window/GPU split, memory-pressure set, and pruned worker details directly in the dashboard Deploy and Prima Preparation views without inspecting raw logs.
- **SC-006**: Existing canonical execution manifest digests and capacity-first planning workflows experience zero regressions when scheduler metadata is unset or when running with the default engine.

## Assumptions

- Kluster LLM remains the single authority for cluster membership, placement manifests, and process lifecycle. Native `prima.cpp` processes launched by Kluster continue to run in manifest mode using the exact placement solved and approved by Kluster's planner, rather than running autonomous unauthenticated discovery at process startup.
- The HiGHS optimization formulation follows Prima's Halda MILP model (optimizing integer decision variables $w_m$ and $n_m$ across divisor cycle counts $k$ and iterative memory sets $M_1..M_4$), generalized to support Windows, Linux, and macOS as well as Vulkan (including MoltenVK), Metal, CUDA, and CPU-only coordinator ranks.
- In Kluster's distributed ring model, network communication latency and bandwidth costs from verified private-link receipts are included in the schedule objective alongside per-node compute, KV copy / host-GPU handoff, and disk read costs.
- Enabling and integrating the HiGHS scheduler in planning and the dashboard does not bypass or relax Prima's source-controlled runtime qualification gates; live hardware qualification remains governed by recorded verification receipts.
