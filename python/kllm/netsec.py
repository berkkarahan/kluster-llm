"""RPC bind hardening and optional WireGuard overlay helpers."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from kllm.state import ensure_home, home_dir

OVERLAY_NONE = "none"
OVERLAY_WIREGUARD = "wireguard"
OVERLAY_CHOICES = (OVERLAY_NONE, OVERLAY_WIREGUARD)

WG_SUBNET = "10.66.87.0/24"
WG_PREFIX = "10.66.87."
WG_LISTEN_PORT = 51820
WG_IFACE = "kllm0"
WG_PSK_INFO = b"kllm-wg-psk-v1"
FIREWALL_RULE_NAME = "kllm-RPC"
SOCKETFILTERFW = Path("/usr/libexec/ApplicationFirewall/socketfilterfw")
_RPC_SERVER_REL_PATHS = (
    "vendor/bin/rpc-server",
    "vendor/bin/rpc-server.exe",
    "worker-windows/rpc-server.exe",
    "vendor/llama.cpp/build/bin/rpc-server",
    "vendor/llama.cpp/build/bin/rpc-server.exe",
    "vendor/llama.cpp/build/bin/ggml-rpc-server",
    "vendor/llama.cpp/build/bin/ggml-rpc-server.exe",
    "vendor/llama.cpp/build/bin/Release/rpc-server.exe",
    "vendor/llama.cpp/build/bin/Release/ggml-rpc-server.exe",
    "vendor/llama.cpp/rpc-server",
    "vendor/llama.cpp/rpc-server.exe",
    "vendor/llama.cpp/ggml-rpc-server",
    "vendor/llama.cpp/ggml-rpc-server.exe",
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
        if ip and ip != "127.0.0.1":
            return ip
    except Exception:
        return None
    return None


def pick_rpc_bind_host(preferred: str | None = None, *, toward: str | None = None) -> str:
    """Resolve the address rpc-server should bind / advertise.

    Explicit preferred wins (including ``0.0.0.0`` for debug). When ``toward`` is
    set (typically the master host on join), prefer the local IP that routes to
    that peer — avoids advertising Hyper-V / VPN / virtual NIC addresses that
    the master cannot reach. Otherwise pick the best non-loopback IPv4,
    preferring RFC1918/lab ranges.
    """
    if preferred is not None and str(preferred).strip() != "":
        return str(preferred).strip()

    toward_ip = local_ip_toward(toward) if toward else None
    if toward_ip:
        return toward_ip

    # Without a peer hint, prefer the default-route interface (UDP egress trick).
    # Lexicographic sort among private IPs often picks VMware/Hyper-V switches
    # (e.g. 192.168.235.1) ahead of the Wi‑Fi/Ethernet LAN the cluster uses.
    route_ip = local_ip_toward("8.8.8.8")
    if route_ip and is_usable_lan_peer(route_ip):
        return route_ip

    candidates = _list_ipv4_addresses()
    ranked = sorted(candidates, key=_rank_bind_candidate)
    if ranked:
        return ranked[0]

    # Fallback: UDP trick used by discovery (may still be a usable LAN IP).
    from kllm.discovery import _local_ip

    ip = _local_ip()
    if ip and ip != "127.0.0.1":
        return ip
    return "127.0.0.1"


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
            if ip:
                found.add(ip)
    except Exception:
        pass

    # Enumerate via OS when possible for multi-NIC hosts.
    # Linux `ip` is not on macOS; hostname getaddrinfo also fails on many Macs.
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetIPAddress -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress"],
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


def ensure_rpc_firewall(port: int, bind_host: str) -> dict[str, Any]:
    """Best-effort allow TCP ``port`` for RPC. Never raises for missing tooling."""
    system = platform.system()
    try:
        if system == "Windows":
            return _firewall_windows(port)
        if system == "Linux":
            return _firewall_linux(port)
        if system == "Darwin":
            return _firewall_darwin(port, bind_host)
        return {"ok": True, "applied": False, "warning": f"no firewall helper for {system}"}
    except Exception as exc:
        return {"ok": False, "applied": False, "warning": str(exc)}


def _resolve_rpc_server_path() -> str | None:
    """Locate rpc-server the same way the native launcher does (best-effort)."""
    env = os.environ.get("KLLM_RPC_SERVER")
    if env and Path(env).is_file():
        return str(Path(env).resolve())

    repo_root_path: Path | None = None
    try:
        from kllm.state import repo_root

        repo_root_path = repo_root()
    except Exception:
        repo_root_path = None
    repo_root_str = str(repo_root_path) if repo_root_path else None

    try:
        from kllm import native

        resolved = native.resolve_rpc_server(repo_root_str)
        if resolved and Path(resolved).is_file():
            return str(Path(resolved).resolve())
    except Exception:
        pass

    roots: list[Path] = []
    repo = os.environ.get("KLLM_REPO_ROOT")
    if repo:
        roots.append(Path(repo))
    if repo_root_path:
        roots.append(repo_root_path)
    try:
        cwd = Path.cwd()
        roots.append(cwd)
        if cwd.parent != cwd:
            roots.append(cwd.parent)
    except OSError:
        pass

    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        for rel in _RPC_SERVER_REL_PATHS:
            candidate = (root / rel).resolve()
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("rpc-server")
    return str(Path(found).resolve()) if found else None


def _macos_app_firewall_permits(binary: str) -> bool:
    if not SOCKETFILTERFW.is_file():
        return False
    result = subprocess.run(
        [str(SOCKETFILTERFW), "--getappblocked", binary],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return False
    out = (result.stdout or result.stderr or "").lower()
    return "permitted" in out or "allow incoming" in out


def _macos_socketfilterfw_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run socketfilterfw; retry with non-interactive sudo when not root."""
    cmd = [str(SOCKETFILTERFW), *args]
    direct = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if direct.returncode == 0:
        return direct
    combined = (direct.stderr or direct.stdout or "").lower()
    if "must be root" not in combined and "not permitted" not in combined:
        return direct
    sudo = shutil.which("sudo")
    if not sudo:
        return direct
    return subprocess.run(
        [sudo, "-n", str(SOCKETFILTERFW), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _firewall_darwin(port: int, bind_host: str) -> dict[str, Any]:
    if not SOCKETFILTERFW.is_file():
        return {
            "ok": True,
            "applied": False,
            "warning": "macOS Application Firewall helper not found",
        }

    rpc = _resolve_rpc_server_path()
    if not rpc:
        return {
            "ok": True,
            "applied": False,
            "warning": (
                f"rpc-server not found; macOS firewall is application-based — allow rpc-server "
                f"for inbound TCP {port} (bind {bind_host}) in System Settings → Network → Firewall"
            ),
        }

    if _macos_app_firewall_permits(rpc):
        return {"ok": True, "applied": False, "warning": None}

    for flag in ("--add", "--unblockapp"):
        result = _macos_socketfilterfw_run([flag, rpc])
        if result.returncode != 0:
            err = (result.stderr or result.stdout or f"socketfilterfw {flag} failed").strip()
            sf = str(SOCKETFILTERFW)
            return {
                "ok": True,
                "applied": False,
                "warning": (
                    f"macOS firewall is application-based; allow rpc-server for TCP {port} "
                    f"(bind {bind_host}): sudo {sf} --add {rpc!r} && "
                    f"sudo {sf} --unblockapp {rpc!r} ({err})"
                ),
            }

    if _macos_app_firewall_permits(rpc):
        return {"ok": True, "applied": True, "warning": None}

    return {
        "ok": True,
        "applied": True,
        "warning": None,
    }


def refresh_rpc_firewall_warnings(port: int, bind_host: str) -> list[str]:
    """Re-run macOS/Linux/Windows RPC firewall helper; return active warnings only."""
    fw = ensure_rpc_firewall(port, bind_host)
    if fw.get("warning"):
        return [str(fw["warning"])]
    return []


def _firewall_windows_create_cmd(port: int) -> str:
    return (
        f"New-NetFirewallRule -Name '{FIREWALL_RULE_NAME}' "
        f"-DisplayName 'Kluster LLM RPC Worker' -Direction Inbound -Action Allow "
        f"-Protocol TCP -LocalPort {port} -Profile Any"
    )


def _firewall_windows_elevation_hint(port: int) -> str:
    return (
        f"Windows Firewall rule '{FIREWALL_RULE_NAME}' needs Administrator once. "
        f"Open elevated PowerShell and run: {_firewall_windows_create_cmd(port)}"
    )


def _firewall_windows(port: int) -> dict[str, Any]:
    """Allow TCP ``port`` for RPC. Creating/updating the rule needs elevation.

    If the rule already exists, succeed without admin. Do not delete/recreate on
    every start — that forced Access Denied for non-elevated agents.
    """
    check = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"if (Get-NetFirewallRule -Name '{FIREWALL_RULE_NAME}' -ErrorAction SilentlyContinue) "
            "{ exit 0 } else { exit 1 }",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check.returncode == 0:
        return {"ok": True, "applied": True, "warning": None}

    create = _firewall_windows_create_cmd(port)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", create],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return {"ok": True, "applied": True, "warning": None}

    err = (result.stderr or result.stdout or "firewall rule failed").strip()
    lowered = err.lower()
    if (
        "access is denied" in lowered
        or "permissiondenied" in lowered
        or "access denied" in lowered
        or "error 5" in lowered
    ):
        return {
            "ok": False,
            "applied": False,
            "warning": _firewall_windows_elevation_hint(port),
        }
    return {"ok": False, "applied": False, "warning": err or _firewall_windows_elevation_hint(port)}


def _firewall_linux(port: int) -> dict[str, Any]:
    if shutil.which("ufw"):
        result = subprocess.run(
            ["ufw", "allow", f"{port}/tcp", "comment", "kllm-RPC"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return {"ok": True, "applied": True, "warning": None}
        return {
            "ok": False,
            "applied": False,
            "warning": (result.stderr or result.stdout or "ufw failed").strip(),
        }
    if shutil.which("nft"):
        return {
            "ok": True,
            "applied": False,
            "warning": f"ufw not found; ensure nftables/iptables allow TCP {port}",
        }
    return {
        "ok": True,
        "applied": False,
        "warning": f"no ufw/nft found; ensure host firewall allows TCP {port}",
    }


def derive_wg_psk(join_key: str) -> str:
    """HKDF-SHA256 → 32-byte WireGuard PSK, base64-encoded."""
    prk = _hkdf_extract(b"", join_key.encode("utf-8"))
    okm = _hkdf_expand(prk, WG_PSK_INFO, 32)
    return base64.b64encode(okm).decode("ascii")


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    if not salt:
        salt = bytes(32)  # HashLen zeros for SHA-256
    import hmac

    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    import hmac

    out = b""
    t = b""
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]


def wg_dir() -> Path:
    d = home_dir() / "wg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def wg_conf_path() -> Path:
    return wg_dir() / f"{WG_IFACE}.conf"


def find_wg_bin() -> str | None:
    env = os.environ.get("KLLM_WG")
    if env and Path(env).exists():
        return env
    which = shutil.which("wg")
    if which:
        return which
    if platform.system() == "Windows":
        candidate = Path(r"C:\Program Files\WireGuard\wg.exe")
        if candidate.exists():
            return str(candidate)
    return None


def find_wg_quick() -> str | None:
    which = shutil.which("wg-quick")
    if which:
        return which
    if platform.system() == "Windows":
        # WireGuard for Windows uses wireguard.exe /manage tunnels; wg-quick is uncommon.
        wgexe = Path(r"C:\Program Files\WireGuard\wireguard.exe")
        if wgexe.exists():
            return str(wgexe)
    return None


def wg_tooling_available() -> bool:
    return find_wg_bin() is not None


def generate_wg_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) via ``wg genkey`` / ``wg pubkey``."""
    wg = find_wg_bin()
    if not wg:
        raise RuntimeError(
            "WireGuard tooling not found (wg). Install WireGuard and ensure `wg` is on PATH, "
            "or set KLLM_WG to wg.exe"
        )
    priv = subprocess.check_output([wg, "genkey"], text=True, timeout=10).strip()
    pub = subprocess.check_output([wg, "pubkey"], input=priv + "\n", text=True, timeout=10).strip()
    return priv, pub


def load_or_create_keypair() -> tuple[str, str]:
    ensure_home()
    priv_path = wg_dir() / "private.key"
    pub_path = wg_dir() / "public.key"
    if priv_path.exists() and pub_path.exists():
        return priv_path.read_text(encoding="utf-8").strip(), pub_path.read_text(encoding="utf-8").strip()
    priv, pub = generate_wg_keypair()
    priv_path.write_text(priv + "\n", encoding="utf-8")
    pub_path.write_text(pub + "\n", encoding="utf-8")
    try:
        os.chmod(priv_path, 0o600)
    except OSError:
        pass
    return priv, pub


def next_wg_address(members: dict[str, dict[str, Any]], *, master: bool = False) -> str:
    if master:
        return f"{WG_PREFIX}1"
    used: set[int] = set()
    for m in members.values():
        addr = m.get("wg_address") or ""
        if isinstance(addr, str) and addr.startswith(WG_PREFIX):
            try:
                used.add(int(addr.rsplit(".", 1)[1]))
            except ValueError:
                pass
    # Also consider master's reserved .1
    used.add(1)
    for n in range(2, 255):
        if n not in used:
            return f"{WG_PREFIX}{n}"
    raise RuntimeError("WireGuard overlay address space exhausted")


def build_wg_conf(
    *,
    private_key: str,
    address: str,
    listen_port: int,
    psk: str,
    peers: list[dict[str, str]],
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {address}/24",
        f"ListenPort = {listen_port}",
        "",
    ]
    for peer in peers:
        lines.extend(
            [
                "[Peer]",
                f"PublicKey = {peer['public_key']}",
                f"PresharedKey = {psk}",
                f"AllowedIPs = {peer.get('allowed_ips', peer.get('address', '') + '/32')}",
            ]
        )
        if peer.get("endpoint"):
            lines.append(f"Endpoint = {peer['endpoint']}")
            lines.append("PersistentKeepalive = 25")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def bring_up_wireguard(conf_text: str) -> dict[str, Any]:
    """Write conf and bring interface up. Raises RuntimeError on hard failure."""
    if not wg_tooling_available():
        raise RuntimeError(
            "WireGuard tooling not found. Install WireGuard (wg/wg-quick) before using --overlay wireguard"
        )
    path = wg_conf_path()
    path.write_text(conf_text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    system = platform.system()
    if system == "Windows":
        return _wg_up_windows(path)
    return _wg_up_unix(path)


def _wg_up_unix(conf_path: Path) -> dict[str, Any]:
    wg_quick = find_wg_quick()
    if not wg_quick or "wireguard.exe" in wg_quick.lower():
        # Fallback: try `wg-quick` name via PATH after install
        wg_quick = shutil.which("wg-quick")
    if not wg_quick:
        raise RuntimeError("wg-quick not found; required for WireGuard overlay on this OS")

    # Down first if already up
    subprocess.run([wg_quick, "down", str(conf_path)], capture_output=True, text=True, timeout=60)
    result = subprocess.run(
        [wg_quick, "up", str(conf_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "wg-quick up failed").strip()
        raise RuntimeError(err)
    return {"ok": True, "iface": WG_IFACE, "conf": str(conf_path)}


def _wg_up_windows(conf_path: Path) -> dict[str, Any]:
    """Install/activate a WireGuard tunnel via wireguard.exe /installtunnelservice."""
    wireguard = find_wg_quick()
    if not wireguard or not wireguard.lower().endswith("wireguard.exe"):
        # Still allow if only wg.exe exists — operator may manage tunnel manually.
        raise RuntimeError(
            "WireGuard for Windows (wireguard.exe) not found. Install from https://www.wireguard.com/install/"
        )
    # Remove existing tunnel service if present, then install.
    name = WG_IFACE
    subprocess.run(
        [wireguard, "/uninstalltunnelservice", name],
        capture_output=True,
        text=True,
        timeout=60,
    )
    time.sleep(0.5)
    result = subprocess.run(
        [wireguard, "/installtunnelservice", str(conf_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "wireguard installtunnelservice failed").strip()
        raise RuntimeError(err)
    return {"ok": True, "iface": name, "conf": str(conf_path)}


def tear_down_wireguard() -> dict[str, Any]:
    """Best-effort bring-down of the kllm WireGuard iface."""
    system = platform.system()
    conf = wg_conf_path()
    try:
        if system == "Windows":
            wireguard = find_wg_quick()
            if wireguard and wireguard.lower().endswith("wireguard.exe"):
                subprocess.run(
                    [wireguard, "/uninstalltunnelservice", WG_IFACE],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
        else:
            wg_quick = shutil.which("wg-quick")
            if wg_quick and conf.exists():
                subprocess.run([wg_quick, "down", str(conf)], capture_output=True, text=True, timeout=60)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "warning": str(exc)}


def add_peer_to_running(
    *,
    public_key: str,
    allowed_ips: str,
    endpoint: str | None = None,
    psk: str | None = None,
) -> dict[str, Any]:
    """Add/update a peer on the live interface via ``wg set`` when possible."""
    wg = find_wg_bin()
    if not wg:
        return {"ok": False, "warning": "wg binary missing; peer not added live"}
    cmd = [wg, "set", WG_IFACE, "peer", public_key, "allowed-ips", allowed_ips]
    if psk:
        # wg set accepts PresharedKey via file descriptor or path on some builds;
        # write temp and pass.
        psk_path = wg_dir() / "peer.psk"
        psk_path.write_text(psk + "\n", encoding="utf-8")
        cmd.extend(["preshared-key", str(psk_path)])
    if endpoint:
        cmd.extend(["endpoint", endpoint])
        cmd.extend(["persistent-keepalive", "25"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return {"ok": False, "warning": (result.stderr or result.stdout or "wg set failed").strip()}
    return {"ok": True}


def normalize_overlay(value: str | None) -> str:
    if not value:
        return OVERLAY_NONE
    v = value.strip().lower()
    if v not in OVERLAY_CHOICES:
        raise ValueError(f"overlay must be one of {OVERLAY_CHOICES}, got {value!r}")
    return v
