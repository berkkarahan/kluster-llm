# Memory Synthesis

## Current Scope
Configure and verify a dedicated 10GbE point-to-point cluster link across Windows, macOS, and Linux. Existing guidance is in `network-config/10gbe-bridging-guide.md`.

## Relevant Decisions
- [D1] Keep network setup in the existing network configuration guide and installer/script conventions (Reason Included: existing user guidance and scoped ownership, Status: active, Source: `docs/agent-install.md`, `network-config/10gbe-bridging-guide.md`).

## Active Architecture Constraints
- [A1] Preserve unrelated interface settings, default routes, and DNS; validate at the OS boundary (Reason Included: network configuration changes are privileged and platform-specific, Source: `.specify/memory/constitution.md`).
- [A2] Prefer existing Python standard library and script patterns; document behavior and run appropriate checks (Reason Included: simplicity and portability, Source: `.specify/memory/constitution.md`).
- [A3] Require explicit interface selection, reported negotiated speed >=10 Gbps, and temporary assignment on each OS; use Windows `ActiveStore` (Reason Included: avoid wrong-interface changes and persistent settings, Source: feature decisions and platform research).

## Accepted Deviations
- [none]

## Relevant Security Constraints
- [C1] RPC port 50052 is unauthenticated upstream and MUST remain private to LAN or WireGuard; never recommend public exposure (Reason Included: service security boundary, Source: `AGENTS.md`, `.specify/memory/constitution.md`).
- [C2] A verification timeout/no echo response is inconclusive; do not report the peer offline without an explicit unreachable result (Reason Included: ICMP filtering is indistinguishable from peer downtime, Source: feature platform research).

## Related Historical Lessons
- [none]

## Conflict Warnings
- None. Existing guide contains manual steps for Windows and macOS but does not yet provide an automated cross-platform workflow; new behavior should preserve the private-network boundary.

## Retrieval Notes
- Reviewed governance constitution, memory workflow/index/synthesis, installer guide, and existing 10GbE setup guide. Architecture constitution file is absent. No memory MCP or executable hook is available in this session; the configured hook command could not be run. Synthesis is based on available repository documentation and stays within the 900-word limit.
