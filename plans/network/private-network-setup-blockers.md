# Private-network setup blockers and management-path recovery

> **Created:** 2026-10-16
> **Status:** In progress — software fixes verified; physical management migration and acceptance pending
> **Repo / scope:** kluster-llm — guided network assessment, dashboard explanations, safe operational recovery

## Conventions captured at creation

- Memory context uses the read-only markdown fallback: `docs/memory/INDEX.md`, architecture A2 and decision D3, plus `.specify/memory/workflow.md`, `constitution.md`, and `architecture_constitution.md`. Feature ownership and owning-module test patches apply.
- `src/kllm/modules/managed_network/interactors/guided_setup.py:GuidedSetup.adapter_reason` excludes nonphysical, disconnected, unknown/sub-gigabit, default-route and management-address adapters. Administrator approval does not override eligibility.
- `src/kllm/modules/managed_network/interactors/service.py:ManagedNetworkService._management_hosts` resolves membership `host`, `lan_host`, `wg_address` and `master_host`. It does not distinguish an independently verified alternative management path from an advertised private endpoint.
- `GuidedSetup.assess` reports `NO_ADAPTER` when no eligible candidate remains; it can overwrite a prerequisite message with the first physical adapter's reason. `GuidedSetup._start` reassesses and rejects blocked selections server-side.
- `dashboard/src/lib/modules/network/view/ManagedNetwork.svelte:canStart` requires at least two selected computers, no selected computer error, and selected adapters. The row shows generic “Needs attention” and delegates the explanation to `dashboard/src/lib/shared/ErrorMessage.svelte`.
- `dashboard/src/lib/shared/errors.ts:createErrorPresentation` suppresses a first snapshot after `start()`. Clearing/dismissing also hides messages while authoritative blockers remain. Fix network status presentation locally rather than changing global notification semantics.
- Existing addresses may be user-owned; stopping managed networking preserves them. Installation/rebuild uses `scripts/install.py` or the documented `scripts/build.py` flow. No credential resets, broad firewall changes, or removal of unknown-owned addresses.

## Verified diagnosis

Two fresh authenticated GET assessments from the running local master returned the same blocker for every computer. Credentials were kept out of evidence.

| Computer | Dedicated adapter | Current address | Reported speed | Administrator | Blocker |
|---|---|---|---|---|---|
| nzxt-karahan | 7 | 10.10.10.3/24 | 1000 Mbps | enabled | NO_ADAPTER: carries cluster management |
| LMUK-CJ7G5VHVFC | en7 | 10.10.10.1/24 | 1000 Mbps | enabled | NO_ADAPTER: carries cluster management |
| Karahan-M1-Pro.local | en4 | 10.10.10.2/24 | 1000 Mbps | enabled | NO_ADAPTER: carries cluster management |

Membership `host` and `lan_host` use these same private addresses. Local PowerShell independently reports Ethernet index 7 up at 1 Gbps; the default route uses Wi-Fi index 14 through 192.168.68.1. Inventories report alternative Wi-Fi addresses 192.168.68.52, .54 and .100 respectively, but those are not yet verified as usable alternate control paths.

The local helper reports enabled, no error and no pending recovery. Assessment includes stopped historical networks; historical STALE_REVISION and HELPER_REFUSED results are not the current setup-button blocker. No live managed network is selected by the panel.

An isolated call to the real `GuidedSetup.adapter_reason` reproduces rejection for the Ethernet address in the management set and acceptance of the same adapter when management uses the Wi-Fi address instead. A direct execution of `createErrorPresentation` reproduces the hidden initial blocker snapshot. These establish the eligibility rule and presentation defect; they do not prove a successful network setup or throughput.

## Intended changes and phases

### 1. Make blocking state continuously understandable

Render each computer's current readiness explanation as persistent status text, independent of dismissible error/toast state. Show administrator readiness separately from adapter readiness. Provide adapter identity, negotiated speed and rejection reason in details; give the disabled setup action a concise explanation naming blocked computers. Refresh must update that status even after global Clear or individual dismissal.

Keep the existing server and client eligibility gates. Do not allow clicking through a management-adapter refusal.

### 2. Improve assessment diagnostics without weakening protection

Preserve prerequisite errors and their messages together; expose adapter rejection reasons separately rather than replacing helper/channel failure text with a physical adapter reason. Distinguish “no Ethernet found” from “Ethernet exists but carries cluster management” in user-facing status, retaining compatible error codes unless a documented contract change is necessary.

Explain that a network already configured manually can carry cluster traffic while the guided panel says “Not set up”: that label describes Kluster-managed setup, not physical reachability. Add this case and the recovery procedure to `docs/troubleshooting.md`.

### 3. Recover this deployment through a separately verified management path

Before any lifecycle changes, inspect current inference/reservations and arrange a controlled stop if required. Verify all three Wi-Fi/LAN control endpoints, bidirectional worker/master reachability and actual routes; a reported Wi-Fi IP alone is insufficient. Inspect current CLI contracts before choosing explicit bind/advertise/join arguments, since the preferred private subnet can be selected automatically.

Move cluster management to the independently verified LAN path using supported lifecycle commands while preserving signing identity, helper grants and existing user-owned Ethernet addresses. Recheck accepted heartbeats, signed channels, membership addresses and helper status. Refresh guided assessment and confirm the dedicated adapters now qualify before starting a new network identity. Do not revive stopped network tombstones.

If supported lifecycle options cannot express independent management and inference endpoints, stop this recovery phase and specify the missing endpoint separation explicitly. Do not merely remove `_management_hosts` entries or bypass the protection. Adoption of an existing management-bearing network would require a separate, non-mutating ownership design and is outside this fix.

### 4. Regression and physical acceptance

- Extend `dashboard/tests/managed-network.test.mjs`: first open, Clear, dismiss, refresh, changed blocker and recovery all retain accurate inline readiness; setup stays disabled while blocked. Preserve the global behavior covered by `dashboard/tests/errors.test.mjs`.
- Extend `tests/test_guided_network.py`: all administrators enabled with management-bearing Ethernet remains blocked; separate management permits otherwise eligible adapters; default-route/unknown-speed/sub-gigabit adapters remain excluded; prerequisite messages match their codes.
- Check assessment/start boundary consistency and stale assessment refusal, using existing managed-network test fixtures and owning-module patches.
- Run targeted Python/dashboard tests and `npm run check`; build through the supported flow before browser acceptance against the served bundle.
- On the actual Windows plus two macOS participants, verify setup completes, management remains connected, private connectivity succeeds in both directions, and Stop preserves user-owned addresses. Capture any subsequent failure separately. Record negotiated speed as 1 Gbps unless fresh hardware evidence changes it; do not claim 10GbE throughput.

## Remaining uncertainties and scope

No adapters, helper grants, cluster membership, firewall settings or application source were changed during diagnosis. Alternative LAN reachability, management migration, actual setup, restoration and inference have not been exercised. The screenshot's exact browser presentation history was not inspected; the initial-snapshot suppression is independently reproduced in current source. Historical helper replay failures may recur after eligibility is resolved and then need their own current operation evidence.
