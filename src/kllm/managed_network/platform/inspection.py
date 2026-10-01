"""Shared read-only adapter inspection used by the manual CLI and managed policy."""
from __future__ import annotations
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from typing import Sequence

INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")

class NetworkError(Exception):
    """Bounded platform operation failure."""

@dataclass(frozen=True)
class InterfaceState:
    name: str
    connected: bool
    speed_mbps: int | None
    dhcp: bool | None
    addresses: tuple[ipaddress.IPv4Interface, ...]
    routes: tuple[ipaddress.IPv4Network, ...]
    physical_ethernet: bool = True

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


def _v4_routes(values: Sequence[str]) -> tuple[ipaddress.IPv4Network, ...]:
    routes = []
    for value in values:
        if value in {"default", "0.0.0.0/0"}:
            continue
        try:
            # BSD netstat abbreviates connected networks (e.g. 192.168.68).
            if re.fullmatch(r"\d+(?:\.\d+){0,3}(?:/\d+)?", value):
                address, _, prefix = value.partition('/')
                parts = address.split('.')
                if len(parts) < 4:
                    prefix = prefix or str(len(parts) * 8)
                    value = '.'.join(parts + ['0'] * (4-len(parts))) + '/' + prefix
            route = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if isinstance(route, ipaddress.IPv4Network):
            routes.append(route)
    return tuple(routes)


def _inspect_windows(interface: str, powershell) -> InterfaceState:
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
        data = json.loads(powershell(script, {"interface": interface}))
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


def _mac_service(interface: str, run) -> tuple[str, str]:
    lines = run(["networksetup", "-listnetworkserviceorder"]).splitlines()
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


def _inspect_macos(interface: str, run) -> InterfaceState:
    service, hardware = _mac_service(interface, run)
    output = run(["ifconfig", interface])
    info = run(["networksetup", "-getinfo", service])
    media = run(["networksetup", "-getmedia", service])
    active = re.search(r"^Active:\s*(.+)$", media, re.MULTILINE | re.IGNORECASE)
    addresses = []
    for line in output.splitlines():
        match = re.search(r"\binet\s+(\d+(?:\.\d+){3})\s+netmask\s+(0x[0-9a-f]+|\d+(?:\.\d+){3})", line)
        if match:
            mask = match.group(2)
            if mask.startswith("0x"):
                mask = socket.inet_ntoa(int(mask, 16).to_bytes(4, "big"))
            addresses.append(ipaddress.IPv4Interface(f"{match.group(1)}/{mask}"))
    routes_out = run(["netstat", "-rn", "-f", "inet"])
    routes = _v4_routes([line.split()[0] for line in routes_out.splitlines()[2:] if line.split()])
    dhcp = True if "DHCP Configuration" in info or "BOOTP Configuration" in info else False if "Manual Configuration" in info else None
    return InterfaceState(
        interface,
        bool(re.search(r"^\s*status:\s*active$", output, re.MULTILINE | re.IGNORECASE)),
        parse_speed_mbps(active.group(1)) if active else None,
        dhcp,
        tuple(addresses),
        routes,
        not re.search(r"wi-?fi|wireless|airport|thunderbolt bridge", hardware, re.I),
    )


def _linux_dhcp(interface: str, run, which) -> bool | None:
    nmcli = which("nmcli")
    if nmcli:
        try:
            connection = run([nmcli, "-g", "GENERAL.CON-UUID", "device", "show", interface]).strip()
            if re.fullmatch(r"[0-9a-fA-F-]{36}", connection):
                method = run([nmcli, "-g", "ipv4.method", "connection", "show", "uuid", connection]).strip().lower()
                if method in {"auto", "dhcp"}:
                    return True
                if method in {"manual", "disabled", "link-local"}:
                    return False
                return None
        except NetworkError:
            return None
    networkctl = which("networkctl")
    if networkctl:
        status = run([networkctl, "status", "--no-pager", interface])
        match = re.search(r"DHCP4 Client:\s*(yes|no)", status, re.I)
        if match:
            return match.group(1).lower() == "yes"
    return None


def _inspect_linux(interface: str, run, dhcp, sys_class_net) -> InterfaceState:
    if not INTERFACE_RE.fullmatch(interface):
        raise NetworkError("invalid Linux interface name")
    sysfs = sys_class_net / interface
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
    out = run(["ip", "-o", "-4", "address", "show", "dev", interface])
    addresses = []
    for match in re.finditer(r"\binet\s+(\d+(?:\.\d+){3}/\d+)", out):
        addresses.append(ipaddress.IPv4Interface(match.group(1)))
    route_json = run(["ip", "-j", "-4", "route", "show", "table", "all"])
    try:
        route_data = json.loads(route_json)
        route_values = [item["dst"] for item in route_data if item.get("dst")]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise NetworkError(f"could not read Linux routes: {exc}") from exc
    return InterfaceState(
        interface,
        operstate == "up",
        speed if speed is not None and speed > 0 else None,
        dhcp(interface),
        tuple(addresses),
        _v4_routes(route_values),
        (sysfs / "device").exists() and not (sysfs / "wireless").exists(),
    )
