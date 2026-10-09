"""Local IPv4 discovery, route selection, and LAN peer validation."""

from __future__ import annotations

import ipaddress
import platform
import re
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path

_IFCONFIG_INET_RE = re.compile(
    r"\binet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)"
)

# App-managed private-network subnets in proposal order (spec 012). A host
# already carrying one of these addresses is on the dedicated link, so it is
# preferred over the default-route management interface for RPC. Keep in sync
# with ``kllm.managed_network.allocation.CANDIDATES``.
PREFERRED_PRIVATE_SUBNETS: tuple[str, ...] = (
    "10.10.10.0/24",
    "192.168.234.0/24",
    "172.29.234.0/24",
    "10.234.234.0/24",
)


def local_ip_toward(remote: str) -> str | None:
    """Return the local IPv4 used to reach ``remote`` (UDP connect trick)."""
    remote = (remote or "").strip()
    if not remote or remote in ("0.0.0.0", "127.0.0.1"):
        return None
    try:
        # Strip brackets / optional port from host:port forms.
        host = remote
        if host.startswith("[") and "]" in host:
            host = host[1 : host.index("]")]
        elif host.count(":") == 1:
            host = host.rsplit(":", 1)[0]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((host, 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if isinstance(ip, str) and ip and ip != "127.0.0.1":
            return ip
    except Exception:
        return None
    return None


def _preferred_private_address(addresses: list[str]) -> str | None:
    """First local address inside the app's private-network subnets, in order."""
    parsed: list[tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address]] = []
    for ip in addresses:
        try:
            parsed.append((str(ip), ipaddress.ip_address(str(ip))))
        except ValueError:
            continue
    for cidr in PREFERRED_PRIVATE_SUBNETS:
        network = ipaddress.ip_network(cidr)
        matches = sorted(ip for ip, addr in parsed if addr.version == 4 and addr in network)
        if matches:
            return matches[0]
    return None


def pick_rpc_bind_host(
    preferred: str | None = None,
    *,
    toward: str | None = None,
    local_ip_resolver: Callable[[str], str | None] | None = None,
    address_provider: Callable[[], list[str]] | None = None,
) -> str:
    """Resolve the address rpc-server should bind / advertise.

    Explicit preferred wins (including ``0.0.0.0`` for debug). When ``toward`` is
    set (typically the master host on join), prefer the local IP that routes to
    that peer — avoids advertising Hyper-V / VPN / virtual NIC addresses that
    the master cannot reach. Otherwise prefer an address on the app's
    private-network subnets (10.10.10.0/24 first), then the default-route
    interface, then the best non-loopback IPv4 (RFC1918/lab ranges).
    """
    if preferred is not None and str(preferred).strip() != "":
        return str(preferred).strip()

    resolve_local_ip = local_ip_resolver or local_ip_toward
    resolve_addresses = address_provider or _list_ipv4_addresses
    toward_ip = resolve_local_ip(toward) if toward else None
    if toward_ip:
        return toward_ip

    candidates = resolve_addresses()

    # Prefer an address already on the app's dedicated private network before
    # the default-route interface, so a multi-homed master does not advertise
    # its management address (spec 012).
    internal = _preferred_private_address(candidates)
    if internal:
        return internal

    # Without a peer hint, prefer the default-route interface (UDP egress trick).
    # Lexicographic sort among private IPs often picks VMware/Hyper-V switches
    # (e.g. 192.168.235.1) ahead of the Wi‑Fi/Ethernet LAN the cluster uses.
    route_ip = resolve_local_ip("8.8.8.8")
    if route_ip and is_usable_lan_peer(route_ip):
        return route_ip

    ranked = sorted(candidates, key=_rank_bind_candidate)
    if ranked:
        return ranked[0]

    # Fallback: UDP trick used by discovery (may still be a usable LAN IP).
    # Deferred import: discovery imports netsec (cycle).
    from kllm.discovery import _local_ip

    ip = _local_ip()
    if ip and ip != "127.0.0.1":
        return ip
    return "127.0.0.1"


def is_local_ipv4(ip: str | None, local_ips: list[str] | None = None) -> bool:
    """True if ``ip`` is currently assigned to this host (non-loopback)."""
    if not ip:
        return False
    ips = local_ips if local_ips is not None else _list_ipv4_addresses()
    return str(ip).strip() in ips


def should_apply_rpc_bind_hint(hint: str, current: str, local_ips: list[str] | None = None) -> bool:
    """Accept a master's rpc_bind_hint only when it is a local address.

    ``request.client.host`` on the master can be a different LAN client (mesh
    hairpin, stale NAT mapping). Binding rpc-server to that IP fails silently
    and leaves RPC on the wrong host.
    """
    if not is_usable_lan_peer(hint) or hint == current:
        return False
    return is_local_ipv4(hint, local_ips)


def wifi_path_warning(toward: str | None) -> str | None:
    """Warn when the route to ``toward`` is a Wi-Fi adapter (RPC is RTT-bound)."""
    toward = (toward or "").strip()
    if not toward or toward in ("0.0.0.0", "127.0.0.1"):
        return None
    try:
        system = platform.system()
        if system == "Windows":
            alias, wifi = _wifi_route_windows(toward)
        elif system == "Darwin":
            alias, wifi = _wifi_route_darwin(toward)
        else:
            alias, wifi = _wifi_route_linux(toward)
    except Exception:
        return None
    if not wifi:
        return None
    label = f" ({alias})" if alias else ""
    return (
        f"path to {toward} is via Wi-Fi{label}; llama.cpp RPC is latency-bound — "
        f"prefer Ethernet or Thunderbolt"
    )


def _wifi_route_windows(toward: str) -> tuple[str | None, bool]:
    out = subprocess.check_output(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$r = Find-NetRoute -RemoteIPAddress '{toward}' -ErrorAction Stop | "
                "Select-Object -First 1; "
                "$a = Get-NetAdapter -InterfaceIndex $r.ifIndex -ErrorAction Stop; "
                "$wifi = ($a.PhysicalMedium -eq 'Native802_11') -or ($a.Name -like 'Wi-Fi*') "
                "-or ($a.InterfaceDescription -match 'Wi-Fi|Wireless|802\\.11'); "
                'Write-Output "$wifi`t$($a.Name)"'
            ),
        ],
        text=True,
        timeout=10,
        stderr=subprocess.DEVNULL,
    ).strip()
    if not out:
        return None, False
    parts = out.split("\t", 1)
    wifi = parts[0].strip().lower() in ("true", "1")
    alias = parts[1].strip() if len(parts) > 1 else None
    return alias, wifi


def _wifi_route_darwin(toward: str) -> tuple[str | None, bool]:
    route = subprocess.check_output(
        ["route", "-n", "get", toward], text=True, timeout=5, stderr=subprocess.DEVNULL
    )
    alias = None
    for line in route.splitlines():
        if "interface:" in line:
            alias = line.split(":", 1)[1].strip()
            break
    if not alias:
        return None, False
    ports = subprocess.check_output(
        ["networksetup", "-listallhardwareports"], text=True, timeout=5, stderr=subprocess.DEVNULL
    )
    current = None
    wifi = False
    for line in ports.splitlines():
        if line.startswith("Hardware Port:"):
            current = line.split(":", 1)[1].strip().lower()
        elif line.startswith("Device:") and alias == line.split(":", 1)[1].strip():
            wifi = current in ("wi-fi", "wifi", "airport")
            break
    return alias, wifi


def _wifi_route_linux(toward: str) -> tuple[str | None, bool]:
    out = subprocess.check_output(
        ["ip", "-o", "route", "get", toward], text=True, timeout=5, stderr=subprocess.DEVNULL
    )
    alias = None
    parts = out.split()
    if "dev" in parts:
        alias = parts[parts.index("dev") + 1]
    if not alias:
        return None, False
    wireless = Path(f"/sys/class/net/{alias}/wireless")
    return alias, wireless.is_dir()


def is_usable_lan_peer(ip: str | None) -> bool:
    """True if ``ip`` is a non-loopback unicast IPv4 suitable for RPC dial."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return False
    if addr.version != 4:
        return False
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast or addr.is_link_local:
        return False
    return True


def _rank_bind_candidate(ip: str) -> tuple[int, str]:
    """Lower is better."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return (100, ip)
    if addr.is_loopback:
        return (90, ip)
    if addr.is_link_local:  # 169.254/16
        return (80, ip)
    if addr.is_private:
        # Prefer common lab / Thunderbolt ranges slightly.
        if ip.startswith("192.168.10.") or ip.startswith("10.66.87."):
            return (0, ip)
        # Lexicographic tie-break is unstable across Hyper-V/VPN adapters; keep
        # private peers equal and let ``toward`` / peer-IP hints decide on join.
        return (1, ip)
    return (50, ip)


def _list_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    try:
        import socket

        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if isinstance(ip, str) and ip:
                found.add(ip)
    except Exception:
        pass

    # Enumerate via OS when possible for multi-NIC hosts.
    # Linux `ip` is not on macOS; hostname getaddrinfo also fails on many Macs.
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress",
                ],
                text=True,
                timeout=10,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                line = line.strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", line):
                    found.add(line)
        elif system == "Darwin":
            _collect_ifconfig_ipv4(found)
        else:
            try:
                out = subprocess.check_output(
                    ["ip", "-4", "-o", "addr", "show"],
                    text=True,
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                )
                for m in re.finditer(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out):
                    found.add(m.group(1))
            except Exception:
                _collect_ifconfig_ipv4(found)
    except Exception:
        pass

    return [ip for ip in found if not ip.startswith("127.")]


def _collect_ifconfig_ipv4(found: set[str]) -> None:
    """Parse ``ifconfig -a`` IPv4 addresses (macOS / BSD)."""
    try:
        out = subprocess.check_output(
            ["ifconfig", "-a"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return
    for m in re.finditer(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", out):
        found.add(m.group(1))


def _netmask_to_prefix(mask: str) -> int | None:
    """Convert an ``ifconfig`` netmask (``0xfffffc00`` or dotted) to a prefix length."""
    mask = mask.strip()
    if mask.lower().startswith("0x"):
        try:
            mask = str(ipaddress.IPv4Address(int(mask, 16)))
        except (ValueError, ipaddress.AddressValueError):
            return None
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except (ValueError, ipaddress.NetmaskValueError):
        return None


def _network_for_ip(ip: str, prefixlen: int | None) -> ipaddress.IPv4Network | None:
    """Build an IPv4 network for ``ip``; unknown masks assume ``/24``."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except (ValueError, ipaddress.AddressValueError):
        return None
    if prefixlen is None:
        prefixlen = 24
    if not 0 <= prefixlen <= 32:
        return None
    try:
        return ipaddress.IPv4Network(f"{addr}/{prefixlen}", strict=False)
    except ValueError:
        return None


def _collect_ipv4_networks_from_ifconfig(out: str) -> list[ipaddress.IPv4Network]:
    """Parse ``ifconfig -a`` ``inet <ip> netmask <mask>`` pairs (macOS / BSD)."""
    nets: list[ipaddress.IPv4Network] = []
    for m in _IFCONFIG_INET_RE.finditer(out):
        net = _network_for_ip(m.group(1), _netmask_to_prefix(m.group(2)))
        if net is not None:
            nets.append(net)
    return nets


def _list_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """IPv4 networks on local interfaces, using the real interface netmask.

    Unlike :func:`_list_ipv4_addresses` this preserves the prefix length, so a
    ``/22`` LAN (e.g. ``192.168.68.0/22``) is not silently treated as a single
    ``/24``. Used by HTTP master discovery when mDNS is unavailable.
    """
    found: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    def add(ip: str, prefixlen: int | None) -> None:
        net = _network_for_ip(ip, prefixlen)
        if net is not None and str(net) not in seen:
            seen.add(str(net))
            found.append(net)

    system = platform.system()
    via_os = False
    try:
        if system == "Windows":
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 | "
                    'ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" }',
                ],
                text=True,
                timeout=10,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                m = re.match(r"^(\d+\.\d+\.\d+\.\d+)/(\d+)$", line.strip())
                if m:
                    add(m.group(1), int(m.group(2)))
                    via_os = True
        elif system == "Darwin":
            try:
                out = subprocess.check_output(
                    ["ifconfig", "-a"],
                    text=True,
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                out = ""
            if out:
                for net in _collect_ipv4_networks_from_ifconfig(out):
                    if str(net) not in seen:
                        seen.add(str(net))
                        found.append(net)
                        via_os = True
        else:
            try:
                out = subprocess.check_output(
                    ["ip", "-4", "-o", "addr", "show"],
                    text=True,
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                out = ""
            if out:
                for m in re.finditer(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", out):
                    add(m.group(1), int(m.group(2)))
                    via_os = True
            else:
                try:
                    out = subprocess.check_output(
                        ["ifconfig", "-a"],
                        text=True,
                        timeout=5,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    out = ""
                for net in _collect_ipv4_networks_from_ifconfig(out):
                    if str(net) not in seen:
                        seen.add(str(net))
                        found.append(net)
                        via_os = True
    except Exception:
        pass

    if not via_os:
        # Last resort: hostname resolution has no mask — assume /24 per address.
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = info[4][0]
                if isinstance(address, str):
                    add(address, 24)
        except Exception:
            pass

    return [n for n in found if not n.network_address.is_loopback]
