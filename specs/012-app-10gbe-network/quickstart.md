# Validation Guide: App-Managed Private 10GbE Network

The routes, network panel, explicit inference selector, reservations and local ownership review are implemented. Local verification separates real HTTP/WebSocket/SQLite and native socket checks from controlled hardware/runtime facts. The physical acceptance journey below remains pending on this machine.

## Minimal Critical Verification Scope

The user's task-generation instruction is to keep end-to-end checks and some tests minimal, checking only critical things. Follow [tasks.md](tasks.md)'s six verification tasks: authority/preparation refusal, the shared setup/admission/recovery integration fixture, compact native transport checks and one serial browser journey. Reuse existing checks rather than add a suite for every row below. The failure matrix is a diagnostic/reference menu; it is not an exhaustive automated-testing requirement. The final physical journey proves real-model distributed inference and safe cleanup, with one loss/recovery drill.

## Prerequisites

- Follow [agent install guide](../../docs/agent-install.md) on every participating machine. Use the committed stable llama-release and run `python scripts/install.py --check` on each node; install recorded TCP-only binaries with `uv run python scripts/install.py --llama-only --rpc-tcp-only` and restart the agents/workers. An older running RPC process is ineligible even when files on disk have been replaced.
- Dedicated connected physical Ethernet adapters, reported negotiated speed >=10 Gbps, readable DHCP configuration, and ordinary cluster management connectivity on a separate unaffected path.
- Original-account agents with the supported native helper binary. Read the administrator notice and enable access locally on each computer through its OS approval prompt. Do not move agent credentials to a root/admin account.
- One master and two workers for the primary acceptance, connected through a dedicated switch; a direct cable suffices for the one-worker subset. Windows, Linux and macOS hardware coverage must be recorded, including one mixed-platform pair.
- Automatically bootstrapped/pinned node fingerprints and protected local administrator grants. Workers must support managed-network-v2; unsupported nodes remain legacy cluster members but cannot participate.
- Stop inference for initial full readiness/protocol verification. Separately exercise setup while a legacy run is active to verify explicit pending status and preserved connections.

## Baseline and Build Checks

Repository commands that already exist:

```bash
uv run python scripts/build.py --skip-ui --skip-llama --skip-wireguard --skip-toolchains
uv run python -m pytest -q
uv run ruff check .
uv run mypy src
npm --prefix dashboard test
npm --prefix dashboard run check
npm --prefix dashboard run build
cargo test --manifest-path crates/kllm-native/Cargo.toml
git diff --check
```

The build is required after dependency/native changes. Root make test remains sequential; use the dashboard runner's existing browser isolation. If a native test requires platform linker configuration, follow the existing build guide and record the command/environment; do not count a failed or skipped check as passed. Targeted tests may be used while implementing, then run the relevant broader integration suite.

Before physical setup, save each node's selected adapter addresses, default routes, DNS/DHCP facts, route to the master, current RPC PID/start identity, and installed tag. Available read-only commands:

| OS | Snapshot commands |
|---|---|
| Linux | `ip -j -4 address show`, `ip -j -4 route show table all`; inspect active manager DHCP configuration and DNS separately. |
| macOS | `ifconfig`, `netstat -rn -f inet`, `scutil --dns`, `networksetup -getinfo '<selected service>'`. |
| Windows (PowerShell) | `Get-NetAdapter`, `Get-NetIPAddress -AddressFamily IPv4`, `Get-NetRoute -AddressFamily IPv4`, `Get-DnsClientServerAddress`, `Get-NetIPInterface -AddressFamily IPv4`. |

Use timestamps and retain output in a test evidence directory outside tracked source. Do not publish tokens/join keys/private identity files. Verify these commands on the host OS version before relying on parsed output.

## End-to-End Operator Scenario

1. Start/join an ordinary cluster using existing controls and verify every management connection. Open each computer's local app, read the administrator notice and enable access through OS approval. Joined updated nodes bootstrap initial signing identities automatically. Denied authorization or changed identity must show a prerequisite failure before networking mutations.
2. On the master overview, open Private 10GbE network. Select the master and two workers; a sole safe adapter and conflict-checked subnet are chosen automatically. Select an adapter only for ambiguous rows. Click Set up private network once.
3. Expect preparing, applying, verifying, then ready within 60s on an idle eligible cluster. Query the accepted operation via [control API](contracts/control-api.md); every node must have a distinct address and current bidirectional/protocol evidence. Confirm management/default route/DNS profiles remain unchanged, dedicated DHCP is disabled during setup, and original settings return after stop.
4. On Run, explicitly choose this network and a locally available model that fits the cluster. Start distributed inference, then perform a real generation through existing inference controls. Inspect run diagnostics: logical endpoints are dedicated worker addresses; actual launch endpoints are master loopback bridges; bridge outbound sources equal the assigned master dedicated address. Confirm worker RPC PID/start identity remains the original one and bridge traffic traverses the selected interfaces.
5. Attempt Stop Network while the run is active: expect INFERENCE_ACTIVE and the run ID; inference continues. Stop inference through existing controls, then Stop Network. Reachable nodes must show stopped and app-owned temporary settings removed within 60s; pre-existing snapshot facts must match.
6. A run with no network selection must still use the pre-existing LAN/WireGuard workflow. Run the manual configure_10gbe script's existing regression checks to verify its arguments and refusal rules remain compatible.

## Failure and Recovery Matrix

| Scenario | Required outcome / spec coverage |
|---|---|
| Slow/unknown speed, unreadable DHCP configuration, authorization denied, adapter absent | All-node preparation fails before any network delta; affected node and actionable reason (SC-002). |
| Conflicting VPN/route/subnet, too few addresses, reused adapter index | Explicit preparation failure; no mutation and no management-path loss. |
| Ten retries, lost ACK, duplicate or altered-body request ID | One assignment per node, no duplicate address; changed-body conflict (SC-003). |
| Invalid signature, shared-key peer impersonation, stale term/session, replay | Zero host changes; persisted fencing survives restart (SC-007). |
| Crash before add, after add before confirmation, during removal | Journal recovery never trusts stale ready; absent owned changes confirmed, ambiguous ownership retained, no broad deletion. |
| User-owned identical address before start; externally changed/replaced setting | Participate only if eligible; never remove user-owned or ambiguous replacement; show ownership conflict. |
| Node disconnect during prepare/apply/cancel | Prepare has no mutations; partial apply rollback results include cleanup pending. On reconnect, safe cleanup <=60s when prerequisites exist (SC-006). |
| Cable loss after ready; firewall blocks dedicated diagnostics or RPC | Degraded/unverified <=15s, no new selected-network run or hidden fallback, ordinary management intact (SC-005). |
| Legacy inference active during start | Address setup preserves current RPC connection; shows verification_pending/RPC_BUSY until idle; no extra upstream protocol probe. |
| Probe during idle and subsequent managed inference | Valid probe returns and disconnects, original RPC listener remains usable, native bridge drains probe before admission. |
| Agent restart or reboot after setup | Ready invalidated; OS address re-observed; disappeared temporary settings can be recreated by explicit retry. Managed active run restart handling follows native-network lifecycle contract. |
| IPv4 collision during ARP preparation or OS address settling | No conflicting address ready; known preflight conflicts cause no mutations; application races trigger rollback. |
| Observer loss or identical external delete/re-add between polls | Ownership uncertain, no automatic deletion, local review required. |
| Concurrent start/add/retry/stop/run; leave/eviction/role change | Shared serialization rejects conflicts, run references retained, pending cleanup survives membership resets. |
| Unsupported/unknown runtime protocol or RDMA transport | Unsupported or unverified before admission; no claim that a generic TCP bridge supports negotiated RDMA. |

Use isolated temporary databases, controlled platform runners and deterministic clocks in the selected critical cases, plus real local sockets/native streams and actual child identity checks. Do not turn every matrix row into additional unit, API and browser tests. Native tests include source binding, exact-address/allowlist refusal, byte preservation, backpressure, half-close, failure cleanup, handle identity and cancellation. Browser checks exercise prerequisite errors, operation progress, expired freshness, blocked stop and explicit run selection.

## Physical Evidence and Timing

For each hardware run record OS/version, adapter/driver, negotiated speed, master/worker roles, ordinary management path, backend, model and llama tag, exact test command/action, timestamps, before/after snapshots, observed socket sources/interfaces and outcomes. A macOS-only mock run does not satisfy mixed-platform acceptance. Verify BOTH runtime ends are TCP-only; zero-capability idle probing alone is insufficient.

SC-001 requires one master plus two workers on idle eligible hardware; active-run waits are reported separately. SC-004 requires a real multi-node generation and endpoint/path evidence, not just a successful plan. For SC-006, distinguish cleanup proven, absent after reboot, and ambiguous ownership requiring local review; never mark pending as success. Review local cleanup through the contract's loopback resolve control only after inspecting the exact current resource.

Measure dedicated transfer/bridge and inference latency against the same model/backend/hardware and workload over the previous path. Optional iperf3 measures the physical link separately; it does not validate RPC correctness or claim a guaranteed 10GbE application rate. No improvement target is claimed by this plan. Publish outstanding hardware checks and failed/unsupported configurations with exact follow-up steps.

## Local implementation checks (2026-10-01)

- Five compact critical Python cases cover authority/replay, durable restart uncertainty, preparation refusal, frozen run reservations/blocked stop, and exact owned-versus-user/ambiguous cleanup with stale review refusal.
- One actual three-agent refusal/retry/cancel/stop test exercises the installed RDMA-enabled runtime's safe refusal before host mutation.
- One shared native-agent journey covers ready setup, selected inference through actual native relays, original RPC PID/start identities, frozen endpoints, active health/lease renewal without a competing RPC probe, blocked stop, actual LAN behavior with omitted selection, RPC_BUSY preserving a legacy run, worker restart after partial application, address preservation after observation loss, local exact review and distributed cleanup.
- **Controlled boundaries in that native lab**: dedicated adapters/aliases, OS routes/address observations and permissions are simulated; logical addresses map onto one real private host IP plus separate socket ports. TCP-only RPC/GPU binaries, process socket inventory and model workload use controlled serial RPC/inference subprocesses. HTTP/WebSocket/signatures/SQLite, native relays/probes, native process identity, signed diagnostic sockets and stream drain are actual. Production code has no lab mapping or eligibility bypass.
- Two native real-socket cases additionally prove selected source, byte forwarding/half-close, quiet-stream survival, same-generation renewal, bind/allowlist refusal, invalidated observation, and valid serial RPC probe release followed by another connection.
- One serial Chrome journey passes local pairing/selection -> cancelled setup -> retry -> add participant -> selected run -> blocked stop -> inference stop -> cleanup. HTTP platform/runtime/status receipts are controlled; app requests/actions are actual.
- `make test`: 697 Python passed, 11 existing skips; all dashboard tests (51 non-browser plus four serial cases), 41 native tests and Svelte check passed. Supported rebuild/editable refresh and dashboard production build pass. Linux/Windows native compile checks pass; these are compilation evidence, not OS lifecycle acceptance.
- Scoped Ruff and mypy pass (23 changed modules). Full-package `uv run mypy` retains six existing errors in src/kllm/log_sink.py:73,74,137; src/kllm/connect.py:68; and src/kllm/commands/connect.py:46 (two argument errors). Those unrelated modules were not changed.

```bash
uv run python -m pytest -q -n 0 tests/test_managed_network_critical.py tests/test_managed_network_e2e.py tests/test_configure_10gbe.py tests/test_install_script.py tests/test_inference_overrides.py tests/test_dashboard_api.py
PYO3_PYTHON="$PWD/.venv/bin/python" cargo test --manifest-path crates/kllm-native/Cargo.toml network::tests -- --nocapture
node --experimental-strip-types --test dashboard/tests/managed-network.test.mjs
uv run python scripts/build.py --skip-ui --skip-llama --skip-wireguard --skip-toolchains
uv sync --reinstall-package kllm
make test
```

T011/T023/T030 are complete with the documented controlled OS/runtime boundaries; only T039 physical acceptance remains pending. This machine has no isolated three-node 10GbE lab, Windows/Linux runtime hosts or privileged temporary-address test environment. Do not mark SC-001–SC-008, physical timing, real-model generation, ARP/DAD behavior, cable-loss or mixed-OS cleanup passed from local tests.

## Guided revision validation (2026-10-01)

New tests cover mutual bootstrap proofs, immutable pins, single-use commit, initial administrator disclosure and local-only authorization, automatic safe adapter/subnet choice across all visible interfaces, stable guided retries, and a real three-agent automatic-pairing/setup/ready/stop journey. Native helper checks cover restricted schema, valid/tampered/wrong-node/stale signed authority, durable revocation, pending-cleanup refusal and actual framed local socket IPC with the size bound. The browser journey covers notice acknowledgment before the OS action and guided setup/restoration before exercising retained Advanced controls.

The three-agent lab controls hardware/OS/runtime facts; it does not elevate or change host networking. The standalone helper links only protected system libraries on macOS, with no Python library. Linux/Windows checks compile the helper for their targets; OS install, permissions, DHCP/firewall mutation/restoration, reboot, physical adapter events and mixed-machine model generation remain unverified.

```bash
uv run python -m pytest -q tests/test_guided_network.py tests/test_managed_network_critical.py tests/test_managed_network_e2e.py tests/test_configure_10gbe.py tests/test_install_script.py
cargo test --manifest-path crates/kllm-native/Cargo.toml --no-default-features --features network-helper --bin kllm-network-helper
uv run python scripts/build.py --skip-llama --skip-wireguard --skip-toolchains
make test
```

The current master has a connected 1Gbps Ethernet link and no eligible dedicated 10GbE adapter. Do not perform physical setup or count it as accepted on this topology until the hardware and per-machine runtime/administrator requirements are met.

Latest guided validation: full Python suite 716 passed / 11 existing skips, 111 focused network/install checks, 42 native library tests plus three standalone-helper tests, scoped Ruff/mypy and Svelte checks, supported native/helper/dashboard build, and Linux/Windows helper compilation. Dashboard cases passed individually; the aggregate runner encountered an unrelated usage-history timeout that passed on isolated rerun. The first broad Python attempt encountered a planner call-count timing failure; isolated rerun and the later complete suite passed. These transient failures are recorded rather than claiming the first `make test` attempt passed.
