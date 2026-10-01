# Managed Network Control API (proposed v1)

These are additive /v2 contracts to implement, not currently available routes. Existing /v1 and omitted run network selection retain their behavior. Network operations execute asynchronously under one per-cluster mutation coordinator.

## Operator Authorization

All routes below require the local node's existing API bearer token. Pairing/trust changes additionally require a loopback peer and valid same-origin request when Origin is supplied; never trust forwarded peer headers. Master cluster mutations require current role master. Worker adapter selection/pinning can be set only through that worker's local app. GET views exclude secrets, journal command text, private keys and join keys. Browser clients follow the existing local token retrieval flow and clear it on teardown; never put it in URLs.

| Method and path | Request / response |
|---|---|
| GET /v2/node/network-identity | Public node_id, public_key, key_id, paired_master fingerprint, current capability and local prerequisites. |
| PUT /v2/node/network-identity/peers/{node_id} | Local operator pins exact public_key/key_id, cluster_binding_id and expected master or worker role. Master generates its persistent cluster_binding_id locally; workers explicitly pin that displayed binding with the master fingerprint. Master may pin registered worker; worker may pin its enrolled current master. Replacing a key requires no active network/run and explicit replace=true. |
| GET /v2/node/network-adapters | Read-only inventory, stable identity, current OS identifier, link/speed/DHCP/permission facts, addresses and eligibility reasons. |
| PUT /v2/node/network-selection | adapter_id, expected_inventory_revision; persist local selection after identity check. No host mutation. |
| GET /v2/cluster/network-adapters | Master aggregated inventory for registered, paired workers and local node. Missing/stale/unsupported participants are explicit entries. |
| GET /v2/cluster/networks | Sanitized current network summaries and subnet proposals. A proposed subnet is not certified conflict-free until all-node prepare. |
| POST /v2/cluster/networks | request_id UUID, subnet optional, participants [{node_id, adapter_id, inventory_revision}], bridge_port default 50052, diagnostic_port default 18767. Local master is required exactly once; at least one worker. Returns 202 accepted operation/network. |
| GET /v2/cluster/networks/{network_id} | Full sanitized network and participant state with revision, assignment/verification facts, current operation and blocking runs. |
| POST /v2/cluster/networks/{network_id}/operations | request_id, expected_revision, action add/retry/stop; add supplies new participant selections, retry supplies optional corrected selections only for affected idle assignments. Returns 202. |
| GET /v2/cluster/network-operations/{operation_id} | Durable phase, participant results, deadline, error and cleanup_pending list. |
| POST /v2/cluster/network-operations/{operation_id}/cancel | request_id, expected_operation_revision. Fences prior commands and schedules rollback; returns 202. |
| GET /v2/node/network-cleanup | Local journal summaries requiring recovery/ownership review; loopback only. |
| POST /v2/node/network-cleanup/{journal_id}/resolve | Local-only operator decision release_ownership or confirm_owned with current snapshot digest. Confirm_owned authorizes removal of that exact reviewed delta only; stale snapshots conflict. Never accept a remote master assertion as proof of ambiguous ownership. |

Defaults and bounds: reject non-RFC1918, noncanonical IPv4 subnets, /31 and /32, duplicate nodes/adapters, network/broadcast addresses, over-capacity participant sets, bool-as-integer ports/revisions, out-of-range ports, oversized identifiers, unknown fields, and participant lists exceeding subnet capacity. Request bodies are <=64 KiB; public fingerprints are SHA-256 of exact 32-byte Ed25519 public keys. Node IDs follow the existing identifier contract, max128 ASCII characters. Subnet choice uses deterministic candidates and reports no_eligible_subnet if all proposals conflict.

## Response Shape

Accepted mutation:

```json
{"network_id":"UUID","operation_id":"UUID","state":"preparing","revision":1}
```

Status contains network_id, subnet, desired_state, observed_state, revision, current_operation_id, participants, blocking_runs, and last_updated_at. Each participant contains node_id, adapter_id, address/prefix, negotiated_speed_mbps (nullable), state, error (nullable), ownership, logical_rpc_endpoint, last_verification_at, verification_freshness and cleanup_status. Missing/unknown values remain null rather than zero/success. Local launch endpoints are exposed only in authenticated run diagnostics and labeled as loopback bridges.

Errors use `detail: {code, message, node_id?, network_id?, operation_id?, current_revision?, blocking_runs?}`. Stable codes include NOT_MASTER, UNPAIRED_IDENTITY, UNSUPPORTED_CAPABILITY, INVALID_ADAPTER, DHCP_ENABLED, UNKNOWN_LINK_SPEED, LINK_TOO_SLOW, INSUFFICIENT_PERMISSION, SUBNET_CONFLICT, ADDRESS_CONFLICT, MANAGEMENT_ROUTE_CONFLICT, STALE_REVISION, OPERATION_IN_PROGRESS, INFERENCE_ACTIVE, RPC_BUSY, RPC_PROTOCOL_UNSUPPORTED, TIMEOUT, OWNERSHIP_CONFLICT, CLEANUP_PENDING.

HTTP: 401 missing/invalid local token, 403 wrong origin/remote trust mutation, 404 unknown resource, 409 role/revision/concurrency/run conflict, 422 structural input error, 503 unavailable store/runtime. Accepted operations that encounter worker preparation failures remain queryable failed results; no node mutates. Application/verification failures report rollback results, including pending nodes. Return the original operation for duplicate request_id with the same normalized body; conflict for changed body.

## Inference Boundary

Add request-only `network_id: string | null` to plan/run request contracts. Omitted/null retains existing LAN/WireGuard behavior. A value must identify the current ready network with all required participants verified. Do not persist this field in model-settings payloads or silently choose a different network.

Planning uses a copied member inventory containing native master loopback bridge endpoints. Return additive `network: {network_id, revision, logical_endpoints, launch_endpoints, assignment_revisions}`. For managed runs, replace ensure_rpc_peers_reachable with paired-worker evidence/grants: loopback endpoints would incorrectly query master HTTP status. Retain that function for legacy LAN/WireGuard only. Revalidate under the admission lock for run; a plan response is not a lasting reservation. Reserve run_id and the current endpoint map before stopping an existing run. Run response and inference activity contain the selected network/run identity. Stop-inference releases network reservations only after confirmed process termination and relay drain.

Stop/add/retry affecting an active dependent network return INFERENCE_ACTIVE with run IDs. An ordinary legacy run is not interrupted by address setup; full verification waits for idle with RPC_BUSY. Replacing an active run with a managed selection requires fresh idle proof; otherwise return RPC_BUSY without stopping it and require explicit Stop Inference first. Membership host and LAN/WireGuard settings remain unchanged.

## Dashboard Boundary

Add optional sanitized `managed_networks` to existing status snapshots. Keep existing /v2/events topics, generation, acknowledgement and freshness behavior. A client without this capability hides the panel; missing/expired network evidence must not render ready. No separate dashboard socket or new viewer authentication mechanism is introduced. Detailed operation polling uses the resource routes while an operation is pending, with bounded cancellation on navigation.
