---
name: kllm-10gbe
description: "Guide users through creating, verifying, troubleshooting, and restoring a dedicated private 10GbE network on Windows, macOS, Linux, or mixed-OS Kluster clusters."
---

# Create a private 10GbE network

Guide the user in small steps, using the OS of each computer. Locate the Kluster checkout through `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`; repository paths below resolve against that root.

Read only the references for participating platforms:
- [Windows](references/windows.md): numeric adapter indexes, PowerShell, local administrator approval, and scoped firewall checks.
- [macOS](references/macos.md): hardware devices versus network services, local helper authorization, and manual address checks.
- [Linux](references/linux.md): link inspection, NetworkManager/systemd-networkd, authorization, and recovery.

## Establish the physical setup

Ask for missing facts that change setup: computers and OS versions, the intended master, adapter identities, direct cable versus dedicated switch, and how each computer currently reaches the internet/master. Two computers can use a direct Ethernet link; more participants normally use a dedicated switch. Check that adapters, cables/transceivers, switch ports and drivers support the intended link; read negotiated speed instead of trusting a product label. Do not substitute Thunderbolt IP, bonding, bridging, internet sharing, or jumbo frames for an Ethernet setup without a separate user request.

Keep the management/internet path on its existing interface. Reject Wi-Fi/virtual interfaces, interfaces carrying the default route or cluster-management connection, speed below 10,000 Mbps, and unknown speed/configuration. Multiple eligible adapters require identification by the user; a single safe eligible adapter can be selected by the application. If no eligible adapter exists, explain the specific hardware/configuration gap before suggesting changes.

## Guided Kluster setup (default)

1. Read the managed-private-network and guided-setup sections in `docs/agent-install.md` and `docs/troubleshooting.md`. Install/update Kluster on each participant through the supported installer; join workers to the intended master over the existing management connection. Use `kllm-cluster` if needed.
2. Open **Home → Private 10GbE network → Refresh computers**. Explain that administrator access changes only the dedicated adapter's owned configuration and is needed locally on each computer. The agent remains in the original account.
3. On each computer, show the administrator notice and use its local enable button. The user completes that computer's native OS approval. A remote master cannot approve it. Denial leaves that computer unconfigured; a missing helper needs a supported rebuild, not arbitrary elevated Python.
4. Refresh the assessment. Let the master choose a non-conflicting private subnet and safe adapters, or select among eligible adapters explicitly. Keep manual pairing/subnet/adapter controls in Advanced details. Do not hardcode an example subnet without checking all relevant routes and addresses.
5. Start setup from the master and follow participant statuses. Reassess stale inventories. Unknown DHCP/profile state, unsupported Linux managers, address conflicts, and external ownership changes need local review; do not force past them.
6. Verify private connectivity before inference. Network verification does not require an independently running RPC server. A joined worker may correctly have no RPC process until the master starts the selected model.

## Explicit manual fallback

For a requested manual two-computer link, use `scripts/configure_10gbe.py` after read-only inspection and recording the selected adapters' original DHCP/address/route settings. Its `configure` action requires administrator access, an explicit physical connected adapter, verified speed >=10 Gbps, DHCP already disabled, and a non-conflicting RFC1918 subnet. It does not disable DHCP, set a gateway/DNS, or prepare persistent settings for you.

Prepare those prerequisites through the relevant OS reference after explaining the exact selected-adapter changes. Run opposite roles on the two hosts, using each one's own adapter ID:

```text
<node-python> scripts/configure_10gbe.py configure --interface <adapter-id> --role coordinator --subnet <unused-private-cidr>
<peer-python> scripts/configure_10gbe.py configure --interface <adapter-id> --role worker --subnet <same-private-cidr>
<node-python> scripts/configure_10gbe.py verify --interface <adapter-id> --peer <peer-private-ip>
```

Coordinator receives the first usable address; worker receives the second. Script-added addresses are temporary until reboot. OS-created manual profiles or DHCP changes can persist; record and restore those separately. Do not reuse the two-role script for a multi-worker switch (it would duplicate worker addresses). Use guided allocation or an explicitly reviewed unique address plan instead.

## Verification and recovery

- Verify the peer in both directions through the dedicated interfaces. `reachable` is an echo response; `unreachable` is explicit routing failure; `unable-to-verify` can be ICMP filtering or an offline peer. Do not report a timeout as proof that the link failed.
- Record negotiated link speed separately from throughput. If requested, use an installed `iperf3` with server/client bound to the dedicated private addresses, then repeat with `-R` for the other direction. Measure actual results; make no fixed latency, bandwidth, or inference-speed promises. Stop only the test server started for this check.
- Check control traffic and, when inference is intentionally started, RPC through the correct private endpoints. Keep upstream RPC TCP 50052 private. Never solve reachability by globally disabling a firewall or by disabling the management NIC.
- Stop dependent inference before network stop/add/retry. Use the application's owned restoration. Reconnect unavailable computers to resolve **cleanup pending**. Lost ownership or changed snapshots require local **Review network recovery**; keep current settings or remove only the exact reviewed owned delta.
- In manual mode, remove only addresses/rules created during this setup and restore the recorded OS profile/DHCP state. If baseline or ownership is uncertain, preserve settings and explain the unresolved recovery instead of guessing.

Completion report: topology and adapters, observed per-node speed, chosen private addressing, local approvals, bidirectional reachability, any measured throughput/inference evidence, management connectivity, and recovery state. Guidance, fixtures, or local tests do not prove physical Windows/macOS/Linux or mixed-node acceptance.
