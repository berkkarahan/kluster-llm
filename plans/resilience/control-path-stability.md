# Control-path stability

> **Created:** 2026-10-08
> **Status:** Planned
> **Repo / scope:** kluster-llm — worker heartbeats, the signed network channel, and RPC lease timing

## Conventions captured at creation

Membership ticks every 2 seconds (`HEARTBEAT_INTERVAL_S` in `src/kllm/membership.py`). The master drops a worker whose `last_seen` is older than 20 seconds (`LIVENESS_TIMEOUT_S` in `src/kllm/membership_state.py`, applied by `sweep_liveness`). A worker heartbeat is delivered by `MembershipHeartbeatService.post_heartbeat`. One transport failure increments a streak; the master is marked unreachable only at `_UNREACHABLE_AFTER = 2`. A successful heartbeat clears the streak. `accepted is False` or a snapshot that omits this node still leaves immediately. The service instance is reused from `kllm.agent` so the streak survives the next tick, and its callables are resolved at call time so tests can patch façade names.

The signed channel is `NetworkChannelHub.client_loop` in `src/kllm/agent/services/network_channel.py`. The worker dials `/v2/cluster/network-control` with a 20 second ping interval and a 20 second pong timeout. `reconnect_delay` returns 1 second after a session that came up, and otherwise doubles from a 0.5 second seed up to 8 seconds. The loop multiplies that by full jitter (`0.5 + random/2`) so workers do not dial together. `_handshake_refused` is true for `IdentityError` or close code 1008; only that sets `reassert`, which retries join-secret bootstrap at `BOOTSTRAP_RETRY_S` (15 seconds). A plain `OSError` or timeout reconnects without re-pairing. `ChannelSession.send` closes the socket with code 1001 when a send exceeds the 5 second `SocketWire` budget, and it drops the socket only after releasing `send_lock` so the pump cannot deadlock on that lock. `SocketWire.receive` still has no idle timeout. WebSocket ping frames are not application text, so the master's `pump` does not observe client pings.

RPC ownership lasts `LEASE_SECONDS = 60` in `src/kllm/agent/services/worker_rpc.py`. The master renews from `RpcRunCoordinator.monitor` about every 5 seconds. `observe_health` reloads immediately when the local llama-server is dead or `runtime_error` is set, and only after `RECOVERY_GRACE_POLLS = 2` when a worker renewal or managed-network ready check fails. Optional WireGuard peers with an endpoint get `PersistentKeepalive = 25` from `build_wg_conf` in `src/kllm/wireguard_config.py`. The managed private network has no equivalent keepalive setting in `src/kllm/managed_network/`.

RPC port 50052 stays unauthenticated and must not be exposed publicly. Tests patch `kllm.agent` and pass collaborators into services. They use fake sockets and clocks, not a live cluster.

## Intended change

Make a black-holed master-side channel fail on its own, and make the managed private network tolerate NAT silence the way the optional WireGuard overlay already does. Do not shorten the 60 second RPC lease or the two-poll reload grace to "fail faster." Those waits exist so a stall does not kill a live `rpc-server` and reload the model.

Today the worker notices a dead master socket because it sends pings. The master notices a dead worker only when it next sends a command, or when TCP itself gives up. `receive` on the server session blocks on the next text frame. An idle signed channel can therefore look connected while the worker is gone, and the next `prepare_runtime` or `renew` pays the 5 second send timeout before `send` drops it.

## Phased steps

1. Add a server-side liveness task beside `ChannelSession.pump` that does not inject a signed frame. Prefer an ASGI ping on the Starlette socket if the installed server honors `websocket.ping`. If it does not, document that and stop; do not invent an unsigned application frame, because `ChannelFence.receive` rejects anything that is not a signed command or runtime command.
2. If server pings exist, close the session when a pong is missing for one interval plus one timeout, using the same drop path as a send timeout (mark closed, close the socket, let `pump` fail pending futures). A regression should use a fake socket that never pongs and assert the session leaves `NetworkChannelHub.sessions`. A socket that answers pings must stay up with no text frames.
3. Keep client `ping_interval=20` and `ping_timeout=20` unless a measured event-loop stall shows 20 seconds is still short enough to cause false reconnects. Do not return to a 2 second pong deadline; that was dropped because a brief loop stall reconciled in-flight commands.
4. For the managed private network, find where peer configuration is rendered (not `build_wg_conf`, which is the optional overlay) and add a keepalive only for peers reached through NAT. A directly attached 10GbE link does not need it. Prove the rendered config with a unit fixture. Do not claim the link is stable without a later ping or iperf on that hardware.
5. Align the operator docs if the server ping interval changes the time a dead worker stays listed as channel-connected. `docs/cli.md` and `docs/troubleshooting.md` already describe the 60 second lease, the two-sample reload grace, and handshake-only re-pairing.

## Open questions

- Does the pinned Uvicorn/Starlette actually deliver application-initiated pings, or only answer client pings? That decides whether step 1 is a small task or a protocol-compatible substitute.
- Should a master-side channel that has been silent for longer than the RPC lease be treated as disconnected in `RuntimeControlService.status` even before the socket errors? Today `channel.connected` follows `sessions` membership.
- Is PersistentKeepalive on the managed network redundant when both peers already have a stable L2 path, and would it hide a routing failure the diagnostic probe is supposed to see?
