from __future__ import annotations

import hashlib
import socket
import time
from dataclasses import dataclass

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


def browse_masters(timeout: float = 2.0, key: str | None = None) -> list[DiscoveredMaster]:
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
