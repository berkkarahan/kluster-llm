# Agent Network Command Socket (managed-network-v1)

## Transport and Trust Establishment

Workers initiate WebSocket connections to /v2/cluster/network-control at their existing master control base. The endpoint is distinct from dashboard /v2/events. Use the existing control proxy where required; absence of browser Origin is permitted for signed node clients. A browser Origin is rejected on this node endpoint. No bearer/join/private key is included in URLs or application frames.

Master and worker public keys must already be pinned by local operator controls. Enrollment using the shared join key alone never grants managed-network authority. A changed key, cluster binding or master node identity requires explicit local re-pairing. The worker's current cluster role/master must agree with its paired authority before every mutation.

Handshake: master sends a signed challenge with master node/key IDs, cluster_binding_id, persistent master_term, master_session_id and 32-byte random nonce. Worker checks its pinned master key, then returns a signed transcript containing both identities, master challenge, worker nonce and node boot/agent identity. Master checks the pinned worker key and current enrollment, then signs session_accept binding both nonces, identities, role and channel_id. Complete within 5s; record a higher accepted master term/session durably before enabling commands, and fence prior sockets/tasks. Reject lower terms or a different session at the same term. A valid reconnect at the same term/session gets new nonces and channel ID.

## Signed Representation

Each text frame is `{payload: string, signature: base64url}`. Ed25519 signs the exact UTF-8 payload bytes preceded by the fixed domain separator `kllm-managed-network-v1` plus a NUL byte. Verify the bytes before parsing their JSON; do not reserialize for verification. Parsed payload rejects duplicate keys, unknown fields, non-ASCII IDs, nonfinite numbers, unsafe counters, invalid enum combinations and excessive nesting. Signature is exactly 64 decoded bytes. Max frame 64 KiB, handshake 8 KiB, message queue 16, one active node channel. Close codes: 1008 auth/protocol failure, 1009 oversize, 1011 internal failure. Sanitized errors never echo raw hostile payloads.

A command envelope contains:

| Field | Rule |
|---|---|
| version, type | 1, command/result/challenge/echo/snapshot/ack according to role; distinct handshake schemas. |
| cluster_binding_id, master_node_id, master_key_id | Exact paired authority. |
| master_term, master_session_id, channel_id | Exact current signed session/channel. |
| sender_node_id, target_node_id | Current paired sender and intended receiver. |
| sequence | Strictly increasing per direction per channel; no replay. |
| network_id, operation_id | UUIDs for lifecycle frames. |
| operation_revision, phase_sequence | Increasing operation fence and ordered phase identifier. |
| phase | inspect, prepare, apply, verify, reconcile, rollback, cleanup, admit_probe, admit_run, renew_admission, release_admission; validated against operation state. |
| assignment_revision, expected_local_revision | Target assignment and local optimistic fence. |
| ttl_ms | Remaining validity, >0 and <=60,000; receiver uses monotonic receipt deadline. |
| body | Phase-specific validated selection/assignment/result data; no arbitrary command strings. |

Network and operation revisions are different: network revision changes as observed status is published; operation_revision is fixed for a committed operation and advances on cancel/supersession. Per-phase expected_local_revision comes from that worker's last accepted result. A newly authenticated worker asks for a signed desired-state snapshot; it cannot revive old commands from a disconnected session.

## Ordered Lifecycle

1. inspect is read-only and reports inventory/capability/trust/prerequisites.
2. prepare receives full network participants/address plan, its adapter identity and assignment, and its master dedicated endpoint. It checks local and route conflicts, permissions, link, DHCP and current inventory; persists the prepared snapshot digest without OS mutation.
3. apply is accepted only for the same prepared assignment/digest while the deadline and session remain valid. Revalidate changing facts, journal intent, apply exactly the address delta, then observe. All-node preparation must precede any apply command. Return ownership and exact observed facts.
4. verify starts scoped bridges/diagnostics and collects signed dedicated path evidence plus idle RPC evidence. RPC busy reports configured/verification_pending and performs no competing connection.
5. rollback/cleanup remove only proved operation-owned changes. A newer assignment or ownership conflict vetoes removal. Mark absent resources as removed. Report unreachable/uncertain cleanup explicitly.
6. reconcile exchanges desired state, current fences and local journal state after restart or reconnect. It never accepts an incoming ready flag as fresh local evidence.

The idempotency key is (authority, network_id, operation_id, operation_revision, node_id, phase_sequence). Persist its body digest and result before acknowledgement. A retransmission on a new socket sequence returns the stored result if the body matches, or rejects altered content. Recheck observable state before repeating effects; never execute a second add/remove merely because an ACK was lost. Preserve terminal operation fences after cleanup so delayed frames cannot recreate an address.

Worker result includes assignment revision, local revision, phase, status, observed snapshot digest, bounded reason, child/bridge identities and journal references. Results are signed by the paired worker and matched to the operation/phase; a shared-key telemetry message cannot overwrite network results. Local master operations use the same executor and journal without a self-socket.

## Authority, Deadlines and Cancellation

A mutation executor checks the authority fence again immediately before every OS command. Serialize identity change, session supersession, operation cancellation and execution locally; abort scheduling old work on disconnect. Cancellation cannot undo an in-flight OS syscall: observe its result, record intent outcome, and reconcile/roll back before reporting cancellation complete. No distributed atomicity claim is made.

On management loss, invalidate ready immediately, stop accepting new network connections, preserve existing admitted streams, and report uncertainty. A worker autonomously cleans up an uncommitted partial operation after its signed deadline when ownership is proven and no dependent stream exists. Committed active assignments are not removed solely because a keepalive is missing. Reconnect resumes cleanup using persisted desired state/fences; observation gaps become ownership conflicts as defined in data-model.md.

Application keepalive every 5s with 2s bounded response; does not renew dedicated verification. Reconnect backoff capped at 5s when cleanup is pending. Prepare/apply/verify operation budgets are 15/20/20s plus 5s publish, with a separate 60s cleanup budget. Socket errors do not change desired state implicitly.

## Dedicated Diagnostic Challenge

Master and workers bind a signed diagnostic service only to assigned private addresses, default port 18767. Each challenge/reply is <=8 KiB and includes the current network/assignment/session, sender/target identities, a fresh nonce and 2s validity; the reply signs that complete request context. Verify source address, expected adapter/route, nonce and signature in both directions. Do not reuse an old reply or treat ICMP as this proof. A port collision or blocked diagnostic path leaves verification failed. These listeners carry no host mutation commands.

## Admission Lease Messages

Admission phases carry lease_id UUID, generation, kind probe/run, run_id (run only), network/assignment revisions, expected worker/master relay instance IDs, expected RPC child identity and receive-relative ttl_ms <=15,000. Grant and acknowledgement bind these fields and the signed session/channel. Workers persist grants before enabling the exact native listener generation. Probe/run admission is exclusive with legacy upstream ownership; unknown/busy ownership returns RPC_BUSY.

Order: persist master reservation; obtain all worker admit_probe ACKs; probe idle workers; release_admission and wait for drained ACKs; obtain all admit_run ACKs; launch with frozen master bridge endpoints. Any failure releases other grants without stopping a previous run. Renew every 5s with increasing generation. Expiration forbids new connections but does not kill admitted streams. Release closes future accepts and reports drained only after upstream/relay streams actually close. Lost ACKs are reconciled by signed snapshots, not presumed successful.

A new managed selection/replacement without fresh idle evidence returns RPC_BUSY and preserves the old run. Active-run health cannot substitute for an idle protocol probe; the operator stops the old run through existing controls first.
