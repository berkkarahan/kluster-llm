# Feature Memory: Central Node Network Directory

Feature-local notes for `specs/014-node-network-directory`. Not durable project memory.

## Constraints inherited from governance

- RPC port 50052 stays private (Constitution).
- Keep Python under `src/kllm`, native under `crates/kllm-native`, UI under `dashboard`.
- `managed-network.sqlite` already stores assignments/operations/journals; identity rows are
  additive. SQLite connections are shared within a process; use the store lock/transactions.
- Spec 012 memory: assignments live separately from transient `ClusterState` membership;
  identity rows must not become liveness state. Liveness stays in `ClusterState.members`.

## Relevant current behavior

- `RuntimeControlService.status()` labels nodes `member.get('host')`; `member.host` is the
  heartbeat `rpc_bind` chosen at process start (`pick_rpc_bind_host`).
- `NodeAssignment.address` is the dedicated private address; it only exists on the master and
  on the node's own local store copy.
- Workers mirror `ClusterState.members` from the heartbeat `cluster_members` snapshot
  (`membership_state.cluster_snapshot`, `_apply_cluster_snapshot`).
- `NetworkStore.pair(replace=True)` refuses while any network is not stopped or a journal is
  unresolved; `adopt_rotated_master` pre-verifies same-key rotation.
- `RuntimeRegistry.build()` memoizes deterministic request ids: a failed job with the same
  request id is returned, not retried. Auto-retry must issue a new request id.

## Open questions / watchpoints

- Same-key adoption with a live network keeps network records whose `master_node_id` is the old
  label; future topology edits may need the explicit re-pair/recovery path. Surface guidance.
- Directory hostname is untrusted network input; validate length/pattern and never use it as a
  path or command.
- Hardware two-node rotation needs validation on a real cluster before claiming full support.
