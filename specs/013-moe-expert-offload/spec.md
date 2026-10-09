# Feature Specification: MoE Expert Offload to Scattered Nodes

**Feature Branch**: `013-moe-expert-offload`

**Created**: 2026-10-02

**Status**: Draft

**Input**: User description: "add option of offloading MoE layers to scattered nodes for MoE models when it can't fit to VRAM. This option should be available both in automatic / partial configuration modes. Also increase the upper limit on batch and micro batch sizes while still keeping the default values, check online or llamacpp codebase for reasonable values."

## Clarifications

### Session 2026-10-02

- Q: When offloading an MoE model that does not fit in GPU memory, which part of the model should move to remote nodes? → A: Decide per model — offload expert weights first, then whole layers only if still over capacity — with the ability to leave the decision automatic (heuristic-driven).
- Q: What does "partial configuration mode" refer to for this offload option? → A: Both — automatic placement, manual node-share configuration, and partial GPU offload (model partially on GPU, partially on system RAM).
- Q: When expert weights are offloaded to scattered nodes, which memory on those nodes should receive them? → A: Remote GPU memory first, then remote system RAM as fallback.
- Q: When an operator overrides the automatic heuristic, what form should the override take? → A: Automatic by default, with an optional explicit count override.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatically offload MoE expert weights to remote nodes (Priority: P1)

An operator wants to run a large MoE (mixture-of-experts) model whose full weight set exceeds the combined GPU memory of the cluster, or of any single node. Today the only choices are to spill whole layers onto system RAM, run the model on CPU, or fail with an overflow. Instead, the operator wants the system to keep the shared/attention portions of the model on fast local memory and automatically distribute the large expert portions across the other nodes in the cluster, so the model still serves even though no single node can hold it entirely in GPU memory.

**Why this priority**: This is the primary capability being requested. It unlocks MoE models that are otherwise un-runnable at acceptable performance, and it is the core value of the feature.

**Independent Test**: With a multi-node cluster and an MoE model sized so it does not fit in GPU memory, plan and run the model with the offload option enabled in the default (automatic) configuration. Verify the plan distributes the expert weights across multiple nodes, the run starts successfully, and the model answers a request. This delivers value on its own without the manual/partial and batch-limit stories.

**Acceptance Scenarios**:

1. **Given** an MoE model that does not fit in any single node's GPU memory, **When** the operator plans the model with the offload option enabled in automatic mode, **Then** the plan succeeds and places the expert weights across two or more nodes, keeping shared weights on local fast memory.
2. **Given** the same model and plan, **When** the operator runs the model, **Then** the model starts and completes a request without an overflow error.
3. **Given** a non-MoE model, **When** the operator enables the offload option, **Then** the option has no effect and the model runs exactly as before.

---

### User Story 2 - Offload MoE expert weights in partial/manual configuration (Priority: P2)

An operator who prefers to partition the workload themselves (manual node shares) wants the MoE expert offload to work there too, so a partially-configured cluster can still spread expert weights across chosen nodes rather than being limited to automatic distribution.

**Why this priority**: Operators who use manual/partial placement would otherwise be excluded from the capability. It is secondary only because the automatic path serves the majority.

**Independent Test**: Using manual node shares across a cluster, plan an MoE model that exceeds a single node's GPU memory with the offload option enabled; verify the expert weights are distributed to the chosen nodes according to the operator's shares and the run starts.

**Acceptance Scenarios**:

1. **Given** manual node shares that alone cannot fit the whole MoE model on GPU, **When** the operator enables expert offload in the partial/manual configuration, **Then** the expert weights are placed on the nodes the operator selected and the plan reports the resulting distribution.
2. **Given** a partial configuration where offload is not enabled, **When** the operator plans, **Then** the existing behavior is unchanged (no expert offload occurs).

---

### User Story 3 - Configure larger batch and micro-batch sizes (Priority: P3)

Operators running high-throughput workloads want to raise the logical batch size and the physical micro-batch size above the current ceilings, while the out-of-the-box defaults stay the same.

**Why this priority**: This is a tuning improvement on top of the main capability; the feature is still valuable without it.

**Independent Test**: Configure a run with an explicit batch size and micro-batch size above the previous ceilings (up to the new maximums) and confirm the resolved settings accept and report those values, while omitting the values still yields the unchanged defaults.

**Acceptance Scenarios**:

1. **Given** a run where the operator requests a batch size larger than the previous ceiling (up to the new maximum), **When** the settings are resolved, **Then** the requested batch size is accepted rather than silently capped to a smaller value.
2. **Given** a run where the operator requests a micro-batch size larger than the previous ceiling (up to the new maximum and no greater than the batch size), **When** the settings are resolved, **Then** the requested micro-batch size is accepted.
3. **Given** a run where the operator leaves batch size and micro-batch size unset, **When** the settings are resolved, **Then** the defaults remain 512 for batch size and 128 for micro-batch size.

---

### Edge Cases

- MoE model that does not fit even when expert weights are distributed across all nodes (combined GPU and CPU capacity insufficient): the plan must fail with an actionable "does not fit" result rather than partially loading.
- MoE model where the shared/attention weights alone exceed the local node's GPU memory: the planner must still produce a valid placement (e.g., distribute more aggressively) or a clear failure.
- A cluster where remote nodes do not advertise the ability to receive expert weights (e.g., older or un-updated nodes): the option must degrade to current behavior and report why offload was not used.
- Batch size requested non-positive: rejected with a clear error. Batch/micro-batch values above the effective ceilings are capped (batch to the larger of 2048 and context; micro-batch to the smaller of batch and 512), consistent with existing clamping.
- Micro-batch size requested larger than the batch size: reduced to the batch size, as today.
- Expert offload requested for an architecture that is not MoE: no effect, no error.
- Manual node shares that total less than or more than the model, combined with expert offload: the plan reports the requested versus actual distribution and blocks run if it cannot fit.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide an option for MoE models that offloads portions of the model — expert weights, whole layers, or a mix — to other nodes in the cluster, chosen per model.
- **FR-002**: When an MoE model does not fit in the available GPU memory of the local node, the planner MUST attempt to place the expert weights on remote nodes first, then whole layers only if still over capacity, before falling back to CPU spilling or reporting overflow.
- **FR-002a**: The offload option MUST default to automatic (heuristic-determined strategy of expert weights, whole layers, or a mix), and the operator MUST be able to override it with an explicit count of experts or layers to offload.
- **FR-002b**: Offloaded expert weights MUST be placed on remote nodes' GPU memory first, and on remote system RAM only when remote GPU memory is insufficient.
- **FR-003**: The expert offload option MUST be available in the default automatic placement configuration.
- **FR-004**: The expert offload option MUST be available in the manual (node-share) configuration.
- **FR-004a**: The expert offload option MUST also apply in partial GPU-offload scenarios where the model only partially fits in GPU memory and the remainder runs on system RAM.
- **FR-005**: The option MUST NOT change behavior for non-MoE model architectures.
- **FR-006**: The plan/estimate output MUST report the resulting distribution of expert weights across nodes, and MUST remain accurate (fit status, per-node allocations) so the reported plan matches the eventual launch.
- **FR-007**: When the model still cannot fit after expert offload is considered, the system MUST return an actionable failure that identifies the shortfall rather than silently loading partially or swallowing the error.
- **FR-008**: The default batch size MUST remain 512, and the default micro-batch size MUST remain 128 when no explicit value is provided.
- **FR-009**: The system MUST accept an explicitly requested batch size up to at least 2048 (the upstream logical batch maximum); the effective batch size is capped at the larger of 2048 and the context window, so no valid request is rejected for exceeding 2048.
- **FR-010**: The system MUST accept an explicitly requested micro-batch size up to at least 512 (the upstream physical micro-batch maximum); the effective micro-batch size is capped at the smaller of the resolved batch size and 512.
- **FR-011**: Non-positive batch or micro-batch values MUST produce a clear validation error. Values above the effective ceilings (batch above the context-derived cap, micro-batch above the batch size or 512) are capped, consistent with existing micro-batch-to-batch clamping behavior.
- **FR-012**: The raised batch and micro-batch ceilings MUST be reflected in both the estimate/plan path and the actual run launch so that planned and launched settings agree.

### Key Entities *(include if feature involves data)*

- **MoE model**: A model with a mixture-of-experts architecture, split between shared (attention) weights and a larger set of expert weights.
- **Expert weights**: The MoE-specific parameters that are the target of offloading; they can be distributed independently of the shared weights.
- **Node**: A cluster member contributing GPU memory and/or system memory; nodes are the targets for scattered expert placement.
- **Offload placement**: The mapping of expert weights to specific nodes, produced by the planner and reported to the operator.
- **Run settings**: The operator-facing batch size and micro-batch size, each with a default value and an upper limit.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An MoE model that exceeds a single node's GPU memory can be planned and launched across a multi-node cluster using the expert offload option, and serves a completed request without an overflow error.
- **SC-002**: The plan/estimate for an MoE offload reports a per-node distribution and fit status that matches the launched run (no divergence between plan and launch).
- **SC-003**: Operators can configure a batch size up to 2048 and a micro-batch size up to 512, and the accepted values are reflected in both the estimate and the launch.
- **SC-004**: Runs that leave batch size and micro-batch size unset continue to resolve to 512 and 128 respectively, with no change from prior behavior.
- **SC-005**: Non-MoE models behave identically to before the change, and invalid batch/micro-batch values are rejected with a clear error.

## Assumptions

- "Scattered nodes" means the existing cluster members that contribute memory through the cluster's distribution mechanism; no new node type is introduced.
- "Automatic / partial configuration modes" covers the automatic placement path and the partial offload paths: manual node-share configuration, and partial GPU offload where the model only partially fits in GPU memory and spills to system RAM. The option must function under all of these.
- The reasonable maximums for batch and micro-batch are drawn from the upstream llama.cpp server defaults: logical batch size 2048 and physical micro-batch (ubatch) size 512. kllm's own defaults (512 / 128) are intentionally kept lower and remain unchanged.
- Existing whole-layer offloading, CPU spilling, and manual node-share behavior remain available and are unchanged except where the new option explicitly participates.
- Hardware validation of multi-node MoE inference is required before the feature is reported as fully validated, per project governance; deterministic local checks are used for the contract-level behavior.
