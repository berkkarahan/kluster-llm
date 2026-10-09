# Quickstart: Validate 10GbE Link Setup

## Prerequisites

- Two hosts on Windows, macOS, or Linux with 10GbE adapters and a connected cable or switch.
- A clean, explicitly selected dedicated adapter on each host.
- DHCP must already be disabled on each selected adapter; the script refuses to change DHCP settings.
- Each selected adapter must report a connected link negotiating at least 10 Gbps; setup refuses lower or unavailable reported speed.
- Administrative privileges (elevated PowerShell, or `sudo` on macOS/Linux).
- A private subnet that does not overlap either host's existing addresses or routes.
- Repository environment available for running Python tests.

## Validate automated behavior

```bash
uv run pytest tests/test_configure_10gbe.py -q
```

Expected: deterministic tests pass without changing the machine's network configuration. Cases cover platform command construction, explicit interface targeting, idempotence, unsafe DHCP/conflict refusal, and failures.

## Configure and verify two hosts

Choose an unused private subnet, then use the same subnet and opposite roles on both hosts. Example below uses `192.168.10.0/24`; first check it does not overlap existing routes or addresses.

```text
Host A (coordinator): configure --interface <dedicated-NIC> --role coordinator --subnet 192.168.10.0/24
Host B (worker):      configure --interface <dedicated-NIC> --role worker --subnet 192.168.10.0/24
```

Run the command elevated on both hosts. Each command reports its assigned address and the peer address. From each host, run `verify` for the other host's reported address on the same selected interface.

Expected: both commands report successful assignment; both peer checks report reachable. A timeout/no echo reply reports `unable-to-verify`, not proof that the peer is down; an explicit no-route/destination-unreachable result reports `unreachable`. Inspect adapter link state, negotiated speed, route selection, and host firewall policy.

## Recovery and limits

- The assignment is temporary and disappears after reboot. Rerun the configure command as needed.
- If configuration reports a DHCP or overlap conflict, do not force it; select a dedicated adapter or choose a non-overlapping subnet.
- The tool does not configure persistent addresses, DNS, gateways, firewall rules, or throughput. Use `iperf3` separately when measuring bandwidth.
- Keep RPC on a private LAN or WireGuard overlay; do not expose TCP 50052 publicly.

## Platform integration follow-up

Before claiming cross-platform validation, run configure and verify on one Windows, one macOS, and one Linux host with actual NICs. Start a timer immediately before invoking configure after the interface has been identified; configuration output must arrive in under five minutes on each OS. Confirm each OS reports negotiated speed >=10 Gbps, the Windows command uses the `ActiveStore` only (no persistent-store entry), and reboot removes the assignment on every OS. Confirm default route/DNS remain unchanged. Exercise 10GbE throughput only with suitable hardware and a separate reproducible `iperf3` setup.

### Validation availability for this implementation

| Platform | Observed adapter/link | Configure/verify | Timing and reboot checks |
|---|---|---|---|
| Windows | Ethernet interface 7 is up at 1 Gbps; no connected adapter reporting >=10 Gbps was available | Not run; configuration correctly requires >=10 Gbps before mutation | Not measured |
| macOS | Host unavailable | Not run | Not measured |
| Linux | Host unavailable | Not run | Not measured |

No live network settings were changed. Cross-platform host validation remains outstanding; mocked platform tests do not count as OS integration passes.
