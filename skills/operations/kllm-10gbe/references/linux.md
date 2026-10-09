# Linux participant

Identify the distribution, dedicated device, actual negotiated speed and its network manager before proposing configuration:

```bash
ip -br link
ip -br address
ip -4 route
ip route get <master-management-ip>
ethtool <selected-device>
```

If `ethtool` is absent or access is refused, obtain speed through the supported OS/application inspection path; do not replace unknown speed with 10 Gbps. Record the selected interface's addresses, DHCP/profile state, routes and DNS. Verify it is physical, connected, with a known negotiated speed of at least 1 Gbps (10 Gbps preferred), and excluded from the internet/default and cluster-management path.

Determine whether NetworkManager or systemd-networkd actually owns this interface. The presence of a binary/service alone is insufficient:

```bash
nmcli device status
nmcli -f GENERAL,IP4 device show <selected-device>
nmcli connection show
networkctl status <selected-device>
```

Run only commands applicable to installed managers. Identify the active connection UUID or matched `.network` file; do not guess ownership from an interface name. Custom DHCP clients, ambiguous profiles or a different manager need manual review.

## Guided setup

The supported build installs the standalone helper; local enablement uses `pkexec` and a protected systemd service with grants/journals under `/etc/kllm-network`. Keep the ordinary agent in its original account. Present the local administrator notice and let the user complete authorization on this node.

Guided DHCP transitions support a readable NetworkManager connection or systemd-networkd network file. After local enablement on all participants, refresh and start setup on the master. If the manager cannot be identified or the current profile mixes ambiguous DHCP/static settings, explain the refusal; do not switch managers or overwrite system-wide network configuration as a shortcut.

## Requested manual two-computer link

Capture the original active profile/config file and create a reviewed private address plan first. Prefer a separate dedicated profile over overwriting an unrelated NetworkManager profile. A concrete NetworkManager example, with every placeholder replaced after inspection, is:

```bash
sudo nmcli connection add type ethernet ifname <selected-device> con-name <new-unique-profile-name> ipv4.method manual ipv4.addresses <private-ip/prefix> ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.never-default yes connection.autoconnect no
sudo nmcli connection up <new-profile-uuid>
```

Check that the name is unused and record the UUID created by the first command. Record the previously active UUID before activation. Keep gateway and DNS unset on the new dedicated profile. This creates a persistent profile with autoconnect off; it must be explicitly activated again after reboot. Do not describe it as a temporary kernel-only address.

For systemd-networkd, inspect the exact matched file and any drop-ins before editing. Prepare a backup and minimal interface-specific change, using `[Match] Name=<selected-device>` and a reviewed `[Network]` manual `Address=<private-ip/prefix>` with `DHCP=no`. A newly dedicated profile should have no gateway/DNS. Never apply a wildcard match or reload a guessed global configuration. Review the diff and other pending configuration changes against the installed manuals. Where supported, use `networkctl reload --no-reconfigure`, then `networkctl reconfigure <selected-device>`. Older versions lack that flag and `reload` can reconfigure every interface matching changed files; proceed only after establishing that the selected dedicated interface is the sole affected one. Conflicting manager ownership is a stopping condition for that configuration path.

Once DHCP is demonstrably disabled, use the guarded script as appropriate:

```bash
sudo .venv/bin/python scripts/configure_10gbe.py configure --interface <selected-device> --role <coordinator-or-worker> --subnet <unused-private-cidr>
sudo .venv/bin/python scripts/configure_10gbe.py verify --interface <selected-device> --peer <peer-private-ip>
```

Already configured exact addresses remain unchanged. A script-added address is temporary until reboot; separately created OS profiles are not. Use this two-role script only for two computers. Verify both directions through the chosen interface; do not infer a link failure solely from ICMP timeout or open a public RPC listener.

## Restore

For managed setup stop inference and use dashboard Stop/recovery. For manual setup, compare against the baseline and remove only the exact owned kernel delta (`ip address del <added-ip/prefix> dev <selected-device>`). For a newly created NetworkManager profile, deactivate/delete only its recorded UUID, then reactivate the prior UUID if one existed. Restore only the exact networkd file/drop-in changed in this attempt and reconfigure that interface. Do not flush addresses/routes, delete unrelated profiles, replace `/etc/resolv.conf`, or change all interfaces. Preserve uncertain/external changes for local review.

Official references: [nmcli](https://www.networkmanager.dev/docs/api/latest/nmcli.html), [NetworkManager settings](https://www.networkmanager.dev/docs/api/latest/nm-settings-nmcli.html), [systemd.network](https://www.freedesktop.org/software/systemd/man/latest/systemd.network.html), [networkctl](https://www.freedesktop.org/software/systemd/man/latest/networkctl.html). Prefer the installed manuals for distribution-specific versions.
