"""Windows ActiveStore secondary addresses, using exact object removal."""

from __future__ import annotations

import json

from kllm.modules.managed_network.entities import NodeAssignment
from kllm.modules.managed_network.gateways.store import digest
from kllm.shared.platform.base import AdapterSnapshot, PlatformAdapter, PlatformRefusal, run_fixed
from kllm.shared.platform.inspection import _inspect_windows


def powershell(script: str, values: dict[str, str]) -> str:
    env = {f"KLLM_10GBE_{key.upper()}": value for key, value in values.items()}
    return run_fixed(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=env
    )


class WindowsAdapter(PlatformAdapter):
    def inventory(self) -> list[AdapterSnapshot]:
        indexes = json.loads(
            powershell(
                "$ErrorActionPreference='Stop'; @(Get-NetAdapter | Select-Object -ExpandProperty ifIndex) | ConvertTo-Json -Compress",
                {},
            )
        )
        if not isinstance(indexes, list):
            indexes = [indexes]
        result = []
        for index in indexes:
            try:
                result.append(self.inspect(str(index)))
            except (OSError, ValueError, PlatformRefusal):
                continue
        return result

    def inspect(self, adapter_id: str) -> AdapterSnapshot:
        if not adapter_id.isdigit():
            raise PlatformRefusal(
                "INVALID_ADAPTER", "Windows selection requires an interface index"
            )
        state = _inspect_windows(adapter_id, powershell)
        data = json.loads(
            powershell(
                r"""
$ErrorActionPreference='Stop'
$i=[int]$env:KLLM_10GBE_INTERFACE
$a=Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop
$addresses=@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -PolicyStore ActiveStore | Select-Object IPAddress,PrefixLength,AddressState,SkipAsSource,InstanceID)
[pscustomobject]@{Guid=$a.InterfaceGuid.ToString();MAC=$a.MacAddress;Index=$a.ifIndex;Addresses=$addresses} | ConvertTo-Json -Compress -Depth 4
""",
                {"interface": adapter_id},
            )
        )
        mac = data["MAC"].replace("-", ":").lower()
        fingerprint = digest({"guid": data["Guid"].lower(), "mac": mac, "index": data["Index"]})
        return AdapterSnapshot(
            adapter_id,
            adapter_id,
            int(adapter_id),
            fingerprint,
            mac,
            state,
            {"guid": data["Guid"], "address_states": data["Addresses"]},
        )

    def conflict_preflight(self, snapshot: AdapterSnapshot, address: str) -> None:
        result = powershell(
            r"""
$ErrorActionPreference='Stop'
$i=[int]$env:KLLM_10GBE_INTERFACE
$a=$env:KLLM_10GBE_ADDRESS
$existing=@(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -eq $a})
$neighbor=@(Get-NetNeighbor -InterfaceIndex $i -AddressFamily IPv4 | Where-Object {$_.IPAddress -eq $a -and $_.State -notin @('Unreachable','Incomplete')})
if ($existing.Count -gt 0 -or $neighbor.Count -gt 0) {'conflict'} else {'no_known_conflict'}
""",
            {"interface": snapshot.os_interface, "address": address},
        ).strip()
        if result != "no_known_conflict":
            raise PlatformRefusal(
                "ADDRESS_CONFLICT", "Address or neighbor already claims the proposed IPv4"
            )

    def add_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        powershell(
            r"""
$ErrorActionPreference='Stop'
$i=[int]$env:KLLM_10GBE_INTERFACE
$a=Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop
if ($a.InterfaceGuid.ToString() -ne $env:KLLM_10GBE_GUID) {throw 'Adapter changed'}
$ipif=Get-NetIPInterface -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction Stop
if ($ipif.Dhcp -ne 'Disabled') {throw 'DHCP must remain disabled'}
New-NetIPAddress -InterfaceIndex $i -IPAddress $env:KLLM_10GBE_ADDRESS -PrefixLength ([int]$env:KLLM_10GBE_PREFIX) -PolicyStore ActiveStore -SkipAsSource $true -ErrorAction Stop | Out-Null
""",
            {
                "interface": snapshot.os_interface,
                "guid": snapshot.facts["guid"],
                "address": assignment.address,
                "prefix": str(assignment.prefix_length),
            },
        )

    def delete_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        matching = [
            item
            for item in snapshot.facts["address_states"]
            if item["IPAddress"] == assignment.address
            and item["PrefixLength"] == assignment.prefix_length
        ]
        if len(matching) != 1 or not matching[0]["SkipAsSource"]:
            raise PlatformRefusal("OWNERSHIP_CONFLICT", "Exact temporary address object changed")
        powershell(
            r"""
$ErrorActionPreference='Stop'
$i=[int]$env:KLLM_10GBE_INTERFACE
$a=Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop
if ($a.InterfaceGuid.ToString() -ne $env:KLLM_10GBE_GUID) {throw 'Adapter changed'}
$row=@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -PolicyStore ActiveStore | Where-Object {$_.IPAddress -eq $env:KLLM_10GBE_ADDRESS -and $_.PrefixLength -eq [int]$env:KLLM_10GBE_PREFIX -and $_.InstanceID -eq $env:KLLM_10GBE_INSTANCE -and $_.SkipAsSource})
if ($row.Count -ne 1) {throw 'Exact owned object changed'}
Remove-NetIPAddress -InputObject $row[0] -Confirm:$false -ErrorAction Stop
""",
            {
                "interface": snapshot.os_interface,
                "guid": snapshot.facts["guid"],
                "address": assignment.address,
                "prefix": str(assignment.prefix_length),
                "instance": matching[0]["InstanceID"],
            },
        )

    def address_usable(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> bool:
        states = [
            item["AddressState"]
            for item in snapshot.facts["address_states"]
            if item["IPAddress"] == assignment.address
            and item["PrefixLength"] == assignment.prefix_length
        ]
        if any(value in (2, "Duplicate") for value in states):
            raise PlatformRefusal(
                "ADDRESS_CONFLICT", "Native IPv4 duplicate-address state detected"
            )
        return len(states) == 1 and states[0] in (4, "Preferred")

    def check_source_route(self, snapshot: AdapterSnapshot, source: str, peer: str) -> None:
        result = powershell(
            r"""
$ErrorActionPreference='Stop'
$r=@(Find-NetRoute -RemoteIPAddress $env:KLLM_10GBE_PEER -LocalIPAddress $env:KLLM_10GBE_SOURCE -ErrorAction Stop)
if (-not $r -or @($r | Where-Object {$_.InterfaceIndex -ne [int]$env:KLLM_10GBE_INTERFACE}).Count -gt 0) {'wrong_interface'} else {'selected_interface'}
""",
            {"interface": snapshot.os_interface, "source": source, "peer": peer},
        ).strip()
        if result != "selected_interface":
            raise PlatformRefusal(
                "MANAGEMENT_ROUTE_CONFLICT", "Dedicated source does not use the selected interface"
            )
