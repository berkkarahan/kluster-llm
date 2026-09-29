# Data Model: Add 10GbE Link Setup

The script has no persistent storage. These values exist only for one invocation.

## Link Profile

Describes the local and peer endpoint on the dedicated private subnet.

| Field | Meaning | Validation |
|---|---|---|
| role | `coordinator` or `worker` | Required; selects one endpoint in the pair |
| subnet | Private IPv4 network | Must be valid IPv4 CIDR and not overlap detected local addresses/routes |
| local address | Address assigned to this host | Derived from the role; not already assigned elsewhere on the selected interface |
| peer address | Address expected on the other host | Distinct from local address and inside the same subnet |
| prefix | IPv4 network prefix | Valid for both endpoints; subnet must provide usable addresses |

The default subnet is `192.168.10.0/24`, matching the existing network guide. The coordinator receives the first usable address and the worker the second. Operators may supply another private CIDR; preflight rejects detected overlaps and networks with fewer than two usable host addresses.

## Network Interface

The OS-specific identity selected by the operator (Windows interface index/alias, macOS device, or Linux device).

- Must resolve to one existing interface.
- Must be supplied explicitly and resolve to one connected Ethernet adapter.
- OS-reported negotiated speed must be at least 10 Gbps; lower or unavailable speed fails without changes.
- DHCP-enabled Windows interfaces are rejected because adding a static address can disable DHCP.
- Existing conflicting local addresses/routes fail preflight rather than being cleared or replaced.

## Address Assignment

An ephemeral association of a local address/prefix with one selected interface. On Windows this MUST use `-PolicyStore ActiveStore` so the assignment does not enter persistent configuration.

- Created by the configure action and removed by operating-system reboot.
- Repeated configure is a no-op when the same address/prefix already exists.
- No DNS server, gateway, default route, or unrelated interface is part of the assignment.
- Failures retain the native command and diagnostic context; cleanup removes only state created by this invocation when safe.

## Verification Result

One of `reachable`, `unreachable`, or `unable-to-verify`, tied to the selected interface and peer address. An echo reply is `reachable`; an explicit OS no-route/destination-unreachable response is `unreachable`; timeout or no echo reply is `unable-to-verify`, because ICMP filtering and an offline peer cannot be distinguished by that probe. Verification never changes network state.
