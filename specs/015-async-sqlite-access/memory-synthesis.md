# Memory Synthesis

## Current Scope

Introduce a common async-compatible query abstraction and bounded local connection reuse for usage history and managed-network state. Keep both existing files, domain validation, synchronous/background consumers, public contracts, and recovery decisions. Plan only; no runtime or dependency changes in this phase.

## Relevant Decisions

- Preserve facade patch surfaces and inject dependencies when extracting services. (Reason Included: agent/lifecycle integration, Status: Active, Source: docs/memory/decisions/2026-09-29-facade-patch-surface.md)
- Keep Pydantic domain models and existing JSON cluster state. (Reason Included: prevent a general state rewrite, Status: Active, Source: docs/memory/architecture/2026-09-29-structural-standards.md)

## Active Architecture Constraints

- Python remains in `src/kllm/`; entry points and native boundaries remain stable. (Reason Included: module placement, Source: .specify/memory/architecture_constitution.md)
- Justify the dependency through current requirements and both existing stores. (Reason Included: avoid speculative abstraction, Source: .specify/memory/constitution.md)
- Preserve atomicity, explicit failures, cleanup, and interruption recovery. (Reason Included: pooled access/cancellation, Source: .specify/memory/constitution.md)
- Verify real persistence/process/HTTP boundaries with isolated state. (Reason Included: contention and execution fencing, Source: .specify/memory/constitution.md)
- Update manifests/lockfile together; use supported rebuild and import checks. (Reason Included: packaging, Source: .specify/memory/constitution.md)

## Accepted Deviations

- None. No governance amendment or safety exception is needed.

## Relevant Security Constraints

- Preserve managed-network identity-file protections, authority checks, idempotency, and the execution fence spanning intent, external mutation, and observation. (Reason Included: transactions cannot replace OS ownership, Source: src/kllm/managed_network/store.py)
- Bind caller values as data and keep failures free of secrets; no public database access. (Reason Included: query/error handling, Source: spec.md and .specify/memory/constitution.md)

## Related Historical Lessons

- Usage has DELETE journaling, FULL synchronization/auto-vacuum and physical byte pruning; network state has WAL, FULL synchronization, foreign keys and explicit write transactions. Keep store-specific policies. (Reason Included: shared pooling; verified current source)
- Historical session memory records rejection of a history index because insert cost and storage size increased. This is not a fresh measurement; avoid unrequested indexes and measure necessary schema changes. (Reason Included: usage storage budget)
- Cross-process execution ownership extends beyond a transaction. Preserve the entire synchronous fenced operation when adapting async callers. (Reason Included: cancellation and thread-affine reentrant locks)

## Conflict Warnings

- General constitution names the old `python/kllm/`; current user instructions and architecture standard require `src/kllm/`. Follow the current layout without amending governance.
- Hook guidance requests automatic durable capture; repository workflow and session instructions require explicit authorization. Keep decisions feature-local. Shared sync was evaluated: no project profile or memory MCP sync tool is configured; no global/durable memory writes were attempted.

## Retrieval Notes

- Required before-plan hook executed through the repository markdown fallback. Configuration absent; defaults apply.
- Reviewed feature context, governance, A1/D1 repository entries and the scoped historical storage-budget lesson. Unrelated D2 excluded. CodeGraph preceded code discovery.
- No unresolved hard conflict. Below the 900-word budget. Cache unavailable: baseline/cached/saved token counts are unmeasured.
