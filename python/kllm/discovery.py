from __future__ import annotations

import concurrent.futures
import hashlib
import ipaddress
import json
import site
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from kllm.state import DEFAULT_CONTROL_PORT, SERVICE_TYPE


def join_key_id(key: str) -> str:
    """Non-secret fingerprint of a join key for mDNS matching (SHA-256 hex, 16 chars)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class DiscoveredMaster:
    name: str
    host: str
    port: int
    key_id: str | None = None


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
        props = {k.decode(): v.decode() if isinstance(v, bytes) else str(v) for k, v in (info.properties or {}).items()}
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
    """Paths so a helper interpreter can import ``kllm`` + venv ``zeroconf``."""
    import kllm

    paths: list[str] = [str(Path(kllm.__file__).resolve().parent.parent)]
    try:
        paths.extend(site.getsitepackages())
    except Exception:
        pass
    user_site = getattr(site, "USER_SITE", None)
    if user_site:
        paths.append(user_site)
    # Dedupe while preserving order
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _mdns_helper_candidates() -> list[str]:
    """Prefer Homebrew / newer Pythons; Apple ``/usr/bin/python3`` is often 3.9."""
    # Deferred import: cp_proxy is optional helper tooling, not needed to import discovery.
    from kllm import cp_proxy

    me = Path(sys.executable).resolve()
    preferred: list[str] = []
    rest: list[str] = []
    for raw in cp_proxy._helper_candidates():
        try:
            if Path(raw).resolve() == me:
                continue
        except OSError:
            continue
        if "homebrew" in raw or "/usr/local/bin" in raw:
            preferred.append(raw)
        else:
            rest.append(raw)
    return preferred + rest


def _browse_via_helper(timeout: float, key: str | None) -> list[DiscoveredMaster] | None:
    """Run in-process browse under a LAN-capable helper Python.

    Returns ``None`` if no helper could execute the browse (caller keeps empty
    in-process result). Returns a list (possibly empty) when a helper ran.
    """
    paths = _package_import_paths()
    key_arg = key if key is not None else ""
    script = (
        "import json,sys\n"
        f"sys.path[:0]={paths!r}\n"
        "from kllm.discovery import _browse_inprocess\n"
        "key=sys.argv[1] or None\n"
        "timeout=float(sys.argv[2])\n"
        "found=_browse_inprocess(timeout, key)\n"
        "print(json.dumps(["
        '{"name":m.name,"host":m.host,"port":m.port,"key_id":m.key_id} '
        "for m in found]))\n"
    )
    for py in _mdns_helper_candidates():
        try:
            ver = subprocess.run(
                [py, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if ver.returncode != 0:
                continue
            major_s, _, minor_s = ver.stdout.strip().partition(".")
            if int(major_s) < 3 or (int(major_s) == 3 and int(minor_s) < 10):
                continue
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
        try:
            proc = subprocess.run(
                [py, "-c", script, key_arg, str(timeout)],
                capture_output=True,
                text=True,
                timeout=timeout + 8.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        line = (proc.stdout or "").strip().splitlines()
        if not line:
            continue
        try:
            raw = json.loads(line[-1])
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, list):
            continue
        out: list[DiscoveredMaster] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            host = item.get("host")
            if not isinstance(host, str) or not host:
                continue
            out.append(
                DiscoveredMaster(
                    name=str(item.get("name") or host),
                    host=host,
                    port=int(item.get("port") or DEFAULT_CONTROL_PORT),
                    key_id=item.get("key_id") if isinstance(item.get("key_id"), str) else None,
                )
            )
        return out
    return None


MAX_PROBE_HOSTS = 4096


def _fallback_http_probe_hosts() -> list[str]:
    """Private /24 peers derived from raw interface IPs (no netmask available)."""
    # Deferred import: netsec imports discovery (cycle).
    from kllm import netsec

    hosts: list[str] = []
    seen: set[str] = {"127.0.0.1"}
    for ip in netsec._list_ipv4_addresses():
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not addr.is_private or addr.is_loopback or addr.is_link_local:
            continue
        parts = ip.split(".")
        if len(parts) != 4:
            continue
        prefix = ".".join(parts[:3])
        for last in range(1, 255):
            cand = f"{prefix}.{last}"
            if cand not in seen:
                seen.add(cand)
                hosts.append(cand)
    return hosts


def _http_probe_hosts(port: int = DEFAULT_CONTROL_PORT) -> list[str]:
    """Private peers on local interface subnets (plus loopback for same-host tests).

    Uses the real interface netmask so a ``/22`` LAN (e.g. ``192.168.68.0/22``)
    is fully covered, not truncated to the caller's ``/24`` — Join-by-key needs
    this when mDNS is silent and the master sits in a neighbouring third octet.
    Host count is capped at :data:`MAX_PROBE_HOSTS` to bound scan time.
    """
    # Deferred import: netsec imports discovery (cycle).
    from kllm import netsec

    hosts: list[str] = ["127.0.0.1"]
    seen: set[str] = {"127.0.0.1"}

    try:
        networks = netsec._list_ipv4_networks()
    except Exception:
        networks = []

    capped = False
    for net in networks:
        addr = ipaddress.ip_address(str(net.network_address))
        if not addr.is_private or addr.is_loopback or addr.is_link_local:
            continue
        for host in net.hosts():
            cand = str(host)
            if cand in seen:
                continue
            seen.add(cand)
            hosts.append(cand)
            if len(hosts) >= MAX_PROBE_HOSTS:
                capped = True
                break
        if capped:
            break

    if len(hosts) == 1:
        # No usable interface networks: keep the historical /24-per-IP fallback.
        for cand in _fallback_http_probe_hosts():
            if cand not in seen:
                seen.add(cand)
                hosts.append(cand)
    return hosts


def _probe_http_master(
    host: str,
    port: int,
    want_kid: str | None,
    timeout: float,
) -> DiscoveredMaster | None:
    """Return a master if ``host:port`` is healthy and matches optional key id."""
    try:
        with urllib.request.urlopen(f"http://{host}:{int(port)}/health", timeout=timeout) as resp:
            health = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(health, dict) or health.get("role") != "master":
        return None
    kid = health.get("kid") if isinstance(health.get("kid"), str) else None
    if kid is None:
        # Older masters: fall back to status.join_key (LAN-only).
        try:
            with urllib.request.urlopen(
                f"http://{host}:{int(port)}/v1/status", timeout=timeout
            ) as resp:
                status = json.loads(resp.read().decode("utf-8", errors="replace"))
            jk = status.get("join_key") if isinstance(status, dict) else None
            if isinstance(jk, str) and jk:
                kid = join_key_id(jk)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
            kid = None
    if want_kid is not None and kid != want_kid:
        return None
    node = health.get("node_id")
    name = f"kllm-{node}" if isinstance(node, str) and node else f"kllm-{host}"
    return DiscoveredMaster(name=name, host=host, port=int(port), key_id=kid)


def _browse_via_http(
    timeout: float,
    key: str | None,
    port: int = DEFAULT_CONTROL_PORT,
) -> list[DiscoveredMaster]:
    """Scan local private interface subnets for a master when mDNS is silent."""
    want = join_key_id(key) if key else None
    hosts = _http_probe_hosts(port)
    per_host = min(0.45, max(0.12, timeout / 10.0))
    found: list[DiscoveredMaster] = []
    # Scale workers/deadline with the subnet size (/24 → /22 etc.) while keeping
    # join latency bounded. Keyed joins still stop at the first match.
    max_workers = min(256, max(64, len(hosts)))
    waves = len(hosts) / max_workers
    budget = max(timeout, 1.0) + max(1.5, waves * per_host + 0.5)
    deadline = time.time() + budget
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futs = [
            pool.submit(_probe_http_master, host, port, want, per_host) for host in hosts
        ]
        try:
            for fut in concurrent.futures.as_completed(futs, timeout=budget + 0.5):
                if time.time() > deadline:
                    break
                try:
                    hit = fut.result()
                except Exception:
                    continue
                if hit is not None:
                    found.append(hit)
                    if want is not None:
                        # Keyed join only needs one match; stop early.
                        break
        except concurrent.futures.TimeoutError:
            pass
    finally:
        # Do not wait for the rest of the scan — join latency matters more.
        pool.shutdown(wait=False, cancel_futures=True)
    # Deduplicate by host:port
    uniq: dict[tuple[str, int], DiscoveredMaster] = {}
    for m in found:
        uniq[(m.host, m.port)] = m
    return list(uniq.values())


def browse_masters(
    timeout: float = 2.0,
    key: str | None = None,
    port: int = DEFAULT_CONTROL_PORT,
) -> list[DiscoveredMaster]:
    """Discover masters on the LAN via mDNS, then HTTP subnet probe.

    When this interpreter cannot see mDNS (macOS Local Network / Zscaler on
    uv-managed Python), fall back to the same helper-Python pattern used by
    ``cp_proxy``. If mDNS is still empty (common for Windows masters without
    Bonjour), probe private LAN subnets for ``/health`` role=master matching
    the join-key fingerprint so Join-by-key works without ``master_host``.
    """
    found = _browse_inprocess(timeout, key)
    if found:
        return found
    helper = _browse_via_helper(timeout, key)
    if helper:
        return helper
    return _browse_via_http(timeout, key, port=port)


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
        return ip
    except Exception:
        return "127.0.0.1"
