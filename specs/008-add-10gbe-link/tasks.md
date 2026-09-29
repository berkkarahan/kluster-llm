# Tasks: Add 10GbE Link Setup

**Input**: Design documents from `specs/008-add-10gbe-link/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/cli.md`, `quickstart.md`

**Tests**: Included because the repository constitution requires deterministic automated checks for non-trivial logic and platform boundaries.

**Organization**: Tasks are grouped by user story for traceability. Shared profile/preflight work is foundational; US2 verification depends on the shared CLI from US1.

## Phase 1: Setup

**Purpose**: Project initialization

The repository already has the `scripts/` and `tests/` directories and a configured `uv`/pytest workflow; no project initialization task is required.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared link-profile validation used by configuration and verification

- [X] T001 [P] Implement role-to-address mapping and private-subnet validation in `scripts/configure_10gbe.py`; default to `192.168.10.0/24`, assign the first usable host address to coordinator and the second to worker, and reject fewer than two usable addresses.
- [X] T002 [P] Add deterministic profile/address validation tests in `tests/test_configure_10gbe.py`, including invalid CIDR, non-private subnet, overlap, invalid role, and exact coordinator/worker address mapping.

**Checkpoint**: Shared profile rules are specified in code and independently testable before platform changes are added.

---

## Phase 3: User Story 1 - Configure a dedicated link (Priority: P1) 🎯 MVP

**Goal**: Configure only the operator-selected dedicated adapter with a temporary coordinator/worker address, refusing unsafe or conflicting changes.

**Independent Test**: Run configuration with a mocked native command runner and confirm the selected adapter gets the expected address, repeat invocation is a no-op, and unsafe/ambiguous preflight makes no network-changing call.

### Tests for User Story 1

- [X] T003 [US1] Add configure contract tests in `tests/test_configure_10gbe.py` for mandatory explicit interface selection, unsupported OS, disconnected/lower-than-10-Gbps/unreported link-speed refusal, Windows DHCP refusal, the `-PolicyStore ActiveStore` argument, detected address/route conflicts, elevation errors, idempotence, native command failure diagnostics, and zero network mutations on rejected inputs.

### Implementation for User Story 1

- [X] T004 [US1] Implement `configure` in `scripts/configure_10gbe.py`: require `--interface`, confirm the adapter is connected and OS-reported negotiated speed is at least 10 Gbps (Windows `Get-NetAdapter.LinkSpeed`, macOS `networksetup -getmedia`, Linux `/sys/class/net/<interface>/speed`), preflight privilege/DHCP/address/route conflicts, then apply only the temporary address through Windows `New-NetIPAddress -PolicyStore ActiveStore`, macOS `ifconfig`, or Linux `ip` using shell-free subprocess arguments.

**Checkpoint**: US1 is complete when the mocked configure contract tests pass and manual configuration succeeds on one supported host without changing its default route or DNS.

---

## Phase 4: User Story 2 - Verify the configured link (Priority: P2)

**Goal**: Check peer reachability through the selected dedicated interface without modifying network state.

**Independent Test**: With native command calls mocked, verify reachable and unreachable peers return distinct outcomes, use the selected interface/source address, and make no configuration command calls.

### Tests for User Story 2

- [X] T005 [US2] Add verification contract tests in `tests/test_configure_10gbe.py`: echo reply is `reachable`; explicit OS no-route/destination-unreachable is `unreachable`; timeout/no echo reply is `unable-to-verify`; also cover invalid peer and unsupported platform, and assert verification never invokes an address-changing command.

### Implementation for User Story 2

- [X] T006 [US2] Implement `verify` in `scripts/configure_10gbe.py` to require the selected interface, test the peer using that interface/source address, report the three outcomes with the T005 semantics, preserve native diagnostics, and return nonzero unless an echo reply verifies reachability.

**Checkpoint**: US2 is complete when verification tests pass and both directions of a two-host link can be checked on a supported host pair.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Complete user guidance and verify feature boundaries

- [X] T007 Run `uv run pytest tests/test_configure_10gbe.py -q` and fix deterministic command-construction, profile, configure, or verify failures in `tests/test_configure_10gbe.py` and `scripts/configure_10gbe.py`.
- [X] T008 Update `network-config/10gbe-bridging-guide.md` with configure/verify examples for Windows, macOS, and Linux, explicit adapter selection, verified negotiated speed, Windows `ActiveStore` lifetime, elevation, subnet conflict handling, recovery, and the private-RPC warning.
- [ ] T009 Run host-level configure on Windows, macOS, and Linux adapters reporting negotiated speed >=10 Gbps; verify both directions on connected two-host pairs; time each configure invocation (under five minutes after interface identification); confirm reboot removes each temporary assignment (including Windows absence from PersistentStore); confirm default route/DNS are unchanged; and record OS/link-speed/timing/results in `specs/008-add-10gbe-link/quickstart.md`.
  - Blocked in this environment: Windows only has an up 1 Gbps Ethernet adapter; macOS, Linux, and a 10GbE test pair are unavailable. Environment evidence is recorded in `quickstart.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No work required; repository tooling already exists.
- **Foundational (Phase 2)**: No setup dependency; must complete before story work.
- **User Story 1 (Phase 3)**: Depends on foundational profile validation.
- **User Story 2 (Phase 4)**: Depends on foundational profile validation and US1's shared CLI/interface selection, but verification itself is independently exercised with a preconfigured/mock link.
- **Polish (Phase 5)**: T007 depends on both stories; T008 depends on the passing deterministic suite; T009 depends on the updated guide.

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2; delivers address configuration without US2.
- **US2 (P2)**: Starts after Phase 2 and the shared CLI/interface-selection foundation from US1; verification has an independent mocked test with preconfigured peer state.

### Parallel Opportunities

- T001 and T002 touch different files and can proceed in parallel.
- Once the shared profile foundation is complete, US1 and US2 test-case design can be reviewed in parallel, but implementation tasks T004 and T006 share `scripts/configure_10gbe.py` and should be applied sequentially.
- T007 deterministic tests complete before T008 documentation; T009 host validation follows both.

### Dependency Graph

```text
T001 ─┬─> T003 ─> T004 ─> T005 ─> T006 ─> T007 ─> T008 ─> T009
T002 ─┘
```

## Parallel Example: Foundational Profile

```text
Task T001: implement role/subnet mapping in scripts/configure_10gbe.py
Task T002: add profile/address tests in tests/test_configure_10gbe.py
```

These can proceed in parallel because the implementation and test file are separate; merge before story-specific platform changes.

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 2.
2. Complete T003 and T004.
3. Validate configuration behavior with deterministic mocked tests and one supported host.
4. Stop at the US1 checkpoint; this delivers the core setup workflow.

### Incremental Delivery

1. Add US1 for safe, repeatable link configuration.
2. Add US2 for interface-bound peer verification.
3. Run deterministic tests and update operator guidance.
4. Complete the Windows/macOS/Linux speed, persistence, timing, and network-preservation checks before claiming full cross-platform support.

## Notes

- Task entries retain the required checkbox, sequential ID, applicable parallel/story labels, and file path; completed tasks are marked `[X]`.
- Hardware integration is a manual platform validation, not a mocked-test pass; record unavailable operating systems or hardware explicitly.
- No task opens port 50052 or changes firewall policy.
