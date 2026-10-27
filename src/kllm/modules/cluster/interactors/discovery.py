from __future__ import annotations

import ipaddress
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from kllm.shared.state import DEFAULT_CONTROL_PORT, SERVICE_TYPE, repo_root


from kllm.modules.cluster.interactors.discovery_http import (
    MAX_PROBE_HOSTS as MAX_PROBE_HOSTS,
    _browse_via_http as _browse_via_http,
    _fallback_http_probe_hosts as _fallback_http_probe_hosts,
    _http_probe_hosts as _http_probe_hosts,
    _probe_http_master as _probe_http_master,
)

from kllm.modules.cluster.entities import DiscoveredMaster as DiscoveredMaster, join_key_id as join_key_id

LOGGER = logging.getLogger(__name__)

NATIVE_HELPER_NAME = "kllm-mdns-browse"
PROBE_MODULE = f"{__package__}.mdns_probe"
MIN_HELPER_PYTHON = (3, 9)
HELPER_FAILURE_GRACE_S = 8.0


@dataclass
class ProbeAttempt:
    """One discovery layer that was tried, with a stable failure reason."""

    layer: str
    target: str
    ok: bool
    reason: str | None = None
    detail: str | None = None
    duration_ms: int = 0
    masters: int = 0


@dataclass
class BrowseTrace:
    """Ordered account of the mDNS/HTTP discovery chain."""

    attempts: list[ProbeAttempt] = field(default_factory=list)
    path: str | None = None
    masters: int = 0

    def record(self, attempt: ProbeAttempt) -> None:
        self.attempts.append(attempt)

    def summary(self) -> str:
        parts: list[str] = []
        for attempt in self.attempts:
            if attempt.ok:
                state = f"{attempt.masters} found"
            else:
                state = f"failed ({attempt.reason or 'unknown'})"
            parts.append(f"{attempt.layer} {attempt.target}: {state}")
        return "; ".join(parts)


class _MasterListener(ServiceListener):
    def __init__(self) -> None:
        self.masters: dict[str, DiscoveredMaster] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info:
            return
        host = socket.inet_ntoa(info.addresses[0]) if info.addresses else None
        if not host:
            return
        props = {
            k.decode(): v.decode() if isinstance(v, bytes) else str(v)
            for k, v in (info.properties or {}).items()
        }
        if props.get("role") != "master":
            return
        kid = props.get("kid") or None
        self.masters[name] = DiscoveredMaster(
            name=name,
            host=host,
            port=info.port or DEFAULT_CONTROL_PORT,
            key_id=kid,
        )

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.masters.pop(name, None)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)


def _browse_inprocess(timeout: float = 2.0, key: str | None = None) -> list[DiscoveredMaster]:
    zc = Zeroconf()
    listener = _MasterListener()
    browser = ServiceBrowser(zc, SERVICE_TYPE, listener)
    time.sleep(timeout)
    browser.cancel()
    zc.close()
    found = list(listener.masters.values())
    if key is not None:
        want = join_key_id(key)
        found = [m for m in found if m.key_id == want]
    return found


def _package_import_paths() -> list[str]:
    """Package root only — the stdlib probe needs no site-packages."""
    import kllm

    return [str(Path(kllm.__file__).resolve().parent.parent)]


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _mdns_helper_candidates() -> list[str]:
    """Helper interpreters, platform binaries first on macOS.

    macOS Local Network privacy and Zscaler filter third-party executables while
    Apple platform binaries keep LAN access, so ``/usr/bin/python3`` (3.9) is the
    most likely helper to succeed there.
    """
    me = Path(sys.executable).resolve()
    out: list[str] = []

    env = os.environ.get("KLLM_MDNS_HELPER_PYTHON", "").strip()
    if env:
        out.append(env)

    ordered: list[str] = []
    if sys.platform == "darwin":
        ordered.append("/usr/bin/python3")
    if sys.platform != "win32":
        ordered.extend(["/opt/homebrew/bin/python3", "/usr/local/bin/python3"])
    names = ("python3", "python") if sys.platform == "win32" else ("python3",)
    for name in names:
        which = shutil.which(name)
        if which:
            ordered.append(which)
    for raw in ordered:
        if raw in out:
            continue
        try:
            if Path(raw).resolve() == me:
                continue
        except OSError:
            continue
        if Path(raw).is_file():
            out.append(raw)
    return out


def _interpreter_version(python: str) -> tuple[int, int] | None:
    try:
        proc = subprocess.run(
            [python, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        major, minor = proc.stdout.strip().split(".", 1)
        return int(major), int(minor)
    except ValueError:
        return None


def _interpreter_supported(version: tuple[int, int] | None) -> bool:
    return version is not None and version >= MIN_HELPER_PYTHON


def _probe_interfaces() -> list[str]:
    """Local private IPv4 addresses, excluding tunnel (100.64/10) and link-local."""
    from kllm.modules.cluster.interactors import network_discovery

    shared = ipaddress.ip_network("100.64.0.0/10")
    out: list[str] = []
    try:
        addresses = network_discovery._list_ipv4_addresses()
    except Exception:
        return out
    for raw in addresses:
        try:
            addr = ipaddress.ip_address(str(raw))
        except ValueError:
            continue
        if addr.version != 4 or addr.is_loopback or addr.is_link_local:
            continue
        if addr in shared:
            continue
        if not addr.is_private:
            continue
        value = str(addr)
        if value not in out:
            out.append(value)
    return out


def native_helper_candidates() -> list[str]:
    """Candidate native browse binaries (env override, vendored, then PATH)."""
    name = NATIVE_HELPER_NAME + (".exe" if os.name == "nt" else "")
    out: list[str] = []
    env = os.environ.get("KLLM_MDNS_BROWSE_BIN", "").strip()
    if env:
        out.append(env)
    root = repo_root()
    if root is not None:
        vendored = root / "vendor" / "bin" / name
        if vendored.is_file() and str(vendored) not in out:
            out.append(str(vendored))
    which = shutil.which(NATIVE_HELPER_NAME)
    if which and which not in out:
        out.append(which)
    return out


def probe_capabilities() -> dict[str, object]:
    """Static view of the discovery inputs, for diagnostics and tests."""
    return {
        "interpreter": sys.executable,
        "python_helpers": _mdns_helper_candidates(),
        "native_helpers": native_helper_candidates(),
        "interfaces": _probe_interfaces(),
        "probe_module": PROBE_MODULE,
    }


def _probe_payload(stdout: str) -> dict | None:
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _masters_from_payload(payload: dict) -> list[DiscoveredMaster] | None:
    raw = payload.get("masters")
    if not isinstance(raw, list):
        return None
    out: list[DiscoveredMaster] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        host = item.get("host")
        if not isinstance(host, str) or not host:
            continue
        try:
            port = int(item.get("port") or DEFAULT_CONTROL_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_CONTROL_PORT
        out.append(
            DiscoveredMaster(
                name=str(item.get("name") or host),
                host=host,
                port=port,
                key_id=item.get("key_id") if isinstance(item.get("key_id"), str) else None,
            )
        )
    return out


def _run_probe_command(
    argv: list[str],
    *,
    layer: str,
    target: str,
    timeout: float,
    env: dict[str, str] | None = None,
) -> tuple[list[DiscoveredMaster] | None, ProbeAttempt]:
    """Run one helper command speaking the JSON probe protocol."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout + HELPER_FAILURE_GRACE_S,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, ProbeAttempt(
            layer, target, False, "SPAWN_FAILED", str(exc), _elapsed_ms(started)
        )
    payload = _probe_payload(proc.stdout)
    if proc.returncode == 0 and payload is not None and payload.get("ok") is True:
        masters = _masters_from_payload(payload)
        if masters is not None:
            return masters, ProbeAttempt(
                layer, target, True, None, None, _elapsed_ms(started), len(masters)
            )
    reason = None
    detail = None
    if payload is not None:
        raw_reason = payload.get("reason")
        raw_detail = payload.get("detail")
        reason = raw_reason if isinstance(raw_reason, str) and raw_reason else None
        detail = raw_detail if isinstance(raw_detail, str) and raw_detail else None
    stderr = (proc.stderr or "").strip()
    if detail is None and stderr:
        detail = stderr.splitlines()[-1][:300]
    return None, ProbeAttempt(
        layer,
        target,
        False,
        reason or f"EXIT_{proc.returncode}",
        detail,
        _elapsed_ms(started),
    )


def _browse_via_native(
    timeout: float, key: str | None, trace: BrowseTrace | None = None
) -> list[DiscoveredMaster] | None:
    candidates = native_helper_candidates()
    if not candidates:
        if trace is not None:
            trace.record(ProbeAttempt("native", NATIVE_HELPER_NAME, False, "NOT_INSTALLED"))
        return None
    for binary in candidates:
        argv = [binary, "--timeout", str(timeout), "--key", key or ""]
        found, attempt = _run_probe_command(
            argv, layer="native", target=binary, timeout=timeout
        )
        if trace is not None:
            trace.record(attempt)
        if found is not None:
            return found
        LOGGER.debug("native mDNS browse failed via %s: %s", binary, attempt.detail or attempt.reason)
    return None


def _browse_via_python(
    timeout: float, key: str | None, trace: BrowseTrace | None = None
) -> list[DiscoveredMaster] | None:
    """Run the stdlib probe module under a helper interpreter.

    Returns ``None`` when no helper could complete a browse. An empty list is a
    completed browse that found nothing.
    """
    env = os.environ.copy()
    paths = _package_import_paths()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(paths + ([existing] if existing else []))
    interfaces = _probe_interfaces()
    for python in _mdns_helper_candidates():
        version = _interpreter_version(python)
        if not _interpreter_supported(version):
            if trace is not None:
                readable = ".".join(str(part) for part in version) if version else "unknown"
                trace.record(
                    ProbeAttempt("python", python, False, f"UNSUPPORTED_PYTHON_{readable}")
                )
            continue
        argv = [
            python,
            "-m",
            PROBE_MODULE,
            "--timeout",
            str(timeout),
            "--key",
            key or "",
        ]
        for interface in interfaces:
            argv.extend(["--interface", interface])
        found, attempt = _run_probe_command(
            argv, layer="python", target=python, timeout=timeout, env=env
        )
        if trace is not None:
            trace.record(attempt)
        if found is not None:
            return found
        LOGGER.debug("mDNS probe failed via %s: %s", python, attempt.detail or attempt.reason)
    return None


def browse_masters(
    timeout: float = 2.0,
    key: str | None = None,
    port: int = DEFAULT_CONTROL_PORT,
    trace: BrowseTrace | None = None,
) -> list[DiscoveredMaster]:
    """Discover masters on the LAN via mDNS, then HTTP subnet probe.

    The mDNS chain is: in-process zeroconf, then the native ``kllm-mdns-browse``
    binary, then the dependency-free ``mdns_probe`` module under helper
    interpreters. macOS Local Network privacy and Zscaler filter third-party
    executables, so the helper chain (which can include Apple's
    ``/usr/bin/python3``) keeps Join-by-key working when the virtualenv cannot
    reach the LAN. If mDNS is still empty (common for Windows masters without
    Bonjour), probe private LAN subnets for ``/health`` role=master matching the
    join-key fingerprint.
    """
    started = time.monotonic()
    try:
        found = _browse_inprocess(timeout, key)
        if trace is not None:
            trace.record(
                ProbeAttempt(
                    "inprocess",
                    "current interpreter",
                    True,
                    None,
                    None,
                    _elapsed_ms(started),
                    len(found),
                )
            )
    except Exception as exc:
        found = []
        if trace is not None:
            trace.record(
                ProbeAttempt(
                    "inprocess",
                    "current interpreter",
                    False,
                    "INPROCESS_FAILED",
                    str(exc),
                    _elapsed_ms(started),
                )
            )
        LOGGER.debug("in-process mDNS browse failed: %s", exc)
    if found:
        if trace is not None:
            trace.path = "inprocess"
            trace.masters = len(found)
        return found

    native = _browse_via_native(timeout, key, trace)
    if native:
        if trace is not None:
            trace.path = "native"
            trace.masters = len(native)
        return native

    helper = _browse_via_python(timeout, key, trace)
    if helper:
        if trace is not None:
            trace.path = "python"
            trace.masters = len(helper)
        return helper

    from kllm.modules.cluster.interactors import discovery_http

    started_http = time.monotonic()
    found = discovery_http._browse_via_http(timeout, key, port=port)
    if trace is not None:
        trace.record(
            ProbeAttempt(
                "http",
                "private subnets",
                True,
                None,
                None,
                _elapsed_ms(started_http),
                len(found),
            )
        )
        if found:
            trace.path = "http"
        trace.masters = len(found)
    return found


class MasterAdvertiser:
    def __init__(
        self,
        cluster_name: str,
        port: int = DEFAULT_CONTROL_PORT,
        join_key: str | None = None,
        advertise_host: str | None = None,
    ) -> None:
        self.cluster_name = cluster_name
        self.port = port
        self.join_key = join_key
        self.advertise_host = advertise_host
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None

    def start(self) -> None:
        hostname = socket.gethostname()
        local_ip = self.advertise_host or _local_ip()
        self._zc = Zeroconf()
        service_name = f"{self.cluster_name}.{SERVICE_TYPE}"
        props: dict[bytes, bytes] = {
            b"role": b"master",
            b"name": self.cluster_name.encode(),
        }
        if self.join_key:
            props[b"kid"] = join_key_id(self.join_key).encode()
        self._info = ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(local_ip)],
            port=self.port,
            properties=props,
            server=f"{hostname}.local.",
        )
        self._zc.register_service(self._info)

    def stop(self) -> None:
        if self._zc and self._info:
            self._zc.unregister_service(self._info)
            self._zc.close()
        self._zc = None
        self._info = None


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if isinstance(ip, str) else "127.0.0.1"
    except Exception:
        return "127.0.0.1"
