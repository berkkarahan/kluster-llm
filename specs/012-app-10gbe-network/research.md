# Research: App-Managed Private 10GbE Network

**Date**: 2026-10-01. Read-only repository inspection and primary documentation research; no host network changes, runtime tests or hardware claims.

## 1. Reuse existing platform primitives with an explicit lifecycle

**Decision**: Extract inspection and address operations from scripts/configure_10gbe.py into src/kllm/managed_network/platform, retaining the script's public arguments and facade. Add separate prepare/apply/observe/remove_owned operations, explicit assignments, and deadlines.

**Rationale**: Existing configure() mutates immediately and make_profile() uses fixed master/worker addresses; neither handles many workers or durable cleanup. Existing tests/test_configure_10gbe.py provide compatibility checks but not crash/cleanup coverage.

**Alternatives considered**: Invoke the script remotely per worker (fixed-address collisions, no ownership journal); duplicate its platform parsers (drift); reuse WireGuard setup (changes the requested dedicated Ethernet model).

## 2. Socket initiation and separate trust

**Decision**: Worker-initiated WebSocket on the master's existing control port, with master-pushed signed commands. Use separate /v2/cluster/network-control rather than viewer /v2/events. Make websockets an explicit client dependency and set bounded queues/frame limits and close deadlines.

**Rationale**: Existing cp_proxy.py forwards TCP and supports the same management path without another listening control port. The client library supplies bounded asynchronous connections and closure. [Official websockets client documentation](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html).

**Alternatives considered**: Raw custom TCP service (new transport/port); heartbeat polling (does not provide the requested master socket signal); piggyback dashboard frames (different viewer trust and state machine).

**Decision**: Use independently generated Ed25519 keys, public fingerprint pairing under local operator authority, signed role-specific challenge transcripts, persistent master term, per-start session UUID, per-socket nonces and frame sequencing. Use cryptography; no secret goes in frame bodies or URLs. This authenticates commands; it does not add encryption to the existing private management path. WSS or the existing WireGuard path may carry the same signed protocol when available.

**Rationale**: _require_key and apply_logging in src/kllm/agent/__init__.py use a shared join key. Any joined peer has that key; a session UUID or master IP is not independent master authentication. Ed25519 provides the needed signing and verification primitives. [Official cryptography documentation](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

**Alternatives considered**: Shared-key HMAC (every worker can forge master messages); silently pinning a key from a shared-key heartbeat (same enrollment weakness); configuring a new certificate authority/mTLS listener (more transport provisioning than this scoped command channel needs). Pairing is an explicit prerequisite before start, like adapter selection and local permission; identity changes require new local authorization.

## 3. Privileges and safe platform mutation

**Decision (guided revision)**: A standalone native helper receives one-time local OS approval after a displayed administrator notice. The normal agent stays under its original account. Only bounded, signed, dedicated network operations pass account-restricted IPC. The helper saves and restores owned DHCP/configuration changes; no remote command triggers elevation. The earlier privileged-agent-only policy is superseded for guided setup.

**Rationale**: Existing is_admin() already enforces this prerequisite. Remote triggers must not reach an unrestricted shell; platform commands are fixed argv or fixed PowerShell with validated values, bounded output, explicit errors and timeouts.

**Alternatives considered**: Passwordless general sudo or remote UAC (unsafe authority scope); globally persistent OS services (installation scope); silently ignoring permission errors (false readiness).

### Windows

**Decision**: Add the exact address using New-NetIPAddress with explicit ActiveStore, no gateway and SkipAsSource true; recheck DHCP immediately before mutation, wait for usable address state, and delete only the exact observed owned object. Source-bound master bridging avoids reliance on automatic source selection.

**Rationale**: Microsoft documents that address creation can disable DHCP and unspecified storage can persist configuration. SkipAsSource suppresses incidental source/DNS registration. Exact deletion avoids broad matches. [New-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/new-netipaddress?view=windowsserver2025-ps), [Remove-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/remove-netipaddress?view=windowsserver2025-ps).

**Alternatives considered**: PersistentStore, Set-NetIPInterface DHCP changes, broad Remove-NetIPAddress (break preservation contract).

### macOS

**Decision**: Runtime ifconfig alias add and exact -alias removal. Inspect the selected service's DHCP state, stable adapter identity, addresses and route selection. Preserve networksetup service profiles.

**Rationale**: Apple documents aliases as additional addresses and exact removal. Source-only ping is insufficient evidence of outgoing interface selection; use route observation and bound sockets. [Apple ifconfig manual](https://raw.githubusercontent.com/apple-oss-distributions/network_cmds/main/ifconfig.tproj/ifconfig.8), [Apple ping manual](https://raw.githubusercontent.com/apple-oss-distributions/network_cmds/main/ping.tproj/ping.8).

**Alternatives considered**: networksetup -setmanual/-setdhcp (changes durable service settings); source-bound ICMP alone (does not establish TCP path).

### Linux

**Decision**: Runtime ip address add/delete with exact address/prefix/device; preserve NetworkManager/systemd profiles. Correct existing DHCP detection: resolve active NetworkManager connection UUID, then inspect ipv4.method. Unsupported/ambiguous manager detection fails closed. Account for the connected route automatically created by address addition.

**Rationale**: Current _linux_dhcp() requests IP4.METHOD from device show instead of the documented connection property. Linux address APIs support exact deletion; kernel route lookup must be checked with source and actual selected device. [NetworkManager examples](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli-examples.html), [iproute2 address manual](https://raw.githubusercontent.com/iproute2/iproute2/main/man/man8/ip-address.8.in), [iproute2 route manual](https://raw.githubusercontent.com/iproute2/iproute2/main/man/man8/ip-route.8.in).

**Alternatives considered**: Inferring no DHCP from no leased address (not authoritative); nmcli profile mutation or ip address flush (changes unrelated/persistent state).

## 4. Durable ownership and conservative recovery

**Decision**: Use separate SQLite transactions for master assignments, worker intent and observed mutations, revision fences, cleanup tombstones and run reservations. Persist before OS mutation; inspect afterward. Existing matching addresses remain user-owned. Store boot/agent observation identifiers; an unobserved mutation window invalidates automatic deletion of an identical ambiguous replacement.

**Rationale**: ClusterState.save is atomic JSON but reset_transient_state and membership shrinking erase transient state; neither supplies multi-step mutation recovery. Platform address APIs lack a portable exclusive creator identity. Conservatively reporting ownership_conflict/cleanup_pending after downtime is a design inference from these limits, not a guarantee of detecting every external change.

**Alternatives considered**: In-memory ownership (lost on restart); member dictionaries (dropped on disconnect); treating the same IP as proof of ownership (can delete external replacement); a new general event-sourcing framework (unneeded).

## 5. Dedicated RPC without rerouting active sessions

**Decision**: Add native source-bound bridges on master and worker. Worker listens on the dedicated address and forwards to its current same-host RPC listener; master offers loopback launch endpoints and binds remote connections to its dedicated address. Keep logical remote endpoints and actual launch endpoints separate.

**Rationale**: crates/kllm-native/src/process.rs accepts one worker --host; runtime.py tracks one GPU child. Windows secondary addresses should not implicitly become normal source addresses. socket2 provides portable bind and connect operations. [Official socket2 API](https://docs.rs/socket2/latest/socket2/struct.Socket.html).

**Alternatives considered**: Rebind worker RPC (interrupts active sessions); second RPC GPU process (duplicate resources); Python byte forwarding (unmeasured high-bandwidth overhead); direct llama remote endpoints (cannot consistently force the selected source without modifying upstream).

## 6. Safe readiness and active-run checks

**Decision**: A native bounded read-only probe performs the pinned runtime's valid HELLO and device-count exchange through the bridges while the worker is idle; close it before launch. Never use raw connect/close or a cached upstream dispatcher. During an active run, use signed dedicated challenges, relay stream health and unchanged RPC child identity; setup without prior evidence waits for idle.

**Rationale**: run_planning.py already prohibits generic RPC probes. The pinned implementation handles accepted clients synchronously and provides read-only handshake/enumeration operations. The valid probe must not occupy the server when inference starts. [Pinned llama.cpp RPC source](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.5.0/ggml/src/ggml-rpc/ggml-rpc.cpp), [pinned transport definitions](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.5.0/ggml/src/ggml-rpc/transport.h).

**Alternatives considered**: PID-only HTTP status (not path proof); raw port scanning (unsafe); persistent device-enumeration session (blocks inference); claiming link speed as throughput (unsupported).

## 7. Integration and compatibility

**Decision**: Separate network state from membership host/lan_host; create a copied placement inventory with bridge endpoints only for an explicit per-run selection. Hold one admission/stop serialization boundary, persist run reservations before replacing a previous run, and release after confirmed child termination and stream drain.

**Rationale**: run_launch consumes planned endpoint strings, and membership can clear last_plan. A separate run reservation is needed to block stop while the selected network is in use. Existing dashboard status events already carry cluster snapshots, so a sanitized optional managed_networks field avoids another viewer protocol.

**Alternatives considered**: Overwriting membership host globally (reroutes legacy callers); storing selection in persistent model defaults (stale network identity); relying on last_plan for stop protection (not durable enough).

## Resolution and Evidence Limits

All technical decisions have a chosen approach and alternatives; no unresolved product clarification remains. Hardware timings, relay throughput, socket permissions, driver identity persistence, and clean protocol-probe behavior are acceptance obligations, not verified results. Any runtime/tag/platform unable to meet them must report unsupported or unverified.

Post-design review resolved admission lease grant/renew/release/drain schemas, explicit cluster binding pairing, bypass of the legacy loopback HTTP reachability gate, and TCP-only evidence for both runtime ends. Platform-lifecycle.md defines IPv4 conflict evidence and authoritative event continuity with conservative uncertainty.

Missing workflow.md and architecture_constitution.md are documented in plan.md. No durable-memory capture or shared import was performed: there is no configured shared project profile/callable sync tool, and unimplemented choices remain feature-local. No host settings were changed during research.

## Approved beginner workflow revision

Automatic mutual bootstrap is limited to initial identity establishment for joined nodes. It cannot replace pins or authorize the privileged helper. Grant installation requires explicit local acknowledgment and OS approval. A sole safe adapter and nonoverlapping subnet are selected automatically; ambiguity asks a small adapter question. Existing technical controls remain in Advanced details.

Configuration policy now permits owned dedicated DHCP changes: temporary NetworkManager clone, runtime networkd override, dedicated macOS service DHCP/off transition, and Windows ActiveStore changes. Original profiles and unrelated adapter settings are preserved. Windows firewall automation is confined to signed private peers, local dedicated address, interface and TCP ports, with owned restoration. Prior alternatives rejecting every DHCP transition apply to the compatibility manual flow, not the approved guided policy.
