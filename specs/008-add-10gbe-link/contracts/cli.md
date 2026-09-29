# CLI Contract: 10GbE Link Setup

The tool is a repository script invoked from the checkout root. Exact option spelling may be finalized during implementation, but the public behaviors are:

```text
python scripts/configure_10gbe.py configure --interface <id> --role coordinator|worker [--subnet <private-cidr>]
python scripts/configure_10gbe.py verify --interface <id> --peer <ipv4-address>
```

## Configure

- Requires an explicitly selected interface and endpoint role; it never auto-selects an adapter.
- Defaults to `192.168.10.0/24` (the existing guide's subnet) unless an explicit private CIDR is supplied; coordinator uses the first usable host address and worker the second. Rejects subnets with fewer than two usable host addresses.
- Requires elevation and rejects unsupported OS, missing/disconnected interfaces, negotiated speed below 10 Gbps or unavailable speed, DHCP-enabled Windows interfaces, and detected conflicts before changing state.
- Adds only the selected interface's temporary IPv4 address. On Windows it MUST pass `-PolicyStore ActiveStore` to `New-NetIPAddress`. It does not configure DNS, a gateway, a default route, firewall rules, or other interfaces.
- Reports the local and peer addresses and that the assignment lasts until reboot.
- Exit status `0` means the requested address is present; nonzero means no successful configuration and a diagnostic was reported.

## Verify

- Requires an interface and peer IPv4 address.
- Tests reachability using the selected interface/source address; does not alter configuration.
- Reports `reachable` only after an echo reply; reports `unreachable` only when the OS explicitly returns no-route/destination-unreachable; reports `unable-to-verify` on timeout/no reply because ICMP filtering and an offline peer cannot be distinguished by the probe.
- Returns `0` only for verified reachability; returns nonzero otherwise.

The tool MUST NOT open RPC port 50052 or modify host firewall policy.
