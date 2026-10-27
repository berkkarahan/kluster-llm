# Implementation Plan: Cross-Platform Prima Planning

**Branch**: `feat/prima-integration` | **Date**: 2026-10-14 | **Spec**: [spec.md](file:///D:/dev/kluster-llm/specs/018-cross-platform-planning/spec.md)
**Input**: Feature specification from `/specs/018-cross-platform-planning/spec.md`

## Summary

Generalize Prima (`prima.cpp`) placement planning, execution manifest serialization, qualification scope resolution, and distributed participant evidence verification from Windows-only assumptions to support `Windows`, `Linux`, and `Darwin` (macOS) nodes. All GPU-participating ranks in a Prima ring must share a Vulkan-compatible backend (`vulkan` or `metal+vulkan` via MoltenVK on macOS), while a coordinator-only CPU master (`rank 0`, `ngl == 0`, `layers == []`, `backend == "cpu"`) remains valid on any supported OS. Qualification policy evaluation dynamically resolves `<platform>-vulkan-<local|distributed>` scopes from `qualification.json`, keeping non-Windows scopes unqualified by default until physical hardware verification receipts are recorded.

## Technical Context

**Language/Version**: Python 3.11+ (managed via `uv`), TypeScript / Svelte 5 (dashboard)  
**Primary Dependencies**: Pydantic v2, FastAPI, Rich/Typer (`kllm` CLI), Svelte 5 (`dashboard`)  
**Storage**: JSON policy and state files (`src/kllm/modules/primacpp/interactors/qualification.json`, isolated runtime library metadata)  
**Testing**: `uv run pytest tests/test_prima_windows.py tests/test_prima_lifecycle.py`, `npm --prefix dashboard test`  
**Target Platform**: Cross-platform (`Windows`, `Linux`, `Darwin` / macOS)  
**Project Type**: Distributed LLM cluster manager & runtime orchestrator (Python backend/CLI + Svelte 5 dashboard)  
**Performance Goals**: Deterministic in-memory planning and qualification evaluation in `< 50ms` per plan preview/materialization  
**Constraints**:
- Must preserve 100% backward compatibility with existing `PrimaPlan.digest()`, `ExecutionManifest.digest()`, and `SignedParticipantEvidence` canonical SHA-256 digests when optional `platform` and `architecture` fields are `None`.
- Must not fabricate or enable hardware qualification gates for `linux-vulkan-*` or `darwin-vulkan-*` without physical hardware receipts.
**Scale/Scope**: 1–16+ cluster nodes across homogeneous or mixed operating systems (`Windows`, `Linux`, `Darwin`).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Phase 0 Gate Evaluation

- **I.Test-First & Fixture-Driven Determinism**: **PASS**. All cross-platform placement, manifest serialization, qualification scope lookup, and participant evidence checks are pure/deterministic interactors testable via pytest fixtures without requiring live non-Windows GPUs.
- **II. VIPER Module Boundaries (`.specify/memory/architecture_constitution.md`)**: **PASS**. Changes stay strictly within `src/kllm/modules/primacpp/` (`entities.py`, `interactors/`), `src/kllm/modules/cluster/interactors/`, `src/kllm/shared/schemas.py`, and `dashboard/src/lib/modules/runtimes/`.
- **III. Security & Explicit Trust**: **PASS**. `SignedParticipantEvidence` continues to require HMAC verification via `cluster_secret` and enforces unified `(runtime_id, source_commit, recipe_digest, model_digest)` across all ranks.
- **IV. Verification Before Claims**: **PASS**. `linux-vulkan-*` and `darwin-vulkan-*` scopes are added to `qualification.json` with all verification gates set to `false` and empty receipts/models/devices.

### Post-Phase 1 Gate Re-Evaluation

- **Digest Stability Check**: **PASS**. Custom `@model_serializer(mode="wrap")` on `RankAllocation` and `ParticipantEvidence` omits `platform` and `architecture` when `None`, ensuring zero regression on existing canonical digests.
- **VIPER Surface Check**: **PASS**. No new non-standard files are introduced at module root; all logic resides in existing `entities.py` and `interactors/` files.

## Project Structure

### Documentation (this feature)

```text
specs/018-cross-platform-planning/
├── spec.md              # Feature specification
├── plan.md              # This implementation plan
├── research.md          # Phase 0 research decisions & rationale
├── data-model.md        # Phase 1 entity & validation model
├── quickstart.md        # Phase 1 verification guide
├── memory.md            # Feature memory log
├── memory-synthesis.md  # Pre-plan memory synthesis
├── checklists/
│   └── requirements.md  # Specification quality checklist
└── contracts/
    └── planning-and-qualification.md # Qualification, planner, and evidence contracts
```

### Source Code (repository root)

```text
src/kllm/
├── shared/
│   └── schemas.py                                   # Add optional platform & architecture to RegisterBody
└── modules/
    ├── cluster/
    │   └── interactors/
    │       ├── capacity.py                          # Include platform & architecture in master_member_record
    │       ├── join_flow.py                         # Include platform & architecture in build_register_payload
    │       ├── heartbeat_payload.py                 # Include platform & architecture in HeartbeatPayloadBuilder
    │       └── membership_state.py                  # Include platform & architecture in _SNAPSHOT_OPTIONAL
    └── primacpp/
        ├── entities.py                              # Add optional platform & architecture to RankAllocation (omit when None)
        └── interactors/
            ├── qualification.json                   # Add linux-vulkan-* and darwin-vulkan-* scope definitions (gates=false)
            ├── qualification.py                     # Dynamic <platform>-vulkan-<topology> scope lookup & mixed-OS/backend checks
            ├── placement.py                         # Validate OS & homogeneous Vulkan backend; propagate platform/architecture to RankAllocation
            ├── planner.py                           # Propagate member platform/architecture/backend into qualification context & preview output
            ├── execution.py                         # Preserve platform/architecture across ExecutionManifest materialization
            ├── participants.py                      # Add optional architecture to ParticipantEvidence (omit when None)
            └── control.py                           # Include node_id and mismatched field details on participant evidence errors

dashboard/src/lib/modules/runtimes/
├── entities.ts                                      # Expand PrimaQualificationScope union to include linux/darwin scopes
└── view/
    └── LlamaRuntimes.svelte                         # Generalize scope badge check from hardcoded 'windows-vulkan-local'

tests/
├── test_prima_windows.py                            # Add cross-platform local/distributed planning, qualification, and backend rejection tests
└── test_prima_lifecycle.py                          # Add cross-platform participant evidence & node-identifying mismatch tests
```

**Structure Decision**: Extend the existing `primacpp` and `cluster` VIPER modules in-place (`entities.py` and `interactors/`) and update the `runtimes` dashboard module type definitions without introducing any new modules or non-standard files.

## Implementation Phases

### Phase 1: Entities, Qualification Scopes & Policy Evaluation (`primacpp`)

1. **`src/kllm/modules/primacpp/entities.py`**:
   - Add `platform: Literal["Windows", "Linux", "Darwin"] | None = None` and `architecture: str | None = None` to `RankAllocation`.
   - Add a `@model_serializer(mode="wrap")` on `RankAllocation` that omits `platform` and `architecture` keys when their values are `None` so existing wire dicts and SHA-256 digests remain unchanged.
2. **`src/kllm/modules/primacpp/interactors/qualification.json`**:
   - Add `linux-vulkan-local`, `linux-vulkan-distributed`, `darwin-vulkan-local`, and `darwin-vulkan-distributed` scope entries with matching `required_gates`, all gate booleans set to `false`, and empty `verified_models`, `verified_devices`, and `receipts`.
3. **`src/kllm/modules/primacpp/interactors/qualification.py`**:
   - Replace hardcoded `windows-vulkan-` scope construction and `platform != "Windows"` checks with helper functions that normalize platform (`Windows` -> `windows`, `Linux` -> `linux`, `Darwin` -> `darwin`) and backend (`vulkan` or `metal+vulkan` -> `vulkan`; coordinator-only CPU master allowed in distributed mode).
   - Reject unsupported platforms or incompatible GPU backends (e.g., `metal` without Vulkan, `cuda`, worker `cpu`) with `reason="unsupported_platform_or_backend"`, `failing_nodes`, and a clear `detail` message naming the node(s) and backend/platform.
   - For supported platforms/backends, evaluate the primary `<runtime_platform>-vulkan-<topology>` scope (and in distributed mode, each participant's `<participant_platform>-vulkan-distributed` scope) against `qualification.json`, reporting `unqualified_scope` or `unverified_gate` with `missing_gates` and `failing_nodes` when gates are not yet verified.

### Phase 2: Placement, Planner & Execution Manifest (`primacpp` & `cluster`)

1. **`src/kllm/modules/primacpp/interactors/placement.py`**:
   - Add platform normalization (`Windows`, `Linux`, `Darwin`) and backend validation in `topology_from_plan`.
   - Enforce that all GPU-participating ranks (`ngl > 0` or `role == "worker"` or `layers` non-empty) use a Vulkan-compatible backend (`vulkan` or `metal+vulkan`), raising a `ValueError` naming the node ID and backend on mismatch (e.g., mixed Metal + Vulkan ring).
   - Permit coordinator-only CPU master (`rank == 0`, `role == "master"`, `ngl == 0`, `layers == []`, `backend == "cpu"`) on any supported OS.
   - Propagate normalized `platform` and `architecture` from member records onto `RankAllocation` when provided.
2. **`src/kllm/modules/primacpp/interactors/planner.py`**:
   - In `PrimaPlanner._live_context`, include each member's `platform`, `architecture`, and normalized backend in the `participants` list passed to `PrimaQualifiers.evaluate_context`.
   - Ensure `preview()` and `materialize()` pass member `platform`/`architecture` through to `topology_from_plan` and `PrimaQualifiers`.
3. **`src/kllm/shared/schemas.py` & `src/kllm/modules/cluster/interactors/`**:
   - Add optional `platform: str | None = None` and `architecture: str | None = None` to `RegisterBody`, `master_member_record`, `build_register_payload`, `HeartbeatPayloadBuilder`, and `_SNAPSHOT_OPTIONAL` so live cluster nodes report their OS and CPU architecture automatically.

### Phase 3: Participant Evidence & Dashboard Scope Support (`primacpp` & `dashboard`)

1. **`src/kllm/modules/primacpp/interactors/participants.py`**:
   - Add `architecture: str | None = None` to `ParticipantEvidence` with a `@model_serializer(mode="wrap")` that omits `architecture` when `None`.
   - Populate `architecture=platform.machine()` in `collect_participant_evidence` when requested or available without breaking existing deterministic test helpers.
2. **`src/kllm/modules/primacpp/interactors/control.py`**:
   - Update distributed preparation evidence validation (`_validate_participant_evidence` / `prepare`) so that any mismatch in `node_id`, `runtime_id`, `source_commit`, `recipe_digest`, `platform` (when specified on `rank`), or `model_digest` raises a `ValueError` explicitly naming `rank.node_id` and the mismatched field(s).
3. **`dashboard/src/lib/modules/runtimes/`**:
   - Update `entities.ts` to allow `linux-vulkan-local`, `linux-vulkan-distributed`, `darwin-vulkan-local`, and `darwin-vulkan-distributed` in qualification scope types.
   - Update `LlamaRuntimes.svelte` so local/distributed scope badge rendering handles any `*-vulkan-local` / `*-vulkan-distributed` scope cleanly.

### Phase 4: Verification & Regression Testing (`tests/`)

1. **`tests/test_prima_windows.py`**:
   - Add unit tests for single-node `Linux` and `Darwin` planning (`preview` and `materialize`), verifying `RankAllocation.platform`, `RankAllocation.architecture`, and deterministic manifest digests.
   - Add unit tests for mixed-OS distributed ring planning (`Windows` + `Linux` + `Darwin` Vulkan workers, plus coordinator-only CPU master on `Linux` / `Darwin`).
   - Add unit tests verifying mixed-backend rings (e.g., `Darwin` `metal` worker + `Windows` `vulkan` worker) are rejected at plan time with node-identifying errors.
   - Add unit tests verifying `PrimaQualifiers.evaluate_context` resolves `linux-vulkan-local`, `darwin-vulkan-local`, `linux-vulkan-distributed`, and `darwin-vulkan-distributed` and reports `unqualified_scope` with `missing_gates` rather than `unsupported_platform_or_backend`.
2. **`tests/test_prima_lifecycle.py`**:
   - Add unit tests for cross-platform `SignedParticipantEvidence` validation across `Windows`, `Linux`, and `Darwin` participants sharing `(runtime_id, source_commit, recipe_digest)` with distinct `(build_id, artifacts_digest, platform, architecture)`, plus node-identifying error assertions when one participant has a mismatched `source_commit` or `recipe_digest`.

## Complexity Tracking

No constitution violations or architectural deviations required.
