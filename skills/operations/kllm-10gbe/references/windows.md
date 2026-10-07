# Windows participant

Use only this computer's actual Ethernet adapter index. Ask for the Windows version and whether a corporate policy manages networking if either affects the available settings. Run read-only inspection before changing anything:

```powershell
Get-NetAdapter | Format-Table Name, InterfaceIndex, Status, LinkSpeed, InterfaceDescription
Get-NetIPInterface -AddressFamily IPv4 | Format-Table InterfaceIndex, InterfaceAlias, Dhcp, ConnectionState
Get-NetRoute -AddressFamily IPv4 | Format-Table InterfaceIndex, DestinationPrefix, NextHop
Get-NetIPConfiguration -InterfaceIndex <selected-index>
```

Record the selected adapter's addresses/prefixes, DHCP state, DNS and routes; identify the internet and cluster-management route using the current master address. The adapter must be physical, connected, with its negotiated speed recorded (any working speed is acceptable; 10 Gbps is preferred), and must not carry either management path. Numeric indexes are local to each computer and can change after device replacement.

## Guided setup

Install/update through `docs/agent-install.md`. The helper is staged under `vendor/bin` as `kllm-network-helper.exe`. In the local Kluster dashboard, read the administrator notice and enable access. The user completes UAC locally; the master cannot approve another computer's UAC prompt. The protected helper uses a SYSTEM scheduled task while the ordinary agent stays in the original account.

After all nodes have enabled access, refresh on the master and use guided setup. The helper's firewall rule is limited to the dedicated address/interface, signed private peers and network ports. Preserve existing rules and report group-policy refusals. Do not disable Windows Firewall or switch all network profiles to Private.

## Requested manual two-computer link

Explain and record the exact chosen interface and unused private subnet before requesting an Administrator PowerShell session. If DHCP is enabled on this dedicated adapter, and changing it is part of the user's requested manual setup, disable it only there:

```powershell
Set-NetIPInterface -InterfaceIndex <selected-index> -AddressFamily IPv4 -Dhcp Disabled
```

Then run the guarded script from the checkout with the node's own role/index:

```powershell
& .\.venv\Scripts\python.exe scripts\configure_10gbe.py configure --interface <selected-index> --role <coordinator-or-worker> --subnet <unused-private-cidr>
& .\.venv\Scripts\python.exe scripts\configure_10gbe.py verify --interface <selected-index> --peer <peer-private-ip>
```

The script checks speed, configuration and conflicts; it adds the address to ActiveStore without a default gateway or DNS change. The added address is temporary, but disabling DHCP persists. Do not mark the setup persistent or reboot-safe without separately verifying those settings.

If a reply is absent, inspect the peer's selected interface, source route and applicable firewall rules. Use `Test-NetConnection <peer-ip> -Port <expected-port>` only when a listener is intentionally active. No RPC listener on an idle joined worker is normal. Do not add a broad public TCP 50052 rule to fix a timeout.

## Restore

For managed setup stop dependent inference, then use the dashboard's Stop/recovery workflow. For manual setup, compare current settings with the recorded baseline and remove only the exact IP/prefix and any narrowly scoped rule created in this attempt. Use `Remove-NetIPAddress` with the selected index and exact owned address, not all addresses on the adapter. Re-enable DHCP only if it was originally enabled and no intervening change conflicts; restore other settings only when this attempt changed them. Preserve and review externally changed state.

Official command references: [Get-NetAdapter](https://learn.microsoft.com/en-us/powershell/module/netadapter/get-netadapter), [Get-NetIPInterface](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipinterface), [Set-NetIPInterface](https://learn.microsoft.com/en-us/powershell/module/nettcpip/set-netipinterface), [Remove-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/remove-netipaddress).
