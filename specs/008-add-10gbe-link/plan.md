# Implementation Plan: Add 10GbE Link Setup

**Branch**: `main` (actual checkout; feature identifier is `008-add-10gbe-link`) | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-add-10gbe-link/spec.md`

## Summary

Add one Python CLI script to assign and verify a private IPv4 address on an explicitly selected dedicated Ethernet adapter on Windows, macOS, and Linux. Require a connected adapter with negotiated speed of at least 10 Gbps; if the OS cannot report the speed, refuse configuration. Use narrow, temporary OS-native configuration operations; reject DHCP-enabled interfaces and conflicting addresses/routes instead of changing unrelated networking state. Update the existing 10GbE guide.

## Technical Context

**Language/Version**: Python 3.10+ (repository minimum)
**Primary Dependencies**: Python standard library; native OS commands (`PowerShell NetTCPIP`, `ifconfig`, `ip`)
**Storage**: None
**Testing**: `uv run pytest -q` plus targeted tests for command construction, preflight, and failures
**Target Platform**: Windows, macOS, Linux
**Project Type**: Python command-line script
**Performance Goals**: Configuration and verification complete within 30 seconds, excluding operator input
**Constraints**: Require elevation; no DNS/default-route changes; temporary IPv4 assignments only (Windows `ActiveStore`); shell-free subprocess argument lists; explicit interface selection; verified negotiated speed >=10 Gbps; no public exposure of RPC port 50052
**Scale/Scope**: One selected interface and one endpoint per invocation; coordinator/worker address pair on a private subnet

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Simple, consistent code**: PASS — one focused script, standard library only, and existing documentation is extended.
- **Behavior-focused regression tests**: PASS — deterministic tests can assert preflight decisions, exact native commands, idempotence, and error handling without modifying host networking.
- **Verified integration and portability**: PASS WITH PLATFORM FOLLOW-UP — unit tests cover all command builders and speed checks; configure/verify and negotiated-speed detection must be smoke-tested on Windows, macOS, and Linux before claiming platform validation. Hardware/link throughput checks require actual 10GbE hardware and are not a default test gate.
- **Explicit contracts and failures**: PASS — explicit interface selection, privilege/DHCP/conflict checks, nonzero errors, and no broad address flush or route replacement.
- **Maintainable, reproducible changes**: PASS — update the 10GbE guide and record platform-specific persistence/verification limits.

No constitution violations require an exception.

## Phase 0: Research

See [research.md](research.md). The key decision is temporary, interface-scoped addressing on all platforms: use Windows `ActiveStore`, macOS `ifconfig`, and Linux `ip`. Refuse a DHCP-enabled Windows adapter because adding a static address can disable DHCP. Require OS-reported negotiated speed of at least 10 Gbps before configuration.

## Phase 1: Design

See [data-model.md](data-model.md), [contracts/cli.md](contracts/cli.md), and [quickstart.md](quickstart.md).

## Constitution Check (Post-Design)

- **No added dependency or broad abstraction**: PASS.
- **No unintended network changes**: PASS — profile is bound to a selected interface, commands do not configure DNS/gateway/default routes, and unsafe DHCP/config conflicts stop before mutation.
- **Platform boundary checks**: PASS WITH FOLLOW-UP — deterministic unit checks plus one host-level speed/configuration check per OS; physical 10GbE throughput is explicitly outside automated verification.
- **Documentation and error contract**: PASS — quickstart and existing network guide describe required privileges, temporary lifetime, checks, and recovery.

## Project Structure

### Documentation (this feature)

```text
specs/008-add-10gbe-link/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── cli.md
```

### Source Code (repository root)

```text
scripts/
└── configure_10gbe.py

tests/
└── test_configure_10gbe.py

network-config/
└── 10gbe-bridging-guide.md  # updated usage and safety guidance
```

**Structure Decision**: Keep this in the existing repository-level `scripts/` and `tests/` layout. It is a stand-alone operational helper, not a runtime service or `kllm` CLI feature.

## Complexity Tracking

No constitution violations.
