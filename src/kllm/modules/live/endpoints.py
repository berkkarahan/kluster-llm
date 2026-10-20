"""Live HTTP endpoints for the agent composition root."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal
from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
)
from fastapi.responses import JSONResponse
from kllm.shared.origin import _live_request as _live_request

_live_feed_provider: Callable[[], Any] | None = None
_live_websocket_handler_provider: Callable[[], Any] | None = None


def set_live_providers(
    *,
    feed: Callable[[], Any] | None = None,
    websocket_handler: Callable[[], Any] | None = None,
) -> None:
    global _live_feed_provider, _live_websocket_handler_provider
    if feed is not None:
        _live_feed_provider = feed
    if websocket_handler is not None:
        _live_websocket_handler_provider = websocket_handler


def _get_live_feed() -> Any:
    if _live_feed_provider is not None:
        return _live_feed_provider()
    raise RuntimeError("live feed provider is not configured")


def _get_websocket_handler() -> Any:
    if _live_websocket_handler_provider is not None:
        return _live_websocket_handler_provider()
    raise RuntimeError("live websocket handler provider is not configured")


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
        payload = _get_live_feed().snapshot(
            selected, logs=wants_logs, after=cursor, continuity_id=continuity
        )
    except OverflowError:
        raise HTTPException(503, "dashboard snapshot is temporarily unavailable") from None
    except ValueError:
        raise HTTPException(400, "invalid log cursor") from None
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


async def dashboard_live_websocket(websocket: WebSocket) -> None:
    await _get_websocket_handler().handle(websocket)


def create_live_router(
    live_feed: Any = None,
    websocket_handler: Any = None,
) -> APIRouter:
    router = APIRouter()

    def get_feed() -> Any:
        if live_feed is not None:
            return live_feed() if callable(live_feed) else live_feed
        return _get_live_feed()

    def get_ws() -> Any:
        if websocket_handler is not None:
            return websocket_handler() if callable(websocket_handler) else websocket_handler
        return _get_websocket_handler()

    @router.get("/v1/dashboard/live")
    @router.get("/dashboard/live")
    def _live(
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
            payload = get_feed().snapshot(
                selected, logs=wants_logs, after=cursor, continuity_id=continuity
            )
        except OverflowError:
            raise HTTPException(503, "dashboard snapshot is temporarily unavailable") from None
        except ValueError:
            raise HTTPException(400, "invalid log cursor") from None
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.websocket("/v1/dashboard/live/ws")
    @router.websocket("/dashboard/live/ws")
    async def _ws(websocket: WebSocket) -> None:
        await get_ws().handle(websocket)

    return router


create_router = create_live_router
