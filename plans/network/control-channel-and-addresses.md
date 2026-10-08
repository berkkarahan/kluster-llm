# Control channel and address stability

> **Created:** 2026-10-08
> **Status:** Planned
> **Repo / scope:** kluster-llm — signed network channel, control-plane proxy, RPC bind

## Conventions captured at creation

- `src/kllm/agent/services/network_channel.py` `NetworkChannelHub.client_loop` dials the master websocket. As of this date the dial uses `ping_interval=20` and `ping_timeout=20`. `reconnect_delay` grows from 1s to 8s after a failed dial and returns to 1s after a session that completed its handshake. Jitter is `0.5 + 0.5 * random()`. Pairing is re-asserted only when `_handshake_refused` is true (an `IdentityError` or websocket close 1008). A dropped socket does not re-bootstrap.
- `ChannelSession.send` closes the session with code 1001 when a frame send times out, so a peer that stops reading does not keep the signed session.
- The master `server` path accepts an ASGI websocket and does not send its own pings. A half-open worker is replaced only when that worker dials again (`sessions.pop` during handshake).
- Worker RPC ownership lasts `LEASE_SECONDS` (60) in `src/kllm/agent/services/worker_rpc.py`. The master renews from `RpcRunCoordinator.monitor` about every 5 seconds. One missed worker renewal waits (`RECOVERY_GRACE_POLLS` in `src/kllm/agent/services/rpc_runs.py`); a dead local llama-server recovers immediately.
- `src/kllm/cp_proxy.py` relays blocked macOS control-plane sockets. Each direction uses `select` with a 120 second idle break. `src/kllm/http_clients.py` keeps control-plane and inference pools alive for 120 seconds (`keepalive_expiry`).
- `docs/engine-decision.md` still says DHCP mid-run can collapse the RPC state machine and that `rpc-server` must stay on a private address. Managed-network assignment is the preferred inference address once applied.
- Membership heartbeats run every `HEARTBEAT_INTERVAL_S` (2) in `src/kllm/membership.py`. `MembershipHeartbeatService` marks the master unreachable after `_UNREACHABLE_AFTER` (2) consecutive failures and resets that streak when the `ClusterState` object is replaced.

## Intended change

Close the remaining ways a live cluster drops RPC or the signed channel even though membership is fine.

1. **Master-side liveness.** The worker already pings. The master session can sit in `pump` until TCP gives up if the worker disappears without a FIN. Add an idle deadline on the master receive path, longer than the worker ping interval, and close that session so the next worker dial replaces it. Do not treat ordinary command silence during a long `to_thread` handler as idle if bytes are still flowing.
2. **Proxy idle versus long requests.** A control-plane request that produces no bytes for 120 seconds is cut by the macOS relay while the httpx pool may still hold the socket. Either send a one-byte heartbeat on idle, or make the relay idle longer than `keepalive_expiry` and drop the pooled client when the relay closes. Cover this with a local socket pair, not a real LAN.
3. **Address change before the next run.** When the worker's `rpc_bind` or managed-network address changes under an active or idle membership, surface it on the master and refuse to reuse the previous RPC endpoint. Do not silently retarget a running stream (the current degraded-network rule).

## Phases

1. Add the master idle close and a unit test that a silent ASGI socket is removed from `sessions` while an active receive is not.
2. Add a relay test that an idle pipe and a slow response do what the chosen timeout says, and that the next control request does not reuse a closed socket.
3. Record the observed bind and assignment on each heartbeat. If they differ from the endpoint stored for the member, set a warning and exclude that endpoint from the next plan until the operator refreshes. Document the DHCP case in `docs/troubleshooting.md` next to the existing private-address paragraph.

## Open questions

- Is 45 seconds of master-side silence the right idle close, or should the master send protocol pings so the deadline matches the worker's 20 second pong timeout?
- Should an address change during an active run mark the run `recovering`, or only block the next plan?
