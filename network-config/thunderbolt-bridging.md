# Thunderbolt IP Bridging Guide

This guide covers setting up Thunderbolt IP bridging between macOS and Windows hosts for distributed LLM inference.

---

## Why Thunderbolt IP Bridging?

Thunderbolt 3/4 natively supports IP networking, providing:

- **Ultra-low latency** (0.05-0.2 ms vs. 5-50 ms on Wi-Fi)
- **High bandwidth** (40 Gbps theoretical, ~32 Gbps actual)
- **No DHCP reassignments** (static IPs prevent state-machine collapse)
- **Direct host-to-host connection** (no switch required)

---

## Prerequisites

- **macOS host**: Mac with Thunderbolt 3+ port (2018 or newer)
- **Windows host**: PC with Thunderbolt port (or Thunderbolt-to-Ethernet adapter)
- **Thunderbolt cable**: Active or passive, ≤0.8 m for best latency
- **Administrative access**: Both hosts require admin/root privileges

---

## Step-by-Step Setup

### 1. Physical Connection

1. Connect Thunderbolt cable between macOS and Windows hosts
2. Verify connection: both hosts should show "Thunderbolt" in system info

### 2. macOS Configuration

#### 2.1 Create Thunderbolt Network Service

```bash
# List existing network services
networksetup -listnetworkserviceorder

# Create new Thunderbolt service (replace "thunderbolt1" with actual interface)
sudo networksetup -createnetworkservice "Thunderbolt IP" thunderbolt1
sudo networksetup -setemptynetworkservice "Thunderbolt IP"
```

#### 2.2 Configure Static IP

```bash
# Set static IP on Thunderbolt interface
sudo networksetup -setstaticnetworksetup thunderbolt1 192.168.10.1 255.255.255.0 "" ""

# Disable DHCP and DNS for this interface
sudo networksetup -setdhcp thunderbolt1 off
sudo networksetup -setdnsservers thunderbolt1 empty
sudo networksetup -setsearchdomains thunderbolt1 empty
```

#### 2.3 Verify Configuration

```bash
# Check IP assignment
ifconfig thunderbolt1 | grep "inet "

# Expected output: inet 192.168.10.1 netmask 0xffffff00
```

### 3. Windows Configuration

#### 3.1 Identify Thunderbolt NIC

```powershell
# List all network adapters
Get-NetAdapter | Format-Table Name, InterfaceDescription, MacAddress

# Look for adapters with "Thunderbolt" or "Intel" in description
```

#### 3.2 Set Static IP

```powershell
# Replace "Ethernet1" with actual Thunderbolt NIC name
New-NetIPAddress -IPAddress 192.168.10.2 -PrefixLength 24 -InterfaceAlias "Ethernet1"

# Disable DHCP and DNS
Set-NetIPInterface -InterfaceAlias "Ethernet1" -Dhcp Disabled
Set-DnsClientServerAddress -InterfaceAlias "Ethernet1" -ServerAddress ""
```

#### 3.3 Verify Configuration

```powershell
# Check IP assignment
Get-NetIPAddress -InterfaceAlias "Ethernet1" | Format-Table IPAddress, PrefixLength

# Expected output: IPAddress = 192.168.10.2, PrefixLength = 24
```

### 4. Test Connectivity

#### 4.1 Ping Test

```bash
# From macOS, ping Windows worker
ping -S 192.168.10.1 192.168.10.2 -c 10

# Expected output:
# 10 packets transmitted, 10 received, 0% packet loss
# rtt min/avg/max = 0.05/0.08/0.12 ms
```

#### 4.2 Bandwidth Test (Optional)

```bash
# macOS side: start iperf3 server
iperf3 -s -B 192.168.10.1

# Windows side: run iperf3 client (in another terminal)
iperf3 -c 192.168.10.1 -t 10

# Expected: ~30-32 Gbps (90% of Thunderbolt theoretical max)
```

---

## Troubleshooting

### "No route to host"
- Verify both hosts have IPs in same subnet (192.168.10.0/24)
- Check Thunderbolt cable is securely connected
- Verify Thunderbolt service is enabled in System Settings

### "Connection timed out"
- Check Windows Firewall is not blocking ICMP/ping
- Verify worker process is running and listening on correct port
- Check network interface is "up" (`ifconfig thunderbolt1` on macOS)

### "High latency (>1 ms)"
- Ensure you're using Thunderbolt interface, not Wi-Fi or Ethernet
- Disable other network interfaces (Wi-Fi, Ethernet) to prevent routing conflicts
- Verify Thunderbolt link speed in System Information

### "DHCP reassignment mid-inference"
- Reboot both hosts
- Verify static IPs are still set (they may reset after reboot)
- Re-disable DHCP if necessary

---

## Advanced Configuration

### Disable Other Interfaces

To prevent routing conflicts, disable non-Thunderbolt interfaces:

```bash
# macOS: disable Wi-Fi and Ethernet
sudo networksetup -setairportpower airport off
sudo networksetup -setdhcp en0 off
sudo networksetup -setdhcp en1 off
```

```powershell
# Windows: disable Ethernet and Wi-Fi
Disable-NetAdapter -Name "Ethernet" -Confirm:$false
Disable-NetAdapter -Name "Wi-Fi" -Confirm:$false
```

### Firewall Configuration

#### macOS (Coordinator)

No firewall changes needed if both hosts are on same subnet. The RPC server binds to `0.0.0.0`.

#### Windows (Worker)

```powershell
# Allow inbound RPC traffic on TCP 50052
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

## Performance Expectations

| Metric | Thunderbolt IP Bridge | Standard Ethernet | Wi-Fi (5 GHz) |
|--------|----------------------|-------------------|---------------|
| Latency | 0.05-0.2 ms | 1-5 ms | 5-50 ms |
| Bandwidth | 30-32 Gbps | 900 Mbps | 500 Mbps |
| Packet Loss | 0% | <0.1% | 0.1-1% |
| DHCP Risk | None (static) | Low | High |

**Thunderbolt IP bridge is strongly recommended** for distributed inference due to lowest latency and highest bandwidth.

---

## Security Considerations

### Network Isolation

Thunderbolt IP bridge creates a dedicated network segment:

```
macOS (192.168.10.1) ←── Thunderbolt Cable ──→ Windows (192.168.10.2)
                         192.168.10.0/24
```

This isolates inference traffic from other network activity, preventing:
- Bandwidth contention
- DHCP reassignments
- Network interface switching

### Firewall Rules

Only allow necessary traffic:

```powershell
# Block all traffic except RPC on TCP 50052
New-NetFirewallRule -Name "Block All - Default" `
    -DisplayName "Block All Inbound - Default" `
    -Direction Inbound `
    -Action Block `
    -Profile Any

# Allow RPC traffic
New-NetFirewallRule -Name "Allow RPC" `
    -DisplayName "Allow Kluster LLM RPC" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 50052 `
    -Profile Any
```

---

## Alternative: Thunderbolt-to-Ethernet Adapter

If you don't have native Thunderbolt ports, use a Thunderbolt-to-10GbE adapter:

1. Connect Thunderbolt-to-10GbE adapter to macOS
2. Connect 10GbE NIC to Windows
3. Use 10GbE switch or direct cable
4. Follow 10GbE setup steps (see `10gbe-bridging-guide.md`)

**Note:** Latency may be slightly higher (0.5-1 ms) compared to native Thunderbolt IP.
