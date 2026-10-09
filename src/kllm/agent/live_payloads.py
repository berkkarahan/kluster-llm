"""Pure formatting and size-budget helpers for dashboard live snapshots."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from typing import Any


class SourceDenied(Exception):
    """A source denied access; preserve that state instead of calling it unavailable."""


def server_time() -> str:
    """Return a compact UTC timestamp for dashboard events."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_value(value: Any) -> Any:
    """Detach a value from mutable application state before publishing it."""
    return copy.deepcopy(value)


def encoded(value: Any) -> bytes:
    """Serialize a live payload deterministically and reject non-finite numbers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fit_log_suffix(
    snapshot: dict[str, Any], budget_bytes: int, result_prefix: dict[str, Any]
) -> tuple[list[Any], dict[str, Any]]:
    """Keep the maximal newest log suffix that fits the complete result envelope."""
    original = list(snapshot.get("lines") or [])
    # Validate every source row before omission so truncation cannot hide bad data.
    encoded({**result_prefix, "snapshot": snapshot})

    def envelope(lines: list[Any], gap: Any, reason: Any) -> bytes:
        return encoded(
            {
                **result_prefix,
                "snapshot": {**snapshot, "lines": lines, "gap": gap, "gap_reason": reason},
            }
        )

    if (
        len(envelope(original, snapshot.get("gap", False), snapshot.get("gap_reason")))
        <= budget_bytes
    ):
        return original, {
            "gap": snapshot.get("gap", False),
            "gap_reason": snapshot.get("gap_reason"),
        }

    reason = "retention" if snapshot.get("gap_reason") == "retention" else "batch_limit"
    if len(envelope([], True, reason)) > budget_bytes:
        raise OverflowError
    low, high = 1, len(original)
    while low < high:
        omitted = (low + high) // 2
        if len(envelope(original[omitted:], True, reason)) <= budget_bytes:
            high = omitted
        else:
            low = omitted + 1
    snapshot["lines"] = original[low:]
    snapshot["gap"] = True
    snapshot["gap_reason"] = reason
    return snapshot["lines"], {"gap": True, "gap_reason": reason}
