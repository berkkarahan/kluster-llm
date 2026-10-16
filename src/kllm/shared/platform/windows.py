"""Windows ActiveStore secondary addresses, using exact object removal."""

from __future__ import annotations

import json

from kllm.modules.managed_network.entities import NodeAssignment
from kllm.modules.managed_network.gateways.store import digest
from kllm.shared.platform.base import AdapterSnapshot, PlatformAdapter, PlatformRefusal, run_fixed
from kllm.shared.platform.inspection import NetworkError, _inspect_windows, _windows_state


def powershell(script: str, values: dict[str, str]) -> str:
    env = {f"KLLM_10GBE_{key.upper()}": value for key, value in values.items()}
    return run_fixed(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=env
    )


_INVENTORY_SCRIPT = r"""
$ErrorActionPreference='Stop'
$routes=@(Get-NetRoute -AddressFamily IPv4 -PolicyStore ActiveStore -ErrorAction SilentlyContinue | Where-Object {$_.DestinationPrefix -ne '0.0.0.0/0'} | Select-Object -ExpandProperty DestinationPrefix)
$defaults=@(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty InterfaceIndex)
$ipifs=@(Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue)
$out=@(Get-NetAdapter | ForEach-Object {
$i=[int]$_.ifIndex
$ipif=$ipifs | Where-Object {$_.InterfaceIndex -eq $i} | Select-Object -First 1
$all=@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object IPAddress,PrefixLength)
$active=@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -PolicyStore ActiveStore -ErrorAction SilentlyContinue | Select-Object IPAddress,PrefixLength,AddressState,SkipAsSource,InstanceID)
[pscustomobject]@{Index=$i;Guid=$(if($_.InterfaceGuid){$_.InterfaceGuid.ToString()}else{''});MAC=$_.MacAddress;Name=$_.Name;Status=$_.Status.ToString();LinkSpeed=$(if($_.LinkSpeed){$_.LinkSpeed.ToString()}else{''});Hardware=[bool]$_.HardwareInterface;Description=$_.InterfaceDescription;Default=($defaults -contains $i);HasIpif=[bool]$ipif;Dhcp=$(if($ipif){$ipif.Dhcp -eq 'Enabled'}else{$false});Addresses=$all;AddressStates=$active;Routes=$routes}})
ConvertTo-Json -InputObject $out -Compress -Depth 5
"""


class WindowsAdapter(PlatformAdapter):
    def inventory(self) -> list[AdapterSnapshot]:
        # One batched query: per-adapter enumeration costs a PowerShell process
        # per call, which overruns the managed-network inventory budget.
        try:
            payload = json.loads(powershell(_INVENTORY_SCRIPT, {}))
        except (OSError, ValueError, PlatformRefusal):
            payload = None
        if payload is not None:
            entries = payload if isinstance(payload, list) else [payload]
            result = []
            for entry in entries:
                try:
                    result.append(self._batched_snapshot(entry))
                except (OSError, KeyError, TypeError, ValueError, NetworkError):
                    continue
            return result
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

    def _batched_snapshot(self, entry: dict) -> AdapterSnapshot:
        """Build one snapshot from the batched payload, matching `inspect` exactly."""
        index = int(entry["Index"])
        guid = entry.get("Guid") or ""
        mac = entry.get("MAC") or ""
        if not entry.get("HasIpif") or not guid or not mac:
            raise ValueError("adapter lacks a complete Windows inspection")
        state = _windows_state(str(index), entry)
        return self._snapshot(index, guid, mac, state, entry.get("AddressStates") or [])

    def _snapshot(
        self, index: int, guid: str, mac_value: str, state, address_states: list[dict]
    ) -> AdapterSnapshot:
        mac = str(mac_value).replace("-", ":").lower()
        fingerprint = digest({"guid": guid.lower(), "mac": mac, "index": index})
        return AdapterSnapshot(
            str(index),
            str(index),
            int(index),
            fingerprint,
            mac,
            state,
            {"guid": guid, "address_states": address_states},
        )

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
        return self._snapshot(int(adapter_id), data["Guid"], data["MAC"], state, data["Addresses"])

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
