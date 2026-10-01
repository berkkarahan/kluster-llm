# Memory Synthesis

## Current Scope

Phase 1 design for app-managed private 10GbE network setup, master socket initiation, worker-local temporary address configuration, verified inference admission and recoverable cleanup. Affected boundaries: src/kllm, crates/kllm-native, dashboard, existing manual script facade and operator documentation. Read spec.md and memory.md before implementation.

## Relevant Decisions

- Validate network payloads and subprocess results at trust boundaries; invalid input must produce actionable failure. Reason included: remote host-network changes. Status: active. Source: .specify/memory/constitution.md, Principle IV.
- Preserve public contracts and existing operational workflows. Reason included: additive v2 selection and unchanged v1/manual script behavior. Status: active. Source: .specify/memory/constitution.md, Principle IV.

## Active Architecture Constraints

- Python policy/platform orchestration uses src/kllm, native forwarding uses crates/kllm-native, dashboard uses existing status events. Reason included: explicit user repository instructions and current packaging.
- Separate durable network operations and ownership from transient membership resets. Reason included: interrupted changes must survive dropped members. Source: current state.py, membership_state.py and feature-local research.md.

## Accepted Deviations

- None. Using src/kllm resolves outdated constitution path wording through the user's explicit instruction; it does not alter governance files.

## Relevant Security Constraints

- RPC port 50052 remains private, with exact selected-address binds and trusted master source allowlists. Reason included: upstream RPC has no authentication. Source: constitution.md Repository Constraints.
- Shared join keys cannot establish unique current-master authority; pinned public identities and session fencing are planned. Reason included: prevent peer impersonation. Source: current agent/__init__.py and research.md.
- Never delete user-owned or ambiguous external replacements; never send private identity material. Reason included: cleanup recoverability and secret handling. Source: constitution.md Principle IV and feature-local data-model.md.

## Related Historical Lessons

- Existing run_planning.py forbids raw connect/close RPC probing. Reason included: avoid disrupting the worker listener; the planned valid probe must be idle-only and released before launch.
- No performance or hardware guarantee follows from mocks or a measured link speed. Reason included: native bridges need actual mixed-platform acceptance. Source: constitution.md Principle III.

## Conflict Warnings

- workflow.md and architecture_constitution.md referenced by repository instructions are absent. Available extension workflow and constitution were used; missing structural standards were not validated.
- Constitution path python/kllm is stale; explicit user instruction and checked packaging require src/kllm.
- Initial verification during active RPC ownership is deferred; state is verification_pending/RPC_BUSY and the current run is preserved.

## Retrieval Notes

- Optimized retrieval considered 2 index entries and read 2 selected source sections; subsequent narrow live code/primary-doc research is recorded in research.md.
- Initial context token comparison: baseline 1,912, cached 437, saved 1,475 (77.1%). Enriched synthesis stays below the configured default 900-word budget.
- Shared sync unavailable without project profile/callable MCP. Capture evaluated; unimplemented decisions stay feature-local, with no durable memory write.
