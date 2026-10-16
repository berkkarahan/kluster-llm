# Phase 0 Research: Cross-Platform Prima Planning

## 1. Platform-Derived Qualification Scopes & Multi-OS Distributed Evaluation

- **Decision**:
  - Normalize host/participant operating system identifiers (`Windows`, `Linux`, `Darwin` from `platform.system()` or lowercase equivalents) into canonical lowercase scope prefixes (`windows`, `linux`, `darwin`).
  - Normalize backend identifiers for Prima qualification: a runtime or participant with `backend` equal to `"vulkan"` or `"metal+vulkan"` (macOS build with MoltenVK enabled) resolves to the `"vulkan"` qualification backend when planning/evaluating a Vulkan ring.
  - Derive local scope as `<platform_lower>-vulkan-local` and distributed scope per participant as `<platform_lower>-vulkan-distributed`.
  - Expand `src/kllm/modules/primacpp/interactors/qualification.json` to define `linux-vulkan-local`, `linux-vulkan-distributed`, `darwin-vulkan-local`, and `darwin-vulkan-distributed` alongside `windows-vulkan-local` and `windows-vulkan-distributed`, with all non-Windows verification gates initialized to `false` and empty `verified_models`, `verified_devices`, and `receipts`.
  - In distributed qualification (`evaluate_context` in `qualification.py`), evaluate every active participant against its own platform's `<participant_platform_lower>-vulkan-distributed` scope in `qualification.json`. If any participant's scope is unqualified or missing required gates/models/devices, aggregate and report the failing participant node IDs, platforms, and missing verification gates (`unqualified_scope` or `unverified_gate`).
- **Rationale**:
  - `qualification.py` previously hardcoded `scope = "windows-vulkan-" + topology` and rejected any `platform != "Windows"` with `unsupported_platform_or_backend`. Driving qualification from `<platform_lower>-<backend>-<topology>` scopes in `qualification.json` fulfills FR-002, FR-003, and FR-007 while preserving Principle IV (Verification Before Claims) because Linux and Darwin scopes remain unqualified by default until real hardware receipts are recorded.
- **Alternatives Considered**:
  - *Single cluster-wide scope string for mixed-OS clusters (e.g., `mixed-vulkan-distributed`)*: Rejected because qualification evidence is collected per platform/backend (`windows-vulkan-distributed`, `linux-vulkan-distributed`, `darwin-vulkan-distributed`). Checking each participant against its own platform's distributed scope accurately reflects whether each OS in a heterogeneous ring has passed distributed qualification.

## 2. Backward-Compatible Wire Serialization for Platform & Architecture Metadata

- **Decision**:
  - Add optional `platform: Literal["Windows", "Linux", "Darwin"] | None = None` and `architecture: str | None = None` fields to `RankAllocation` (`src/kllm/modules/primacpp/entities.py`) and optional `architecture: str | None = None` to `ParticipantEvidence` (`src/kllm/modules/primacpp/interactors/participants.py`).
  - Implement a `@model_serializer(mode="wrap")` on `RankAllocation` and `ParticipantEvidence` that strips `platform` and `architecture` keys when their values are `None`.
  - Populate `platform` and `architecture` on `RankAllocation` during `topology_from_plan` (`placement.py`) only when the member dictionary (or node record) explicitly provides `platform` / `architecture`.
- **Rationale**:
  - `ExecutionManifest.digest()`, `PrimaPlan.digest()`, and `SignedParticipantEvidence` rely on `canonical_json()` over `model_dump(mode="json")`. Omitting `platform` and `architecture` when `None` guarantees 100% digest compatibility with all existing tests and persisted manifests where `platform`/`architecture` were not supplied, while deterministic canonical digests include `platform` and `architecture` whenever they are present (satisfying FR-001, FR-004, FR-006, and SC-003).
- **Alternatives Considered**:
  - *Always serializing `"platform": null, "architecture": null`*: Rejected because it changes canonical JSON output and invalidates existing SHA-256 manifest/plan digest assertions in `tests/test_prima_windows.py` and `tests/test_prima_lifecycle.py`.

## 3. Homogeneous Vulkan Ring Enforcement vs. Coordinator-Only CPU Master

- **Decision**:
  - Enforce in `placement.py` (`topology_from_plan`) and `qualification.py` (`evaluate_context`) that all GPU-offloading participants (`ngl > 0` or `layers` non-empty, or non-coordinator participants) in a Prima ring must use a Vulkan-compatible backend (`"vulkan"` or `"metal+vulkan"`).
  - If any participant reports an incompatible backend (such as `"metal"`, `"cuda"`, `"rocm"`, or `"cpu"` on a worker rank), raise a `ValueError` in planner/placement (and return `unsupported_platform_or_backend` in qualification) that explicitly names the offending node ID(s) and backend(s).
  - Preserve the coordinator-only CPU master exemption (`rank == 0`, `role == "master"`, `ngl == 0`, `layers == []`, `backend == "cpu"`) across all supported operating systems (`Windows`, `Linux`, `Darwin`).
- **Rationale**:
  - Directly implements the clarification recorded in `spec.md` (FR-008, FR-009): mixed-backend GPU rings are rejected at plan time with node-specific diagnostics, while a CPU-only master coordinating Vulkan workers is valid on any OS.
- **Alternatives Considered**:
  - *Deferring backend mismatch errors until `prima-server` startup*: Rejected because fail-fast rejection at plan/preview time prevents wasted model loading and gives immediate UI/CLI feedback.

## 4. Cross-Platform Build Identity Verification in Distributed Preparation

- **Decision**:
  - In `src/kllm/modules/primacpp/interactors/control.py` (distributed preparation / participant evidence validation), verify that every participant's `SignedParticipantEvidence` matches the master's logical runtime identity (`runtime_id`, `source_commit`, `recipe_digest`) while permitting node-specific `platform`, `architecture`, `build_id`, and `artifacts_digest`.
  - When a participant's evidence fails node ID, runtime ID, source commit, recipe digest, or model digest verification, include the specific `rank.node_id` and mismatched field details in the raised `ValueError` message (e.g., `f"Signed participant evidence mismatch on node {rank.node_id}: expected runtime_id={runtime_id}, commit={runtime['commit']}, recipe={runtime['recipe_digest']}"`).
- **Rationale**:
  - Windows, Linux, and macOS nodes compiling the exact same `prima.cpp` commit and recipe naturally produce different platform binaries (`build_id` and `artifacts_digest`), but they share `runtime_id`, `source_commit`, and `recipe_digest`. Naming the specific node on mismatch satisfies FR-005 and FR-008.
- **Alternatives Considered**:
  - *Requiring identical `artifacts_digest` across all nodes*: Already avoided in `control.py`, as binary hashes differ across OS/toolchains; making the node-specific diagnostic explicit completes the requirement.

## 5. Cluster Membership Platform & Architecture Propagation

- **Decision**:
  - Add optional `platform: str | None = None` and `architecture: str | None = None` to `RegisterBody` (`src/kllm/shared/schemas.py`), `master_member_record` (`src/kllm/modules/cluster/interactors/capacity.py`), `build_register_payload` (`src/kllm/modules/cluster/interactors/join_flow.py`), `HeartbeatPayloadBuilder` (`src/kllm/modules/cluster/interactors/heartbeat_payload.py`), and `_SNAPSHOT_OPTIONAL` (`src/kllm/modules/cluster/interactors/membership_state.py`).
  - In `PrimaPlanner._live_context` (`src/kllm/modules/primacpp/interactors/planner.py`), propagate each member's `platform`, `architecture`, and `backend` (`actual_backend` or `backend`) into `participants` passed to `PrimaQualifiers.evaluate_context`.
- **Rationale**:
  - Live cluster nodes register and heartbeat their capabilities through `cluster` interactors. Propagating `platform` and `architecture` through membership state allows `PrimaPlanner` and `placement.py` to automatically inspect every node's OS and CPU architecture in live clusters as well as synthetic planner previews.
- **Alternatives Considered**:
  - *Querying `/status` on each worker during every plan call*: Rejected because membership state already tracks node hardware metadata via registration and heartbeats without extra network round-trips during planning.
