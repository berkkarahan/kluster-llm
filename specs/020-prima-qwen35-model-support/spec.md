# Feature Specification: Prima Qwen3.5 (`qwen35`) Support and Meaningful Model-Family Rejection

**Feature Branch**: `020-prima-qwen35-model-support`

**Created**: 2026-10-15

**Status**: Draft

**Input**: User description: "add qwen35 (Qwen3.5 hybrid SSM + MTP) support to the Prima fork berkkarahan/prima.cpp, publish it under release 0.0.1, and update kluster-llm's pin/recipe so `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf` deploys on the single-machine Kluster cluster. Also make Prima reject unsupported model families with a meaningful error message instead of the current silent fast-fail."

## Clarifications

### Session 2026-10-15

- Q: How should the corrected build be published for the forks's consumers? → A: Move the existing release tag `0.0.1` to the new commit, matching the established pattern (the tag has already moved repeatedly across recipe 11 … 29). No new `0.0.2` tag.
- Q: Is the "meaningful error for unsupported model families" change scoped to Prima only, or must llama.cpp runtimes be gated too? → A: Prima only. The llama.cpp runtime path is not gated in this feature.
- Q: Which execution backends must the new kernels land on for this feature to be considered delivered? → A: Both. A CPU-correct milestone gates a Vulkan milestone; the release tag moves only after both pass.
- Q: How does Kluster learn which model families a pinned Prima build supports? → A: Prima advertises its supported `architectures` (derived from its architecture/name table) plus a `qwen35_segmented_v1` capability in the existing `KLLM_PRIMA_CAPABILITIES=` payload. Kluster refuses at plan time and again pre-spawn with a distinct detail code. A missing `architectures` field produces a warning, not a refusal.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a Qwen3.5 hybrid model on Prima (Priority: P1)

A cluster operator has `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf` prepared and wants Kluster to serve it through the Prima engine instead of the llama.cpp runtime. Prima accepts the model, loads every tensor it needs, and generates tokens — first with CPU-only offload correctness, then with the full layer stack offloaded to the Vulkan GPU.

**Why this priority**: This is the entire point of the feature. Today the pinned Prima build has no `qwen35` architecture at all, so the run aborts during model load and nothing is served. Without this, the model cannot be deployed on Prima at all.

**Independent Test**: Prepare the Saluki GGUF, run a Prima local plan with `ngl 0` (CPU) and then with full Vulkan offload, and confirm both reach model readiness and return coherent generated tokens with evidence captured in `validation.md`.

**Acceptance Scenarios**:

1. **Given** a prepared `qwen35` GGUF and the updated Prima build, **When** an operator launches Prima with CPU-only offload, **Then** the model loads, pages populate from the file rather than being fabricated, and generation completes without an abort.
2. **Given** the same model and a Vulkan-capable device, **When** an operator plans and launches with the entire 65-layer stack assigned to the GPU, **Then** the run reaches readiness, generation proceeds on the GPU without silent CPU fallback, and GPU execution evidence is captured.
3. **Given** a `qwen35` model whose metadata carries multi-token-prediction tensors, **When** Prima loads it, **Then** the extra tensors are tolerated (skipped or unused with a stated reason) and speculative/draft decoding stays disabled — the run must not fail because of them.

---

### User Story 2 - Unsupported model families fail loudly, not silently (Priority: P2)

An operator points Prima at a GGUF whose architecture Prima has never implemented (for example `kolibri1` or `gpt-oss`). Instead of the process disappearing with a Windows error-report fast-fail and no usable message, the operator gets a clear, readable failure naming the unsupported architecture, and Kluster refuses the deployment before spawning anything.

**Why this priority**: Today's failure is the reason this feature was requested. It burns operator time: the model appears to load (GGUF metadata is printed), then the process dies with an opaque `0xC0000409` fast-fail, losing the error text because stderr is buffered. That must become a first-class, actionable error.

**Independent Test**: Launch the pinned Prima build against a GGUF for an architecture it does not implement, and confirm the process exits non-zero with a readable message naming the architecture, no abort code, and no Windows Error Reporting event.

**Acceptance Scenarios**:

1. **Given** a GGUF whose architecture Prima does not implement, **When** Prima is launched directly, **Then** it writes a readable error naming the architecture, exits with a non-zero status, and produces no `0xC0000409` abort/WER event.
2. **Given** the same model in the dashboard or CLI, **When** an operator requests a plan preview, **Then** the preview reports the deployment as unsupported with a distinct detail code and does not start any process.
3. **Given** the same model and a forced launch attempt that bypasses preview, **When** the pre-spawn check runs, **Then** the run is refused before a rank process is created.
4. **Given** a Prima build whose capability payload carries no `architectures` field, **When** an operator plans a run, **Then** Kluster warns about the missing capability and continues rather than refusing.

---

### User Story 3 - Prima publishes capabilities Kluster can trust (Priority: P3)

An operator (or a future maintainer) needs to know what a pinned Prima build can actually run. Prima's existing capability report gains the set of model architectures it supports and a marker for segmented execution of the new family, and Kluster validates the report while building and preparing runtimes so drifting or partial builds are caught before they are used.

**Why this priority**: Without published capabilities, Kluster can only discover unsupported models by launching them and watching them fail. Publishing them turns an operator-visible crash into a validated up-front contract, and it is what makes US2's plan-time refusal possible.

**Independent Test**: Build the updated Prima, read its capability payload from `llama-server --list-devices`, and confirm `architectures` contains `qwen35`; feed a report with a missing or malformed `architectures` field to Kluster's validation and confirm it warns rather than refuses.

**Acceptance Scenarios**:

1. **Given** a built Prima binary, **When** `--list-devices` runs, **Then** the capability payload includes `architectures` derived from the compiled-in architecture table and includes `qwen35`.
2. **Given** a capability payload with a malformed `architectures` value, **When** Kluster parses it during build preparation, **Then** the parse is rejected with a clear validation error rather than silently propagating bad data.
3. **Given** the new pin is recorded in Kluster, **When** the recipe ledger, docs, and package version are read, **Then** they agree on recipe 133, the new commit, and the same release identity.

---

### Edge Cases

- **MTP/draft tensors with no draft path enabled**: The Saluki file carries `blk.64.nextn.*` tensors. Loading must not require a speculative-decode implementation. Unused tensors are reported once and ignored.
- **Quantization of the new family**: `IQ2_mix` uses quantized matrix types Prima already supports for other families; if a required type is genuinely absent, the load must fail with a message naming the type rather than aborting.
- **Partial Vulkan coverage**: If a kernel needed by the new family is missing on the Vulkan backend, the run must fail loudly instead of silently computing on the CPU while claiming GPU offload.
- **Non-Windows platforms**: Rejection and loading behavior must be identical on Linux/macOS; the fast-fail symptom is Windows-specific but the root cause is not.
- **Very long context on the hybrid cache**: The new family uses a hybrid attention/SSM cache; context growth must stay bounded and must not corrupt an existing run's cache state.
- **Concurrent model families in one cluster**: A node serving a `qwen35` run must remain able to serve an existing supported family; capability changes must not regress existing families.
- **Recipe pin drift**: If the fork tag is moved again after this feature lands, Kluster's moved-release guard must still report the mismatch instead of silently accepting it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Prima MUST accept and load GGUF files whose architecture is `qwen35` (Qwen3.5 hybrid SSM/attention with MTP tensors), including the metadata, tokenizer, and tensor set the family requires.
- **FR-002**: Prima MUST execute the new family's operators correctly on the CPU backend before any GPU claim is made.
- **FR-003**: Prima MUST execute the same operators on the Vulkan backend, so that a full layer offload runs on the GPU without silent CPU substitution.
- **FR-004**: Tensor entries used only by speculative decoding MUST be tolerated (skipped with a stated reason) and MUST NOT enable draft/speculative decoding.
- **FR-005**: Prima MUST report an unsupported model architecture as a readable error naming the architecture, with a non-zero exit and no process abort fast-fail, when that architecture is not implemented.
- **FR-006**: Kluster MUST refuse a Prima deployment whose model architecture is absent from the runtime's advertised `architectures` set at plan time, using a distinct detail code.
- **FR-007**: Kluster MUST refuse the same deployment again immediately before spawning a rank process, so that a bypassed preview cannot start an unsupported run.
- **FR-008**: Kluster MUST treat a missing `architectures` capability field as a warning, not a refusal, preserving compatibility with older pinned builds.
- **FR-009**: Prima's capability payload MUST advertise `architectures` (derived from its architecture table, not hand-maintained) and a `qwen35_segmented_v1` capability.
- **FR-010**: Kluster MUST validate the parsed capability payload for the new fields and reject malformed values with a clear validation error while tolerating absence (FR-008).
- **FR-011**: Launch failures MUST include the tail of the rank log in the operator-visible error, so any residual unexplained failure is diagnosable without manual log archaeology.
- **FR-012**: The dashboard MUST block launching a Prima run whose model is reported unsupported, using the same detail code the API returns.
- **FR-013**: Documentation MUST record the new pin: the Prima integration recipe ledger gains a Recipe 133 entry with the new source commit and build recipe version, and the fork's own model-support documentation lists the new family.
- **FR-014**: Kluster's recipe/package pin, recipe digest, and related tests MUST be updated consistently, so that a prepared runtime for recipe 133 validates against the pin it claims.
- **FR-015**: Existing supported Prima model families and existing deployment behavior MUST NOT regress: all currently passing Prima planning, preparation, launch, and qualification checks continue to pass.

### Key Entities

- **Prima model capability report**: the payload Prima emits from `--list-devices`; gains `architectures` (list of architecture identifiers) and a segmentation capability marker; consumed by Kluster's runtime validation and planning.
- **Recipe pin**: the recorded Prima source commit, release commit, release identity, and build recipe version that define which Prima build a prepared runtime corresponds to; advanced to 133 for this feature.
- **Unsupported-model detail code**: the distinct machine-readable reason Kluster returns when a model's architecture is not in the runtime's advertised set; surfaced in plan previews, run errors, and the dashboard.
- **Rank log tail**: the trailing portion of a rank's log attached to launch failures for diagnosis.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The Saluki `qwen35` GGUF loads on Prima with CPU-only offload and produces generated tokens, verified against a reference runtime's output on the same file and prompt.
- **SC-002**: The same GGUF runs with 100% of its 65 layers offloaded to Vulkan, with GPU execution evidence and zero aborts.
- **SC-003**: Launching Prima against a model family it does not implement produces one readable error line naming the architecture, exits non-zero, and produces zero abort-code/WER events — replacing the observed `0xC0000409` fast-fail entirely.
- **SC-004**: A plan preview and a launch attempt for an unsupported model are both refused with the distinct detail code, and no rank process is spawned in either case.
- **SC-005**: The Prima capability payload exposes `architectures` including `qwen35` plus `qwen35_segmented_v1`, and Kluster's validation accepts it while warning on absence.
- **SC-006**: Recipe 133, its digest, the recipe ledger, and the release identity all agree, and the existing Prima test suite plus the new regression checks pass.
- **SC-007**: No regression in existing supported families: the pre-existing Prima planning/preparation/launch checks that passed before this feature still pass.

## Assumptions

- The reference implementation of this model family in upstream llama.cpp (the runtime already installed on this machine, which loads the file successfully today) is a valid correctness oracle for CPU output comparison.
- The fork keeps its current monolith source layout; this feature ports behavior into the existing structure rather than adopting upstream's file-per-architecture layout.
- The single-machine cluster (one Windows node, one Vulkan GPU) is the acceptance environment for this feature; no multi-node or performance claim is made.
- Kluster's existing capability pipeline (`KLLM_PRIMA_CAPABILITIES=` from `--list-devices`, parsed during build preparation and planning) is extended rather than replaced.
- Operators may still run the same model through the llama.cpp runtime; this feature does not remove or gate that path.

## Out of Scope

- `qwen35moe` and any other new model family beyond `qwen35`.
- Multi-token prediction / speculative decoding execution (tensors are tolerated only).
- Any change to qualification gates, performance thresholds, or performance claims.
- Gating llama.cpp runtimes for unsupported model families.
- Distributed or multi-node execution of the new family beyond what is needed for a single-machine acceptance run.
