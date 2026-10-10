"""HTTP subnet probing fallback for discovering cluster masters."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import time
import urllib.error
import urllib.request

from kllm.modules.cluster.entities import DiscoveredMaster, join_key_id
from kllm.shared.state import DEFAULT_CONTROL_PORT


MAX_PROBE_HOSTS = 4096


def _fallback_http_probe_hosts() -> list[str]:
    """Private /24 peers derived from raw interface IPs (no netmask available)."""
    # Deferred import: netsec imports discovery (cycle).
    from kllm.shared import netsec

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
    from kllm.shared import netsec

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
        futs = [pool.submit(_probe_http_master, host, port, want, per_host) for host in hosts]
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
