"""WireGuard runtime lifecycle and key material management."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from kllm.shared.state import ensure_home, home_dir
from kllm.shared.wireguard_config import WG_IFACE


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
        return priv_path.read_text(encoding="utf-8").strip(), pub_path.read_text(
            encoding="utf-8"
        ).strip()
    priv, pub = generate_wg_keypair()
    priv_path.write_text(priv + "\n", encoding="utf-8")
    pub_path.write_text(pub + "\n", encoding="utf-8")
    try:
        os.chmod(priv_path, 0o600)
    except OSError:
        pass
    return priv, pub


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
                subprocess.run(
                    [wg_quick, "down", str(conf)], capture_output=True, text=True, timeout=60
                )
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
