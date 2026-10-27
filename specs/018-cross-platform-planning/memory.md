# Feature Memory: 018-cross-platform-planning

Scratch context for this feature only; not durable project memory.

## Sources consulted

- Governance: `.specify/memory/constitution.md`, `architecture_constitution.md`, `workflow.md`.
- `docs/memory/INDEX.md` (no Prima/qualification entries; nothing conflicts).
- Spec 017 (`017-prima-integration`): Windows/Linux/macOS with Vulkan (MoltenVK on macOS) is
  the stated target; macOS controlled lifecycle assessment passed but qualification is `open`.
- Code check: the qualification status function in
  `src/kllm/modules/primacpp/interactors/qualification.py` derives the scope as
  `windows-vulkan-<topology>` and rejects any non-Windows or non-Vulkan runtime/participant,
  except a coordinator-only CPU master (rank 0, `ngl` 0, no layers) in distributed topology.

## Constraints carried into the spec

- Constitution III: platform behavior is checked on Windows, Linux and macOS; hardware
  behavior is not reported validated without the hardware. Unverified items are listed.
- Constitution IV: failures are explicit and actionable; no silent fallback to another scope.
- Spec 017: a successful build is not platform parity; new scopes must not grant
  qualification without recorded evidence.

## Open questions (none blocking)

- Whether macOS Metal backends ever join a Prima ring is out of scope (Vulkan only).

## Implementation outcome (2026-10-14)

- Scope is selected from the node's own platform (`platforms.py`), never defaulted to Windows.
  Linux/macOS scopes exist with every gate false and no receipts; defining a scope qualifies nothing.
- Platform/architecture travel through member records, signed `ParticipantEvidence`
  (`architecture`, omitted when unset), the plan preview and `inventory_identity`; they were
  deliberately NOT added to `RankAllocation` (sent to workers with `extra="forbid"` and written to
  `execution.json` for native; native acceptance of new keys is unverified here).
- Ring backend homogeneity is enforced in `topology_from_plan`; CPU-only ranks are exempt.
- In a mixed-OS run each non-primary participant platform is evaluated against its own scope and
  its errors are prefixed with that scope name.
- Verification: `uv run pytest -q` 1713 passed / 19 skipped; dashboard check, tests (10/10) and build
  pass. 28 of 36 new tests fail on `HEAD`. Nothing was run on Linux, macOS or a multi-node cluster.

## Durable-memory candidates (not registered)

`speckit_memory_register` (MCP) is unavailable in this session and the CLI only searches, so these
were not captured. Duplicate search found no existing entry. Proposed, confidence ~75%:

- **D4 - Platform metadata must not ride on `RankAllocation`.** Rank records are sent to workers
  (`control.py` `rank.model_dump()`, `extra="forbid"`) and serialized to `execution.json`; new keys
  break mixed-version workers/native. Use optional omit-when-unset fields on evidence/member records.
