"""Dashboard WebSocket protocol handling."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from kllm.modules.logs.interactors import cluster_log
from kllm.modules.live.interactors.live import LiveFeed, TOPICS


def safe_control(value: Any) -> bool:
    """Accept only JSON-safe integer control identifiers."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**53


def parse_subscription(
    control: dict[str, Any], generation: int
) -> tuple[int, list[str], str | None, str | None] | None:
    """Validate and normalize a version-one dashboard subscription frame."""
    if (
        set(control) != {"type", "version", "generation", "topics", "logs"}
        or control.get("type") != "subscribe"
        or control.get("version") != 1
    ):
        return None
    next_generation = control.get("generation")
    topics = control.get("topics")
    logs = control.get("logs")
    if (
        not isinstance(next_generation, int)
        or isinstance(next_generation, bool)
        or next_generation <= generation
        or not isinstance(topics, list)
    ):
        return None
    if any(not isinstance(topic, str) or topic not in TOPICS for topic in topics):
        return None
    if len(topics) != len(set(topics)):
        return None
    cursor: str | None
    continuity_id: str | None
    if logs is None:
        cursor = None
        continuity_id = None
    elif isinstance(logs, dict) and set(logs) == {"after", "continuity_id"}:
        cursor = logs.get("after")
        continuity_id = logs.get("continuity_id")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 128):
            return None
        if continuity_id is not None:
            try:
                uuid.UUID(continuity_id)
            except (ValueError, TypeError, AttributeError):
                return None
    else:
        return None
    if not topics and logs is None:
        return None
    try:
        if cursor is not None:
            cluster_log.parse_cursor(cursor)
    except cluster_log.CursorError:
        return None
    return next_generation, topics, cursor, continuity_id


@dataclass(frozen=True)
class LiveWebSocketDependencies:
    """Route-bound dependencies used by each live dashboard connection."""

    live_feed: Callable[[], LiveFeed]
    websocket_origin: Callable[[WebSocket], tuple[str, str, int] | None]
    origin_allowed: Callable[[str, tuple[str, str, int] | None, str | None], bool]
    server_time: Callable[[], str]
    log_signature: Callable[[Any], tuple[Any, ...] | None]
    subscription: Callable[
        [dict[str, Any], int], tuple[int, list[str], str | None, str | None] | None
    ]
    safe_control: Callable[[Any], bool]


class DashboardLiveWebSocket:
    """Serve the acknowledged, bounded live dashboard protocol."""

    def __init__(self, dependencies: LiveWebSocketDependencies) -> None:
        self._live_feed = dependencies.live_feed
        self._websocket_origin = dependencies.websocket_origin
        self._origin_allowed = dependencies.origin_allowed
        self._server_time = dependencies.server_time
        self._log_signature = dependencies.log_signature
        self._subscription = dependencies.subscription
        self._safe_control = dependencies.safe_control

    async def handle(self, websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        target = self._websocket_origin(websocket)
        peer = websocket.client.host if websocket.client else None
        if origin is None or not self._origin_allowed(origin, target, peer):
            await websocket.close(code=1008)
            return
        feed = self._live_feed()
        viewer_id = str(uuid.uuid4())
        serving_epoch = feed.current_epoch()
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "epoch": serving_epoch,
                "server_time": self._server_time(),
                "heartbeat_ms": 2000,
            }
        )
        generation = 0
        topics: list[str] = []
        logs_requested = False
        log_cursor: str | None = None
        log_continuity: str | None = None
        outstanding: tuple[int, float, str | None, str | None, tuple[Any, ...] | None] | None = None
        sent_revisions: dict[str, int] = {}
        sent_log_signature: tuple[Any, ...] | None = None
        batch = 0
        next_ping = time.monotonic() + 2
        next_refresh = time.monotonic()
        peer_seen = time.monotonic()
        control_times: list[float] = []
        ping_id = 0
        pending_ping: int | None = None

        async def fail(code: str, message: str, close_code: int = 1008) -> None:
            await websocket.send_json(
                {"type": "error", "area": "connection", "code": code, "message": message}
            )
            await websocket.close(code=close_code)

        try:
            while True:
                now = time.monotonic()
                if feed.current_epoch() != serving_epoch:
                    await websocket.close(code=1012)
                    return
                if now - peer_seen > 4:
                    await websocket.close(code=1013)
                    return
                if outstanding is not None and now - outstanding[1] > 5:
                    await websocket.close(code=1013)
                    return
                if generation and outstanding is None and now >= next_refresh:
                    try:
                        snapshot = await asyncio.to_thread(
                            feed.snapshot,
                            topics,
                            logs=logs_requested,
                            after=log_cursor,
                            continuity_id=log_continuity,
                            lease=False,
                        )
                    except ValueError:
                        await fail("invalid_cursor", "Invalid log cursor")
                        return
                    if feed.current_epoch() != serving_epoch:
                        await websocket.close(code=1012)
                        return
                    changed = {
                        topic: record
                        for topic, record in snapshot["areas"].items()
                        if sent_revisions.get(topic) != record["revision"]
                    }
                    log_result = snapshot["logs"]
                    current_cursor = (
                        (log_result or {}).get("snapshot", {}).get("cursor") if log_result else None
                    )
                    current_signature = self._log_signature(log_result) if log_result else None
                    if changed or (
                        log_result is not None and current_signature != sent_log_signature
                    ):
                        batch += 1
                        await websocket.send_json(
                            {
                                "type": "data",
                                "generation": generation,
                                "batch": batch,
                                "initial": False,
                                "epoch": snapshot["epoch"],
                                "server_time": self._server_time(),
                                "areas": changed,
                                "logs": log_result,
                            }
                        )
                        current_continuity = (
                            (log_result or {}).get("snapshot", {}).get("continuity_id")
                            if log_result
                            else None
                        )
                        outstanding = (
                            batch,
                            time.monotonic(),
                            current_cursor,
                            current_continuity,
                            current_signature,
                        )
                        sent_revisions.update(
                            {topic: record["revision"] for topic, record in changed.items()}
                        )
                    next_refresh = time.monotonic() + 1
                timeout = min(max(0.01, next_ping - now), max(0.01, next_refresh - now), 1.0)
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout)
                except TimeoutError:
                    if time.monotonic() >= next_ping:
                        ping_id += 1
                        pending_ping = ping_id
                        await websocket.send_json({"type": "ping", "id": ping_id})
                        next_ping = time.monotonic() + 2
                    continue
                if message["type"] == "websocket.disconnect":
                    return
                raw = message.get("text")
                if raw is None:
                    await fail("invalid_request", "JSON text controls are required")
                    return
                if len(raw.encode()) > 4096:
                    await websocket.close(code=1009)
                    return
                try:
                    control = json.loads(raw)
                except ValueError:
                    await fail("invalid_request", "Invalid JSON control")
                    return
                now = time.monotonic()
                control_times = [item for item in control_times if now - item < 1]
                control_times.append(now)
                if len(control_times) > 20 or not isinstance(control, dict):
                    await fail("invalid_request", "Invalid control")
                    return
                peer_seen = now
                kind = control.get("type")
                if kind == "subscribe":
                    parsed = self._subscription(control, generation)
                    if parsed is None:
                        await fail("invalid_request", "Invalid subscription")
                        return
                    generation, topics, log_cursor, log_continuity = parsed
                    logs_requested = control["logs"] is not None
                    feed.subscribe(viewer_id, topics)
                    sent_revisions = {}
                    sent_log_signature = None
                    try:
                        snapshot = await asyncio.to_thread(
                            feed.snapshot,
                            topics,
                            logs=logs_requested,
                            after=log_cursor,
                            continuity_id=log_continuity,
                            lease=False,
                        )
                    except ValueError:
                        await fail("invalid_cursor", "Invalid log cursor")
                        return
                    if feed.current_epoch() != serving_epoch:
                        await websocket.close(code=1012)
                        return
                    batch += 1
                    current_cursor = (
                        (snapshot["logs"] or {}).get("snapshot", {}).get("cursor")
                        if snapshot["logs"]
                        else None
                    )
                    await websocket.send_json(
                        {
                            "type": "data",
                            "generation": generation,
                            "batch": batch,
                            "initial": True,
                            "epoch": snapshot["epoch"],
                            "server_time": self._server_time(),
                            "areas": snapshot["areas"],
                            "logs": snapshot["logs"],
                        }
                    )
                    current_continuity = (
                        (snapshot["logs"] or {}).get("snapshot", {}).get("continuity_id")
                        if snapshot["logs"]
                        else None
                    )
                    logs = snapshot["logs"]
                    current_signature = self._log_signature(logs)
                    outstanding = (
                        batch,
                        time.monotonic(),
                        current_cursor,
                        current_continuity,
                        current_signature,
                    )
                    sent_revisions = {
                        topic: record["revision"] for topic, record in snapshot["areas"].items()
                    }
                    next_refresh = time.monotonic() + 1
                elif (
                    kind == "ack"
                    and set(control) == {"type", "generation", "batch"}
                    and self._safe_control(control.get("generation"))
                    and self._safe_control(control.get("batch"))
                ):
                    if (
                        outstanding is not None
                        and control["generation"] == generation
                        and control["batch"] == outstanding[0]
                    ):
                        log_cursor = outstanding[2]
                        log_continuity = outstanding[3]
                        sent_log_signature = outstanding[4]
                        outstanding = None
                    elif control["generation"] > generation or (
                        control["generation"] == generation
                        and (outstanding is None or control["batch"] > outstanding[0])
                    ):
                        await fail("invalid_request", "Invalid acknowledgement")
                        return
                elif (
                    kind == "ping"
                    and set(control) == {"type", "id"}
                    and self._safe_control(control.get("id"))
                ):
                    await websocket.send_json(
                        {"type": "pong", "id": control["id"], "server_time": self._server_time()}
                    )
                elif (
                    kind == "pong"
                    and set(control) == {"type", "id"}
                    and control.get("id") == pending_ping
                ):
                    pending_ping = None
                else:
                    await fail("invalid_request", "Invalid control")
                    return
        except WebSocketDisconnect:
            return
        finally:
            feed.unsubscribe(viewer_id)

LEGACY_V1: tuple[tuple[str, str, str], ...] = (
    ('/v1/dashboard/live', 'GET', 'dashboard_live'),
    ('/v1/dashboard/live', 'WEBSOCKET', 'dashboard_live_websocket'),
)

CONTROL_V2: tuple[tuple[str, str, str], ...] = (
    ('/events', 'GET', 'dashboard_live'),
    ('/events', 'WEBSOCKET', 'dashboard_live_websocket'),
)

