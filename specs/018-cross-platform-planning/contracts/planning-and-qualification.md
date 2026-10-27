# Contracts: Cross-Platform Prima Planning & Qualification

## 1. Qualification Policy Contract (`PrimaQualifiers`)

### Scope Resolution (`scope_for_platform_and_topology`)

- **Input**:
  - `runtime`: `dict[str, Any]` (`platform`, `backend`, `ready`, `source_commit`, `recipe_digest`, etc.)
  - `participants`: `list[dict[str, Any]]` (each with `node_id`, `role`, `platform`, `architecture`, `backend`, `ngl`, `layers`, `devices`)
  - `model_sha256`: `str | None`
- **Resolution Rules**:
  1. **Topology**: `"distributed"` when `len(participants) > 1`, else `"local"`.
  2. **Platform Normalization**:
     - `"windows"` -> `"windows"`
     - `"linux"` -> `"linux"`
     - `"darwin"` / `"macos"` -> `"darwin"`
     - Any other value -> rejected with `reason = "unsupported_platform_or_backend"`.
  3. **Backend Normalization**:
     - `"vulkan"` or `"metal+vulkan"` -> `"vulkan"`
     - Coordinator-only master (`role == "master"`, `ngl == 0`, `layers == []`, `backend == "cpu"` in distributed topology) -> exempt from GPU backend requirement; its host OS still resolves to `<platform_lower>-vulkan-distributed`.
     - Any other backend (e.g., `"metal"`, `"cuda"`, `"cpu"` on a worker or local run) -> rejected with `reason = "unsupported_platform_or_backend"` and `failing_nodes` listing `<node_id> (<backend>)`.
  4. **Scope Key**:
     - Local: `<runtime_platform_lower>-vulkan-local`
     - Distributed: `<runtime_platform_lower>-vulkan-distributed` as primary `scope`, plus per-participant validation against each participant's `<participant_platform_lower>-vulkan-distributed` entry in `qualification.json`.
- **Output (`dict[str, Any]`)**:
  ```json
  {
    "qualified": false,
    "scope": "linux-vulkan-local",
    "participant_scopes": ["linux-vulkan-local"],
    "reason": "unqualified_scope",
    "missing_gates": ["build_install", "metadata_probe", "local_smoke"],
    "unverified_devices": [],
    "failing_nodes": ["node-linux-1"],
    "detail": "Prima scope linux-vulkan-local is not qualified (missing gates: build_install, metadata_probe, local_smoke)"
  }
  ```

## 2. Planner & Placement Contract (`PrimaPlanner` & `topology_from_plan`)

### Input (`PrimaPlanner.preview` / `PrimaPlanner.materialize` / `topology_from_plan`)

- `members`: Mapping of `node_id -> member_dict` where each `member_dict` may include:
  - `platform`: `"Windows" | "Linux" | "Darwin"` (optional in legacy callers, populated in live cluster state and cross-platform previews)
  - `architecture`: `str` (e.g., `"AMD64"`, `"x86_64"`, `"arm64"`, `"aarch64"`, optional)
  - `backend` / `actual_backend`: `"vulkan" | "metal+vulkan" | "cpu" | ...`
  - `devices`, `vram_bytes`, `total_ram_bytes`, `host`, `role`

### Validation & Error Contract

- **Unsupported OS**: Raises `ValueError("Unsupported Prima platform on node <node_id>: <platform>")`.
- **Incompatible / Mixed GPU Backend**: If any active participant (except a coordinator-only CPU master in distributed mode) has a backend other than `"vulkan"` / `"metal+vulkan"`, raises `ValueError("Incompatible Prima backend on node <node_id>: <backend> (all GPU ring participants must use vulkan)")`.
- **Rank Metadata Propagation**:
  - Each `RankAllocation` in `PrimaTopology.ranks` and `ExecutionManifest.ranks` carries `platform` (normalized to `"Windows" | "Linux" | "Darwin"` when provided) and `architecture` (when provided).
  - Canonical JSON serialization (`ExecutionManifest.wire()` / `RankAllocation.serialize()`) omits `platform` and `architecture` when `None`, and includes them deterministically when set.

## 3. Distributed Preparation Evidence Contract (`PrimaCoordinator` / `control.py`)

### Verification Rules

When validating `SignedParticipantEvidence` for each `rank` in `manifest.ranks`:
1. Verify HMAC signature with `cluster_secret`.
2. Verify logical runtime and model identity:
   - `evidence.node_id == rank.node_id`
   - `evidence.runtime_id == runtime_id`
   - `evidence.source_commit == runtime["commit"]`
   - `evidence.recipe_digest == runtime["recipe_digest"]`
   - `evidence.model_digest == manifest.model.sha256`
   - If `rank.platform` is specified in the manifest, verify `evidence.platform == rank.platform`.
3. Allow heterogeneous native artifact fields across nodes:
   - `evidence.build_id` and `evidence.artifacts_digest` may differ between Windows, Linux, and Darwin participants.
4. **Error Reporting**:
   - On any identity mismatch, raise `ValueError` naming `rank.node_id` and the mismatched attribute(s) (e.g., `node_id`, `runtime_id`, `source_commit`, `recipe_digest`, `platform`, or `model_digest`).
