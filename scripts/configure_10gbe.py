#!/usr/bin/env python3
"""Configure and verify a temporary private 10GbE link on Windows, macOS, or Linux."""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
from typing import Sequence

DEFAULT_SUBNET = "192.168.10.0/24"
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
SYS_CLASS_NET = Path("/sys/class/net")
INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")


class NetworkError(Exception):
    """Expected setup/verification failure suitable for an operator-facing message."""


@dataclass(frozen=True)
class LinkProfile:
    role: str
    subnet: ipaddress.IPv4Network
    local: ipaddress.IPv4Address
    peer: ipaddress.IPv4Address


@dataclass(frozen=True)
class InterfaceState:
    name: str
    connected: bool
    speed_mbps: int | None
    dhcp: bool | None
    addresses: tuple[ipaddress.IPv4Interface, ...]
    routes: tuple[ipaddress.IPv4Network, ...]
    physical_ethernet: bool = True


def make_profile(role: str, subnet: str = DEFAULT_SUBNET) -> LinkProfile:
    if role not in {"coordinator", "worker"}:
        raise NetworkError("role must be coordinator or worker")
    try:
        network = ipaddress.ip_network(subnet, strict=True)
    except ValueError as exc:
        raise NetworkError(f"invalid subnet {subnet!r}: {exc}") from exc
    if not isinstance(network, ipaddress.IPv4Network) or not any(
        network.subnet_of(private) for private in PRIVATE_NETWORKS
    ):
        raise NetworkError("subnet must be within an RFC 1918 private IPv4 range")
    hosts = iter(network.hosts())
    try:
        first, second = next(hosts), next(hosts)
    except StopIteration as exc:
        raise NetworkError("subnet must provide at least two usable host addresses") from exc
    local, peer = (first, second) if role == "coordinator" else (second, first)
    return LinkProfile(role, network, local, peer)


def routes_overlap(subnet: ipaddress.IPv4Network, routes: Sequence[ipaddress.IPv4Network]) -> bool:
    return any(route.prefixlen and subnet.overlaps(route) for route in routes)


def parse_speed_mbps(value: str | int | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    # macOS networksetup reports media names such as "10Gbase-T".
    media = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*G(?:base|bps)", text, re.IGNORECASE)
    if media:
        return int(float(media.group(1)) * 1000)
    number = re.search(r"(\d+(?:\.\d+)?)\s*(T|G|M|K)?(?:bit/s|bps|b/s)?", text, re.IGNORECASE)
    if not number:
        return None
    amount = float(number.group(1))
    unit = (number.group(2) or "M").upper()
    multiplier = {"T": 1_000_000, "G": 1000, "M": 1, "K": 0.001}[unit]
    return int(amount * multiplier)


def _run(args: Sequence[str], *, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            list(args), text=True, capture_output=True, timeout=15, check=False, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkError(f"could not run {args[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise NetworkError(f"{args[0]} failed ({result.returncode}): {detail or 'no details'}")
    return result.stdout.strip()


def _powershell(script: str, values: dict[str, str]) -> str:
    env = os.environ.copy()
    env.update({f"KLLM_10GBE_{key.upper()}": value for key, value in values.items()})
    return _run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=env,
    )


def _v4_routes(values: Sequence[str]) -> tuple[ipaddress.IPv4Network, ...]:
    routes = []
    for value in values:
        if value in {"default", "0.0.0.0/0"}:
            continue
        try:
            route = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if isinstance(route, ipaddress.IPv4Network):
            routes.append(route)
    return tuple(routes)


def _inspect_windows(interface: str) -> InterfaceState:
    if not interface.isdigit():
        raise NetworkError("Windows --interface must be the adapter's numeric interface index")
    script = r"""
$ErrorActionPreference = 'Stop'
$i = [int]$env:KLLM_10GBE_INTERFACE
$a = Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop
$ipif = Get-NetIPInterface -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction Stop
$addresses = @(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ IPAddress=$_.IPAddress; PrefixLength=$_.PrefixLength } })
$routes = @(Get-NetRoute -AddressFamily IPv4 -PolicyStore ActiveStore -ErrorAction SilentlyContinue | Where-Object { $_.DestinationPrefix -ne '0.0.0.0/0' } | Select-Object -ExpandProperty DestinationPrefix)
[pscustomobject]@{ Name=$a.Name; Status=$a.Status.ToString(); LinkSpeed=$a.LinkSpeed.ToString(); Hardware=[bool]$a.HardwareInterface; Description=$a.InterfaceDescription; Dhcp=($ipif.Dhcp -eq 'Enabled'); Addresses=$addresses; Routes=$routes } | ConvertTo-Json -Compress -Depth 5
"""
    try:
        data = json.loads(_powershell(script, {"interface": interface}))
        addresses = tuple(
            ipaddress.IPv4Interface(f"{item['IPAddress']}/{item['PrefixLength']}")
            for item in data.get("Addresses", [])
        )
        routes = _v4_routes(data.get("Routes", []))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise NetworkError(f"could not read Windows adapter state: {exc}") from exc
    description = data.get("Description", "")
    physical = data.get("Hardware", False) and not re.search(r"wi-?fi|wireless|802\.11", description, re.I)
    return InterfaceState(
        data.get("Name", interface), data.get("Status") == "Up",
        parse_speed_mbps(data.get("LinkSpeed")), data.get("Dhcp"), addresses,
        routes, bool(physical),
    )


def _mac_service(interface: str) -> tuple[str, str]:
    lines = _run(["networksetup", "-listnetworkserviceorder"]).splitlines()
    service = None
    for line in lines:
        match = re.match(r"\(\d+\)\s*(.+)$", line.strip())
        if match:
            service = match.group(1).strip()
            continue
        hardware = re.search(r"Hardware Port:\s*(.*?),\s*Device:\s*([\w.-]+)", line)
        if hardware and hardware.group(2) == interface and service:
            return service, hardware.group(1).strip()
    raise NetworkError(f"no network service maps to interface {interface!r}")


def _inspect_macos(interface: str) -> InterfaceState:
    service, hardware = _mac_service(interface)
    output = _run(["ifconfig", interface])
    info = _run(["networksetup", "-getinfo", service])
    media = _run(["networksetup", "-getmedia", service])
    active = re.search(r"^Active:\s*(.+)$", media, re.MULTILINE | re.IGNORECASE)
    addresses = []
    for line in output.splitlines():
        match = re.search(r"\binet\s+(\d+(?:\.\d+){3})\s+netmask\s+(0x[0-9a-f]+|\d+(?:\.\d+){3})", line)
        if match:
            mask = match.group(2)
            if mask.startswith("0x"):
                mask = socket.inet_ntoa(int(mask, 16).to_bytes(4, "big"))
            addresses.append(ipaddress.IPv4Interface(f"{match.group(1)}/{mask}"))
    routes_out = _run(["netstat", "-rn", "-f", "inet"])
    routes = _v4_routes([line.split()[0] for line in routes_out.splitlines()[2:] if line.split()])
    dhcp = "DHCP Configuration" in info
    return InterfaceState(
        interface,
        bool(re.search(r"^\s*status:\s*active$", output, re.MULTILINE | re.IGNORECASE)),
        parse_speed_mbps(active.group(1)) if active else None,
        dhcp,
        tuple(addresses),
        routes,
        not re.search(r"wi-?fi|wireless|airport|thunderbolt bridge", hardware, re.I),
    )


def _linux_dhcp(interface: str) -> bool | None:
    nmcli = shutil.which("nmcli")
    if nmcli:
        method = _run([nmcli, "-g", "IP4.METHOD", "device", "show", interface]).strip().lower()
        if method in {"auto", "dhcp"}:
            return True
        if method in {"manual", "disabled", "link-local", "ignore"}:
            return False
    networkctl = shutil.which("networkctl")
    if networkctl:
        status = _run([networkctl, "status", "--no-pager", interface])
        match = re.search(r"DHCP4 Client:\s*(yes|no)", status, re.I)
        if match:
            return match.group(1).lower() == "yes"
    return None


def _inspect_linux(interface: str) -> InterfaceState:
    if not INTERFACE_RE.fullmatch(interface):
        raise NetworkError("invalid Linux interface name")
    sysfs = SYS_CLASS_NET / interface
    if not sysfs.exists():
        raise NetworkError(f"interface {interface!r} does not exist")
    try:
        operstate = (sysfs / "operstate").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise NetworkError(f"could not read link state for {interface}: {exc}") from exc
    try:
        speed = int((sysfs / "speed").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        speed = None
    out = _run(["ip", "-o", "-4", "address", "show", "dev", interface])
    addresses = []
    for match in re.finditer(r"\binet\s+(\d+(?:\.\d+){3}/\d+)", out):
        addresses.append(ipaddress.IPv4Interface(match.group(1)))
    route_json = _run(["ip", "-j", "-4", "route", "show"])
    try:
        route_data = json.loads(route_json)
        route_values = [item["dst"] for item in route_data if item.get("dst")]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise NetworkError(f"could not read Linux routes: {exc}") from exc
    return InterfaceState(
        interface,
        operstate == "up",
        speed if speed > 0 else None,
        _linux_dhcp(interface),
        tuple(addresses),
        _v4_routes(route_values),
        (sysfs / "device").exists() and not (sysfs / "wireless").exists(),
    )


def inspect_interface(interface: str) -> InterfaceState:
    system = platform.system()
    if system == "Windows":
        return _inspect_windows(interface)
    if system == "Darwin":
        if not INTERFACE_RE.fullmatch(interface):
            raise NetworkError("invalid macOS interface name")
        return _inspect_macos(interface)
    if system == "Linux":
        return _inspect_linux(interface)
    raise NetworkError(f"unsupported operating system: {system}")


def is_admin() -> bool:
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _apply_address(interface: str, profile: LinkProfile) -> None:
    system = platform.system()
    if system == "Windows":
        script = r"""
$ErrorActionPreference = 'Stop'
New-NetIPAddress -InterfaceIndex ([int]$env:KLLM_10GBE_INTERFACE) -IPAddress $env:KLLM_10GBE_ADDRESS -PrefixLength ([int]$env:KLLM_10GBE_PREFIX) -PolicyStore ActiveStore -ErrorAction Stop | Out-Null
"""
        _powershell(script, {"interface": interface, "address": str(profile.local), "prefix": str(profile.subnet.prefixlen)})
    elif system == "Darwin":
        _run(["ifconfig", interface, "inet", str(profile.local), "netmask", str(profile.subnet.netmask), "alias"])
    elif system == "Linux":
        _run(["ip", "address", "add", f"{profile.local}/{profile.subnet.prefixlen}", "dev", interface])
    else:
        raise NetworkError(f"unsupported operating system: {system}")


def configure(interface: str, role: str, subnet: str = DEFAULT_SUBNET) -> bool:
    if not interface:
        raise NetworkError("--interface is required; interfaces are never selected automatically")
    if not is_admin():
        raise NetworkError("administrator/root privileges are required")
    profile = make_profile(role, subnet)
    state = inspect_interface(interface)
    if not state.physical_ethernet:
        raise NetworkError("selected interface is not a physical Ethernet adapter")
    if not state.connected:
        raise NetworkError("selected interface is not connected")
    if state.speed_mbps is None:
        raise NetworkError("negotiated link speed could not be verified; no changes were made")
    if state.speed_mbps < 10_000:
        raise NetworkError(f"negotiated link speed is {state.speed_mbps} Mbps; at least 10000 Mbps is required")
    if state.dhcp is None:
        raise NetworkError("could not verify DHCP is disabled on the selected interface")
    if state.dhcp:
        raise NetworkError("selected interface uses DHCP; configure a dedicated non-DHCP adapter first")
    matching = [address for address in state.addresses if address.ip == profile.local]
    if matching:
        if any(address.network == profile.subnet for address in matching):
            return False
        raise NetworkError("the desired address already exists with a conflicting prefix")
    if routes_overlap(profile.subnet, state.routes):
        raise NetworkError(f"subnet {profile.subnet} overlaps an existing route")
    if any(address.network.overlaps(profile.subnet) for address in state.addresses):
        raise NetworkError("selected interface has an IPv4 address overlapping the requested subnet")
    _apply_address(interface, profile)
    return True


def _ping(interface: str, peer: str) -> bool | None:
    system = platform.system()
    if system == "Windows":
        script = r"""
$ErrorActionPreference = 'Stop'
$route = Find-NetRoute -RemoteIPAddress $env:KLLM_10GBE_PEER -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $route -or $route.ifIndex -ne [int]$env:KLLM_10GBE_INTERFACE) { 'unreachable'; exit 0 }
$source = $route.IPAddress
if (-not $source) { throw 'No usable IPv4 source address on selected interface' }
'routed:' + $source
"""
        result = _powershell(script, {"interface": interface, "peer": peer}).strip().lower()
        if result == "unreachable":
            return False
        if not result.startswith("routed:"):
            return None
        source = result.split(":", 1)[1].strip()
        args = ["ping.exe", "-n", "1", "-w", "2000", "-S", source, peer]
    elif system == "Darwin":
        source = _interface_local_address(interface, peer)
        args = ["ping", "-n", "-c", "1", "-W", "2000", "-S", source, peer]
    elif system == "Linux":
        args = ["ping", "-n", "-c", "1", "-W", "2", "-I", interface, peer]
    else:
        raise NetworkError(f"unsupported operating system: {system}")
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkError(f"could not run peer verification: {exc}") from exc
    if result.returncode == 0:
        return True
    output = f"{result.stdout}\n{result.stderr}".lower()
    if any(token in output for token in ("network is unreachable", "no route to host", "destination unreachable")):
        return False
    return None


def _interface_local_address(interface: str, peer: str) -> str:
    state = inspect_interface(interface)
    for address in state.addresses:
        if ipaddress.ip_address(peer) in address.network:
            return str(address.ip)
    raise NetworkError("selected interface has no address in the peer subnet for source-bound verification")


def verify(interface: str, peer: str) -> str:
    if not interface:
        raise NetworkError("--interface is required; interfaces are never selected automatically")
    try:
        address = ipaddress.ip_address(peer)
    except ValueError as exc:
        raise NetworkError(f"invalid peer address {peer!r}") from exc
    if not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
        raise NetworkError("peer must be a usable unicast IPv4 address")
    result = _ping(interface, str(address))
    if result is True:
        return "reachable"
    if result is False:
        return "unreachable"
    return "unable-to-verify"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    configure_parser = subparsers.add_parser("configure", help="assign a temporary link address")
    configure_parser.add_argument("--interface", required=True, help="explicit adapter ID (Windows index, macOS/Linux device)")
    configure_parser.add_argument("--role", choices=("coordinator", "worker"), required=True)
    configure_parser.add_argument("--subnet", default=DEFAULT_SUBNET, help=f"private IPv4 CIDR (default: {DEFAULT_SUBNET})")
    verify_parser = subparsers.add_parser("verify", help="verify a peer without changing network settings")
    verify_parser.add_argument("--interface", required=True, help="explicit adapter ID (Windows index, macOS/Linux device)")
    verify_parser.add_argument("--peer", required=True, help="peer IPv4 address")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "configure":
            changed = configure(args.interface, args.role, args.subnet)
            profile = make_profile(args.role, args.subnet)
            verb = "Configured" if changed else "Already configured"
            print(f"{verb} {args.role} on {args.interface}: {profile.local}/{profile.subnet.prefixlen}; peer {profile.peer} (temporary until reboot).")
            return 0
        outcome = verify(args.interface, args.peer)
        print(f"Peer {args.peer} via {args.interface}: {outcome}.")
        if outcome != "reachable":
            print("Check link state, negotiated speed, interface selection, routes, and ICMP firewall policy.", file=sys.stderr)
            return 1
        return 0
    except NetworkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
