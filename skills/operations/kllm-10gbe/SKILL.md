---
name: kllm-10gbe
description: "Guide users through creating, verifying, troubleshooting, and restoring a dedicated private network (10GbE preferred; use the best available negotiated link) on Windows, macOS, Linux, or mixed-OS Kluster clusters."
---

# Create a dedicated private network

10 Gbps is preferred, not required: use the best negotiated physical link the hardware actually provides.

Guide the user in small steps, using the OS of each computer. Locate the Kluster checkout through `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`; repository paths below resolve against that root.

Read only the references for participating platforms:
- [Windows](references/windows.md): numeric adapter indexes, PowerShell, local administrator approval, and scoped firewall checks.
- [macOS](references/macos.md): hardware devices versus network services, local helper authorization, and manual address checks.
- [Linux](references/linux.md): link inspection, NetworkManager/systemd-networkd, authorization, and recovery.

## Establish the physical setup

Ask for missing facts that change setup: computers and OS versions, the intended master, adapter identities, direct cable versus dedicated switch, and how each computer currently reaches the internet/master. Two computers can use a direct Ethernet link; more participants normally use a dedicated switch. Check that adapters, cables/transceivers, switch ports and drivers support the intended link; read negotiated speed instead of trusting a product label. Do not substitute Thunderbolt IP, bonding, bridging, internet sharing, or jumbo frames for an Ethernet setup without a separate user request.

Keep the management/internet path on its existing interface. Reject Wi-Fi/virtual interfaces, interfaces carrying the default route or cluster-management connection, and unknown speed/configuration. Record the negotiated speed of every candidate and prefer the fastest available link; accept any negotiated speed the user has, and never report an assumed speed as measured. Multiple eligible adapters require identification by the user; a single safe eligible adapter can be selected by the application. If no eligible adapter exists, explain the specific hardware/configuration gap before suggesting changes.

## Guided Kluster setup (default)

1. Read the managed-private-network and guided-setup sections in `docs/agent-install.md` and `docs/troubleshooting.md`. Install/update Kluster on each participant through the supported installer; join workers to the intended master over the existing management connection. Use `kllm-cluster` if needed.
2. Open **Home → Private 10GbE network → Refresh computers**. The panel is hidden by default; if it is absent, run `kllm flags private-network-setup --enable` first (`--disable` hides it again). Explain that administrator access changes only the dedicated adapter's owned configuration and is needed locally on each computer. The agent remains in the original account.
3. On each computer, show the administrator notice and use its local enable button. The user completes that computer's native OS approval. A remote master cannot approve it. Denial leaves that computer unconfigured; a missing helper needs a supported rebuild, not arbitrary elevated Python.
4. Refresh the assessment. Let the master choose a non-conflicting private subnet and safe adapters, or select among eligible adapters explicitly. Keep manual pairing/subnet/adapter controls in Advanced details. Do not hardcode an example subnet without checking all relevant routes and addresses.
5. Start setup from the master and follow participant statuses. Reassess stale inventories. Unknown DHCP/profile state, unsupported Linux managers, address conflicts, and external ownership changes need local review; do not force past them.
6. Verify private connectivity before inference. Network verification does not require an independently running RPC server. A joined worker may correctly have no RPC process until the master starts the selected model.

## Explicit manual fallback

For a requested manual two-computer link, use `scripts/configure_10gbe.py` after read-only inspection and recording the selected adapters' original DHCP/address/route settings. Its `configure` action requires administrator access, an explicit physical connected adapter, verified speed >=10 Gbps, DHCP already disabled, and a non-conflicting RFC1918 subnet. It does not disable DHCP, set a gateway/DNS, or prepare persistent settings for you. The 10 Gbps gate is a property of the script, not a cluster requirement: when the script rejects a slower link, use the documented manual direct-link operation below instead of forcing the script.

Prepare those prerequisites through the relevant OS reference after explaining the exact selected-adapter changes. Run opposite roles on the two hosts, using each one's own adapter ID:

```text
<node-python> scripts/configure_10gbe.py configure --interface <adapter-id> --role coordinator --subnet <unused-private-cidr>
<peer-python> scripts/configure_10gbe.py configure --interface <adapter-id> --role worker --subnet <same-private-cidr>
<node-python> scripts/configure_10gbe.py verify --interface <adapter-id> --peer <peer-private-ip>
```

Coordinator receives the first usable address; worker receives the second. Script-added addresses are temporary until reboot. OS-created manual profiles or DHCP changes can persist; record and restore those separately. Do not reuse the two-role script for a multi-worker switch (it would duplicate worker addresses). Use guided allocation or an explicitly reviewed unique address plan instead.

## Documented manual direct-link operation

This is the approved path when the script's 10 Gbps gate rejects the link, when no helper is available, or when the user configures a direct cable by hand. Observed on a two-Mac AX88179A USB-Ethernet link (1000baseT full-duplex, macOS 26.7 and 27.0).

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

## Verification and recovery

- Verify the peer in both directions through the dedicated interfaces. `reachable` is an echo response; `unreachable` is explicit routing failure; `unable-to-verify` can be ICMP filtering or an offline peer. Do not report a timeout as proof that the link failed. When inbound ping fails but the link is up, check the OS firewall before blaming the link: macOS firewall stealth mode silently drops ICMP while TCP to listening services still works. Confirm layer 2 with `arp -an | grep <device>` (the peer MAC must appear) and layer 3 with a TCP connect such as `nc -z -w 3 <peer-ip> <open-port>` against a known listener. Do not disable the firewall to make ping work; on managed computers it cannot be disabled anyway. Find a peer's addresses with `dscacheutil -q host -a name <name>.local` or `ping <name>.local`, and expect several answers (management and dedicated) — match the address to the interface you intend to test.
- Record negotiated link speed separately from throughput. If requested, use an installed `iperf3` with server/client bound to the dedicated private addresses, then repeat with `-R` for the other direction. Measure actual results; make no fixed latency, bandwidth, or inference-speed promises. Stop only the test server started for this check.
- Check control traffic and, when inference is intentionally started, RPC through the correct private endpoints. Keep upstream RPC TCP 50052 private. Never solve reachability by globally disabling a firewall or by disabling the management NIC.
- Stop dependent inference before network stop/add/retry. Use the application's owned restoration. Reconnect unavailable computers to resolve **cleanup pending**. Lost ownership or changed snapshots require local **Review network recovery**; keep current settings or remove only the exact reviewed owned delta.
- In manual mode, remove only addresses/rules created during this setup and restore the recorded OS profile/DHCP state — a macOS service that was DHCP before the manual change returns with `networksetup -setdhcp '<service>'`. If baseline or ownership is uncertain, preserve settings and explain the unresolved recovery instead of guessing.

Completion report: topology and adapters, observed per-node speed, chosen private addressing, local approvals, bidirectional reachability, any measured throughput/inference evidence, management connectivity, and recovery state. Guidance, fixtures, or local tests do not prove physical Windows/macOS/Linux or mixed-node acceptance.
