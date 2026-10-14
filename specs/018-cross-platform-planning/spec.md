# Feature Specification: Cross-Platform Planning from Prima.cpp

**Feature Branch**: `018-cross-platform-planning`

**Created**: 2026-10-14

**Status**: Draft

**Input**: User description: "implement cross-platform planning from primacpp"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mixed-OS Cluster Planning (Priority: P1)

A cluster operator has machines running different operating systems (for example a Windows master with Linux workers, or a macOS worker in a Windows-led cluster). When they request a Prima inference run, the system produces a valid execution plan that accounts for each node's operating system, architecture, and GPU backend, without requiring every node to run the same OS.

**Why this priority**: This is the core capability. Today Prima qualification only recognizes Windows with Vulkan, so any cluster containing a Linux or macOS node is refused outright. That blocks the entire cross-platform use case.

**Independent Test**: Enroll heterogeneous node records (different operating systems, same Vulkan backend) and request a plan. The result is a valid placement with a per-node backend assignment, not a refusal caused by operating system.

**Acceptance Scenarios**:

1. **Given** a cluster with a Windows/Vulkan master and a Linux/Vulkan worker, **When** a Prima plan is requested for a model that fits across both, **Then** the plan is produced with each node's operating system, architecture and backend recorded, and no refusal citing operating system mismatch.
2. **Given** a cluster where one node uses the Metal backend and another uses Vulkan, **When** a Prima plan is requested, **Then** the plan is rejected before anything starts, with an error naming both nodes and their backends and stating that Prima rings require one shared compatible backend.
3. **Given** a single-node macOS cluster using Vulkan (MoltenVK), **When** a local Prima plan is requested, **Then** the plan is evaluated against a macOS scope and is not refused for being a non-Windows node.
4. **Given** a distributed cluster whose master only coordinates (no model layers, CPU), **When** a Prima plan is requested, **Then** that coordinator-only master remains accepted on any operating system, as it is on Windows today.

---

### User Story 2 - Platform-Aware Qualification (Priority: P2)

A node operator views Prima qualification status for their runtime. Qualification is evaluated from the node's actual operating system, backend and run topology (local or distributed) rather than a fixed Windows gate. Each supported platform/backend pair has its own scope with independent evidence requirements.

**Why this priority**: Without platform-aware scopes, cross-platform plans always fail the qualification gate even when everything else is valid. This unblocks planning for non-Windows nodes.

**Independent Test**: Provide runtime records with different operating system and backend values and confirm each reports the scope matching its own platform, never a Windows scope.

**Acceptance Scenarios**:

1. **Given** a Linux node with a Vulkan backend, **When** qualification status is checked, **Then** the reported scope identifies Linux, Vulkan and the requested topology, and evidence is evaluated for that scope only.
2. **Given** a macOS node with a Vulkan (MoltenVK) backend, **When** qualification status is checked, **Then** the reported scope identifies macOS, Vulkan and the requested topology.
3. **Given** a platform/backend pair with no recorded evidence, **When** qualification status is checked, **Then** it is reported as unqualified with a reason stating that evidence is missing for that exact scope; it never falls back to another platform's scope or evidence.
4. **Given** a distributed cluster mixing Windows and Linux nodes, **When** qualification status is checked, **Then** each participant is evaluated against its own platform scope and the cluster result is qualified only if every participant is.

---

### User Story 3 - Cross-Platform Build Identity Verification (Priority: P3)

When a distributed Prima run is prepared, each participating node reports its build identity (operating system, architecture, source commit, recipe and runtime identity). Preparation verifies that all participants share the same source identity and recipe even though their binaries are platform-specific.

**Why this priority**: Shared source identity is a prerequisite for distributed runs. Nodes built from the same recipe on different platforms must be accepted; nodes built from a different recipe must be rejected.

**Independent Test**: Build participant evidence with matching source identity but differing operating system and architecture and confirm preparation accepts it; change one node's recipe and confirm rejection.

**Acceptance Scenarios**:

1. **Given** two nodes built from the same recipe and source commit on different operating systems, **When** preparation assembles the run record, **Then** preparation succeeds and records each node's actual operating system and architecture.
2. **Given** one node whose recipe or source identity differs, **When** preparation assembles the run record, **Then** preparation fails with an error naming the mismatched node and the differing identity.

---

### Edge Cases

- A node's operating system or backend is missing or unrecognized in its membership record: the node is reported unqualified with an explicit "platform unrecognized" reason; it is never assumed to be Windows.
- A MoltenVK (macOS) Vulkan device reports different capabilities than the same recipe on Windows or Linux: capacity and feature checks use the node's own reported values, not another platform's.
- A node's operating system or driver changes between profile measurement and plan execution: the plan is revalidated and refused as stale instead of running on outdated evidence.
- Adjacent ring nodes differ in architecture (for example x86_64 Windows next to arm64 macOS): planning accepts the pairing when backends are compatible and records both architectures.
- A cluster contains only one node of a platform that has no recorded evidence: the cluster result is unqualified for that node, even if all other nodes are qualified.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The planner MUST accept clusters whose nodes run different operating systems (Windows, Linux, macOS) and produce a placement in which each node's backend assignment reflects that node's own reported capabilities.
- **FR-002**: Qualification MUST derive its scope from each node's own operating system, backend and run topology instead of a fixed Windows scope.
- **FR-003**: The qualification policy MUST hold independent entries per platform/backend/topology scope, each with its own gates, evidence receipts, qualified models, devices and features. Adding an entry for one scope MUST NOT change the result for another.
- **FR-004**: A scope with no recorded evidence MUST be reported unqualified with an explicit reason. The system MUST NOT fall through to another scope or treat a defined-but-empty scope as qualified.
- **FR-005**: Build identity verification during preparation MUST accept participants that share source commit and recipe identity regardless of operating system or architecture, and MUST reject participants whose identity differs, naming the node.
- **FR-006**: The planner MUST reject a distributed ring whose nodes cannot share a compatible backend and ring transport, before any process starts.
- **FR-007**: Operating system and architecture MUST be recorded per node in run records, participant evidence and qualification results so each node is auditable.
- **FR-008**: Every plan rejection caused by cross-platform incompatibility MUST name the affected nodes and the specific platform, backend or identity constraint that failed.
- **FR-009**: Existing Windows qualification evidence and Windows-only workflows MUST keep producing the same results as before, including acceptance of a coordinator-only CPU master in distributed runs.
- **FR-010**: Platform-specific qualification behavior MUST have checks covering Windows, Linux and macOS node records. Checks that require real GPUs or multiple physical machines MUST state their operating system, backend and hardware prerequisites, and results not obtained on that hardware MUST be reported as unverified rather than passed.

### Key Entities

- **Qualification Scope**: One platform + backend + topology combination (for example Linux, Vulkan, distributed). Holds its own gates, evidence receipts, qualified models, devices and features.
- **Participant Platform**: The operating system, architecture and actual GPU backend of one cluster node, recorded in membership, run records and qualification results.
- **Cross-Platform Placement**: A placement plan in which each rank carries its node's platform and architecture alongside its backend assignment.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can obtain a valid Prima plan for a cluster containing nodes on at least two different operating systems whenever their backends are ring-compatible.
- **SC-002**: For node records on each of Windows, Linux and macOS with Vulkan, qualification reports a scope naming that node's own platform in 100% of cases, with zero refusals caused only by operating system.
- **SC-003**: 100% of cross-platform plan rejections state which nodes and which constraint (platform, backend or identity) caused them.
- **SC-004**: Distributed preparation accepts participants with identical source identity across different operating systems without manual overrides, and rejects every participant with a non-matching identity.
- **SC-005**: All existing Windows qualification outcomes are unchanged after this feature (zero regressions in the existing Windows-scope checks).
- **SC-006**: No platform/backend pair without recorded evidence is ever reported as qualified.

## Assumptions

- Scope in this feature is planning, qualification and build-identity verification. It defines how platforms are evaluated; it does not itself produce Linux or macOS qualification evidence. Those scopes report unqualified until live hardware evidence is recorded, consistent with the Prima integration feature that keeps qualification open pending physical runs.
- All participants in one Prima ring must share a compatible backend and ring transport. Mixing different backends (for example Metal with Vulkan) in one ring is out of scope and is rejected at plan time.
- macOS Prima support uses Vulkan through MoltenVK, matching the Prima integration target of Vulkan on Windows, Linux and macOS.
- Source and recipe identity digests are platform-independent, so identical sources built on different operating systems produce matching identities.
- Linux support in this feature means the Vulkan backend. CUDA and ROCm are out of scope.
- The qualification policy can gain new scope entries without affecting consumers that look scopes up by name.
- Existing profiling data (kernel profiles, generation probes, link measurements) is platform-independent at the data level; platform-specific profiling adjustments are out of scope.
- Real multi-OS acceptance runs require physical hardware that may not be available during development. Fixture-based checks are used meanwhile and are not presented as hardware validation.
