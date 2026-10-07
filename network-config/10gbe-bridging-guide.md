# 10GbE and Thunderbolt IP Bridging Guide

This guide covers setting up high-bandwidth, low-latency network connections between your macOS coordinator and Windows worker for distributed LLM inference.

---

## Why High-Speed Network Matters

Distributed inference over RPC requires sub-millisecond latency between coordinator and worker. Standard Ethernet (1 Gbps) or Wi-Fi introduces:
- **Packet queuing delays** (5-50 ms)
- **DHCP lease renewals** (state-machine collapse mid-inference)
- **Network interface switching** (Wi-Fi handoff between access points)

A dedicated 10GbE link or Thunderbolt IP bridge provides:
- **<0.1 ms latency** (vs. 5-50 ms on standard networks)
- **Stable IPs** (no DHCP reassignments)
- **Full duplex bandwidth** (no contention with other traffic)

---

## Option 1: Thunderbolt IP Bridge (Recommended for macOS ↔ Windows)

Thunderbolt 3/4 supports IP networking natively, making it ideal for bridging macOS and Windows hosts.

### Prerequisites
- Both hosts with Thunderbolt 3+ ports
- Thunderbolt cable (passive or active, ≤ 0.8 m for best latency)

### Setup Steps

#### 1. macOS Side (Coordinator)

```bash
# List Thunderbolt interfaces
networksetup -listnetworkserviceorder

# Create a new Thunderbolt network service
sudo networksetup -createnetworkservice "Thunderbolt IP" thunderbolt1
sudo networksetup -setemptynetworkservice "Thunderbolt IP"

# Configure static IP
sudo networksetup -setstaticnetworksetup thunderbolt1 192.168.10.1 255.255.255.0 "" ""

# Disable DHCP for this interface
sudo networksetup -setdnsservers thunderbolt1 empty
sudo networksetup -setsearchdomains thunderbolt1 empty
```

#### 2. Windows Side (Worker)

```powershell
# Open PowerShell as Administrator
# Find the Thunderbolt NIC
Get-NetAdapter | Where-Object { $_.Name -match "Thunderbolt|Intel|RJ45" } | Format-Table Name, InterfaceDescription

# Set static IP on the Thunderbolt NIC (replace "Ethernet1" with actual name)
New-NetIPAddress -IPAddress 192.168.10.2 -PrefixLength 24 -InterfaceAlias "Ethernet1"

# Disable DNS and DHCP for this interface
Set-NetIPConfiguration -InterfaceAlias "Ethernet1" -DnsDomain ""
```

#### 3. Verify Connectivity

```bash
# From macOS, ping the worker
ping -S 192.168.10.1 192.168.10.2

# Expected: <0.1 ms latency
# Packet loss: 0%
```

---

## Option 2: 10GbE Dedicated Link

If you have 10GbE NICs, set up a dedicated point-to-point link.

### Prerequisites
- Two 10GbE NICs (one per host)
- 10GbE switch (optional for direct cable connection)
- Direct cable or crossover cable

### Setup Steps

Use the repository script on both hosts. It requires an explicitly selected adapter with DHCP disabled and an OS-reported negotiated speed of at least 10 Gbps. It refuses lower or unreported speed, address/route conflicts, and unsupported adapters without changing network settings. It does not set DNS or a gateway.

Choose an unused private subnet; this example uses `192.168.10.0/24`. Run one command per host with opposite roles.

#### macOS and Linux

```bash
# Use the explicit device name from the OS, for example en3 or en8:
sudo .venv/bin/python scripts/configure_10gbe.py configure \
  --interface en3 --role coordinator --subnet 192.168.10.0/24

# On the peer host:
sudo .venv/bin/python scripts/configure_10gbe.py configure \
  --interface enp5s0 --role worker --subnet 192.168.10.0/24
```

#### Windows

Run PowerShell as Administrator. Use the adapter's numeric interface index from `Get-NetAdapter`.

```powershell
Get-NetAdapter | Format-Table Name, InterfaceIndex, Status, LinkSpeed
& .\.venv\Scripts\python.exe scripts\configure_10gbe.py configure `
  --interface 12 --role coordinator --subnet 192.168.10.0/24
```

Run the worker command on the peer with its own interface index and `--role worker`. Coordinator receives the first usable address (`192.168.10.1` in this example); worker receives the second (`192.168.10.2`).

The address is temporary and disappears after reboot. Run configure again after reboot. On Windows, the script uses `New-NetIPAddress -PolicyStore ActiveStore`; it does not add the address to the persistent store. A Windows adapter using DHCP is rejected rather than having DHCP disabled implicitly.

### Verify the peer

Run from each host using the selected interface and the peer address printed by configure:

```bash
sudo .venv/bin/python scripts/configure_10gbe.py verify \
  --interface en3 --peer 192.168.10.2
```

On Windows, use the same Admin PowerShell Python invocation with `verify --interface 12 --peer 192.168.10.2`.

An echo reply reports `reachable`. An explicit OS no-route/destination-unreachable reports `unreachable`. A timeout or missing echo reply reports `unable-to-verify`, since ICMP filtering and an offline peer cannot be distinguished by that probe. Check cable/link state, negotiated speed, route selection, and host firewall policy. Use `iperf3` separately to measure throughput; reachability does not prove 10GbE performance.

---

## Option 3: Thunderbolt to Ethernet Adapter (Budget Option)

If you don't have native Thunderbolt networking, use a Thunderbolt-to-10GbE adapter.

### Setup

1. Connect Thunderbolt-to-10GbE adapter to macOS
2. Connect 10GbE NIC to Windows
3. Use a 10GbE switch or direct cable
4. Follow the 10GbE setup steps above

**Note:** Latency may be slightly higher (0.5-1 ms) compared to native Thunderbolt IP.

---

## Network Verification Checklist

After setup, verify:

```bash
# 1. Check static IPs
ifconfig | grep "192.168.10"  # macOS
# or
ipconfig | findstr "192.168.10"  # Windows

# 2. Verify from each host using that host's dedicated interface
sudo .venv/bin/python scripts/configure_10gbe.py verify --interface en3 --peer 192.168.10.2

# 3. Bandwidth test (optional)
# macOS side:
iperf3 -s -B 192.168.10.1

# Windows side (another terminal):
iperf3 -c 192.168.10.1 -t 10

# Expected: ~9 Gbps (90% of 10GbE theoretical max)
```

---

## DHCP Prevention

**Critical:** Disable DHCP on your dedicated network interface to prevent IP reassignment during long inference runs.

### macOS

```bash
# Check DHCP status
networksetup -getinfo thunderbolt1

# Disable DHCP
sudo networksetup -setdhcp thunderbolt1 off

# Or set to manual
sudo networksetup -setmanual thunderbolt1 192.168.10.1 255.255.255.0 "" ""
```

### Windows

```powershell
# Disable DHCP on the dedicated NIC
Set-NetIPInterface -InterfaceAlias "Ethernet1" -Dhcp Disabled

# Or set static via GUI:
# Settings → Network → Ethernet → Edit IP settings → Manual
```

---

## Firewall Configuration

### macOS

`kllm` selects a private interface address for RPC and registers the installed binary with the application firewall when needed.

### Windows

`kllm join` attempts to open TCP 50052. If elevation is unavailable, configure the rule from an Administrator PowerShell session:

```powershell
# Allow inbound RPC traffic
New-NetFirewallRule -Name "kllm-RPC" `
    -DisplayName "Kluster LLM RPC Worker" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 50052 `
    -Profile Private

# Verify
Get-NetFirewallRule -Name "kllm-RPC" | Format-Table DisplayName, Enabled
```

Keep this rule on the Private profile and restrict it to trusted LAN/WireGuard networks. RPC is unauthenticated upstream; never expose TCP 50052 on public networks.

---

## Troubleshooting

### "No route to host"
- Verify both hosts have IPs in the same subnet: `192.168.10.0/24`
- Check that the dedicated NIC is enabled and connected

### "Connection timed out"
- Verify firewall is not blocking TCP 50052 on Windows
- Check that the worker is running: `netstat -an | findstr 50052`

### High latency (>1 ms)
- Ensure you're using the dedicated interface, not Wi-Fi or standard Ethernet
- Check for other traffic on the network (disable other NICs if needed)
- Verify Thunderbolt/10GbE link is at full speed (check link speed in system settings)

### DHCP reassignment mid-inference
- Reboot both hosts
- Verify static IPs are still set (they may have been reset)
- Re-disable DHCP if necessary

---

## Performance Expectations

| Network Type | Latency | Bandwidth | Use Case |
|--------------|---------|-----------|----------|
| Wi-Fi (5 GHz) | 5-50 ms | 500 Mbps | Not recommended |
| Standard Ethernet (1 Gbps) | 1-5 ms | 900 Mbps | Acceptable for small models |
| 10GbE Dedicated | 0.1-0.5 ms | 9 Gbps | Recommended for production |
| Thunderbolt IP Bridge | 0.05-0.2 ms | 40 Gbps | Best performance |

For distributed inference, **Thunderbolt IP bridge is strongly recommended** due to lowest latency and highest bandwidth.

## App-managed setup

For temporary dedicated addressing, use the Home → Private 10GbE network panel. Follow the supported agent installation, install a TCP-only RPC source build with `uv run python scripts/install.py --llama-only --rpc-tcp-only` on every node, and restart the agents. Keep agents under the original account. Read the administrator notice and enable local access once on each computer through its OS approval prompt. The selected interfaces must be dedicated physical Ethernet, with readable DHCP configuration, link up and negotiated at least 1 Gbps (10 Gbps preferred).

Updated joined nodes bootstrap and pin their initial public identities automatically. Return to the master and click Set up private network; sole safe adapters and a subnet are chosen automatically. Multiple suitable adapters require a choice. Advanced details retains manual pairing and configuration. The operation prepares every participant before applying any exact temporary IPv4 address. LAN/WireGuard management, DNS profiles and default gateways remain in use. Dedicated IPv4 DHCP changes are saved and restored with the exact owned address on stop. The manual script below remains available for operator-managed configuration.

Readiness includes dedicated source/interface checks, signed bidirectional TCP diagnostics and a bounded valid idle RPC probe. The original worker RPC process remains the sole GPU owner. Inference must explicitly select the network on Plan & Run; launch endpoints are master-local loopback relays whose outbound source is the assigned dedicated IP. The default and saved model settings do not select this network.

Stop dependent inference before network stop, retry or adding participants. Unreachable cleanup is pending; lost address-observer continuity requires local review rather than automatic deletion. Use Review network recovery locally: Keep current settings preserves the current configuration and releases Kluster ownership; Restore Kluster’s changes authorizes restoration of the exact reviewed configuration. Advanced details retains manual address-only recovery. Do not expose unauthenticated upstream RPC port 50052 publicly. Real multi-node generation, cable-loss and mixed-OS acceptance must be recorded separately from local contract/socket tests.
