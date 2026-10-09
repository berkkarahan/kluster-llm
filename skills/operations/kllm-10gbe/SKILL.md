---
name: kllm-10gbe
description: "Guide users through creating, verifying, troubleshooting, and restoring a dedicated private Ethernet network (at least 1 Gbps; 10GbE preferred) on Windows, macOS, Linux, or mixed-OS Kluster clusters."
---

# Create a dedicated private network

Require a known negotiated physical Ethernet speed of at least 1 Gbps; 10 Gbps is preferred, not required. Report the speed each endpoint actually negotiates rather than inferring it from the switch, adapter label, or cable.

Guide the user in small steps, using the OS of each computer. Locate the Kluster checkout through `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`; repository paths below resolve against that root.

Read only the references for participating platforms:
- [Windows](references/windows.md): numeric adapter indexes, PowerShell, local administrator approval, and scoped firewall checks.
- [macOS](references/macos.md): hardware devices versus network services, local helper authorization, and manual address checks.
- [Linux](references/linux.md): link inspection, NetworkManager/systemd-networkd, authorization, and recovery.

## Establish the physical setup

Ask for missing facts that change setup: computers and OS versions, the intended master, adapter identities, direct cable versus dedicated switch, and how each computer currently reaches the internet/master. Two computers can use a direct Ethernet link; more participants normally use a dedicated switch. Check that adapters, cables/transceivers, switch ports and drivers support the intended link; read negotiated speed instead of trusting a product label. Do not substitute Thunderbolt IP, bonding, bridging, internet sharing, or jumbo frames for an Ethernet setup without a separate user request.

Keep the management/internet path on its existing interface. Reject Wi-Fi/virtual interfaces, interfaces carrying the default route or cluster-management connection, speeds below 1 Gbps, and unknown speed/configuration. Record each candidate's negotiated speed and prefer the fastest eligible link. Multiple eligible adapters require identification by the user; a single safe eligible adapter can be selected by the application. If no eligible adapter exists, explain the specific hardware/configuration gap before suggesting changes.

## Guided Kluster setup (default)

1. Read the managed-private-network and guided-setup sections in `docs/agent-install.md` and `docs/troubleshooting.md`. Install/update Kluster on each participant through the supported installer; join workers to the intended master over the existing management connection. Use `kllm-cluster` if needed.
2. Open **Home → Private 10GbE network → Refresh computers**. The panel is hidden by default; if it is absent, run `kllm flags private-network-setup --enable` first (`--disable` hides it again). Explain that administrator access changes only the dedicated adapter's owned configuration and is needed locally on each computer. The agent remains in the original account.
3. On each computer, show the administrator notice and use its local enable button. The user completes that computer's native OS approval. A remote master cannot approve it. Denial leaves that computer unconfigured; a missing helper needs a supported rebuild, not arbitrary elevated Python.
4. Refresh the assessment. Let the master choose a non-conflicting private subnet and safe adapters, or select among eligible adapters explicitly. Keep manual pairing/subnet/adapter controls in Advanced details. Do not hardcode an example subnet without checking all relevant routes and addresses.
5. Start setup from the master and follow participant statuses. Reassess stale inventories. Unknown DHCP/profile state, unsupported Linux managers, address conflicts, and external ownership changes need local review; do not force past them.
6. Verify private connectivity before inference. Network verification does not require an independently running RPC server. A joined worker may correctly have no RPC process until the master starts the selected model.

## Explicit manual fallback

For a requested manual two-computer link, `scripts/configure_10gbe.py` remains a 10 Gbps-only utility. Its `configure` action requires administrator access, an explicit physical connected adapter, verified speed >=10 Gbps, DHCP already disabled, and a non-conflicting RFC1918 subnet. It does not disable DHCP, set a gateway/DNS, or prepare persistent settings for you. This utility's speed gate is stricter than the managed-network requirement; when it rejects a slower link, use the documented OS-native fallback below rather than forcing the script.

Prepare those prerequisites through the relevant OS reference after explaining the exact selected-adapter changes. Run opposite roles on the two hosts, using each one's own adapter ID:

```text
<node-python> scripts/configure_10gbe.py configure --interface <adapter-id> --role coordinator --subnet <unused-private-cidr>
<peer-python> scripts/configure_10gbe.py configure --interface <adapter-id> --role worker --subnet <same-private-cidr>
<node-python> scripts/configure_10gbe.py verify --interface <adapter-id> --peer <peer-private-ip>
```

Coordinator receives the first usable address; worker receives the second. Script-added addresses are temporary until reboot. OS-created manual profiles or DHCP changes can persist; record and restore those separately. Do not reuse the two-role script for a multi-worker switch (it would duplicate worker addresses). Use guided allocation or an explicitly reviewed unique address plan instead.

## Documented manual direct-link operation

Use the OS-native fallback when the standalone script's 10 Gbps gate rejects a supported 1 Gbps link, when no helper is available, or when the user requests manual configuration. An Anker AX88179A is a Gigabit Ethernet adapter: a 10GbE switch does not raise its negotiated endpoint speed. In one mixed Windows/Mac switched setup, a Windows Realtek 5GbE adapter and the Mac AX88179A adapters all negotiated 1 Gbps.

1. Inspect on each computer: OS version, hardware ports, network services, addresses, and routes:

   ```bash
   sw_vers
   networksetup -listallhardwareports
   networksetup -listnetworkserviceorder
   ifconfig <device>
   networksetup -getinfo '<service>'
   netstat -rn -f inet
   route -n get <peer-or-management-ip>
   ```

2. Identify the real adapter by chipset name and active status. Ignore inactive `Ethernet Adapter (enX)` entries whose MACs are locally administered (`42:ff:34:...`); those are virtual adapters. The device number is per-machine (`en7` on one Mac, `en4` on another) — never copy it between computers.
3. Self-assigned `169.254.x.x` with no router is expected on a direct cable with no DHCP server. It is not a failure and not evidence of a bad link.
4. Choose a subnet only after checking `netstat -rn -f inet` for overlapping prefixes. A dedicated `/24` inside the management LAN's range shadows the LAN route and blackholes those hosts; pick a range with no existing routes (for example `10.10.10.0/24` when only `10.1.x`/`10.205.x` host routes exist).
5. Configure both ends inside the same subnet, with no router, using the observed service name:

   ```bash
   networksetup -setmanual '<service>' <ip> <mask> ''
   networksetup -getinfo '<service>'
   ```

   The empty router argument is valid and persists across reboot. Keep the default and management routes on the existing interface. A shared subnet on only one end causes asymmetric routing: the peer answers over the management LAN where the address does not exist, and the test fails for a reason unrelated to the link.
6. Verify both directions (see below) before reporting success, then record the addresses for the completion report.

For a Windows participant on a dedicated switch, use the Windows reference's manual switched-network procedure and allocate a distinct address to every node. Do not run the two-role script repeatedly on a multi-worker switch because every worker would receive the same address.

## Verification and recovery

- Verify the peer in both directions through the dedicated interfaces. `reachable` is an echo response; `unreachable` is explicit routing failure; `unable-to-verify` can be ICMP filtering or an offline peer. Do not report a timeout as proof that the link failed. When inbound ping fails but the link is up, check the OS firewall before blaming the link: macOS firewall stealth mode silently drops ICMP while TCP to listening services still works. Confirm layer 2 with `arp -an | grep <device>` (the peer MAC must appear) and layer 3 with a TCP connect such as `nc -z -w 3 <peer-ip> <open-port>` against a known listener. Do not disable the firewall to make ping work; on managed computers it cannot be disabled anyway. Find a peer's addresses with `dscacheutil -q host -a name <name>.local` or `ping <name>.local`, and expect several answers (management and dedicated) — match the address to the interface you intend to test.
- Record negotiated link speed separately from throughput. If requested, use an installed `iperf3` with server/client bound to the dedicated private addresses, then repeat with `-R` for the other direction. Measure actual results; make no fixed latency, bandwidth, or inference-speed promises. Stop only the test server started for this check.
- Configure the master to advertise/bind its private address and each worker to join that private master while binding its own private address. On the app's preferred private subnets (`10.10.10.0/24` first, then the documented fallbacks) `kllm start` binds the dedicated address automatically; for any other subnet pass `--rpc-bind <master-private-ip>`. Check fresh accepted heartbeats, not cached membership. Keep join keys out of command logs and reports; do not dump raw master status JSON because it can contain the join key.
- Check control traffic over the private endpoints. Keep upstream RPC TCP 50052 private to the node address, private subnet, and dedicated interface; verify TCP 50052 only while inference is intentionally running. No listener on an idle node is normal. Never solve reachability by globally disabling a firewall or by disabling the management NIC.
- Stop dependent inference before network stop/add/retry. Use the application's owned restoration. Reconnect unavailable computers to resolve **cleanup pending**. Lost ownership or changed snapshots require local **Review network recovery**; keep current settings or remove only the exact reviewed owned delta.
- In manual mode, remove only addresses/rules created during this setup and restore the recorded OS profile/DHCP state — a macOS service that was DHCP before the manual change returns with `networksetup -setdhcp '<service>'`. If baseline or ownership is uncertain, preserve settings and explain the unresolved recovery instead of guessing.

Completion report: topology and adapters, observed per-node speed, chosen private addressing, local approvals, bidirectional reachability, any measured throughput/inference evidence, management connectivity, and recovery state. Guidance, fixtures, or local tests do not prove physical Windows/macOS/Linux or mixed-node acceptance.
