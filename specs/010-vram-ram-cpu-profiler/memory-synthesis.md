# Memory Synthesis

## Current Scope

- Feature: `specs/010-vram-ram-cpu-profiler`; implementation present and Windows acceptance
  recorded. Full delivery is pending Mac hardware verification and outstanding task checks.
- Rust host/process CPU/RAM and device memory on Windows/macOS, served through existing agent.
  User confirmed both host-wide and managed-process CPU/RAM. Legacy capacity/Linux preserved.
- Native ownership: `crates/kllm-native/src/lib.rs`, new concrete usage/platform siblings.
  Python integration: `native.py`, `usage.py`, agent facade, private helper lifecycle.
- New readings remain separate from legacy capacity/budgets and process-history persistence.

## Relevant Decisions

- A1, active: preserve concrete helpers, platform cfg branches and existing exports.
  Reason: new OS-specific collection must not become a provider framework or delete other targets.
  Source: `.specify/memory/architecture_constitution.md` and indexed structural-standards entry.
- D1, active: patched agent/installer symbols remain on their facade; siblings receive dependencies.
  Reason: integration touches `_usage_loop`, `usage_ingest`, `usage_live`, process identity.
  Source: `docs/memory/decisions/2026-09-29-facade-patch-surface.md`.

## Active Architecture Constraints

- Keep PyO3 exports in lib.rs and current Pydantic/API and persisted JSON ownership. Additive
  optional metadata is backward-compatible; no replacement state format or second database.
- Imports at module top except documented existing cycles/optional modules/test reloads.
- Constitution II/III: deterministic logic checks and real rebuilt binding/helper/agent boundary
  checks; Windows/macOS hardware evidence required before validation claims. Preserve Linux builds.
- Constitution IV/V: validate resource input and IPC; preserve old contracts; define timeout,
  cancellation and child cleanup; rebuild packaging through supported installer/build tooling.
  Sources: project constitution 1.1.0 and architecture constitution 1.0.0.

## Accepted Deviations

- None. Two private helper slots are a feature-specific isolation choice justified by FR-013/014,
  not a new installed service or a durable general architecture policy.

## Relevant Security Constraints

- Existing local/private-cluster boundary; RPC port 50052 remains private.
- Only selected managed lifetimes: no unrelated process inventory, prompts, credentials or raw
  unsanitized diagnostics in readings. Validate node/PID/start identity on ingestion.
- Helper slots/pipes/payloads are bounded and parent-owned; stale generations cannot publish.

## Related Historical Lessons

- A1/D1 facade constraints above; no additional historical GPU bug entry retrieved.
- Verified feature watchpoints: Windows venv redirectors require real-interpreter ownership;
  native wheel rebuilds can be shadowed by a locked in-tree .pyd. Proposals are in memory.md,
  awaiting approval/registration; no new durable entry was written.
- Feature research watchpoints: DXGI application budgets and Metal own allocations are not
  device-wide use; sysinfo refresh cannot alone establish failure status/exact lifetime.
  These are provisional feature-local design findings, not newly captured durable memory.

## Conflict Warnings

- Hard conflicts: 0. Soft conflicts: 0 after separating live measurements from legacy policy.
- Preserve the existing Metal 80% capacity policy for its callers without treating it as usage.
- A timed-out thread cannot be reclaimed safely; owned helpers use bounded reaping/quarantine.
  Windows CPU/RAM/GPU comparisons and private-agent delivery pass; Mac runtime is unverified.
- Cross-platform follow-up fixed the Windows absolute/quoted SDK cache-path root cause.
  All 54 installer tests and the full Python suite now pass (508 passed, 5 explicit skips).
- The user declined hosted verification and owns the remaining Mac runtime/Metal checks.
  Mac and Linux runtime results are unrun, not inferred from passing cross-target type checks.

## Retrieval Notes

- Markdown fallback under `.specify/memory/workflow.md`; memory-cache MCP tools unavailable.
  Baseline / cached / saved token statistics: unavailable, no cache operation or savings claimed.
- Two indexed entries A1/D1 reused; governance/config/feature memory refreshed. Under 900 words.
- Shared-sync command evaluated: no project_profile or accessible shared-memory tool, no import.
- Mandatory after-implement capture reviewed the completed code and evidence. Two candidate
  lessons were proposed in memory.md; no registration tool or explicit approval is available.
  No durable memory was changed or automatically approved; governance requires user approval.
- Research sources and current-code references are in research.md; hardware support matrix is
  explicitly proposed, with local counter availability observation distinguished from acceptance.
- Tasks preparation refreshed governance/spec/design context using the same Markdown fallback.
  Preserve story IDs when ordering P1 work as US1, US2, US5 before P2 work US3, US4; shared
  source files limit parallel execution. No new hard conflicts or durable decisions identified.
