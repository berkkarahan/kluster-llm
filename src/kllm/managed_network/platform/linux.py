"""Linux temporary addresses; persistent network-manager profiles are read-only."""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

from ..models import NodeAssignment
from ..store import digest
from .base import AdapterSnapshot, PlatformAdapter, PlatformRefusal, run_fixed
from .inspection import INTERFACE_RE, NetworkError, _inspect_linux, _linux_dhcp


class LinuxAdapter(PlatformAdapter):
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
            raise PlatformRefusal("INVALID_ADAPTER", "Invalid Linux adapter identifier")
        path = Path("/sys/class/net") / adapter_id
        index = socket.if_nametoindex(adapter_id)
        mac = (path / "address").read_text().strip().lower()
        device = str((path / "device").resolve())
        state = _inspect_linux(
            adapter_id,
            run_fixed,
            lambda name: _linux_dhcp(name, run_fixed, shutil.which),
            Path("/sys/class/net"),
        )
        fingerprint = digest(
            {"device": device, "mac": mac, "index": index, "interface": adapter_id}
        )
        return AdapterSnapshot(adapter_id, adapter_id, index, fingerprint, mac, state)

    def add_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        run_fixed(
            [
                "ip",
                "-4",
                "address",
                "add",
                f"{assignment.address}/{assignment.prefix_length}",
                "dev",
                snapshot.os_interface,
            ]
        )

    def delete_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        run_fixed(
            [
                "ip",
                "-4",
                "address",
                "delete",
                f"{assignment.address}/{assignment.prefix_length}",
                "dev",
                snapshot.os_interface,
            ]
        )

    def check_source_route(self, snapshot: AdapterSnapshot, source: str, peer: str) -> None:
        routes = json.loads(run_fixed(["ip", "-j", "-4", "route", "get", peer, "from", source]))
        if (
            len(routes) != 1
            or routes[0].get("dev") != snapshot.os_interface
            or routes[0].get("gateway")
            or routes[0].get("prefsrc", routes[0].get("src", source)) != source
        ):
            raise PlatformRefusal(
                "MANAGEMENT_ROUTE_CONFLICT",
                "Dedicated source does not use the selected direct interface",
            )
