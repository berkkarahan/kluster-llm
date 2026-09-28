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

#### 1. macOS Side

```bash
# List Ethernet interfaces
networksetup -listnetworkserviceorder

# Configure static IP on the dedicated 10GbE NIC (replace "en3" with actual)
sudo networksetup -setstaticnetworksetup en3 192.168.10.1 255.255.255.0 "" ""

# Disable other services to prevent interference
sudo networksetup -setdnsservers en3 empty
```

#### 2. Windows Side

```powershell
# Find the 10GbE NIC
Get-NetAdapter | Where-Object { $_.Speed -gt 1000000000 } | Format-Table Name, Speed

# Set static IP (replace "Ethernet2" with actual name)
New-NetIPAddress -IPAddress 192.168.10.2 -PrefixLength 24 -InterfaceAlias "Ethernet2"

Set-NetIPConfiguration -InterfaceAlias "Ethernet2" -DnsDomain ""
```

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

# 2. Ping test (sub-millisecond expected)
ping -S 192.168.10.1 192.168.10.2 -c 10

# Expected output:
# 10 packets transmitted, 10 received, 0% packet loss, time 0ms
# rtt min/avg/max = 0.05/0.08/0.12 ms

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
    -Profile Any

# Verify
Get-NetFirewallRule -Name "kllm-RPC" | Format-Table DisplayName, Enabled
```

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
