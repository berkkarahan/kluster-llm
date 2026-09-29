# Feature Specification: Add 10GbE Link Setup

**Feature Branch**: `main` (feature identifier: `008-add-10gbe-link`)
**Created**: 2026-09-29
**Status**: Draft
**Input**: User description: "add a new script to facilitate cross-platform build of 10GBe network"

## User Scenarios & Testing

### User Story 1 - Configure a dedicated link (Priority: P1)

As a cluster operator, I want one guided command to configure the dedicated 10GbE interface on each node so cluster traffic can use a stable high-bandwidth private link.

**Why this priority**: Reliable node connectivity is the primary outcome requested.

**Independent Test**: On supported hosts with a connected 10GbE adapter, run the script with an explicitly selected interface and node role; confirm that the selected interface receives the expected private address and other interfaces retain their existing settings.

**Acceptance Scenarios**:
1. **Given** a supported host and a selected adapter, **When** the operator chooses the coordinator or worker role, **Then** the script configures the corresponding address on the dedicated subnet and reports the result.
2. **Given** the selected adapter already has the intended configuration, **When** the script is run again, **Then** it reports success without creating duplicate or conflicting settings.
3. **Given** the operator has not selected an adapter, **When** configuration is requested, **Then** it requires an explicit interface selection and makes no network changes, even if only one eligible adapter is detected.
4. **Given** the selected adapter is disconnected, reports less than 10 Gbps negotiated speed, or its negotiated speed cannot be verified, **When** configuration is requested, **Then** it makes no network changes and reports the observed speed or why speed could not be verified.
5. **Given** a supported Windows host, **When** configuration succeeds, **Then** the IPv4 address is added to the active store only and is not present in the persistent store.

### User Story 2 - Verify the configured link (Priority: P2)

As a cluster operator, I want the script to verify the peer is reachable so I can distinguish successful configuration from a connected but unusable link.

**Independent Test**: Configure two nodes on the dedicated subnet, run verification from either node, and confirm reachable and unreachable peers produce distinct results.

**Acceptance Scenarios**:
1. **Given** both nodes have compatible addresses and a working cable or switch path, **When** the operator verifies the peer, **Then** the script reports successful reachability.
2. **Given** the peer does not reply, **When** verification runs, **Then** the script reports failure without altering network settings; it reports `unreachable` only for an explicit OS no-route/destination-unreachable result and `unable-to-verify` for a timeout or missing echo reply.

### Edge Cases

- The script is not running with required elevated permissions.
- The selected adapter is absent, disconnected, not 10GbE-capable, negotiates below 10 Gbps, or its negotiated speed cannot be verified.
- The requested subnet/address conflicts with an existing address or route.
- The host has no eligible adapter or is on an unsupported operating system.
- Configuration succeeds but peer verification fails because the peer is not configured or a firewall blocks probes.
- The operator repeats the command, cancels it, or a native network command fails partway through.
- Existing default routes, DNS, Wi-Fi, and unrelated interface configuration must remain intact.

## Requirements

### Functional Requirements

- **FR-001**: The script MUST support Windows, macOS, and Linux hosts using each platform's native network configuration facilities.
- **FR-002**: The configure and verify actions MUST require an explicit interface selection; they MUST NOT choose an adapter automatically.
- **FR-003**: The script MUST configure a dedicated private point-to-point subnet with distinct coordinator and worker addresses, and report the selected interface and resulting address. The assignment MUST be temporary and MUST NOT survive reboot.
- **FR-004**: The script MUST NOT modify unrelated interfaces, default routes, DNS settings, or Wi-Fi configuration.
- **FR-005**: The script MUST detect missing privileges and configuration conflicts before applying changes where possible, and provide actionable errors.
- **FR-006**: The script MUST support safe repeated invocation without duplicate or contradictory configuration. If the adapter uses DHCP or has conflicting settings that the script cannot preserve, the script MUST refuse to change it and explain the conflict.
- **FR-007**: The script MUST offer a verification action that tests peer reachability without changing network configuration.
- **FR-008**: The script MUST return a non-zero result when configuration or requested verification fails, and retain native command diagnostics without exposing secrets.
- **FR-009**: The script MUST document prerequisites, supported platforms, required privileges, usage, recovery, and verification in the existing network setup documentation.
- **FR-010**: The configured link MUST remain private and MUST NOT expose unauthenticated RPC port 50052 to public networks.
- **FR-011**: Before configuration, the script MUST confirm that the selected adapter is connected and its negotiated link speed is at least 10 Gbps. If speed cannot be verified, it MUST report that limitation and make no network changes.

### Key Entities

- **Network interface**: The explicitly selected physical adapter dedicated to cluster traffic.
- **Link profile**: The private subnet, node role, local address, prefix, and peer address used for configuration and verification.

## Success Criteria

### Measurable Outcomes

- **SC-001**: On each supported operating system with a connected adapter negotiating at least 10 Gbps, an operator can complete configure command execution in under five minutes after identifying the interface.
- **SC-002**: In a two-host setup on the supported operating systems, the configured peer passes the script's reachability check in every tested case where the physical link and host firewall permit it.
- **SC-003**: Repeating a successful configuration produces no duplicate address or conflicting link profile, and a reboot removes the temporary assignment.
- **SC-004**: Tests confirm that a failed, ambiguous, or unprivileged attempt leaves unrelated interface settings unchanged.

## Assumptions

- “Build a 10GbE network” means configuring a dedicated 10GbE link between existing cluster nodes; driver installation, physical cabling, and switch provisioning are outside scope.
- Initial supported operating systems are Windows, macOS, and Linux, matching the repository's cross-platform install guidance.
- Operators supply or select a dedicated adapter and physical link; the script does not change global routing or DNS.
- Link addresses are temporary so setup remains reversible and consistent across operating systems; operators rerun configuration after reboot.
- The existing `network-config/10gbe-bridging-guide.md` remains the user-facing setup guide and will be updated rather than replaced.
