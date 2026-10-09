"""Immutable progress snapshot for one model download."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DownloadProgress:
    """Stable internal representation serialized at API and callback boundaries."""

    operation_id: str
    filename: str
    bytes: int
    total: int | None
    pct: int
    phase: str
    rate_bps: float | None
    eta_seconds: float | None
    started_at: str
    updated_at: str
    finished_at: str | None
    error: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        """Return the established JSON-compatible progress payload."""
        payload: dict[str, Any] = {
            "operation_id": self.operation_id,
            "filename": self.filename,
            "bytes": self.bytes,
            "total": self.total,
            "pct": self.pct,
            "phase": self.phase,
            "rate_bps": self.rate_bps,
            "eta_seconds": self.eta_seconds,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload
