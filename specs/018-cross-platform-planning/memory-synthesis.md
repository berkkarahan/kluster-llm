# Memory Synthesis

## Current Scope

- **Feature**: `018-cross-platform-planning` — Cross-Platform Prima Planning
- **Modules in Scope**:
  - `src/kllm/modules/primacpp/` (`entities.py`, `interactors/qualification.py`, `interactors/qualification.json`, `interactors/placement.py`, `interactors/planner.py`, `interactors/execution.py`, `interactors/participants.py`, `interactors/control.py`)
  - `src/kllm/modules/cluster/` (`interactors/capacity.py`, `interactors/join_flow.py`, `interactors/heartbeat_payload.py`, `interactors/membership_state.py`)
  - `src/kllm/shared/schemas.py`
  - `dashboard/src/lib/modules/runtimes/` (`entities.ts`, `view/LlamaRuntimes.svelte`)
  - `tests/test_prima_windows.py`, `tests/test_prima_lifecycle.py`, and cross-platform planning unit tests

## Relevant Decisions

- **[DEC-018-001]** Qualification scope keys follow `<platform_lower>-<backend>-<topology>` (`windows-vulkan-local`, `windows-vulkan-distributed`, `linux-vulkan-local`, `linux-vulkan-distributed`, `darwin-vulkan-local`, `darwin-vulkan-distributed`) rather than hardcoding `windows-vulkan-<topology>`.
- **[DEC-018-002]** Mixed-backend Prima rings (e.g., Metal + Vulkan GPU participants in the same ring) are rejected at plan time with an actionable diagnostic naming the conflicting nodes and backends; all GPU participants in a Prima ring must share a compatible Vulkan backend (including MoltenVK on macOS).
- **[DEC-018-003]** Coordinator-only CPU master (rank 0, `role == "master"`, `ngl == 0`, `layers == []`, `backend == "cpu"`) remains permitted on any supported OS (`Windows`, `Linux`, `Darwin`) alongside Vulkan GPU workers.
- **[DEC-018-004]** Cross-platform distributed runs validate unified logical runtime identity (`runtime_id`, `source_commit`, `recipe_digest`) across all participants while allowing OS/architecture-specific native binary artifacts (`build_id`, `artifacts_digest`, `platform`, `architecture`) per node.
- **[DEC-018-005]** Linux and macOS qualification scopes are added to `qualification.json` with all verification gates set to `false` and empty receipts/models/devices so qualification policy evaluation is platform-aware without fabricating physical hardware evidence.

## Active Architecture Constraints

- **[ARCH-001]** Strictly adhere to VIPER module boundaries (`view`, `interactors`, `entities`, `protocols`, `router`, `services`) in `src/kllm/modules/primacpp/` and `src/kllm/modules/cluster/` (`.specify/memory/architecture_constitution.md`).
- **[ARCH-002]** Keep canonical wire serialization backward-compatible: optional `platform` and `architecture` fields on `RankAllocation` and `ParticipantEvidence` must be omitted from wire dicts when `None` so existing canonical SHA-256 digests (`ExecutionManifest.digest()`, `PrimaPlan.digest()`, `SignedParticipantEvidence`) remain unchanged when those fields are unset.
- **[ARCH-003]** Expose module-level patch points cleanly on owning modules and avoid cross-module private helper imports.

## Accepted Deviations

- None.

## Relevant Security Constraints

- **[SEC-001]** Signed participant evidence (`SignedParticipantEvidence`) remains HMAC-authenticated with `cluster_secret`; cross-platform metadata (`platform`, `architecture`) included in participant evidence must be covered by the canonical JSON payload when present.
- **[SEC-002]** Never expose unauthenticated upstream RPC ports or bypass runtime/model digest verification during distributed preparation.

## Related Historical Lessons

- **[HIST-017]** Feature 017 (`017-prima-cpp`) established the `primacpp` VIPER module, deterministic `ExecutionManifest` canonical JSON digests, and `qualification.json` policy evaluation for Windows + Vulkan. Hardcoded `platform != "Windows"` checks in `qualification.py` were intentional Phase 1 guards that now require generalization into per-platform scope lookups.
- **[HIST-005]** In `managed_prima_build.py`, macOS builds with `KLLM_PRIMA_VULKAN=1` stamp `backend` as `"metal+vulkan"`, whereas Windows/Linux stamp `"vulkan"`. Platform/backend normalization in `qualification.py` and `placement.py` must recognize `"vulkan"` within `"metal+vulkan"` when evaluating Vulkan ring compatibility on Darwin.

## Conflict Warnings

- **Digest Stability vs. New Metadata**: Adding `platform` and `architecture` directly to `RankAllocation` or `ParticipantEvidence` without omitting `None` values in `model_serializer` would alter canonical JSON digests in existing test fixtures (`tests/test_prima_windows.py`, `tests/test_prima_lifecycle.py`). Mitigation: omit `platform` and `architecture` keys in custom serializers whenever their value is `None`.

## Retrieval Notes

- Reviewed `.specify/memory/workflow.md`, `.specify/memory/constitution.md`, `.specify/memory/architecture_constitution.md`.
- Inspected `specs/018-cross-platform-planning/spec.md`, `specs/018-cross-platform-planning/memory.md`, `src/kllm/modules/primacpp/interactors/qualification.py`, `placement.py`, `planner.py`, `execution.py`, `participants.py`, and `control.py`.
