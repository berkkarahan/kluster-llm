"""Read-only queries and cursor pagination for usage history."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from contextlib import nullcontext

from sqlalchemy import Connection, and_, func, literal, or_, select
from kllm.modules.usage import entities as s
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from kllm.shared.exceptions import ValidationError
from kllm.modules.usage.interactors.usage_support import (
    WINDOW,
    WINDOWS,
    _decode_reading,
    _parse_saved_utc,
    parse_utc,
    utc,
)


class UsageHistoryQueries:
    """Bounded read views over an operation-owned Core connection."""

    def __init__(
        self,
        db: Connection,
        meta: Callable[[str], str | None],
        status: Callable[[], tuple[str, str | None, str | None]],
    ) -> None:
        self.db = db
        self.lock = nullcontext()
        self._meta = meta
        self._status = status

    def count(self) -> int:
        with self.lock:
            row = self.db.execute(select(func.count()).select_from(s.readings)).fetchone()
            return int(row[0]) if row else 0

    def latest(self, node_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                select(s.readings.c.payload, s.readings.c.received_at)
                .where(s.readings.c.node_id == node_id)
                .order_by(s.readings.c.observed_at.desc(), s.readings.c.id.desc())
                .limit(1)
            ).fetchone()
            return {**_decode_reading(row[0]), "received_at": row[1]} if row else None

    def known_nodes(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {
                nid: {"host": host, "role": role, **json.loads(context)}
                for nid, host, role, context in self.db.execute(
                    select(s.nodes.c.node_id, s.nodes.c.host, s.nodes.c.role, s.nodes.c.context)
                )
            }

    def coverage(self, node_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            statement = select(
                func.min(s.readings.c.observed_at), func.max(s.readings.c.observed_at)
            )
            if node_id is not None:
                statement = statement.where(s.readings.c.node_id == node_id)
            row = self.db.execute(statement).one()
            recording, error, last_successful_save = self._status()
            evicted_at = self._meta("storage_truncated_at")
            return {
                "earliest_retained": row[0],
                "latest_retained": row[1],
                "coverage": "recorded" if row[0] else "unrecorded",
                "storage_truncated": self._meta("storage_truncated") == "1"
                and (
                    evicted_at is None
                    or _parse_saved_utc(evicted_at) >= datetime.now(timezone.utc) - WINDOW
                ),
                "recording": recording if row[0] or recording == "failed" else "interrupted",
                "error": error,
                "last_successful_save": last_successful_save,
            }

    def lifetimes(self, node_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                select(
                    s.lifetimes.c.lifetime_id,
                    s.lifetimes.c.pid,
                    s.lifetimes.c.role,
                    s.lifetimes.c.started_at,
                    s.lifetimes.c.ended_at,
                    s.lifetimes.c.state,
                    s.lifetimes.c.model,
                )
                .where(s.lifetimes.c.node_id == node_id)
                .order_by(s.lifetimes.c.started_at.desc())
            ).fetchall()
            result = []
            for row in rows:
                life = dict(
                    zip(
                        ("lifetime_id", "pid", "role", "started_at", "ended_at", "state", "model"),
                        row,
                    )
                )
                life["associations"] = [
                    dict(zip(("model", "observed_from", "observed_until"), entry))
                    for entry in self.db.execute(
                        select(
                            s.associations.c.model,
                            s.associations.c.observed_from,
                            s.associations.c.observed_until,
                        )
                        .where(s.associations.c.lifetime_id == row[0])
                        .order_by(s.associations.c.observed_from)
                    )
                ]
                result.append(life)
            return result

    def _filters(self, node_id: str, life_id: str, window: str, now: datetime) -> tuple[str, str]:
        if window not in WINDOWS:
            raise ValidationError("window must be 15m, 1h or 24h")
        if not self.db.execute(
            select(literal(1))
            .select_from(s.lifetimes)
            .where(s.lifetimes.c.node_id == node_id, s.lifetimes.c.lifetime_id == life_id)
        ).fetchone():
            raise LookupError("unknown node or process lifetime")
        return utc(now - WINDOWS[window]), utc(now)

    def _cursor_signature(self, data: dict[str, Any]) -> str:
        content = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        secret = self._meta("cursor_secret")
        if secret is None:
            raise RuntimeError("usage cursor secret is missing")
        return hmac.new(bytes.fromhex(secret), content, hashlib.sha256).hexdigest()

    def series_snapshot(
        self, node_id: str, life_id: str, window: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            start, end = self._filters(node_id, life_id, window, now)
            rows = self.db.execute(
                select(s.readings.c.observed_at, s.readings.c.payload)
                .where(
                    s.readings.c.node_id == node_id,
                    s.readings.c.lifetime_id == life_id,
                    s.readings.c.observed_at >= start,
                    s.readings.c.observed_at <= end,
                )
                .order_by(s.readings.c.observed_at, s.readings.c.id)
            ).fetchall()
            coverage = self.coverage(node_id)

        return rows, coverage, start, end

    @staticmethod
    def aggregate_series(rows, coverage, start, end, window):
        # Consume a materialized snapshot after the store releases its lease.
        step = {"15m": 2, "1h": 5, "24h": 120}[window]
        length = int(WINDOWS[window].total_seconds() / step)
        start_at = parse_utc(start)
        buckets: list[dict[str, Any]] = [
            {
                "start": utc(start_at + timedelta(seconds=i * step)),
                "end": utc(start_at + timedelta(seconds=(i + 1) * step)),
                "count": 0,
                "process_cpu_min": None,
                "process_cpu_max": None,
                "process_rss_min": None,
                "process_rss_max": None,
                "host_cpu_min": None,
                "host_cpu_max": None,
                "host_used_bytes_min": None,
                "host_used_bytes_max": None,
                "devices": [],
            }
            for i in range(length)
        ]
        devices_by_bucket: list[dict[str, dict[str, Any]]] = [{} for _ in range(length)]
        for stamp, payload in rows:
            index = min(
                int((_parse_saved_utc(stamp) - start_at).total_seconds() // step), length - 1
            )
            if index < 0:
                continue
            bucket = buckets[index]
            point = _decode_reading(payload)
            bucket["count"] += 1
            for field, value in (
                ("process_cpu", point["process"]["cpu_percent_machine"]),
                ("process_rss", point["process"]["rss_bytes"]),
                ("host_cpu", point["host"]["cpu_percent"]),
                ("host_used_bytes", point["host"]["used_bytes"]),
            ):
                if value is not None:
                    low, high = field + "_min", field + "_max"
                    bucket[low] = value if bucket[low] is None else min(value, bucket[low])
                    bucket[high] = value if bucket[high] is None else max(value, bucket[high])
            for device in point["devices"]:
                device_id = device["device_id"]
                entry = devices_by_bucket[index].get(device_id)
                if entry is None:
                    entry = {
                        "device_id": device_id,
                        "scope": "device",
                        "total_bytes": None,
                        "unavailable_reason": None,
                    }
                    devices_by_bucket[index][device_id] = entry
                    bucket["devices"].append(entry)
                entry["total_bytes"] = device.get("total_bytes") or entry["total_bytes"]
                entry["unavailable_reason"] = (
                    device.get("unavailable_reason") or entry["unavailable_reason"]
                )
                for field in ("utilization_percent", "used_bytes", "temperature_c", "power_w"):
                    value = device.get(field)
                    if value is not None:
                        low, high = field + "_min", field + "_max"
                        entry[low] = value if entry.get(low) is None else min(value, entry[low])
                        entry[high] = value if entry.get(high) is None else max(value, entry[high])
        return {
            "buckets": buckets,
            "window_start": start,
            "window_end": end,
            "time_zone": "UTC",
            **coverage,
        }

    def readings(
        self,
        node_id: str,
        life_id: str,
        window: str,
        *,
        limit: int = 500,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValidationError("limit must be 1–1000")
        now = now or datetime.now(timezone.utc)
        with self.lock:
            start, end = self._filters(node_id, life_id, window, now)
            seek = ("", 0)
            if cursor is not None:
                try:
                    if len(cursor) > 2048:
                        raise ValidationError("cursor too long")
                    doc = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                    signature = doc.pop("sig")
                    if not isinstance(signature, str) or not hmac.compare_digest(
                        signature, self._cursor_signature(doc)
                    ):
                        raise ValidationError("cursor signature invalid")
                    if (doc["node"], doc["life"], doc["window"]) != (node_id, life_id, window):
                        raise ValidationError("cursor does not match filters")
                    start, end = doc["start"], doc["end"]
                    parse_utc(start), parse_utc(end)
                    if (
                        parse_utc(end) - parse_utc(start) != WINDOWS[window]
                        or parse_utc(end) > now + timedelta(seconds=30)
                        or parse_utc(start) < now - WINDOW - timedelta(minutes=1)
                    ):
                        raise ValidationError("cursor window expired")
                    seek = (doc["last"], int(doc["id"]))
                    if (
                        not start <= seek[0] <= end
                        or seek[1] <= 0
                        or not self.db.execute(
                            select(literal(1))
                            .select_from(s.readings)
                            .where(
                                s.readings.c.node_id == node_id,
                                s.readings.c.lifetime_id == life_id,
                                s.readings.c.observed_at == seek[0],
                                s.readings.c.id == seek[1],
                            )
                        ).fetchone()
                    ):
                        raise ValidationError("cursor position invalid")
                except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error):
                    raise ValidationError("invalid or mismatched history cursor") from None
            rows = self.db.execute(
                select(
                    s.readings.c.id,
                    s.readings.c.observed_at,
                    s.readings.c.payload,
                    s.readings.c.received_at,
                )
                .where(
                    s.readings.c.node_id == node_id,
                    s.readings.c.lifetime_id == life_id,
                    s.readings.c.observed_at >= start,
                    s.readings.c.observed_at <= end,
                    or_(
                        s.readings.c.observed_at > seek[0],
                        and_(s.readings.c.observed_at == seek[0], s.readings.c.id > seek[1]),
                    ),
                )
                .order_by(s.readings.c.observed_at, s.readings.c.id)
                .limit(limit + 1)
            ).fetchall()
            page = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                data = {
                    "node": node_id,
                    "life": life_id,
                    "window": window,
                    "start": start,
                    "end": end,
                    "last": page[-1][1],
                    "id": page[-1][0],
                }
                data["sig"] = self._cursor_signature(data)
                next_cursor = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
            return {
                "readings": [{**_decode_reading(row[2]), "received_at": row[3]} for row in page],
                "next_cursor": next_cursor,
                "window_start": start,
                "window_end": end,
                "time_zone": "UTC",
                **self.coverage(node_id),
            }
