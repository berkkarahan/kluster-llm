"""Typed identities used by LAN master discovery."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def join_key_id(key: str) -> str:
    """Non-secret fingerprint of a join key for mDNS matching (SHA-256 hex, 16 chars)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class DiscoveredMaster:
    name: str
    host: str
    port: int
    key_id: str | None = None
