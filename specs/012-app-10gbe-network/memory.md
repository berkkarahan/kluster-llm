# Active Feature Context

## Request and Scope

- Continue the interrupted speckit-specify request for master-triggered private 10GbE setup through socket signals, with workers configuring themselves.
- Specification and Phase 1 planning are complete; task generation and runtime implementation remain subsequent phases.
- Reuse 012-app-10gbe-network and preserve unrelated implementation changes.

## Constraints for Planning

- Python application code belongs in src/kllm, native code in crates/kllm-native, and app controls in dashboard.
- Preserve dedicated-link eligibility: selected adapter, DHCP disabled, reported speed at least 10 Gbps, conflict checks, temporary addresses, and no gateway or DNS changes.
- The standalone configure_10gbe workflow assigns fixed coordinator/worker addresses. Multi-worker app setup requires unique master-owned assignments rather than repeating its fixed worker address.
- Preserve ordinary management and LAN/WireGuard workflows. Verify dedicated endpoints before new inference runs use them.
- Keep unauthenticated upstream RPC private; authenticate setup signals using current cluster trust and master ownership.
- Design ownership, repeated delivery, rollback, restart reconciliation, and cleanup pending before host mutations.

## Governance Gaps

- Referenced .specify/memory/workflow.md and architecture_constitution.md are absent. Available guidance comes from the installed extension's governed-memory-workflow.md and existing constitution; missing structural standards were not checked.
- The constitution and generated synthesis still name python/kllm. The explicit user instruction and current src/kllm layout take precedence. Governance files were not amended.
- The prior turn prepared memory and documentation context through the installed Memory Hub CLI. Refresh feature synthesis after replacing the spec template.

## Defaults and Open Questions

- Existing cluster enrollment, adapter selections, local permissions, private IPv4, and temporary settings are explicit defaults.
- No blocking product clarifications remain. Platform mechanisms, socket transport, permissions handling, and stage timeout allocation belong to technical planning.

## Planning Decisions (Feature-Local)

- Worker-initiated signed WebSocket commands use pinned Ed25519 node identities with explicit local pairing, persistent master term/session, fresh channel nonces and ordered operation fences. Shared join keys alone are insufficient master authentication.
- SQLite stores assignments, mutation intent/results, tombstones and run reservations separately from transient ClusterState membership. No private keys enter public status or transport frames.
- Platform operations use temporary exact secondary addresses, recheck eligibility immediately before mutation, and preserve user settings. Ambiguous identical replacement after an observation gap remains ownership_conflict/cleanup_pending.
- Native master loopback bridges bind remote sockets to the dedicated source; worker dedicated bridges preserve the existing private RPC listener. Logical and launch endpoints are separate. TCP-only transport is required for this design.
- The pinned RPC server handles a client synchronously: valid idle HELLO/device-count probing must close before inference. During active runs, signed dedicated probes and existing stream/child identity provide health; initial setup can remain pending until idle.
- New cryptography/websockets/socket2 dependencies are planned, not installed. OS changes, performance and hardware acceptance have not been executed.
- Shared-memory synchronization was evaluated but project profile and callable sync tool are absent. Capture review keeps unimplemented choices feature-local; no durable personal/project lessons were written.

## Task Generation Constraint

- User requested full end-to-end delivery with minimal new tests focused on critical things.
- tasks.md uses 39 tasks: US1 12, US2 8, US3 7, shared setup/foundation 9, polish/validation 3.
- Six focused verification tasks reuse shared fixtures/existing checks; the quickstart failure matrix is reference material rather than an exhaustive test-generation mandate.
- Runtime implementation and hardware checks remain unexecuted by task generation.

## Implementation baseline

- Implementation began on main with the existing Python/native/dashboard edits and untracked log/connect work preserved. No baseline changes were reverted.
- Scope remains all three stories and critical verification; physical three-node acceptance remains pending until hardware is available.
- Referenced workflow.md and architecture_constitution.md remain missing; current src/kllm boundaries take precedence over stale constitution paths.

## Foundation validation

- T001-T009 implemented; two compact native socket cases pass on macOS and native code checks for x86_64 Linux/Windows (compile checks, not OS/hardware acceptance).
- Python critical identity/replay/restart checks plus existing manual platform and installer checks: 100 pass. New package mypy and scoped Ruff pass.
- Rebuilt using scripts/build.py; uv editable installation needed an explicit reinstall-package refresh to replace the old source-tree extension.
- Current installed llama runtime has GGML_RPC_RDMA=ON. Added explicit --rpc-tcp-only installer option and artifact-bound transport evidence; runtime itself was not rebuilt/switched. Unknown/RDMA builds must be rejected before address mutation.
- No OS addresses have been changed and no full feature readiness or physical validation is claimed.

## Initial story implementation and validation (superseded by follow-through below)

- Added signed worker channels, all-node orchestration, write-ahead local executor, bidirectional diagnostics, released protocol probes, authenticated v2 resources and app setup/status/recovery controls. Both TCP-only installation hashes and running RPC child provenance are required before admission.
- Added request-only network_id, copied placement inventory, durable frozen reservations, probe/run grants with drain receipts, shared launch/stop/topology serialization, 5s health/renewal sampling and receive-relative 15s leases. Native status exposes whether admission was consumed so an ended run stream invalidates readiness.
- Added retry/add/cancel fences, local exact ownership review and lifecycle cleanup before role/membership resets. Restart never restores ready or continuous ownership; uncertain records remain pending. Compatible existing private addresses are preserved/reused; adapter profiles and original RPC ownership remain unchanged.
- Minimal tests: five critical Python cases, one actual authenticated three-agent refusal/retry/cancel/stop case, two native real-socket cases and one controlled serial Chrome journey. Browser controls include cancel/retry/add/run/stop/cleanup.
- `make test`: 696 Python passed, 11 skipped; all dashboard tests passed; 41 native passed; Svelte check zero errors/warnings. Supported native rebuild/editable refresh and production dashboard build passed. Linux/Windows compile checks passed. Latest scoped tests/type checks pass. Full-package mypy has six existing log_sink/connect errors, recorded in quickstart.md.
- Four tasks remain open: T011/T023/T030 combined real-native three-agent readiness/run/partial-apply checks, and T039 physical/mixed-OS generation/cable-loss acceptance. No physical speed, host mutation or end-to-end generation result is claimed.

## Combined native integration follow-through

- Added shared tests/managed_network_lab.py with three actual HTTP/WebSocket agents and native relay/probe/identity/drain boundaries. Controlled OS address topology maps logical aliases onto a real private IP and separate ports; controlled RPC/inference subprocesses replace GPU/model workload. No production bypass or host mutation was added.
- Ready/admission/renewal/blocked stop/legacy preservation and worker restart after partial application now pass in that combined lab. Lost ownership preserves the address until local review; reachable owned deltas are removed.
- The combined test exposed three concrete issues: relay UUID arrays were rejected after JSON serialization; BSD nonblocking socket inheritance caused quiet/chained relays to fail; renewal rejected its own generation. All are fixed and covered within the same minimal checks.
- T011/T023/T030 are completed; 38/39 tasks complete, with only physical T039 pending. Final full checks contain 697 Python passed/11 skipped, all dashboard tests, 41 native tests and clean Svelte check. Physical speed, real-model generation and mixed-OS/kernel lifecycle acceptance remain unclaimed.

## Post-implementation capture evaluation

- Executed the memory-md capture instructions after reviewing the implementation, task ledger and validation evidence. No extension config exists, so its default roots would be docs/memory and specs.
- Candidate durable lessons were canonical UUID-array parsing across signed JSON, BSD accepted-socket blocking mode, and same-generation lease renewal. Each has direct integration evidence, but no configured memory search/register MCP is available to establish non-duplication. Combined correct/durable/non-duplicate confidence is 40%, below the capture threshold; no durable memory was registered or published. Evidence remains in this feature-local record and tests. A future explicit manual capture can bypass that confidence threshold, as the extension permits.
