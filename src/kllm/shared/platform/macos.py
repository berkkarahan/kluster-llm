"""macOS exact aliases; networksetup services, DNS and gateways are never changed."""

from __future__ import annotations

import ipaddress
import re
import socket

from kllm.modules.managed_network.entities import NodeAssignment
from kllm.modules.managed_network.gateways.store import digest
from kllm.shared.platform.base import AdapterSnapshot, PlatformAdapter, PlatformRefusal, run_fixed
from kllm.shared.platform.inspection import INTERFACE_RE, NetworkError, _inspect_macos, _mac_service


class MacOSAdapter(PlatformAdapter):
    def inventory(self) -> list[AdapterSnapshot]:
        result = []
        for _, name in socket.if_nameindex():
            try:
                result.append(self.inspect(name))
            except (OSError, ValueError, NetworkError):
                continue
        return result

    def inspect(self, adapter_id: str) -> AdapterSnapshot:
        if not INTERFACE_RE.fullmatch(adapter_id):
            raise PlatformRefusal("INVALID_ADAPTER", "Invalid macOS adapter identifier")
        service, hardware = _mac_service(adapter_id, run_fixed)
        index = socket.if_nametoindex(adapter_id)
        mac = self._mac(adapter_id)
        state = _inspect_macos(adapter_id, run_fixed)
        info = run_fixed(["networksetup", "-getinfo", service])
        gateway = re.search(r"^Router:\s*(.*)$", info, re.M)
        facts = {
            "service": service,
            "hardware": hardware,
            "dns": run_fixed(["networksetup", "-getdnsservers", service]).strip(),
            "gateway": gateway.group(1).strip() if gateway else None,
        }
        fingerprint = digest(
            {
                "service": service,
                "hardware": hardware,
                "mac": mac,
                "index": index,
                "interface": adapter_id,
            }
        )
        return AdapterSnapshot(adapter_id, adapter_id, index, fingerprint, mac, state, facts)

    @staticmethod
    def _mac(adapter_id: str) -> str:
        """Read the adapter's real Ethernet address.

        Some macOS versions redact `ifconfig` MAC values for unprivileged
        callers (02:00:00:00:00:00); networksetup reports the real address and
        remains the first choice, with ifconfig kept as a fallback.
        """
        try:
            output = run_fixed(["networksetup", "-getmacaddress", adapter_id])
        except (OSError, PlatformRefusal):
            output = ""
        match = re.search(r"Ethernet Address:\s*([0-9a-f:]{17})", output, re.I)
        if match and match.group(1).lower() != "02:00:00:00:00:00":
            return match.group(1).lower()
        output = run_fixed(["ifconfig", adapter_id])
        fallback = re.search(r"\bether\s+([0-9a-f:]{17})", output, re.I)
        if not fallback:
            raise PlatformRefusal(
                "INVALID_ADAPTER", "Adapter does not have a verifiable Ethernet MAC"
            )
        return fallback.group(1).lower()

    def add_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        network = ipaddress.IPv4Network(
            f"{assignment.address}/{assignment.prefix_length}", strict=False
        )
        run_fixed(
            [
                "ifconfig",
                snapshot.os_interface,
                "inet",
                assignment.address,
                "netmask",
                str(network.netmask),
                "alias",
            ]
        )

    def delete_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        run_fixed(["ifconfig", snapshot.os_interface, "inet", assignment.address, "-alias"])

    def check_source_route(self, snapshot: AdapterSnapshot, source: str, peer: str) -> None:
        output = run_fixed(["route", "-n", "get", peer])
        interface = re.search(r"^\s*interface:\s*(\S+)", output, re.M)
        flags = re.search(r"^\s*flags:\s*(.*)", output, re.M)
        if (
            not interface
            or interface.group(1) != snapshot.os_interface
            or (flags and "GATEWAY" in flags.group(1))
            or not any(str(address.ip) == source for address in snapshot.state.addresses)
        ):
            raise PlatformRefusal(
                "MANAGEMENT_ROUTE_CONFLICT",
                "Dedicated source does not use the selected direct interface",
            )
