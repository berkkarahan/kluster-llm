# Phase 1 Data Model: Cross-Platform Prima Planning

## Entities & Value Objects

### 1. `PlatformId` & `BackendId` (Normalized Literals)

- **Supported Operating Systems (`PlatformId`)**: `"Windows" | "Linux" | "Darwin"` (canonical runtime values from `platform.system()`, with `"macOS"` / `"macos"` accepted as an input alias normalizing to `"Darwin"`; normalized to lowercase `"windows" | "linux" | "darwin"` in qualification scope keys).
- **Supported Qualification Backends**:
  - Ring GPU backend: `"vulkan"` (where runtime `"metal+vulkan"` on Darwin satisfies `"vulkan"` ring compatibility when MoltenVK is built).
  - Coordinator-only master backend: `"cpu"` (valid only when `rank == 0`, `role == "master"`, `ngl == 0`, `layers == []`).

### 2. `QualificationScope` (`src/kllm/modules/primacpp/interactors/qualification.json`)

Represents the qualification state of a specific `(platform, backend, topology)` tuple.

| Field | Type | Description |
|-------|------|-------------|
| `scope_key` | `str` | `<platform_lower>-<backend>-<topology>`, e.g., `windows-vulkan-local`, `linux-vulkan-distributed`, `darwin-vulkan-local`. |
| `qualified` | `bool` | Derived at evaluation time: `True` only when all `required_gates` are `True` in `gates` and model/device checks pass. |
| `required_gates` | `list[str]` | List of gate keys required for this scope (e.g., `build_install`, `metadata_probe`, `local_smoke`, etc.). |
| `gates` | `dict[str, bool]` | Gate completion map. `false` by default for `linux-*` and `darwin-*` until physical hardware receipts are recorded. |
| `verified_models` | `list[str]` | List of model SHA-256 digests verified under this scope. |
| `verified_devices` | `list[str]` | List of GPU device names verified under this scope. |
| `receipts` | `dict[str, str]` | Mapping of gate names to verification receipt identifiers. |

### 3. `RankAllocation` (`src/kllm/modules/primacpp/entities.py`)

Represents a single participant's rank assignment in `PrimaTopology` and `ExecutionManifest`.

| Field | Type | Constraints / Defaults |
|-------|------|------------------------|
| `rank` | `int` | `>= 0`; rank `0` is `"master"`, `> 0` is `"worker"`. |
| `node_id` | `str` | Non-empty unique node identifier. |
| `role` | `Literal["master", "worker"]` | `"master"` for rank 0, `"worker"` for ranks > 0. |
| `host` | `str` | Node IP/hostname on the active cluster transport. |
| `control_port` | `int` | `1..65535` |
| `data_port` | `int` | `1..65535` |
| `next_host` | `str` | Next hop host in the ring. |
| `next_port` | `int` | `1..65535` |
| `layers` | `list[int]` | Assigned model layer indices. |
| `ngl` | `int` | `>= 0`; GPU layer count on this rank. |
| `backend` | `str` | Default `"vulkan"` (`"cpu"` allowed only for coordinator-only rank 0). |
| `devices` | `list[str]` | Selected device identifiers. |
| `vram_bytes` | `int` | `>= 0` |
| `ram_bytes` | `int` | `>= 0` |
| `platform` | `Literal["Windows", "Linux", "Darwin"] \| None` | Optional; omitted from serialized wire output when `None`. |
| `architecture` | `str \| None` | Optional (e.g., `"x86_64"`, `"AMD64"`, `"arm64"`, `"aarch64"`); omitted from serialized wire output when `None`. |

### 4. `ParticipantEvidence` (`src/kllm/modules/primacpp/interactors/participants.py`)

Signed capability and runtime evidence emitted by a participant during distributed preparation.

| Field | Type | Constraints / Defaults |
|-------|------|------------------------|
| `node_id` | `str` | Must match assigned `RankAllocation.node_id`. |
| `runtime_id` | `str` | Must match master's logical `runtime_id`. |
| `source_commit` | `str` | Must match master's `source_commit`. |
| `recipe_digest` | `str` | Must match master's `recipe_digest`. |
| `build_id` | `str` | Node-specific build identifier (may differ across OS/arch). |
| `platform` | `Literal["Windows", "Linux", "Darwin"]` | Node operating system. |
| `architecture` | `str \| None` | Optional node CPU architecture (`platform.machine()`); omitted from wire dict when `None`. |
| `backend` | `Literal["vulkan", "cpu"]` | Active runtime backend for the participant. |
| `artifacts_digest` | `str` | Node-specific SHA-256 digest of compiled binaries. |
| `model_digest` | `str` | SHA-256 digest of the local GGUF model file (must match manifest). |
| `host` | `HostEvidence` | Host probe evidence (devices, RAM, Disk, link speed). |

### 5. Cluster Member Record (`src/kllm/shared/schemas.py` & `cluster` interactors)

| Field | Type | Description |
|-------|------|-------------|
| `platform` | `str \| None` | Optional node OS (`platform.system()`), propagated via registration and heartbeats. |
| `architecture` | `str \| None` | Optional node CPU architecture (`platform.machine()`), propagated via registration and heartbeats. |

## State & Validation Rules

1. **Supported OS Validation**: Any participant with a `platform` outside `{"Windows", "Linux", "Darwin"}` (case-insensitive) is rejected during qualification and placement with node ID and platform details.
2. **Homogeneous Vulkan Ring Validation**: All GPU-participating ranks (`ngl > 0` or `role == "worker"` or `layers` non-empty) must use a Vulkan-compatible backend (`"vulkan"` or `"metal+vulkan"`, normalized to `"vulkan"` on `RankAllocation.backend`). Any GPU rank requesting `"metal"`, `"cuda"`, `"rocm"`, or `"cpu"` on a worker rank is rejected at plan time naming the node ID and backend.
3. **Coordinator-Only CPU Master Exemption**: Rank 0 (`role == "master"`) may use `backend == "cpu"` with `ngl == 0` and `layers == []` on `"Windows"`, `"Linux"`, or `"Darwin"` without failing Vulkan backend checks.
4. **Cross-Platform Logical Runtime Identity**: Distributed preparation requires identical `(runtime_id, source_commit, recipe_digest, model_digest)` across all ranks while allowing distinct `(platform, architecture, build_id, artifacts_digest)` per rank.
5. **Deterministic Wire Digest Preservation**: `RankAllocation` and `ParticipantEvidence` omit `platform` and `architecture` when `None` so existing manifests/evidence without those fields produce identical SHA-256 digests.
