# Windows participant

Use only this computer's actual Ethernet adapter index. Ask for the Windows version and whether a corporate policy manages networking if either affects the available settings. Run read-only inspection before changing anything:

```powershell
Get-NetAdapter | Format-Table Name, InterfaceIndex, Status, LinkSpeed, InterfaceDescription
Get-NetIPInterface -AddressFamily IPv4 | Format-Table InterfaceIndex, InterfaceAlias, Dhcp, ConnectionState
Get-NetRoute -AddressFamily IPv4 | Format-Table InterfaceIndex, DestinationPrefix, NextHop
Get-NetIPConfiguration -InterfaceIndex <selected-index>
```

Record the selected adapter's addresses/prefixes, DHCP state, DNS and routes; identify the internet and cluster-management route using the current master address. The adapter must be physical, connected, and negotiate at least 1 Gbps (10 Gbps preferred); it must not carry either management path. Numeric indexes are local to each computer and can change after device replacement.

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

## Manual switched network

Use this fallback only for a requested manual setup. A switch permits more than two nodes, so choose one unused private subnet and assign a unique address to every participant; do not reuse the two-role script's fixed worker address. A 10GbE switch does not make 1GbE adapters negotiate at 10 Gbps.

1. Inspect each computer's actual adapter, negotiated speed, DHCP/address state, and routes. Confirm the chosen adapter is physical, connected at >=1 Gbps, and carries neither the default route nor cluster management. Check every participant for an overlapping route or address before selecting the subnet. Record the original DHCP, address, DNS, and route settings.
2. If DHCP must be disabled to perform the requested manual setup, change it only on the dedicated adapter and only after recording the original state. Assign a unique address on each node without a default gateway:

   ```powershell
   Set-NetIPInterface -InterfaceIndex <selected-index> -AddressFamily IPv4 -Dhcp Disabled
   New-NetIPAddress -InterfaceIndex <selected-index> -IPAddress <node-private-ip> -PrefixLength <prefix>
   ```

   Do not pass `-DefaultGateway` or change DNS. For example, use distinct host addresses such as `.1`, `.2`, and `.3` only after confirming the chosen `/24` has no overlap on every computer.
3. Keep ordinary management on its existing interface. Configure the master to advertise/bind its own private address and rejoin workers using the private master address plus each worker's own `--rpc-bind`. On the app's preferred private subnets (`10.10.10.0/24` first, then the documented fallbacks) `kllm start` binds the dedicated address automatically; for any other subnet pass `--rpc-bind <master-private-ip>`. Use `kllm-cluster` for lifecycle commands; keep join keys out of logs/history. Confirm worker status points at the private master and that accepted heartbeats are fresh, not only persisted membership.
4. On Windows nodes that accept inbound RPC, keep TCP 50052 scoped to that node's private address, the private subnet, and the selected Ethernet interface. For an existing local allow rule, confirm it is TCP 50052, then modify its `PersistentStore` filters with local administrator approval and verify the effective `ActiveStore` filters:

   ```powershell
   $rule = Get-NetFirewallRule -PolicyStore PersistentStore -Name '<existing-rule-name>'
   Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule
   Set-NetFirewallAddressFilter -InputObject (Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule) -LocalAddress '<node-private-ip>' -RemoteAddress '<private-subnet>'
   Set-NetFirewallInterfaceFilter -InputObject (Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $rule) -InterfaceAlias '<dedicated-interface>'
   $active = Get-NetFirewallRule -PolicyStore ActiveStore -Name '<existing-rule-name>'
   Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $active
   Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $active
   ```

   Do not create or retain an Any/Any rule. If the effective filters remain broad or policy prevents the change, stop and report it rather than claiming RPC is restricted. TCP 50052 has no listener until inference starts, so only test it during an active run.
5. Verify control TCP connectivity using the private peer address and confirm the selected interface is Ethernet. A ping timeout alone does not establish link failure. Keep the default Wi-Fi route unchanged, then record negotiated link speed separately from throughput.

For restore, remove only this node's recorded private address. Re-enable DHCP or restore DNS/routes only when this setup changed them and no external change conflicts.

## Restore

For managed setup stop dependent inference, then use the dashboard's Stop/recovery workflow. For manual setup, compare current settings with the recorded baseline and remove only the exact IP/prefix and any narrowly scoped rule created in this attempt. Use `Remove-NetIPAddress` with the selected index and exact owned address, not all addresses on the adapter. Re-enable DHCP only if it was originally enabled and no intervening change conflicts; restore other settings only when this attempt changed them. Preserve and review externally changed state.

Official command references: [Get-NetAdapter](https://learn.microsoft.com/en-us/powershell/module/netadapter/get-netadapter), [Get-NetIPInterface](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipinterface), [Set-NetIPInterface](https://learn.microsoft.com/en-us/powershell/module/nettcpip/set-netipinterface), [Remove-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/remove-netipaddress).
