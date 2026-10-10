"""Usage reading validation and time/serialization helpers."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from kllm.shared.exceptions import ValidationError

WINDOW = timedelta(hours=24)
MAX_BYTES = 256 * 1024 * 1024
WINDOWS = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "24h": WINDOW}


def utc(value: datetime | float) -> str:
    if not isinstance(value, datetime):
        value = datetime.fromtimestamp(value, timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValidationError("timestamp must be ISO 8601 UTC with Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValidationError("invalid UTC timestamp") from exc


def _parse_saved_utc(value: str) -> datetime:
    try:
        return parse_utc(value)
    except ValueError as exc:
        raise sqlite3.DatabaseError("usage history contains a damaged timestamp") from exc


def lifetime_id(node_id: str, pid: int, started: str) -> str:
    return f"{node_id}:{pid}:{started}"


def _decode_reading(payload: str) -> dict[str, Any]:
    try:
        point = json.loads(payload)
        if (
            not isinstance(point, dict)
            or not isinstance(point["process"], dict)
            or not isinstance(point["host"], dict)
            or not isinstance(point["devices"], list)
            or not {"cpu_percent_machine", "rss_bytes"} <= point["process"].keys()
            or not {"cpu_percent", "used_bytes", "total_bytes"} <= point["host"].keys()
        ):
            raise ValidationError("invalid reading")
        _parse_saved_utc(point["observed_at"])
        _parse_saved_utc(point["process_started_at"])
        return point
    except sqlite3.DatabaseError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise sqlite3.DatabaseError("usage history contains a damaged reading") from exc


def _number(value: Any, name: str, maximum: float | None = None) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValidationError(f"invalid {name}")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{name} out of range")


def validate(
    node_id: str,
    point: dict[str, Any],
    *,
    now: datetime,
    expected_pid: int | None = None,
    expected_start: str | None = None,
    expected_role: str | None = None,
) -> None:
    if not isinstance(point, dict) or set(point) - {
        "lifetime_id",
        "pid",
        "process_started_at",
        "process_role",
        "observed_at",
        "process",
        "host",
        "devices",
        "availability",
        "collector_error",
    }:
        raise ValidationError("unexpected reading fields")
    if not isinstance(node_id, str) or not node_id or len(node_id) > 128:
        raise ValidationError("invalid node_id")
    pid = point.get("pid")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or (expected_pid is not None and pid != expected_pid)
    ):
        raise ValidationError("invalid tracked PID")
    start = point.get("process_started_at")
    if not isinstance(start, str):
        raise ValidationError("invalid process start time")
    began = parse_utc(start)
    observed_at = point.get("observed_at")
    if not isinstance(observed_at, str):
        raise ValidationError("invalid observation time")
    observed = parse_utc(observed_at)
    if began > observed or observed > now + timedelta(seconds=30) or observed < now - WINDOW:
        raise ValidationError("reading timestamp outside accepted window")
    if expected_start is not None and parse_utc(expected_start) != began:
        raise ValidationError("process start does not match registered process")
    role = point.get("process_role")
    if role not in ("master", "worker") or (expected_role is not None and role != expected_role):
        raise ValidationError("invalid process role")
    if point.get("lifetime_id") != lifetime_id(node_id, pid, start):
        raise ValidationError("lifetime identity does not match node, PID and start")
    for scope, names in (
        ("process", {"cpu_percent_machine", "rss_bytes"}),
        ("host", {"cpu_percent", "used_bytes", "total_bytes"}),
    ):
        data = point.get(scope)
        if not isinstance(data, dict) or set(data) != names:
            raise ValidationError(f"invalid {scope} fields")
        for key, value in data.items():
            _number(value, key, 100 if "percent" in key else None)
    host = point["host"]
    if (
        host["used_bytes"] is not None
        and host["total_bytes"] is not None
        and host["used_bytes"] > host["total_bytes"]
    ):
        raise ValidationError("used host memory exceeds capacity")
    devices = point.get("devices")
    if not isinstance(devices, list) or len(devices) > 32:
        raise ValidationError("invalid device list")
    seen = set()
    for device in devices:
        if (
            not isinstance(device, dict)
            or set(device)
            - {
                "device_id",
                "scope",
                "utilization_percent",
                "used_bytes",
                "total_bytes",
                "temperature_c",
                "power_w",
                "unavailable_reason",
            }
            or not isinstance(device.get("device_id"), str)
            or len(device["device_id"]) > 64
            or not device["device_id"]
        ):
            raise ValidationError("invalid device fields")
        if device.get("scope") != "device" or device["device_id"] in seen:
            raise ValidationError("invalid device scope or duplicate device")
        seen.add(device["device_id"])
        for key in ("utilization_percent", "used_bytes", "total_bytes", "power_w"):
            _number(device.get(key), key, 100 if "percent" in key else None)
        temp = device.get("temperature_c")
        if temp is not None and (
            isinstance(temp, bool)
            or not isinstance(temp, (int, float))
            or not math.isfinite(temp)
            or not -50 <= temp <= 150
        ):
            raise ValidationError("temperature_c out of sensor range")
        if (
            device.get("used_bytes") is not None
            and device.get("total_bytes") is not None
            and device["used_bytes"] > device["total_bytes"]
        ):
            raise ValidationError("used device memory exceeds capacity")
        if device.get("unavailable_reason") is not None and (
            not isinstance(device["unavailable_reason"], str)
            or len(device["unavailable_reason"]) > 256
        ):
            raise ValidationError("invalid unavailable reason")
        if any(
            device.get(key) is None
            for key in (
                "utilization_percent",
                "used_bytes",
                "total_bytes",
                "temperature_c",
                "power_w",
            )
        ) and not device.get("unavailable_reason"):
            raise ValidationError("unavailable device fields require a reason")
    availability = point.get("availability")
    if not isinstance(availability, dict) or set(availability) - {
        "process_cpu",
        "process_rss",
        "host_cpu",
        "host_memory",
        "device_utilization",
    }:
        raise ValidationError("invalid availability fields")
    if any(
        value not in ("measured", "initializing", "unsupported", "error")
        for value in availability.values()
    ):
        raise ValidationError("invalid availability status")
    for field, value in (
        ("process_cpu", point["process"]["cpu_percent_machine"]),
        ("process_rss", point["process"]["rss_bytes"]),
    ):
        if availability.get(field) != (
            "measured" if value is not None else availability.get(field)
        ) or (
            value is None
            and availability.get(field) not in ("initializing", "unsupported", "error")
        ):
            raise ValidationError(f"{field} availability does not match reading")
    for field, value in (
        ("host_cpu", host["cpu_percent"]),
        (
            "host_memory",
            host["used_bytes"]
            if host["used_bytes"] is not None and host["total_bytes"] is not None
            else None,
        ),
    ):
        status = availability.get(field)
        if (value is None and status not in ("initializing", "unsupported", "error")) or (
            value is not None and status not in (None, "measured")
        ):
            raise ValidationError(f"{field} availability does not match reading")
    if point.get("collector_error") is not None and (
        not isinstance(point["collector_error"], str) or len(point["collector_error"]) > 256
    ):
        raise ValidationError("invalid collector error")
