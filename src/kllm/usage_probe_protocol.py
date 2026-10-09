"""Strict JSON framing and normalized resource payload helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, NoReturn

LIMIT = 65536


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def encode(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
        + b"\n"
    )


def decode(frame: bytes, *, framed: bool = True) -> Any:
    if len(frame) > LIMIT or (framed and not frame.endswith(b"\n")):
        raise ValueError("invalid helper frame size/framing")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def invalid(_value: str) -> NoReturn:
        raise ValueError("non-finite JSON value")

    return json.loads(frame.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def metric(unit: str, scope: str, reason: str) -> dict[str, Any]:
    return dict(
        value=None,
        unit=unit,
        scope=scope,
        status="unsupported"
        if reason in ("unsupported_counter", "native_unavailable")
        else "error",
        reason=reason,
        detail=None,
        observed_at=utc(),
        interval_start=None,
        interval_end=None,
        duration_ms=None,
        source="native.supervisor",
    )


def failure(
    source: str,
    reason: str,
    selections: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    node_id: str = "",
) -> dict[str, Any]:
    if source == "gpu":
        return dict(
            host=None,
            devices=[],
            processes=[],
            device_discovery=dict(
                status="unsupported"
                if reason in ("native_unavailable", "unsupported_counter")
                else "error",
                reason=reason,
                observed_at=utc(),
            ),
        )
    return dict(
        host=dict(
            logical_cpu_count=None,
            cpu=metric("percent_machine", "host", reason),
            memory=dict(
                pool_id="host:ram",
                **{
                    k: metric("bytes", "host", reason)
                    for k in ("total", "used", "free", "available")
                },
            ),
        ),
        devices=[],
        processes=[
            dict(
                **p,
                node_id=node_id,
                lifetime_id=f"{node_id}:{p['pid']}:{p['started_at']}",
                state="unavailable",
                identity_verified=False,
                cpu=metric("percent_machine", "process", reason),
                rss=metric("bytes", "process", reason),
            )
            for p in selections
        ],
        device_discovery=None,
    )


def memory_totals(host: dict[str, Any], gpu: dict[str, Any]) -> dict[str, Any]:
    pools: dict[str, dict[str, Any]] = {}
    for device in gpu["devices"]:
        for view in device["memory"]:
            if view["kind"] == "dedicated":
                pools.setdefault(view["pool_id"], view)

    def total(field: str, physical: bool = False) -> dict[str, Any]:
        contributors = {key: view[field] for key, view in pools.items()}
        if physical:
            contributors["host:ram"] = host["memory"]["total"]
        included = [key for key, m in contributors.items() if m["status"] == "measured"]
        missing = [key for key in contributors if key not in included]
        complete = gpu["device_discovery"]["status"] == "measured" and not missing
        return dict(
            value=sum(contributors[k]["value"] for k in included) if included else None,
            unit="bytes",
            status=("complete" if complete else "partial") if included else "unavailable",
            included_pool_ids=included,
            missing_pool_ids=missing,
            observed_at=[contributors[k]["observed_at"] for k in included],
        )

    return dict(
        dedicated_capacity=total("total"),
        dedicated_used=total("used"),
        physical_capacity=total("total", True),
    )
