"""Localhost TCP proxy for worker → master control-plane HTTP.

On some macOS hosts (Local Network TCC + Zscaler), the uv-managed Python that
runs ``kllm-agent`` cannot open LAN sockets (``errno 65``) while ``curl`` /
``/usr/bin/python3`` can. Join/heartbeat/unregister then fail even though the
master is reachable.

This module starts a tiny TCP relay under a helper interpreter that *can*
reach the LAN, bound to ``127.0.0.1``. The agent keeps talking HTTP to
localhost; ``rpc_bind`` / ``toward=`` still use the real master LAN IP.
"""

from __future__ import annotations

import errno
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# macOS Local Network / filtered routing typically surfaces as these.
_LAN_BLOCKED_ERRNOS = {
    errno.EHOSTUNREACH,  # 65 on macOS
    errno.ENETUNREACH,
}

_RELAY = r"""
import select, socket, sys, threading

host, port = sys.argv[1], int(sys.argv[2])
target = (host, port)
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(64)
print(srv.getsockname()[1], flush=True)

def pipe(a, b):
    try:
        while True:
            r, _, _ = select.select([a], [], [], 120)
            if not r:
                break
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass

def handle(cs):
    try:
        us = socket.create_connection(target, timeout=5)
    except Exception:
        cs.close()
        return
    threading.Thread(target=pipe, args=(cs, us), daemon=True).start()
    pipe(us, cs)

while True:
    cs, _ = srv.accept()
    threading.Thread(target=handle, args=(cs,), daemon=True).start()
"""

_current: "ProxyHandle | None" = None


@dataclass
class ProxyHandle:
    target_host: str
    target_port: int
    listen_port: int
    pid: int
    helper: str


def tcp_reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    """True if *this* interpreter can TCP-connect to host:port."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def probe_master(host: str, port: int, timeout: float = 1.5) -> str:
    """Return ``ok``, ``blocked`` (need proxy), or ``down`` (let HTTP fail normally)."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return "ok"
    except OSError as exc:
        if exc.errno in _LAN_BLOCKED_ERRNOS:
            return "blocked"
        return "down"


def _helper_candidates() -> list[str]:
    env = os.environ.get("KLLM_CP_PROXY_PYTHON", "").strip()
    out: list[str] = []
    if env:
        out.append(env)
    for path in (
        "/usr/bin/python3",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
    ):
        if path not in out and Path(path).is_file():
            out.append(path)
    which = shutil.which("python3")
    if which and which not in out and Path(which).resolve() != Path(sys.executable).resolve():
        out.append(which)
    return out


def _helper_can_reach(python: str, host: str, port: int) -> bool:
    probe = (
        "import socket,sys;\n"
        f"s=socket.socket(); s.settimeout(2);\n"
        f"s.connect(({host!r}, {int(port)})); s.close();\n"
    )
    try:
        r = subprocess.run(
            [python, "-c", probe],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def find_lan_python(host: str, port: int) -> str | None:
    """Return a Python binary that can reach host:port (not this interpreter)."""
    me = Path(sys.executable).resolve()
    for py in _helper_candidates():
        try:
            if Path(py).resolve() == me:
                continue
        except OSError:
            continue
        if _helper_can_reach(py, host, port):
            return py
    return None


def stop_current() -> None:
    global _current
    handle = _current
    _current = None
    if not handle:
        return
    try:
        os.kill(handle.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        os.waitpid(handle.pid, os.WNOHANG)
    except OSError:
        pass


def start(target_host: str, target_port: int) -> ProxyHandle:
    """Start (or reuse) a localhost relay to target_host:target_port."""
    global _current
    if (
        _current
        and _current.target_host == target_host
        and _current.target_port == int(target_port)
    ):
        try:
            os.kill(_current.pid, 0)
            if tcp_reachable("127.0.0.1", _current.listen_port, timeout=0.5):
                return _current
        except OSError:
            _current = None

    stop_current()
    helper = find_lan_python(target_host, int(target_port))
    if not helper:
        raise RuntimeError(
            f"cannot reach {target_host}:{target_port} from this Python, and no "
            "helper interpreter (e.g. /usr/bin/python3) can either; grant Local "
            "Network access or set KLLM_CP_PROXY_PYTHON"
        )

    proc = subprocess.Popen(
        [helper, "-c", _RELAY, target_host, str(int(target_port))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().decode("utf-8", errors="replace").strip()
    if not line.isdigit() or proc.poll() is not None:
        err = ""
        if proc.stderr is not None:
            err = proc.stderr.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"control-plane proxy failed to start: {err or line!r}")

    listen_port = int(line)
    for _ in range(20):
        if tcp_reachable("127.0.0.1", listen_port, timeout=0.25):
            break
        time.sleep(0.05)
    else:
        try:
            proc.kill()
        except OSError:
            pass
        raise RuntimeError("control-plane proxy started but localhost is not accepting")

    handle = ProxyHandle(
        target_host=target_host,
        target_port=int(target_port),
        listen_port=listen_port,
        pid=proc.pid,
        helper=helper,
    )
    _current = handle
    try:
        proc.stdout.close()
    except Exception:
        pass
    return handle


def ensure_for_master(host: str, port: int) -> ProxyHandle | None:
    """If this interpreter is LAN-blocked but a helper can reach the master, proxy.

    Connection refused / timeouts return None (callers surface the HTTP error).
    ``EHOSTUNREACH`` alone is not enough — incomplete ARP looks the same — so we
    only start a relay when ``find_lan_python`` proves another interpreter can
    reach the master (the uv-Python + Local Network / Zscaler case).
    """
    status = probe_master(host, int(port))
    if status == "ok":
        stop_current()
        return None
    if status == "down":
        return None
    if find_lan_python(host, int(port)) is None:
        return None
    return start(host, int(port))
