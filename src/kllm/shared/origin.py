"""Origin validation for the dashboard's HTTP and WebSocket feeds.

Lives outside ``kllm.agent`` so feature routers can import it without loading
the composition root.
"""

from __future__ import annotations

import ipaddress
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket

from kllm.modules.logs.interactors import cluster_log
from kllm.modules.live.interactors.live import TOPICS as LIVE_TOPICS


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def _live_request(
    topics: str | None,
    logs: str,
    after: str | None,
    continuity_id: str | None,
    page_origin: str | None,
    origin: str | None,
    request: Request,
) -> tuple[list[str], bool, str | None, str | None]:
    if topics is None:
        selected = list(LIVE_TOPICS)
    elif topics == "":
        selected = []
    else:
        selected = topics.split(",")
        if len(selected) != len(set(selected)) or any(
            topic not in LIVE_TOPICS for topic in selected
        ):
            raise HTTPException(
                422, "topics must be a unique subset of status, usage, load, download"
            )
    wants_logs = logs == "1"
    if (after is not None or continuity_id is not None) and not wants_logs:
        raise HTTPException(422, "after and continuity_id require logs=1")
    if not selected and not wants_logs:
        raise HTTPException(422, "request at least one topic or logs")
    if after is not None:
        try:
            cluster_log.parse_cursor(after)
        except cluster_log.CursorError:
            raise HTTPException(400, "invalid log cursor") from None
    if continuity_id is not None:
        try:
            uuid.UUID(continuity_id)
        except ValueError:
            raise HTTPException(422, "invalid continuity_id") from None
    target = _request_origin(request)
    if page_origin is not None:
        if _origin(page_origin) is None:
            raise HTTPException(422, "invalid page_origin")
        if not _origin_allowed(
            page_origin, target, request.client.host if request.client else None
        ):
            raise HTTPException(403, "page origin is not permitted")
    if origin is not None and not _origin_allowed(
        origin, target, request.client.host if request.client else None
    ):
        raise HTTPException(403, "origin is not permitted")
    return selected, wants_logs, after, continuity_id


def _origin(value: str, *, limit: int = 512) -> tuple[str, str, int] | None:
    if not value or len(value) > limit:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        if parsed.path or parsed.query or parsed.fragment:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.rstrip(".").lower(), port


def _origin_allowed(value: str, target: tuple[str, str, int] | None, peer: str | None) -> bool:
    origin = _origin(value)
    if origin is None or target is None:
        return False
    if origin == target:
        return True
    return (
        origin[0] == "http"
        and origin[2] == 5173
        and _is_loopback(origin[1])
        and _is_loopback(target[1])
        and _is_loopback(peer)
    )


def _request_origin(request: Request) -> tuple[str, str, int] | None:
    parsed = urlsplit(str(request.url))
    return _origin(f"{parsed.scheme}://{parsed.netloc}")


def _websocket_origin(websocket: WebSocket) -> tuple[str, str, int] | None:
    parsed = urlsplit(str(websocket.url))
    scheme = "https" if parsed.scheme == "wss" else "http"
    return _origin(f"{scheme}://{parsed.netloc}")
