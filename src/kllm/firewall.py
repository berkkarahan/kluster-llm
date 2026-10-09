"""Best-effort platform firewall setup for the RPC server."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

FIREWALL_RULE_NAME = "kllm-RPC"
SOCKETFILTERFW = Path("/usr/libexec/ApplicationFirewall/socketfilterfw")
_RPC_SERVER_REL_PATHS = (
    "vendor/bin/rpc-server",
    "vendor/bin/rpc-server.exe",
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
        # Deferred import: native loads the extension; netsec must stay importable without it.
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
