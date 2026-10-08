# Feature Specification: Central Node Network Directory and Runtime Sync Recovery

**Feature Branch**: `014-node-network-directory`

**Created**: 2026-10-08

**Status**: Draft

**Input**: User description: "implement a common, centralized network store and share it
between cluster view and settings llama.cpp view; also fix syncing llama.cpp from master to
worker."

## Problem

The cluster view and Settings → llama.cpp build their node lists from `ClusterState.members`,
but the llama.cpp tab labels nodes with `member.host`, which is the RPC bind address chosen
once at process start (often the DHCP management NIC). The dedicated private-network address
assigned by the managed network exists only in `managed-network.sqlite`
(`NodeAssignment.address`) and never reaches the runtime view, the usage store, or worker
mirrors. Node identity is therefore duplicated and inconsistent between views.

Separately, a same-key master rotation (rebuilt/restored label, same Ed25519 key) is refused
while any managed network is not fully stopped. The worker cannot re-pair, the signed runtime
channel stays dead, Settings → llama.cpp never receives the cluster target, and forcing a sync
returns `CHANNEL_UNAVAILABLE`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One node list everywhere (Priority: P1)

An operator opens the cluster view and Settings → llama.cpp and sees the same node names and
addresses. Each node is identified by its hostname, with its dedicated private-network address
(or management address when no private network is ready) as the secondary line.

**Independent Test**: With a ready private network, open the cluster view and Settings →
llama.cpp on the master and on a worker. Both lists show the same hostname and address for
every node.

**Acceptance Scenarios**:

1. **Given** a ready managed network, **When** any node list renders, **Then** the dedicated
   private address is shown for every participating node and matches the private-network panel.
2. **Given** no managed network, **When** any node list renders, **Then** the management/LAN
   address is shown for every node.
3. **Given** an older agent that does not report directory data, **When** any node list renders,
   **Then** the list falls back to the membership host without error.

### User Story 2 - Worker receives the cluster target after a master rotation (Priority: P1)

An operator rebuilds or restores master state (same signing key, new node label or binding)
while a private network is live. The worker re-pairs automatically, the master re-sends the
saved llama.cpp target, and Settings → llama.cpp on the worker shows the target and reaches a
prepared state.

**Independent Test**: Rotate the master label (same key) with a live managed network, then
observe the worker re-pair and become prepared for the saved target without manual re-pairing.

**Acceptance Scenarios**:

1. **Given** a worker pinned to a previous same-key master label, **When** the master presents
   its rotated identity, **Then** the worker adopts it even while a managed network is not
   stopped, and the signed channel reconnects.
2. **Given** a stored target from the previous master label with the same cluster binding,
   **When** the master reconciles, **Then** the target is re-stamped with the current label and
   pushed to the workers.
3. **Given** a changed cluster binding, **When** Settings → llama.cpp renders, **Then** the
   target is reported as needing re-selection and a one-click re-apply keeps the saved version.
4. **Given** a changed signing key, **When** the worker re-asserts pairing, **Then** the worker
   still refuses automatic adoption and requires explicit re-pairing.

### User Story 3 - Transient failures self-heal (Priority: P2)

A preparation attempt that failed because a node was restarting or a download was interrupted
is retried automatically with backoff; a manual Retry remains immediate.

**Independent Test**: Fail one preparation attempt, leave the UI untouched, and observe the
automatic retry complete the preparation and show the node as prepared.

**Acceptance Scenarios**:

1. **Given** a failed or interrupted preparation, **When** the cooldown elapses and the target
   and node are unchanged, **Then** the master re-sends the preparation with a new attempt.
2. **Given** an interrupted-installer job, **When** it is retried automatically, **Then** the
   existing deterministic idempotency for the same request is not broken for in-flight work.

## Requirements *(mandatory)*

- The managed-network store MUST own durable per-node network identity: hostname, role,
  management/LAN host, RPC bind, private address, private state, link speed, and timestamps.
- The master MUST be the authority; workers MUST receive the directory through the existing
  membership snapshot and persist it locally for their own dashboards.
- Both cluster view and Settings → llama.cpp MUST read the same directory, with membership
  fallback for older agents.
- A same-key master rotation MUST be adopted even while a managed network is not stopped;
  a changed key MUST remain refused.
- A stored runtime target whose master label changed under the same binding MUST be re-stamped
  automatically; a changed binding MUST be surfaced as needing re-selection, never silently
  hidden.
- Failed/interrupted preparation MUST be retried automatically with a cooldown and a bounded
  attempt count; manual retry stays immediate.
- Per-node signed-channel state MUST be visible in the runtime status consumers.

## Success Criteria

- Cluster view and Settings → llama.cpp show identical hostname + address for every node.
- A worker with a rotated same-key master reaches `prepared` for the saved target with no
  manual re-pairing.
- No regression to changed-key refusal or explicit re-pairing flows.
- Targeted Python and dashboard suites pass; hardware multi-node rotation remains a stated
  unverified follow-up when no cluster is available.

## Edge Cases

- Master and worker both rotated labels but retained bindings and keys.
- Directory rows for a node that leaves the cluster must not linger.
- A node with no hostname reported falls back to management host, then node id.
- A target selected while no private network is ready stays valid when the network arrives.
- An unresolved local mutation journal during rotation: adoption proceeds for same-key
  identity; owned-resource cleanup stays local.
