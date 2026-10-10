"""Live HTTP endpoints for the agent composition root."""

from __future__ import annotations

from typing import Literal
from fastapi import (
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
)
from fastapi.responses import JSONResponse
from kllm.shared.origin import _live_request as _live_request

def _host():
    from kllm.agent import runtime as host

    return host

def dashboard_live(
    request: Request,
    topics: str | None = Query(default=None, max_length=256),
    logs: Literal["0", "1"] = "0",
    after: str | None = Query(default=None, max_length=128),
    continuity_id: str | None = Query(default=None, max_length=64),
    page_origin: str | None = Query(default=None, max_length=512),
    origin: str | None = Header(default=None),
) -> JSONResponse:
    selected, wants_logs, cursor, continuity = _live_request(
        topics, logs, after, continuity_id, page_origin, origin, request
    )
    try:
        payload = _host()._live_feed().snapshot(
            selected, logs=wants_logs, after=cursor, continuity_id=continuity
        )
    except OverflowError:
        raise HTTPException(503, "dashboard snapshot is temporarily unavailable") from None
    except ValueError:
        raise HTTPException(400, "invalid log cursor") from None
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})

async def dashboard_live_websocket(websocket: WebSocket) -> None:
    await _host()._live_websocket_handler().handle(websocket)
