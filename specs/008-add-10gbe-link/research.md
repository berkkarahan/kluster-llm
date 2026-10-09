# Research: Add 10GbE Link Setup

## Decision 1: Configure through native commands with a single Python CLI

- **Decision**: Use Python's standard library for arguments, platform detection, subprocess invocation, and diagnostics. Call platform-native network tools with argument arrays and no shell interpolation.
- **Rationale**: The repository already uses Python scripts and `argparse`/`subprocess`; OS interfaces differ, but no new dependency is needed.
- **Alternatives considered**: A platform-specific script per OS duplicates the user workflow; a new dependency cannot remove OS privilege and network-manager differences.

## Decision 2: Temporary interface-scoped assignment

- **Decision**: Configure an address only on the selected interface and do not persist it across reboot. The operator must run the tool again after reboot.
- **Rationale**: Linux persistence varies by distribution/network manager; macOS's documented persistent service command requires a router argument; Windows persistent configuration is the default unless `-PolicyStore ActiveStore` is explicitly used. Temporary operations give a consistent, reversible cross-platform scope and avoid editing unknown network-manager configuration.
- **Alternatives considered**: Persistent setup would require platform/network-manager-specific configuration and rollback behavior beyond the request; manual configuration remains documented for operators who need persistence.

## Decision 3: Refuse unsafe or ambiguous targets

- **Decision**: Require an explicit interface argument. Preflight the interface, address, routes, and DHCP state. Refuse DHCP-enabled Windows adapters and conflicting configuration rather than disabling DHCP, flushing addresses, or replacing routes.
- **Rationale**: Microsoft documents that `New-NetIPAddress` disables DHCP on a DHCP-enabled interface. Broad resets risk disconnecting a host or changing DNS behavior.
- **Alternatives considered**: Automatic adapter choice is ambiguous on multi-NIC hosts; forcing DHCP off or clearing addresses violates the non-destructive requirement.

## Decision 4: Require verified 10Gbps link speed

- **Decision**: Require a connected adapter whose OS-reported negotiated link speed is at least 10 Gbps before configuration; refuse to configure when speed is lower or unavailable.
- **Rationale**: Configuring an address on a 1Gbps fallback link would falsely present the requested 10GbE setup as complete. Hardware capability alone does not prove the current cable, peer, and negotiated mode provide 10Gbps.
- **Alternatives considered**: A warning-only mode can leave operators believing a slower or unverified link meets the feature's 10GbE goal.

## Decision 5: Keep verification separate from configuration

- **Decision**: Provide a verification action that tests reachability through the selected interface/source address and makes no configuration changes. An echo reply means `reachable`; an explicit OS route/destination-unreachable response means `unreachable`; timeout/no reply means `unable-to-verify` because ICMP filtering and an offline peer cannot be distinguished by that probe.
- **Rationale**: A successful ping through Wi-Fi does not validate the dedicated link, and configuration should not mutate state during diagnosis.
- **Alternatives considered**: Throughput testing with `iperf3` is optional hardware/performance validation and should not be a script dependency.

## Platform evidence

- **Windows**: `Get-NetAdapter`/`Get-NetIPAddress` for inspection; require `Get-NetAdapter.LinkSpeed` to report at least 10 Gbps. Add addresses with `New-NetIPAddress -PolicyStore ActiveStore`, an explicit interface, and no gateway. The command requires elevation; DHCP-enabled interfaces are rejected before mutation.
- **macOS**: `networksetup -listnetworkserviceorder` maps the selected device to its service and `networksetup -getmedia` reports current/active media where supported; require a reported negotiated speed of at least 10 Gbps. Use `ifconfig <device> inet <address> netmask <mask>` for temporary device-scoped assignment. If media speed cannot be established, refuse configuration.
- **Linux**: Read `/sys/class/net/<interface>/speed` for the negotiated speed where exposed and require at least 10000 Mbps. Use `ip address add <address>/<prefix> dev <interface>` for a temporary address; use `ip address show`/`ip route get` for inspection. If speed is unavailable, refuse configuration; do not flush addresses or write distribution-specific persistent files.
- **Address collision**: Check local addresses/routes and let platform duplicate-address detection operate; a preflight probe cannot prove that an absent/asleep peer does not own an address. Explain the limitation rather than promising conflict-free allocation.

## Sources

- Microsoft Learn: [New-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/new-netipaddress), [Get-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipaddress), [Get-NetAdapter](https://learn.microsoft.com/en-us/powershell/module/netadapter/get-netadapter)
- macOS `networksetup(8)` and `ifconfig(8)` manuals: https://manp.gs/mac/8/networksetup and https://manp.gs/mac/8/ifconfig
- Linux `ip-address(8)` and `ip-route(8)` manuals: https://man7.org/linux/man-pages/man8/ip-address.8.html and https://man7.org/linux/man-pages/man8/ip-route.8.html
- RFC 1918 private IPv4 allocation: https://www.rfc-editor.org/rfc/rfc1918.html
- RFC 5227 IPv4 address conflict detection: https://www.rfc-editor.org/rfc/rfc5227.html
