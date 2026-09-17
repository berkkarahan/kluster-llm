"""RPC bind hardening and optional WireGuard overlay helpers."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import platform
import re
import shutil
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


def pick_rpc_bind_host(preferred: str | None = None) -> str:
    """Resolve the address rpc-server should bind / advertise.

    Explicit preferred wins (including ``0.0.0.0`` for debug). Otherwise pick the
    best non-loopback IPv4, preferring RFC1918/link-local-cluster addresses.
    """
    if preferred is not None and str(preferred).strip() != "":
        return str(preferred).strip()

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
        else:
            out = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], text=True, timeout=5, stderr=subprocess.DEVNULL)
            for m in re.finditer(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out):
                found.add(m.group(1))
    except Exception:
        pass

    return [ip for ip in found if not ip.startswith("127.")]


def ensure_rpc_firewall(port: int, bind_host: str) -> dict[str, Any]:
    """Best-effort allow TCP ``port`` for RPC. Never raises for missing tooling."""
    system = platform.system()
    try:
        if system == "Windows":
            return _firewall_windows(port)
        if system == "Linux":
            return _firewall_linux(port)
        if system == "Darwin":
            return {
                "ok": True,
                "applied": False,
                "warning": "macOS firewall is application-based; ensure rpc-server is allowed for TCP "
                f"{port} (bind {bind_host})",
            }
        return {"ok": True, "applied": False, "warning": f"no firewall helper for {system}"}
    except Exception as exc:
        return {"ok": False, "applied": False, "warning": str(exc)}


def _firewall_windows(port: int) -> dict[str, Any]:
    # Remove stale rule then recreate (idempotent enough for lab use).
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Remove-NetFirewallRule -Name '{FIREWALL_RULE_NAME}' -ErrorAction SilentlyContinue",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    cmd = (
        f"New-NetFirewallRule -Name '{FIREWALL_RULE_NAME}' "
        f"-DisplayName 'Kluster LLM RPC Worker' -Direction Inbound -Action Allow "
        f"-Protocol TCP -LocalPort {port} -Profile Any"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "firewall rule failed").strip()
        return {"ok": False, "applied": False, "warning": err}
    return {"ok": True, "applied": True, "warning": None}


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
