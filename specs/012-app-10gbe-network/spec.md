# Feature Specification: App-Managed Private 10GbE Network

**Feature Branch**: `main` (existing branch; no feature branch created)

**Feature Directory**: `specs/012-app-10gbe-network`

**Created**: 2026-10-01

**Status**: Ready for planning

**Input**: User description: "implement creating a 10gbe ethernet private network in the app itself. It should be started by master via a socket signal, workers will do their own part and join the network."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start the private network from the master (Priority: P1)

As a beginner cluster operator, I read the administrator notice, enable access once on each participating computer, and start a private 10GbE network from the master in the app. The master signals the workers, and each worker performs its own local setup and reports its result without separate per-node configuration commands.

**Why this priority**: Coordinated setup is the requested capability and removes repetitive manual configuration.

**Independent Test**: With one master and two joined workers on a dedicated 10GbE switch, approve access locally, let Kluster select safe adapters and a subnet, start setup once, and verify distinct private addresses and readiness on all three nodes.

**Acceptance Scenarios**:

1. **Given** joined nodes with selected eligible adapters and sufficient permissions, **When** the operator starts setup on the master, **Then** each node applies its assigned local configuration and the app reports every participant's result.
2. **Given** multiple eligible adapters and no selection on a participant, **When** setup is requested, **Then** the app requires an adapter selection and no participant changes configuration.
3. **Given** a selected adapter with a slow or unknown negotiated speed, unreadable DHCP configuration, or no physical link, **When** setup is requested, **Then** the app identifies the failed prerequisite and no participant changes configuration.
4. **Given** insufficient privileges on a participant, **When** setup is requested, **Then** the app identifies that node and required permission, preserves existing connectivity, and allows retry after the prerequisite is resolved.

---

### User Story 2 - Use and inspect the verified private network (Priority: P2)

As an operator, I see which nodes have joined the private network and use it for distributed inference once all selected nodes are verified. A configured address is distinguished from a working network.

**Why this priority**: Address configuration alone does not establish that cluster traffic can use the dedicated link.

**Independent Test**: Establish the network, run distributed inference, and verify use of the assigned dedicated worker endpoints. Disconnect a worker's dedicated cable and verify degraded status while its separate management connection remains available.

**Acceptance Scenarios**:

1. **Given** locally configured nodes, **When** connectivity is checked, **Then** each worker becomes ready only after verified communication with the master over their dedicated addresses and verification of connectivity required for inference.
2. **Given** all participants ready, **When** a new inference run selects the private network, **Then** it uses the verified dedicated worker endpoints and the app identifies the selected network.
3. **Given** an active inference run, **When** setup completes, **Then** the app reports availability for subsequent runs without silently moving the active run.
4. **Given** a ready worker whose dedicated link fails, **When** the loss is detected, **Then** the app marks it degraded, reports the cause or uncertainty, and prevents new runs from selecting its unverified private endpoint.
5. **Given** addresses configured but connectivity unverified, **When** status is viewed, **Then** it shows configured but unverified or failed rather than ready. Reachability is not presented as measured throughput.

---

### User Story 3 - Stop, retry, and recover safely (Priority: P3)

As an operator, I stop or retry the managed network and recover after interruption without losing ordinary host network settings or leaving unexplained changes behind.

**Why this priority**: Host network changes must remain recoverable when nodes disconnect or agents restart.

**Independent Test**: Interrupt setup after one worker applies its address, restart the worker, retry, and stop the network. Verify no duplicate assignments and removal of only app-owned changes.

**Acceptance Scenarios**:

1. **Given** an active network with no dependent inference run, **When** the master stops it, **Then** reachable nodes remove only app-owned changes and report cleanup results.
2. **Given** a duplicate or delayed signal, **When** a worker receives it, **Then** it retains one assignment for the current operation and rejects stale signals without reconfiguration.
3. **Given** a participant unreachable after partial setup, **When** setup fails or is cancelled, **Then** reachable nodes roll back, the app reports cleanup pending for the unreachable node, and it reconciles owned changes when management connectivity returns.
4. **Given** an agent restart or host reboot, **When** state is reconciled, **Then** readiness requires fresh checks; missing temporary addresses are reported and can be recreated by retry.
5. **Given** an active run using the network, **When** stop is requested, **Then** the app identifies the blocking run and requires it to be stopped through existing run controls before network removal.

### Edge Cases

- Subnet overlap with existing addresses, routes, VPNs, or overlays, or insufficient usable addresses: reject before application and identify the conflict.
- Adapter identity changes or link disappears between preparation and application: fail the participant and roll back operation-owned changes.
- A worker joins the cluster later: keep it outside the managed network until its adapter is selected and it is explicitly added; preserve existing assignments.
- A participant disconnects during preparation: fail without configuration changes. During application: expose partial setup and cleanup pending.
- Concurrent lifecycle requests: serialize operations for a network and reject conflicting requests with the current operation status.
- Signals from a non-master, unauthenticated peer, or stale master session: reject without host changes.
- External modification of an app-owned address or route: report an ownership conflict without overwriting or deleting the user's replacement.
- Unsupported platform capability or blocked firewall: report the unmet prerequisite or failed check; do not broaden public access or claim readiness.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The app MUST disclose administrator access before the OS prompt, allow one-time local authorization on each computer, and let an operator on the current master select joined participants; select a single safe dedicated adapter automatically and ask only when multiple eligible adapters exist, then start, inspect, retry, add nodes to, and stop a managed private network.
- **FR-002**: The master MUST initiate worker setup through an authenticated socket signal over existing cluster management connectivity. Each worker MUST perform its own local setup and report preparation, application, verification, and cleanup results.
- **FR-003**: Workers MUST accept network-changing signals only from their authenticated current master. Unauthorized, malformed, stale, and conflicting signals MUST fail explicitly without host changes.
- **FR-004**: Before any participant changes configuration, every selected node MUST pass preparation checks for adapter identity, physical link, OS-reported negotiated speed of at least 10 Gbps, known DHCP configuration, supported configuration manager, protected helper authorization, platform capability, local permission, and address/route conflicts. Conditions that can change MUST be rechecked before application.
- **FR-005**: The app MUST offer an unused private IPv4 subnet and allow operator selection of another. The master MUST allocate unique usable addresses, reject detected conflicts and insufficient capacity, and preserve current assignments when adding nodes.
- **FR-006**: Setup MUST add temporary configuration to selected adapters without changing default routes, DNS profiles, gateways, unrelated adapters, or user-owned addresses. It MAY disable IPv4 DHCP on the selected dedicated adapter after saving the original configuration, and MUST restore only owned configuration changes on stop or rollback. Uncertain ownership requires local review.
- **FR-007**: Setup and recovery MUST preserve the existing management path. Failure of the dedicated link MUST NOT deliberately remove ordinary management connectivity.
- **FR-008**: Overall and per-node status MUST distinguish preparation, configuration, verification, ready, degraded, failure, and cleanup pending. Status MUST include selected adapter, assigned address, negotiated speed or unknown status, last verification time, and actionable failure reason. Overall ready MUST require all selected participants ready.
- **FR-009**: Readiness MUST require verified master/worker communication over dedicated addresses and verified connectivity required for inference. Failed or inconclusive checks MUST prevent readiness. Reachability, negotiated speed, and measured throughput MUST be distinct facts.
- **FR-010**: New inference runs selecting this network MUST use verified dedicated endpoints. Setup MUST NOT silently reroute active runs or silently fall back to another network if the requested network is unavailable.
- **FR-011**: Failed or cancelled application MUST roll back operation-owned changes on reachable nodes. Unreachable or failed cleanup MUST remain visibly pending until reconciliation. Partial setup MUST NOT be reported as success.
- **FR-012**: Signals MUST identify the network, operation, and current master session so repeated delivery is safe and delayed signals cannot override newer state. Conflicting lifecycle operations MUST NOT apply concurrently.
- **FR-013**: Ownership and operation state MUST support reconciliation after restart or interruption, fresh verification before readiness, and preservation of externally changed configuration. Temporary settings MUST NOT be made permanent across host reboot.
- **FR-014**: Stop MUST remove only app-owned changes and report each cleanup result. Stop MUST be blocked by active dependent inference runs and identify those runs.
- **FR-015**: Windows, Linux, and macOS MUST have the same lifecycle guarantees; unsupported capabilities MUST be reported before changes. Validation records MUST identify platform and hardware combinations actually exercised.
- **FR-016**: Unauthenticated upstream RPC MUST remain on trusted private interfaces. Setup MUST NOT expose port 50052 publicly or broaden firewall access beyond the selected trusted network. Diagnostics MUST omit credentials and join keys.
- **FR-017**: Existing manual dedicated-link setup and LAN/WireGuard workflows MUST remain usable when app-managed networking is not selected. Documentation MUST explain prerequisites, temporary settings, setup, verification, and recovery.
- **FR-018**: Each lifecycle stage MUST complete or report a bounded timeout. Unconfirmed connectivity or cleanup MUST be shown as uncertainty, not ready or successfully removed.

### Key Entities

- **Managed Private Network**: Master-owned identity, private subnet, selected members, desired lifecycle state, and aggregate readiness.
- **Node Network Assignment**: Node and adapter identity, unique address, eligibility evidence, observed state, verification time, and ownership of local changes.
- **Network Operation**: Start, add, retry, or stop request with identity, master session, participants, stage results, timeout, and cleanup status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With eligible dedicated hardware and permissions available, one master and two workers reach verified readiness within 60 seconds of one in-app start action, with no per-node configuration commands.
- **SC-002**: Acceptance attempts covering slow or unknown speed, unreadable DHCP configuration, denied administrator authorization, conflicting subnet, and missing adapter identify the node and reason in 100% of cases before any participant changes networking.
- **SC-003**: Across ten repeated starts/retries and duplicate-signal cases, each node retains one current assignment and every participating address is unique.
- **SC-004**: Multi-node inference acceptance runs use all selected workers' assigned dedicated endpoints; zero new runs select failed or unverified dedicated endpoints.
- **SC-005**: A disconnected ready worker is shown as degraded or unverified within 15 seconds. Its existing management connection remains available when that connection's physical network is intact.
- **SC-006**: Stop or failed setup removes app-owned changes on reachable nodes within 60 seconds while preserving all pre-existing addresses, default routes, and DNS settings in acceptance snapshots. Unreachable nodes remain visibly pending and reconcile within 60 seconds of management reconnection when cleanup prerequisites are available.
- **SC-007**: Unauthorized and stale-master signals cause zero host changes in acceptance cases. Restart and reboot cases never display ready before fresh verification.
- **SC-008**: Lifecycle acceptance checks cover Windows, Linux, and macOS participants, including a mixed-platform pair. Untested combinations are identified and never counted as passed.

## Assumptions

- Operators supply connected 10GbE adapters, compatible cables, and a switch where needed. Software configures existing hardware; it cannot create physical capacity.
- Workers are already enrolled and reachable over ordinary cluster management connectivity. Initial enrollment over an unconfigured dedicated link is outside scope.
- Initial scope is one private IPv4 subnet, a direct link for one worker or a switch for several. Automatic physical bridging, IPv6, routing across sites, and permanent OS configuration are outside scope.
- Operators select each adapter once and arrange local OS permissions. Master initiation coordinates prepared nodes; it does not grant remote administrator rights.
- The guided flow handles dedicated adapter DHCP configuration and restoration through a scoped native helper; the manual workflow retains its DHCP-disabled prerequisite. Unknown negotiated speed is ineligible until verified.
- Already-configured adapters may participate when eligible; pre-existing settings remain user-owned.
- Existing cluster authentication and current-master ownership apply to setup signals; a private address alone does not establish trust.
- The timings above are acceptance targets, not claims of current performance or guaranteed throughput.

## Guided setup revision (2026-10-01)

The approved beginner workflow supersedes the original manual-pairing and already-root prerequisites. First-time identity bootstrap uses mutual join-secret proofs and Ed25519 transcript proofs, then pins immutable identities. Possession of the join secret does not authorize privileged host operations: a local user must acknowledge the notice and approve the OS installation prompt on every participating machine. Changed pins require explicit settled local replacement.

The helper runs from an administrator-owned installation, loads no Python runtime, and accepts bounded account-restricted local IPC. Its installed grant fixes the current cluster master key, binding, node and account. Signed network phases authorize exact dedicated assignments; no arbitrary commands, remote elevation or public helper listener exist. The original agent account and credential paths remain unchanged. Normal setup contains no public-identity JSON, manual subnet entry or shell instructions. Recovery exposes a deliberate keep-current or restore-reviewed decision. Advanced details retains manual controls.
