# macOS participant

Distinguish the hardware device (`en3`, for example) from the configured network service name (possibly `Thunderbolt Ethernet`). They are not interchangeable. Identify this Mac's actual mapping and negotiated media:

```bash
networksetup -listallhardwareports
networksetup -listnetworkserviceorder
networksetup -getinfo '<selected-service>'
ifconfig <selected-device>
netstat -rn -f inet
route -n get <master-management-ip>
```

Record addresses/prefixes, DHCP/manual mode, service, DNS and routes before changes. Inspect `ifconfig` media/status for the connected link and record the negotiated speed (for example `1000baseT <full-duplex>`); any working negotiated speed is acceptable and 10 Gbps is preferred. Do not infer speed from the adapter model or Thunderbolt bus rating. If speed is unreported, fix inspection/driver support before applying network changes. Inactive `Ethernet Adapter (enX)` entries with locally administered MACs (`42:ff:34:...`) are virtual adapters — identify the real adapter by chipset name (for example `AX88179A`) and `status: active`. Device numbers are per-machine; map them fresh every time. The chosen device must not carry the default or management route. Do not treat macOS neighbor-cache host routes as proof of a subnet conflict; use the application's current assessment.

## Guided setup

Follow `docs/agent-install.md` to stage the standalone network helper. Open the local dashboard, read the administrator notice and use the local enable button. The user approves the native administrator prompt. The helper is protected under `/Library/PrivilegedHelperTools/com.kllm.network` and a system LaunchDaemon; do not run the ordinary Python agent as root.

Refresh computers on the master and start the private network after each participant has enabled access. Keep ambiguous adapter selection in Advanced details. Network setup is distinct from preparing llama.cpp or starting inference.

## Requested manual two-computer link

The guarded script requires DHCP already disabled. If the dedicated service currently uses DHCP, review its exact baseline and an unused private address first. In **System Settings → Network → selected Ethernet service → Details → TCP/IP**, choose **Manually**, use the reviewed private address/subnet mask, and leave router unset for this dedicated link. Do not choose DHCP with manual address, change service order, or rewrite other services' DNS.

Check the service again with `networksetup -getinfo`. If using CLI configuration instead of the GUI, consult this Mac's `networksetup -help`: `-setmanual` takes a network service, IP, subnet and router; it is not `-setstaticnetworksetup`, and `-setdhcp <service> off` does not disable DHCP. Review the exact command against the observed service before executing it.

From the checkout, use the hardware device and the intended role:

```bash
sudo .venv/bin/python scripts/configure_10gbe.py configure --interface <selected-device> --role <coordinator-or-worker> --subnet <unused-private-cidr>
sudo .venv/bin/python scripts/configure_10gbe.py verify --interface <selected-device> --peer <peer-private-ip>
```

If the OS already has the exact address, configure reports it unchanged. The script only adds a temporary address alias; a manual service configuration created through System Settings is separately persistent. Do not confuse those ownership/persistence boundaries. It sets no gateway/DNS.

## Direct link when the script's speed gate rejects the adapter

The guarded script requires >=10 Gbps; a working slower link (for example an AX88179A USB adapter at `1000baseT <full-duplex>`) is configured manually instead. Observed on a two-Mac direct cable.

1. Record the baseline: both services were usually DHCP with a `169.254.x.x` APIPA address and no router — expected on a direct cable with no DHCP server, not a fault.
2. Check `netstat -rn -f inet` for an existing prefix that overlaps the intended subnet. Assigning a `/24` inside the management LAN's range (for example `192.168.68.0/24` on a LAN that uses `/22`) shadows the LAN route with a more specific prefix and blackholes those hosts. Choose a range with no existing routes (for example `10.10.10.0/24`).
3. Configure the service, with the router argument empty — this persists across reboot:

   ```bash
   networksetup -setmanual 'AX88179A' 10.10.10.1 255.255.255.0 ''
   networksetup -getinfo 'AX88179A'
   ```

   Both ends must sit in the same dedicated subnet. A dedicated address on only one end makes the peer reply over its management LAN, where that address does not exist, and the test fails for routing reasons while the cable is fine.
4. Leave the default and management routes alone: after the change, `route -n get <management-ip>` must still resolve through the management interface.

## Verification patterns

- Both directions: `ping -c 5 <peer-ip>`. ICMP working one way but not the other is a firewall or routing symptom, not a broken cable.
- macOS firewall stealth mode drops inbound echo while TCP still passes: confirm with `/usr/libexec/ApplicationFirewall/socketfilterfw --getstealthmode`. Verify with layer 2 (`arp -an | grep <device>` must show the peer MAC) and a TCP connect (`nc -z -w 3 <peer-ip> <port>` against a known listener such as ControlCenter's `5000`/`7000`). Managed computers cannot have the firewall disabled — verify with TCP and record the ICMP limitation instead.
- Peer discovery: `dscacheutil -q host -a name <name>.local` and `ping <name>.local` return every address the peer advertises (management and dedicated); test the address that matches the interface under test, and confirm the target host before concluding a name belongs to it.
- A missing echo can be filtering, routing or an offline peer. If testing throughput, bind an installed `iperf3` server/client to the two private addresses. A Thunderbolt IP interface is a separate topology and is not automatically an eligible physical Ethernet adapter.

## Restore

Prefer the managed dashboard's owned restoration. For manual work, remove only a confirmed script-added alias (`ifconfig <device> inet <exact-added-ip> -alias`) after checking its current prefix and ownership. Restore a changed network service using the captured original method/address/router. If it was originally DHCP, return that selected service to DHCP through its TCP/IP settings or `networksetup -setdhcp '<service>'`; do not assume every Ethernet service originally used DHCP. DNS and routes left unchanged should remain unchanged. Review uncertain or externally changed snapshots instead of deleting all addresses.

Official references: [Ethernet configuration](https://support.apple.com/en-gb/guide/mac-help/mh119392/mac), [DHCP or manual addressing](https://support.apple.com/en-au/guide/mac-help/mchlp2718/mac), [networksetup](https://support.apple.com/en-ae/guide/remote-desktop/apdd0c5a2d5/mac). Local `networksetup -help` is the command reference for the installed macOS version.
