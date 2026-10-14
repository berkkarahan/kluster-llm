# 11 · Run lifecycle & privileged network helpers

**Use when:** You work on run readiness and launch journaling, signed run ownership, network reservations, the privileged firewall helper and its watchdog/recovery/dashboard, admission locks and rank listeners, or manual adapter cleanup and direct-mode native policy.

**Keywords:** readiness, acknowledgement, HTTP health, journal, coordinator, launch intent, recovery, idle, network reservation, RPC admission, Run/Stop, firewall, helper, protected, watchdog, rule, restore_owned, keep_current, direct mode, stdio-prima, pipe, framing, deadline, admission, lease, renewal, cleanup, DHCP.

**Related:** 10-profile-costs.md (reservations/claims interplay), 02-build-lifecycle.md (managed runs), 12-placement-qualification.md (qualification binding).

## Quick rules

- Signed wire ownership: wire operations stay signed and bound to sender/run/authority; preserve readiness, PID/start-token ownership, leases, cancellation, cleanup and recovery boundaries.
- Readiness: require an owned post-initialization native rank acknowledgement — configured output and PID presence alone are insufficient; rank zero also needs HTTP health; after validated readiness retain the acknowledgement in owned state and check PID/start token plus HTTP health; growing serving logs must not trigger the startup parser's size limit or require another native startup acknowledgement; exercise the CLI worker's actual manifest startup and zero-transformer coordinator path with controlled same-host model processes; retry pending cancellation/cleanup and admission release without publishing idle or restarting a cancelled run early.
- Launch journaling: journal each coordinator launch intent before sending a signed start — a lost reply can still leave a child running; persist the exact pending ranks and command authority, narrow them only on stop acknowledgement, and restore runs for cleanup after an agent restart; reconstructed owners must not resume inference automatically; test bounded recovery, cancellation during readiness publication, persistence/release failures and unrelated-process preservation.
- Network reservations: keep Prima network reservations distinct from RPC admission — bind exact private assignments/edges, manifest and master authority, and obtain signed route/address diagnostics without starting RPC children or relays; release only after every owned rank acknowledges idle (a dead master is insufficient); verify normal prepared Run/Stop, HTTP readiness, cancellation and recovery over real signed channels with owned fixture processes, labelling mapped sockets, simulated adapters and fixture model execution explicitly.
- Firewall helper basics: derive inbound native listener ports and exact peer addresses from the approved manifest edges; keep privileged mutations behind the protected local helper with its pinned-master signature/assignment/replay checks; journal each run's intent before mutation and retain exact rule instance snapshots; refuse adoption or removal of foreign/changed rules; restore cleanup only after helper restart and drain owned listeners before network restoration or authorization revocation; contract vectors and non-executing OS script parsing do not prove privileged firewall mutation, coordinator admission/renewal or physical connectivity — complete those production paths and record their acceptance separately before qualification.
- Firewall recovery: for changed/lost creation receipts use the loopback-only token-authenticated listener recovery API after the owned rank stops; bind local decisions to a fresh digest of durable ownership and current snapshots; `restore_owned` must retain changed/unknown rules while `keep_current` explicitly preserves rules and relinquishes local ownership without adopting them or releasing the cluster reservation; cover stale/foreign/live-rank denial, helper upgrade, persistence failure and cleanup retry; connect local recovery to the existing network dashboard for master and worker views with reviews bound to the selected run/manifest, restoration disabled for changed or unknown ownership, acknowledgement required before keeping rules, and decisions invalidated after refresh/close/failure; local recovery must not optimistically release the cluster reservation or restart inference.
- Helper I/O & cancellation: bound the local helper's queue, connection and framed I/O with one monotonic deadline; exercise isolated real Windows pipes with fragmented/oversized replies, stalled reads/writes, queue expiry and cancelled Prima waits; synchronous cancellation must target an owned thread handle and settle before the client lock opens; retry across call gaps and flush server replies before disconnect under the same deadline; never treat cancellation requests/timeouts as proof that a native mutation or rank stopped; preserve pending ownership and distinguish helper timeout from missing authorization in API/UI; keep fixture installation checks, real protected-service image rejection and privileged service/ACL/OS acceptance separate — do not weaken the protected identity check to make a fixture pass.
- Direct mode: on already elevated Windows manual adapters use the direct native policy child over private stdio without silently switching durable protected/direct ledgers; require native administrator refusal, mode negotiation, signed scope/rank commands, exact address observation and the same rule/watchdog ledger; direct mode must never mutate DHCP/addresses or restore them on restart; correlate replies with request UUIDs, preserve partial frames across read timeout and settle abandoned write/framing before another owner starts; guard local rank/port ownership before manual network cleanup, reconcile only unchanged signed assignments, and drain old scopes/rules before changing grants; require explicit typed empty scope/rule lists from the matching native grant before retirement — missing fields are not empty ownership; probe the replacement native owner before forwarding signed authorization; test owned child shutdown and separate controlled pipe replies/native signature checks from accepted elevated physical OS evidence.
- Admission: negotiate native listener capability before mutation and persist every attempted participant before sending admission; require all rank listeners before any child starts; hold the node admission lock through validation/spawn, then bind running leases; on lost replies, cancellation, expiry or reconstruction, stop only the matching owned rank before releasing its rules; narrow durable cleanup per acknowledgement and continue draining reachable nodes when one release fails.
- Async/lease discipline: cancelling an async wait does not cancel its blocking helper call — settle that call before acknowledging cleanup and generation-fence delayed admissions; keep node-local agent leases distinct from a privileged helper's independent watchdog (one does not prove cleanup when the other process is absent); preserve old-worker upgrade errors and never infer OS/helper availability from wire version; require fresh exact-contract signed native renewals — status reads cannot extend the helper watchdog; check expiry/non-revival and changed-rule retention with a timer independent of IPC activity; keep rule removal separate from owned process termination and cluster-release proof (it cannot establish that existing TCP traffic or generation stopped); validate privileged OS/service behavior separately.

## Full text (normative)

### Signed wire ownership

  Wire operations stay signed and bound to sender/run/authority. Preserve readiness,
  PID/start-token ownership, leases, cancellation, cleanup and recovery boundaries.


### Readiness acknowledgement

- Require owned post-initialization native rank acknowledgement for readiness;
  configured output and PID presence alone are insufficient. Rank zero also needs
  HTTP health. Exercise the CLI worker's actual manifest startup and zero-transformer
  coordinator path with controlled same-host model processes before claiming those
  boundaries work. Keep physical distributed acceptance separately deferred when
  the user requests it. Retry pending cancellation/cleanup and admission release
  without publishing idle or restarting a cancelled run early.
  After validated readiness, retain the acknowledgement in owned state and check
  PID/start token plus HTTP health; growing serving logs must not trigger the
  startup parser's size limit or require another native startup acknowledgement.


### Coordinator launch journaling & recovery

- Journal each coordinator launch intent before sending a signed start; a lost
  reply can still leave a child running. Persist the exact pending ranks and
  command authority, narrow them only on stop acknowledgement, and restore runs
  for cleanup after an agent restart. Test bounded recovery, cancellation during
  readiness publication, persistence/release failures and unrelated-process
  preservation. Reconstructed owners must not resume inference automatically.


### Network reservations vs RPC admission

- Keep Prima network reservations distinct from RPC admission: bind exact private
  assignments/edges, manifest and master authority, and obtain signed route/address
  diagnostics without starting RPC children or relays. Release only after every
  owned rank acknowledges idle; a dead master is insufficient. Verify normal
  prepared Run/Stop, HTTP readiness, cancellation and recovery over real signed
  channels with owned fixture processes. Label mapped sockets, simulated adapters
  and fixture model execution explicitly, and retain physical acceptance separately.


### Privileged firewall helper

- Derive inbound native listener ports and exact peer addresses from the approved
  manifest edges. Keep privileged mutations behind the protected local helper and
  its pinned-master signature/assignment/replay checks. Journal each run's intent
  before mutation and retain exact rule instance snapshots; refuse adoption or
  removal of foreign/changed rules. Restore cleanup only after helper restart and
  drain owned listeners before network restoration or authorization revocation.
  Contract vectors and non-executing OS script parsing do not prove privileged
  firewall mutation, coordinator admission/renewal or physical connectivity. Complete
  those production paths and record their acceptance separately before qualification.
  For changed/lost creation receipts, use the loopback-only token-authenticated
  listener recovery API after the owned rank stops. Bind local decisions to a fresh
  digest of durable ownership and current snapshots. `restore_owned` must retain
  changed/unknown rules; `keep_current` explicitly preserves rules and relinquishes
  local ownership without adopting them or releasing the cluster reservation.
  Cover stale/foreign/live-rank denial, helper upgrade, persistence failure and
  cleanup retry. Keep HTTP/native fixtures separate from privileged OS acceptance.
  Connect local recovery to the existing network dashboard for master and worker
  views. Bind reviews to the selected run/manifest, disable restoration for changed
  or unknown ownership, require acknowledgement before keeping rules and invalidate
  decisions after refresh/close/failure. Verify real browser interactions at narrow
  and wide CSS widths with production styles and viewport metadata. Local recovery
  must not optimistically release the cluster reservation or restart inference.
  Bound local helper queue, connection and framed I/O with one monotonic deadline.
  Exercise isolated real Windows pipes with fragmented/oversized replies, stalled
  reads/writes, queue expiry and cancelled Prima waits. Synchronous cancellation
  must target an owned thread handle and settle before the client lock opens;
  retry across call gaps and flush server replies before disconnect under the
  same deadline. Never treat cancellation requests/timeouts as proof that a native
  mutation or rank stopped. Preserve pending ownership and distinguish helper
  timeout from missing authorization in API/UI. Keep fixture installation checks,
  real protected-service image rejection and privileged service/ACL/OS acceptance
  separate; do not weaken the protected identity check to make a fixture pass.
  On already elevated Windows manual adapters, use the direct native policy child
  over private stdio; do not silently switch durable protected/direct ledgers.
  Require native administrator refusal, mode negotiation, signed scope/rank commands,
  exact address observation and the same rule/watchdog ledger. Direct mode must never
  mutate DHCP/addresses or restore them on restart. Correlate replies with request
  UUIDs, preserve partial frames across read timeout and settle abandoned write/framing
  before another owner starts. Guard local rank/port ownership before manual network
  cleanup, reconcile only unchanged signed assignments and drain old scopes/rules
  before changing grants. Require explicit typed empty scope/rule lists from the
  matching native grant before retirement; missing fields are not empty ownership.
  Probe the replacement native owner before forwarding signed authorization.
  Test owned child shutdown and separate controlled pipe
  replies/native signature checks from accepted elevated physical OS evidence.


### Admission, leases & watchdog discipline

- Negotiate native listener capability before mutation and persist every attempted
  participant before sending admission. Require all rank listeners before any child
  starts; hold the node admission lock through validation/spawn, then bind running
  leases. On lost replies, cancellation, expiry or reconstruction, stop only the
  matching owned rank before releasing its rules. Narrow durable cleanup per
  acknowledgement and continue draining reachable nodes when one release fails.
  Cancelling an async wait does not cancel its blocking helper call: settle that
  call before acknowledging cleanup, and generation-fence delayed admissions.
  Keep node-local agent leases distinct from a privileged helper's independent
  watchdog; one does not prove cleanup when the other process is absent. Preserve
  old-worker upgrade errors and never infer OS/helper availability from wire version.
  Require fresh exact-contract signed native renewals: status reads cannot extend
  the helper watchdog. Check expiry/non-revival and changed-rule retention with a
  timer independent of IPC activity. Keep rule removal separate from owned process
  termination and cluster-release proof; it cannot establish that existing TCP
  traffic or generation stopped. Validate privileged OS/service behavior separately.


---

**Related:** node-work reservation before model resolution/listener startup and resource-capability negotiation live in 10-profile-costs.md.
