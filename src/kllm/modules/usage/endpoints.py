"""Usage HTTP endpoints for the agent composition root."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast
from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)
from kllm.modules.http_deps import require_key

UsageWindow = Literal["15m", "1h", "24h"]

_usage_ingest_handlers_provider: Callable[[], Any] | None = None
_usage_live_handlers_provider: Callable[[], Any] | None = None
_usage_history_handlers_provider: Callable[[], Any] | None = None


def set_usage_handlers_provider(
    *,
    ingest: Callable[[], Any] | None = None,
    live: Callable[[], Any] | None = None,
    history: Callable[[], Any] | None = None,
) -> None:
    global _usage_ingest_handlers_provider, _usage_live_handlers_provider, _usage_history_handlers_provider
    if ingest is not None:
        _usage_ingest_handlers_provider = ingest
    if live is not None:
        _usage_live_handlers_provider = live
    if history is not None:
        _usage_history_handlers_provider = history


def _get_ingest_handlers() -> Any:
    if _usage_ingest_handlers_provider is not None:
        return _usage_ingest_handlers_provider()
    raise RuntimeError("usage ingest handlers provider is not configured")


def _get_live_handlers() -> Any:
    if _usage_live_handlers_provider is not None:
        return _usage_live_handlers_provider()
    raise RuntimeError("usage live handlers provider is not configured")


def _get_history_handlers() -> Any:
    if _usage_history_handlers_provider is not None:
        return _usage_history_handlers_provider()
    raise RuntimeError("usage history handlers provider is not configured")


async def usage_ingest(request: Request, _: None = Depends(require_key)) -> dict[str, Any]:
    return cast(dict[str, Any], await _get_ingest_handlers().ingest(request))


def usage_live() -> dict[str, Any]:
    return cast(dict[str, Any], _get_live_handlers().live())


def usage_lifetimes(node_id: str = Query(min_length=1, max_length=128)) -> dict[str, Any]:
    return cast(dict[str, Any], _get_history_handlers().lifetimes(node_id))


def usage_series(
    node_id: str = Query(min_length=1, max_length=128),
    lifetime_id: str = Query(min_length=1, max_length=256),
    window: UsageWindow = "15m",
) -> dict[str, Any]:
    return cast(dict[str, Any], _get_history_handlers().series(node_id, lifetime_id, window))


def usage_readings(
    node_id: str = Query(min_length=1, max_length=128),
    lifetime_id: str = Query(min_length=1, max_length=256),
    window: UsageWindow = "15m",
    limit: int = Query(default=500, ge=1, le=1000),
    cursor: str | None = Query(default=None, max_length=2048),
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _get_history_handlers().readings(node_id, lifetime_id, window, limit, cursor),
    )


def create_usage_router(
    live_handlers: Any = None,
    history_handlers: Any = None,
    ingest_handlers: Any = None,
) -> APIRouter:
    router = APIRouter()

    def live_h() -> Any:
        return live_handlers if live_handlers is not None else _get_live_handlers()

    def hist_h() -> Any:
        return history_handlers if history_handlers is not None else _get_history_handlers()

    def ing_h() -> Any:
        return ingest_handlers if ingest_handlers is not None else _get_ingest_handlers()

    @router.post("/v1/usage/ingest")
    @router.post("/telemetry/ingest")
    async def _ingest(request: Request, _: None = Depends(require_key)) -> dict[str, Any]:
        return cast(dict[str, Any], await ing_h().ingest(request))

    @router.get("/v1/usage/live")
    @router.get("/telemetry")
    def _live() -> dict[str, Any]:
        return cast(dict[str, Any], live_h().live())

    @router.get("/v1/usage/lifetimes")
    @router.get("/telemetry/lifetimes")
    def _lifetimes(node_id: str = Query(min_length=1, max_length=128)) -> dict[str, Any]:
        return cast(dict[str, Any], hist_h().lifetimes(node_id))

    @router.get("/v1/usage/series")
    @router.get("/telemetry/series")
    def _series(
        node_id: str = Query(min_length=1, max_length=128),
        lifetime_id: str = Query(min_length=1, max_length=256),
        window: UsageWindow = "15m",
    ) -> dict[str, Any]:
        return cast(dict[str, Any], hist_h().series(node_id, lifetime_id, window))

    @router.get("/v1/usage/readings")
    @router.get("/telemetry/readings")
    def _readings(
        node_id: str = Query(min_length=1, max_length=128),
        lifetime_id: str = Query(min_length=1, max_length=256),
        window: UsageWindow = "15m",
        limit: int = Query(default=500, ge=1, le=1000),
        cursor: str | None = Query(default=None, max_length=2048),
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            hist_h().readings(node_id, lifetime_id, window, limit, cursor),
        )

    return router


create_router = create_usage_router