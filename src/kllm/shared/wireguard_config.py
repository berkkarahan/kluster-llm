"""Pure WireGuard key derivation, address allocation, and config rendering."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

WG_PREFIX = "10.66.87."
WG_PSK_INFO = b"kllm-wg-psk-v1"
WG_SUBNET = "10.66.87.0/24"
WG_LISTEN_PORT = 51820
WG_IFACE = "kllm0"


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    if not salt:
        salt = bytes(32)  # HashLen zeros for SHA-256
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out = b""
    t = b""
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]


def derive_wg_psk(join_key: str) -> str:
    """HKDF-SHA256 → 32-byte WireGuard PSK, base64-encoded."""
    prk = _hkdf_extract(b"", join_key.encode("utf-8"))
    okm = _hkdf_expand(prk, WG_PSK_INFO, 32)
    return base64.b64encode(okm).decode("ascii")


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
