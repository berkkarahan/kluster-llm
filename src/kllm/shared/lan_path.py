"""LAN reachability diagnostics for hosts that filter network clients.

macOS Local Network privacy and Zscaler both filter third-party executables at
the socket layer: ``connect()``/multicast calls fail with ``EHOSTUNREACH`` even
though routing, the gateway, and the master are healthy. Apple platform
binaries (for example ``/usr/bin/python3``) keep LAN access, while the
virtualenv interpreter and binaries shipped by Kluster may not.

This module reports the detected tunnel environment and the advice that the
join/browse surfaces should show. It never changes network state.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from dataclasses import dataclass

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_TUNNEL_PREFIXES = ("utun", "ipsec", "tun", "tap")


@dataclass(frozen=True)
class LanPath:
    zscaler: bool
    tunnel_interfaces: tuple[str, ...]

    def advice(self) -> list[str]:
        tips: list[str] = []
        if self.zscaler:
            tips.append(
                "Zscaler tunnel detected: third-party executables may be blocked from "
                "the LAN; Kluster retries discovery through platform helpers"
            )
            tips.append(
                "ask IT for a Zscaler tunnel bypass of this LAN subnet, or pass --master"
            )
        elif self.tunnel_interfaces:
            names = ", ".join(self.tunnel_interfaces[:3])
            tips.append(f"tunnel interfaces present ({names}); LAN traffic may be filtered")
        return tips


def _process_names() -> list[str]:
    try:
        if sys.platform == "win32":
            output = subprocess.check_output(
                ["tasklist", "/fo", "csv", "/nh"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            )
        else:
            output = subprocess.check_output(
                ["ps", "-axo", "comm="],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip().lower() for line in output.splitlines() if line.strip()]


def _route_gateways() -> list[str]:
    if sys.platform == "win32":
        return []
    try:
        output = subprocess.check_output(
            ["netstat", "-rn"], text=True, timeout=5, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gateways: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) > 1 and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", parts[1]):
            gateways.append(parts[1])
    return gateways


def tunnel_interfaces() -> tuple[str, ...]:
    try:
        names = [name for _, name in socket.if_nameindex()]
    except OSError:
        return ()
    return tuple(sorted(name for name in names if name.startswith(_TUNNEL_PREFIXES)))


def detect() -> LanPath:
    processes = _process_names()
    zscaler = any("zscaler" in name for name in processes)
    if not zscaler:
        for gateway in _route_gateways():
            try:
                if ipaddress.ip_address(gateway) in _CGNAT:
                    zscaler = True
                    break
            except ValueError:
                continue
    return LanPath(zscaler=zscaler, tunnel_interfaces=tunnel_interfaces())


def advice() -> list[str]:
    return detect().advice()
