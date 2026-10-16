# Tasks: Cross-Platform Planning from Prima.cpp

**Input**: Design documents from `/specs/018-cross-platform-planning/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/planning-and-qualification.md, quickstart.md

**Tests**: Included per FR-010 ("Platform-specific qualification behavior MUST have checks covering Windows, Linux and macOS node records") and Constitution Principle II (Behavior-Focused Regression Tests).

**Organization**: Tasks are grouped by user story (`US1`, `US2`, `US3`) to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (`[US1]`, `[US2]`, `[US3]`)
- Exact file paths and verbatim `data-model.md` constraints are included in task descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify baseline test suite and prepare shared platform/backend normalization helpers before modifying user story flows.

- [X] T001 Verify baseline Prima planning and lifecycle suites pass via `uv run pytest tests/test_prima_windows.py tests/test_prima_lifecycle.py -q` from `tests/test_prima_windows.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core entity metadata (`RankAllocation`, `ParticipantEvidence`) and cluster membership schema propagation that MUST be complete before user stories can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Update `RankAllocation` in `src/kllm/modules/primacpp/entities.py` to add `platform` (`Literal["Windows", "Linux", "Darwin"] | None`, "Optional; omitted from serialized wire output when `None`") and `architecture` (`str | None`, "Optional (e.g., `\"x86_64\"`, `\"AMD64\"`, `\"arm64\"`, `\"aarch64\"`); omitted from serialized wire output when `None`") with a `@model_serializer(mode="wrap")` that omits `platform` and `architecture` when `None`
- [X] T003 [P] Update `RegisterBody` in `src/kllm/shared/schemas.py` to add `platform` (`str | None`, "Optional node OS (`platform.system()`), propagated via registration and heartbeats") and `architecture` (`str | None`, "Optional node CPU architecture (`platform.machine()`), propagated via registration and heartbeats")
- [X] T004 [P] Update `master_member_record` in `src/kllm/modules/cluster/interactors/capacity.py`, `build_register_payload` in `src/kllm/modules/cluster/interactors/join_flow.py`, `HeartbeatPayloadBuilder` in `src/kllm/modules/cluster/interactors/heartbeat_payload.py`, and `_SNAPSHOT_OPTIONAL` in `src/kllm/modules/cluster/interactors/membership_state.py` to propagate optional `platform` (`platform.system()`) and `architecture` (`platform.machine()`)

**Checkpoint**: Foundation ready — `RankAllocation` and cluster member records support backward-compatible `platform` and `architecture` metadata without changing existing SHA-256 digests.

---

## Phase 3: User Story 1 - Mixed-OS Cluster Planning (Priority: P1) 🎯 MVP

**Goal**: Enable the Prima planner and placement engine to produce valid execution plans across `Windows`, `Linux`, and `Darwin` nodes when all GPU participants share a compatible Vulkan backend (`"vulkan"` or `"metal+vulkan"`), preserve coordinator-only CPU master support (`rank == 0`, `role == "master"`, `ngl == 0`, `layers == []`, `backend == "cpu"`) on any OS, and reject mixed-backend GPU rings (e.g., Metal + Vulkan) at plan time naming the conflicting nodes and backends.

**Independent Test**: Enroll heterogeneous node records (`Windows`, `Linux`, `Darwin`) with Vulkan backends and request a plan; verify a valid placement is produced with each rank's `platform`, `architecture`, and `backend` recorded, and confirm mixed-backend GPU rings (`metal` + `vulkan`) are rejected with node-identifying errors.

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T005 [P] [US1] Add unit tests in `tests/test_prima_windows.py` covering mixed-OS distributed placement (`Windows` + `Linux` + `Darwin` Vulkan ranks), single-node `Darwin` (`"metal+vulkan"`, including `"macOS"` input alias normalization to `"Darwin"`) and `Linux` (`"vulkan"`) placement, coordinator-only CPU master on `Linux`/`Darwin`, mixed-backend rejection (`"metal"` + `"vulkan"` naming offending `node_id` and backend), manifest staleness rejection when a member's `platform` or `architecture` changes after materialization, and canonical digest preservation when `platform`/`architecture` are omitted vs populated

### Implementation for User Story 1

- [X] T006 [US1] Implement platform validation (`"Windows" | "Linux" | "Darwin"`, accepting `"macOS"` / `"macos"` as an input alias normalizing to `"Darwin"`) and homogeneous Vulkan ring enforcement (`"vulkan"` or `"metal+vulkan"` normalized to `"vulkan"` on `RankAllocation.backend`, with `"cpu"` allowed only for coordinator-only rank 0 where `rank == 0`, `role == "master"`, `ngl == 0`, `layers == []`) in `topology_from_plan` inside `src/kllm/modules/primacpp/interactors/placement.py`, raising `ValueError` naming the offending `node_id` and `platform`/`backend` on incompatibility and populating `RankAllocation.platform` and `RankAllocation.architecture`
- [X] T007 [US1] Update `ExecutionManifest` materialization and staleness validation in `src/kllm/modules/primacpp/interactors/execution.py` so rank `platform` and `architecture` metadata are preserved in `ExecutionManifest.ranks` and revalidated against live member `platform`/`architecture` when present
- [X] T008 [US1] Update `PrimaPlanner._live_context`, `preview`, and `materialize` in `src/kllm/modules/primacpp/interactors/planner.py` to pass each member's `platform`, `architecture`, and backend (`actual_backend` or `backend`) into `topology_from_plan` and qualification context evaluation

**Checkpoint**: User Story 1 is complete and independently testable via `uv run pytest tests/test_prima_windows.py -k "cross_platform or mixed_os or mixed_backend" -q`.

---

## Phase 4: User Story 2 - Platform-Aware Qualification (Priority: P2)

**Goal**: Derive Prima qualification scopes dynamically from each node's actual operating system (`windows`, `linux`, `darwin`), backend (`vulkan`), and topology (`local`, `distributed`) against independent entries in `qualification.json`, evaluating every participant in mixed-OS distributed clusters against its own platform scope and reporting non-Windows scopes as unqualified (`unqualified_scope` with missing gates) until physical hardware receipts exist.

**Independent Test**: Provide runtime and participant records for `Windows`, `Linux`, and `Darwin` in local and distributed topologies and confirm `PrimaQualifiers` resolves `<platform_lower>-vulkan-<topology>`, evaluates each distributed participant against its own platform scope, and never falls back to a Windows scope or reports unverified scopes as qualified.

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T009 [P] [US2] Add unit tests in `tests/test_prima_windows.py` verifying `PrimaQualifiers.evaluate_context` resolves `linux-vulkan-local`, `darwin-vulkan-local` (for both `"Darwin"` and `"macOS"` inputs), `linux-vulkan-distributed`, and `darwin-vulkan-distributed`, reports `reason == "unqualified_scope"` with `missing_gates` for unverified non-Windows scopes, evaluates mixed `Windows` + `Linux` distributed participants against their respective platform scopes, and rejects unrecognized platforms or non-Vulkan GPU backends with `reason == "unsupported_platform_or_backend"` naming the failing nodes

### Implementation for User Story 2

- [X] T010 [P] [US2] Update `src/kllm/modules/primacpp/interactors/qualification.json` to add `linux-vulkan-local`, `linux-vulkan-distributed`, `darwin-vulkan-local`, and `darwin-vulkan-distributed` scope entries with `gates` ("`false` by default for `linux-*` and `darwin-*` until physical hardware receipts are recorded"), matching `required_gates`, and empty `verified_models`, `verified_devices`, and `receipts`
- [X] T011 [US2] Refactor `PrimaQualifiers` in `src/kllm/modules/primacpp/interactors/qualification.py` to normalize `PlatformId` (`"Windows" | "Linux" | "Darwin"`, plus `"macOS"` / `"macos"` alias, to lowercase `"windows" | "linux" | "darwin"`) and backend (`"vulkan"` or `"metal+vulkan"` to `"vulkan"`, preserving coordinator-only CPU master exemption when `rank == 0`, `role == "master"`, `ngl == 0`, `layers == []`, `backend == "cpu"`), derive `scope_key` as `<platform_lower>-<backend>-<topology>`, evaluate each distributed participant against its own `<participant_platform_lower>-vulkan-distributed` scope, and include `failing_nodes` and `participant_scopes` in the returned dictionary
- [X] T012 [P] [US2] Update qualification scope union types in `dashboard/src/lib/modules/runtimes/entities.ts` and scope badge rendering in `dashboard/src/lib/modules/runtimes/view/LlamaRuntimes.svelte` to support `linux-vulkan-local`, `linux-vulkan-distributed`, `darwin-vulkan-local`, and `darwin-vulkan-distributed` alongside `windows-vulkan-*`

**Checkpoint**: User Stories 1 and 2 are complete and independently testable via `uv run pytest tests/test_prima_windows.py -q` and `npm --prefix dashboard test`.

---

## Phase 5: User Story 3 - Cross-Platform Build Identity Verification (Priority: P3)

**Goal**: Record node CPU `architecture` alongside `platform` in signed participant evidence and verify during distributed preparation that all participating nodes share identical `(runtime_id, source_commit, recipe_digest, model_digest)` while allowing OS/architecture-specific `(build_id, artifacts_digest, platform, architecture)`, rejecting any mismatched node with an error naming its `node_id` and mismatched identity field.

**Independent Test**: Construct `SignedParticipantEvidence` records for `Windows` (`AMD64`), `Linux` (`x86_64`), and `Darwin` (`arm64`) sharing `(runtime_id, source_commit, recipe_digest, model_digest)` with distinct `(build_id, artifacts_digest)` and verify distributed preparation succeeds; alter one participant's `recipe_digest` or `source_commit` and verify preparation fails with an error naming that participant's `node_id`.

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T013 [P] [US3] Add unit tests in `tests/test_prima_lifecycle.py` for cross-platform `SignedParticipantEvidence` verification across `Windows`, `Linux`, and `Darwin` participants with distinct `(build_id, artifacts_digest, architecture)` and shared `(runtime_id, source_commit, recipe_digest, model_digest)`, plus negative tests asserting node-specific error messages when `source_commit`, `recipe_digest`, `runtime_id`, or `platform` mismatches

### Implementation for User Story 3

- [X] T014 [P] [US3] Update `ParticipantEvidence` in `src/kllm/modules/primacpp/interactors/participants.py` to add `architecture` (`str | None`, "Optional node CPU architecture (`platform.machine()`); omitted from wire dict when `None`") with a `@model_serializer(mode="wrap")` that omits `architecture` when `None`, and populate `architecture` in `collect_participant_evidence` when provided or detected
- [X] T015 [US3] Update distributed preparation evidence verification in `src/kllm/modules/primacpp/interactors/control.py` to validate `evidence.node_id == rank.node_id`, `evidence.runtime_id == runtime_id`, `evidence.source_commit == runtime["commit"]`, `evidence.recipe_digest == runtime["recipe_digest"]`, `evidence.model_digest == manifest.model.sha256`, and `evidence.platform == rank.platform` (when `rank.platform` is set), while allowing distinct `build_id`, `artifacts_digest`, `platform`, and `architecture` across nodes and raising a `ValueError` naming `rank.node_id` and the specific mismatched field(s)

**Checkpoint**: All three user stories (`US1`, `US2`, `US3`) are complete and independently verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full regression validation across Python and Dashboard test suites and feature memory synchronization.

- [X] T016 [P] Update `specs/018-cross-platform-planning/memory.md` with final implementation decisions and verification receipts
- [X] T017 Run full quickstart and regression validation (`uv run pytest tests/test_prima_windows.py tests/test_prima_lifecycle.py -q` and `npm --prefix dashboard test`) per `specs/018-cross-platform-planning/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately (`T001`).
- **Foundational (Phase 2)**: Depends on Setup (`T001`) — BLOCKS all user stories (`T002`–`T004`).
- **User Story 1 (Phase 3 - P1)**: Depends on Foundational (`T002`–`T004`).
- **User Story 2 (Phase 4 - P2)**: Depends on Foundational (`T002`–`T004`); integrates cleanly with `US1` planner context (`T008`).
- **User Story 3 (Phase 5 - P3)**: Depends on Foundational (`T002`–`T004`); can execute in parallel with `US1` and `US2` since it touches `participants.py` and `control.py`.
- **Polish (Phase 6)**: Depends on completion of `US1`, `US2`, and `US3` (`T005`–`T015`).

### User Story Dependency Graph

```text
Phase 1: Setup (T001)
   │
   ▼
Phase 2: Foundational (T002, T003, T004)
   ├──► Phase 3: US1 - Mixed-OS Cluster Planning (T005 → T006 → T007 → T008)
   ├──► Phase 4: US2 - Platform-Aware Qualification (T009, T010, T012 → T011)
   └──► Phase 5: US3 - Cross-Platform Build Identity Verification (T013, T014 → T015)
                                   │
                                   ▼
                    Phase 6: Polish & Validation (T016, T017)
```

---

## Parallel Opportunities

### Phase 2 (Foundational)

```bash
# Run all Foundational tasks in parallel across separate files:
Task T002: "Update RankAllocation in src/kllm/modules/primacpp/entities.py"
Task T003: "Update RegisterBody in src/kllm/shared/schemas.py"
Task T004: "Update cluster membership interactors in src/kllm/modules/cluster/interactors/"
```

### Phase 3, 4 & 5 (Across User Stories)

```bash
# Once Phase 2 completes, tests and independent model/policy updates can run in parallel:
Task T005: "[US1] Add mixed-OS placement tests in tests/test_prima_windows.py"
Task T010: "[US2] Add Linux and Darwin scopes in src/kllm/modules/primacpp/interactors/qualification.json"
Task T012: "[US2] Update scope types and badge in dashboard/src/lib/modules/runtimes/"
Task T013: "[US3] Add cross-platform participant evidence tests in tests/test_prima_lifecycle.py"
Task T014: "[US3] Add optional architecture to ParticipantEvidence in src/kllm/modules/primacpp/interactors/participants.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (`T001`)
2. Complete Phase 2: Foundational (`T002`–`T004`)
3. Complete Phase 3: User Story 1 (`T005`–`T008`)
4. **STOP and VALIDATE**: Run `uv run pytest tests/test_prima_windows.py -q` to verify cross-platform placement, homogeneous Vulkan ring validation, and digest preservation.

### Incremental Delivery

1. **Foundation (`T001`–`T004`)**: Backward-compatible `platform` and `architecture` fields on `RankAllocation` and cluster membership records.
2. **US1 (`T005`–`T008`)**: Cross-platform placement, homogeneous Vulkan ring enforcement, and coordinator-only CPU master support across OSes.
3. **US2 (`T009`–`T012`)**: Dynamic `<platform>-vulkan-<topology>` qualification scopes in `qualification.json`, `qualification.py`, and dashboard runtime views.
4. **US3 (`T013`–`T015`)**: Cross-platform `SignedParticipantEvidence` verification with node-identifying mismatch diagnostics in `participants.py` and `control.py`.
5. **Polish (`T016`–`T017`)**: Full regression suite and memory capture.

---

## Implementation Record

Completed 2026-10-14 on branch `feat/prima-integration`. T016 recorded the outcome in `memory.md`; the durable-memory registration done by the `after_implement` capture hook could not run (no `speckit_memory_register` MCP tool in this session). Where the code differed from what the plan assumed, the implementation followed the code; these are the deviations from the task text above:

- **T002 (no `RankAllocation` fields)**: not changed. `RankAllocation` is sent to workers (`rank.model_dump()` in `control.py`) into a model with `extra="forbid"` and is serialized into `execution.json` for the native runtime, so new keys could break mixed-version workers and the native parser (not verifiable in this repository). Platform and architecture are instead carried by signed `ParticipantEvidence` (`architecture` added, omitted when unset), by member records, by the plan preview (`participant_platforms`) and by `inventory_identity` (staleness). `platform` is already on `ParticipantEvidence`.
- **T004 (file locations)**: the master record is `CapacityService.master_member_record` in `modules/inference/interactors/capacity.py` and the heartbeat builder is `modules/cluster/presenters/heartbeat_payload.py`.
- **T006**: backend/platform enforcement lives in `topology_from_plan` via the new `interactors/platforms.py` (`normalize_platform`, `scope_platform`, `vulkan_build`, `require_shared_ring_backend`).
- **T007**: staleness is delivered by adding optional `platform`/`architecture` to `inventory_identity` (only when reported, so existing digests are unchanged); the manifest model itself is unchanged.
- **T009/T013 (test location)**: tests are in `tests/test_prima_cross_platform.py` instead of appending to `test_prima_windows.py` / `test_prima_lifecycle.py`.
- **T010 (policy path)**: `src/kllm/modules/primacpp/qualification.json` (not under `interactors/`).
- **T011**: the entry point is `qualification_status`; there is no `PrimaQualifiers` class. Errors for a non-local participant platform are prefixed with that scope instead of adding `failing_nodes`/`participant_scopes` keys.
- **T015**: delivered as `require_shared_identity` in `participants.py` (called from `control.py`) plus node naming in `ExecutionManifest` validation. `model_digest` and per-rank `platform` comparisons were not added: evidence already binds the model through the manifest assets and `allocation_digest`, and ranks carry no platform.

### Verification (this machine: Windows, no Linux/macOS hosts, no multi-node cluster)

- `uv run pytest -q`: 1713 passed, 19 skipped, 0 failed. 28 of the 36 new tests fail against `HEAD` and pass now; the other 8 guard unchanged Windows/non-Vulkan outcomes.
- `npm --prefix dashboard run check` (0 errors), `npm --prefix dashboard test` (10/10) and `npm --prefix dashboard run build` pass.
- **Unverified (needs hardware):** any real Linux or macOS/MoltenVK run, a physical mixed-OS ring, and native acceptance of the new evidence field. The Linux and macOS qualification scopes stay unqualified.