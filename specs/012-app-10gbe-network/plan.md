# Implementation Plan: App-Managed Private 10GbE Network

**Branch**: `main` | **Date**: 2026-10-01 | **Spec**: [spec.md](spec.md)

**Feature identifier**: `012-app-10gbe-network`. The setup script reports this identifier as BRANCH; the actual checked-out Git branch is `main`. No branch switch is required.

## Summary

Add a master-controlled network lifecycle to the existing agent and dashboard. Joined workers establish authenticated command sockets to the master; a start operation prepares every participant, allocates unique private addresses, applies temporary settings locally, and verifies dedicated connectivity before admitting new inference runs. Record host changes in a durable ownership journal and retain pending cleanup through disconnects and restarts.

Existing shared-key membership does not authenticate a unique master. Bootstrap and pin node signing identities through mutual join-secret and Ed25519 proofs; require one-time local OS authorization of the cluster master before privileged setup. Existing RPC binds to one address and serves one client at a time. Use native dedicated network bridges to preserve the original RPC listener and bind outgoing traffic to the selected master address. Fully verify RPC only while idle, then release the verification connection before inference admission. Setup during an existing run can configure addresses but remains verification pending until that connection is idle.

## Technical Context

**Language/Version**: Python >=3.10 (checkout runtime 3.12); Rust edition 2021 with existing PyO3 0.23; TypeScript and Svelte 5/SvelteKit 2.

**Primary Dependencies**: Existing FastAPI, Uvicorn, Pydantic 2, httpx, psutil, Rust serde/sysinfo and platform tools. Add a direct websockets dependency for the worker client, cryptography for audited Ed25519 primitives, and Rust socket2 for portable bind-before-connect. Select stable versions compatible with Python 3.10 and the current Rust toolchain during implementation, and update uv.lock/Cargo.lock together. Do not implement cryptographic primitives or platform sockets manually.

**Storage**: A separate SQLite database at KLLM_HOME/managed-network.sqlite for operation transactions, assignment revisions, trust metadata, local write-ahead ownership, cleanup tombstones, and run reservations. Signing private keys are separate restricted files in KLLM_HOME/network-identity; never dashboard/heartbeat data. Serialize writes through one coordinator per agent; enable foreign keys and durable transactions. Keep ClusterState membership and its transient reset semantics intact.

**Testing**: Existing pytest/pytest-asyncio/xdist, Rust unit/integration tests, Node dashboard tests, Svelte check and production build. Add controlled subprocess/platform fixtures, actual loopback WebSocket/native bridge tests, crash/restart persistence tests, and a physical mixed-OS acceptance matrix.

**Target Platform**: Windows, Linux and macOS with dedicated physical Ethernet negotiating at least 1 Gbps (10 Gbps preferred), known DHCP configuration, automatically selected safe adapter, helper permissions, existing cluster management, and matching stable llama-release.

**Project Type**: Distributed Python CLI/agent with Rust native runtime and a local browser dashboard.

**Performance Goals**: Spec targets: idle eligible three-node setup <=60 seconds; degradation visible <=15 seconds; reachable cleanup <=60 seconds. No throughput claim follows from link speed. Measure bridge overhead and actual inference separately on identified hardware.

**Constraints**: No wildcard/public RPC listeners; no default-route, DNS-profile or unrelated persistent-profile changes; save and restore dedicated IPv4 DHCP changes; OS elevation only after explicit local notice and acknowledgment; no raw connect/close RPC probes; no inference rerouting; no trust replacement through shared-key messages; no deletion of ambiguous ownership.

**Scale/Scope**: One active managed IPv4 subnet per cluster in this version, one selected adapter per node, direct link or dedicated switch. Address capacity bounds membership; use a /24 proposal from RFC1918 candidate pools, validated across every participant. Do not impose a smaller hidden worker limit. Concurrent local bridge sessions are bounded to admitted run/probe reservations, with excess connections rejected before opening upstream RPC.

## Constitution Check

Pre-research and post-design evaluation use .specify/memory/constitution.md and the explicit repository instructions.

| Gate | Pre-research | Post-design evidence |
|---|---|---|
| Scope and reuse | Pass | Extract existing script primitives into src/kllm; retain script facade; reuse agent lifecycle, v2 router and status stream. |
| Behavior tests | Pass in design | Failure, ownership, trust, replay, run admission and platform cases mapped in quickstart.md. Runtime tests remain future work. |
| Integration and portability | Pass in design | Real loopback transports/native bridge plus OS hardware matrix; no mocked test substitutes for hardware claims. |
| Contracts and recoverability | Pass in design | Bounded stages, signed fencing, durable intent, conservative cleanup, and serialized run reservations in contracts and data model. |
| Public RPC prohibition | Pass | Exact private worker binding and dedicated master source allowlist; no broad firewall automation. |
| Dependencies and build | Pass in design | Three focused dependency additions have specific needs; update lockfiles and rebuild using scripts/build.py. |
| Structural standard availability | Unavailable | workflow.md and architecture_constitution.md referenced by AGENTS.md are absent; available extension workflow used. Do not claim missing rules were validated. Recheck if restored before implementation. |
| Python path conflict | Resolved by explicit user instruction | Constitution still says python/kllm; current packaging and user instruction require src/kllm. No governance amendment made. |

No known design violates the available mandatory rules. The missing structural document is an explicit validation limitation, not an invented exception or a claim of full governance coverage. No hard product clarification remains.

## Project Structure

### Documentation (this feature)

```text
specs/012-app-10gbe-network/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── memory.md
├── memory-synthesis.md
├── doc-synthesis.md
├── checklists/requirements.md
└── contracts/
    ├── control-api.md
    ├── command-socket.md
    ├── native-network.md
    └── platform-lifecycle.md
```

### Source Code (repository root)

Paths marked new are proposed; all other paths are existing integration points.

```text
src/kllm/
├── managed_network/                    # new focused package
│   ├── models.py                       # typed records, validation
│   ├── allocation.py                   # subnet/address checks; pure logic
│   ├── store.py                        # transaction and ownership persistence
│   ├── identity.py                     # signing, pairing and frame verification
│   ├── platform/
│   │   ├── base.py                     # inspect/prepare/apply/observe/remove contract
│   │   ├── windows.py
│   │   ├── macos.py
│   │   └── linux.py
│   ├── verification.py                 # dedicated evidence and invalidation
│   └── run_admission.py                # network reservations and frozen endpoint views
├── agent/
│   ├── schemas.py                      # additive capability/selection contracts
│   ├── routes/v2.py
│   ├── handlers/managed_network.py      # new thin REST/WS handlers
│   ├── services/managed_network.py      # new coordinator and local worker operations
│   ├── services/network_channel.py      # new client/server socket lifecycle
│   ├── application_lifecycle.py
│   ├── handlers/lifecycle.py
│   ├── handlers/inference.py
│   └── services/dashboard_status.py
├── native.py                           # new binding facade methods only
├── run_settings.py                     # request-only network selector
├── run_planning.py
└── run_launch.py
crates/kllm-native/src/
├── lib.rs
└── network/                            # new native data path; no OS config orchestration
    ├── mod.rs
    ├── relay.rs
    ├── rpc_probe.rs
    └── address_events.rs
scripts/configure_10gbe.py               # existing standalone compatibility facade
 dashboard/src/
├── lib/components/ManagedNetwork.svelte # new panel on existing cluster overview
├── lib/types.ts
├── lib/store.svelte.ts
├── routes/+page.svelte
└── routes/run/+page.svelte
 tests/                                 # new Python network unit/integration files
 dashboard/tests/                       # new managed-network tests
 docs/cli.md
 docs/troubleshooting.md
 network-config/10gbe-bridging-guide.md
```

**Structure Decision**: Python owns policy, platform configuration and lifecycle; Rust owns byte forwarding and safe bounded RPC probing; dashboard consumes resource contracts and existing status events. Platform operations never import dashboard or agent orchestration. Agent __init__.py remains a composition point, with injected dependencies preserving existing test patch surfaces.

## Design and Integration

1. **Trust and inventory**: Generate local signing identity; bootstrap public fingerprints through bounded mutual agent proofs, retaining authenticated loopback manual pairing controls. Install a narrowly scoped native helper only after local administrator acknowledgment and OS approval. Report missing pairing or unsupported managed-network-v1 capability as preparation failure, while preserving legacy membership. Workers connect to /v2/cluster/network-control over the existing control path, including cp_proxy if already required. Signed challenges bind the master and worker identities, current master term/session and fresh socket nonces.
2. **Prepare and apply**: Master writes an operation and deterministic assignment set, validates all participants, then commits application intent. Workers journal before each bounded OS mutation and verify observed state afterward. Only app-created exact addresses become removable. Candidate subnet suggestions are proposals until all nodes validate them; duplicate-address detection and post-add usability follow contracts/platform-lifecycle.md; IPv6 DAD is not IPv4 proof.
3. **Dedicated data path**: Require TCP-only builds for both llama-server and rpc-server; an idle zero-capability probe alone is insufficient evidence. Native worker bridge listens only on its dedicated address and selected port and forwards to its existing same-host private RPC endpoint. Native master bridge exposes a loopback listener per worker; its outbound connection is explicitly bound to the master's dedicated address. Planner launch endpoints are loopback bridge addresses with a separate mapping to the logical dedicated addresses shown in the app. No second RPC process, GPU inventory, or wildcard listener is introduced.
4. **Verification and admission**: Check route/interface identities; exchange signed source-bound challenges in both directions on separate dedicated diagnostic listeners; then make one bounded valid read-only RPC probe through the master and worker bridges while RPC is idle. Close it and confirm relay drain before launch. Freeze network revisions and endpoints in a durable run reservation before stopping a previous run. Managed runs replace ensure_rpc_peers_reachable with signed worker evidence/grants; loopback endpoints must never go to that HTTP-status gate. A managed replacement without fresh idle proof returns RPC_BUSY and preserves the old run; the operator stops it first. Active-run status uses signed dedicated probes, bridge stream state and child identity; it never opens a competing RPC verification connection.
5. **Recovery and cleanup**: Restart invalidates readiness, inspects journal and OS state, and restores only safely observed control/bridge state. An unobserved window makes identical replacement ownership ambiguous: retain it as cleanup pending rather than delete. Stop, leave, eviction and role transitions close admission, respect dependent runs and reconcile owned changes before discarding authority; unreachable nodes and local uncertainty remain durable.
6. **UI and compatibility**: Add a managed-network panel to the existing overview with adapter selection, subnet proposal, start/retry/add/stop and per-node failures. Add optional managed_networks to the existing status topic, without extending its fixed topic protocol. Add an explicit per-run network selector; default omitted selector retains LAN/WireGuard. Network selection is not saved as reusable model settings. Preserve all /v1 endpoints and manual script arguments.

## Time and Failure Policy

Parallel preparation/apply/verification across participants uses operation-wide budgets, not per-node multiplication: prepare 15s, apply 20s, verify 20s, final publish 5s. Each command has a <=5s subprocess/socket deadline within that budget. DAD settling is observed within apply. Rollback/stop has a separate 60s budget; failures become cleanup pending. Waiting for an active RPC owner is explicit verification_pending/RPC_BUSY, not a hidden timeout extension or readiness pass.

Sample dedicated and control liveness every 5s; a 2s bounded failure invalidates admission immediately and is published within the 15s spec bound. Idle RPC readiness evidence must be <=10s old at admission; probe and reservation share one lock. Repeated challenges carry fresh identities and deadlines; keepalive alone never renews evidence. Reconnection backoff is capped at 5s during pending cleanup. Pending ownership conflicts require local inspection/release rather than destructive automated guesses.

## Requirement Coverage

| Spec requirements | Design location | Acceptance focus |
|---|---|---|
| FR-001, FR-008, FR-017 | control-api, dashboard panel/status, docs | Operator flow, defaults, legacy compatibility |
| FR-002, FR-003, FR-012 | identity and command-socket | Shared-key peer impersonation, stale session, duplicate/reordered delivery |
| FR-004, FR-005, FR-006 | allocation and platform adapters | All-node preparation refusal, unique assignments, host snapshot preservation |
| FR-007, FR-009, FR-010 | native-network, verification, run_admission | Correct physical/source path, idle probe, frozen run endpoints |
| FR-011, FR-013, FR-014 | SQLite journal, lifecycle hooks, run reservations | Partial application, restart, external change, stop/run race |
| FR-015, FR-016, FR-018 | platform matrix, private listeners, deadlines | Mixed OS, nonpublic binding, stage failure and cleanup pending |

## Research and Phase 1 Outputs

[research.md](research.md) resolves transport, authentication, privilege, ownership, platform, routing, probing and UI choices. [data-model.md](data-model.md) defines persisted invariants and transitions. [control-api](contracts/control-api.md), [command-socket](contracts/command-socket.md), [native-network](contracts/native-network.md), and [platform lifecycle](contracts/platform-lifecycle.md) define boundaries. [quickstart.md](quickstart.md) defines implementation and physical acceptance checks. The implementation and validation records are maintained in tasks.md and quickstart.md.

## Complexity Tracking

No constitutional violation is requested. The added identity, journal and bridge components solve requirements missing from existing interfaces; their alternatives and reasons are recorded in research.md. Native two-sided forwarding has measurable cost and needs hardware validation; no throughput improvement is claimed.

## Hook Completion

The mandatory before_plan memory hook ran via the installed CLI; context savings were 1,912 baseline / 437 cached / 1,475 saved tokens (77.1%). Shared-memory sync was evaluated: project_profile/config and a callable sync MCP are absent, so no shared import was performed. Capture was evaluated using the installed command guidance; unimplemented design choices remain feature-local and durable memory was not written. The user's planning request does not authorize durable personal memory updates. There are no after_plan hooks in extensions.yml.

## Guided setup implementation

Python adds bootstrap.py, guided_setup.py, helper.py and helper_platform.py under src/kllm/managed_network. Additive /v2 network-setup resources assess computers, retain request identity across retries, authorize the local helper, and reuse signed lifecycle phases. The dashboard shows an administrator notice first, computer status, automatic adapter/subnet choices, and setup/cancel/retry/add/stop actions. NetworkAdvanced.svelte preserves manual diagnostics.

The standalone Rust binary is built with --no-default-features --features network-helper; it never links PyO3. Protected launchd/systemd/SYSTEM installations use restricted local sockets or named pipes. Grants and journals live in protected OS locations. Configuration intent precedes fixed OS changes; native observation independently guards removal. Linux supports a temporary NetworkManager clone or owned runtime networkd drop-in; macOS saves DHCP state and uses networksetup only on the dedicated service; Windows uses ActiveStore DHCP/address changes and exact peer/address/port/interface firewall rules. Missing platform capabilities fail closed.
