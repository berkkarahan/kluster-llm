"""Log-feed bookkeeping and dashboard websocket frame signatures."""

from __future__ import annotations

import math
import threading
import time
import uuid
from typing import Any, cast

from kllm.modules.logs.interactors import cluster_log
from kllm.modules.live.interactors.live import SourceDenied, server_time as live_server_time


def new_worker_log_state() -> dict[str, Any]:
    """Per-process mirror used when a worker proxies the master's log journal."""
    return {
        "lock": threading.Lock(),
        "flight": None,
        "last_acquired": 0.0,
        "mirror": cluster_log.LogHub(),
        "upstream_cursor": None,
        "source": None,
        "continuity_id": str(uuid.uuid4()),
        "discontinuity": None,
        "changed": False,
        "error": None,
    }


def _valid_upstream_line(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and cluster_log.normalize_event_id(item.get("event_id")) is not None
        and isinstance(item.get("ts"), (int, float))
        and math.isfinite(item["ts"])
        and isinstance(item.get("node_id"), str)
        and bool(item["node_id"])
        and item.get("source") in cluster_log.STORED_SOURCES
        and isinstance(item.get("line"), str)
    )


def apply_upstream_log_batch(state: dict[str, Any], upstream: dict[str, Any] | None) -> None:
    """Validate one master log page and fold it into ``state`` under its lock."""
    lines: list[Any] | None = None
    cursor = None
    if upstream is not None:
        cursor = upstream.get("cursor")
        lines = upstream.get("lines")
        if not isinstance(lines, list):
            raise RuntimeError("Master log source returned invalid data")
        cluster_log.parse_cursor(cursor)
        for item in lines:
            if not _valid_upstream_line(item):
                raise RuntimeError("Master log source returned invalid data")
    with state["lock"]:
        source = "cluster" if upstream is not None else "local"
        changed = state["source"] is not None and state["source"] != source
        if changed:
            state["continuity_id"] = str(uuid.uuid4())
            state["discontinuity"] = {
                "id": state["continuity_id"],
                "reason": "coverage_change",
                "detected_at": live_server_time(),
            }
            state["changed"] = True
        state["source"] = source
        if upstream is not None:
            reset = upstream.get("reset") is not None or bool(upstream.get("gap"))
            if reset:
                state["mirror"].reset()
                state["continuity_id"] = str(uuid.uuid4())
                gap_reason = upstream.get("gap_reason")
                reason = (
                    "upstream_retention"
                    if gap_reason == "retention"
                    else "upstream_batch_limit"
                    if gap_reason == "batch_limit"
                    else "upstream_reset"
                )
                state["discontinuity"] = {
                    "id": state["continuity_id"],
                    "reason": reason,
                    "detected_at": live_server_time(),
                }
                state["changed"] = True
            if lines:
                state["mirror"].ingest(lines, persist=False)
            state["upstream_cursor"] = cursor
        state["error"] = None
        state["last_acquired"] = time.monotonic()


def worker_log_snapshot(state: dict[str, Any], after: str | None, node_id: str) -> dict[str, Any]:
    """Read the worker mirror (or the local hub) after a refresh attempt."""
    with state["lock"]:
        error = state.get("error")
        source = state["source"] or "local"
        continuity_id = state["continuity_id"]
        discontinuity = dict(state["discontinuity"]) if state["discontinuity"] else None
        changed = state["changed"]
        state["changed"] = False
        mirror = state["mirror"]
    if error == "denied":
        raise SourceDenied
    if error == "unavailable":
        raise RuntimeError("Master log source is unavailable")
    if source == "cluster":
        snapshot = cast(dict[str, Any], mirror.snapshot(200, after=after))
        snapshot.update(source="cluster", continuity_id=continuity_id, discontinuity=discontinuity)
    else:
        snapshot = cluster_log.HUB.snapshot(200, node_id, None, after)
        snapshot.update(
            source="local",
            warning="master unreachable; showing local logs",
            continuity_id=continuity_id,
            discontinuity=discontinuity,
        )
    if changed:
        snapshot.update(reset="origin_changed", gap=True, gap_reason="feed_reset")
    return snapshot


def log_result_signature(log_result: dict[str, Any] | None) -> tuple[Any, ...] | None:
    """Identity of a log area used to decide whether a websocket data frame changed."""
    if log_result is None:
        return None
    log_snapshot = log_result.get("snapshot") or {}
    log_error = log_result.get("error") or {}
    discontinuity = log_snapshot.get("discontinuity") or {}
    return (
        log_result.get("availability"),
        log_snapshot.get("cursor"),
        log_snapshot.get("continuity_id"),
        log_snapshot.get("reset"),
        log_snapshot.get("gap"),
        log_snapshot.get("gap_reason"),
        log_snapshot.get("source"),
        log_snapshot.get("warning"),
        log_error.get("code"),
        log_error.get("message"),
        discontinuity.get("id"),
    )
