"""Usage HTTP endpoints for the agent composition root."""

from __future__ import annotations

from typing import Any, cast
from fastapi import (
    Depends,
    Query,
    Request,
)
from kllm.modules.usage.router_http import UsageWindow

from kllm.modules.http_deps import require_key

def _host():
    from kllm.agent import runtime as host

    return host

async def usage_ingest(request: Request, _: None = Depends(require_key)) -> dict[str, Any]:
    return cast(dict[str, Any], await _host()._usage_ingest_handlers().ingest(request))

def usage_live() -> dict[str, Any]:
    return cast(dict[str, Any], _host()._usage_live_handlers().live())

def usage_lifetimes(node_id: str = Query(min_length=1, max_length=128)) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._usage_history_handlers().lifetimes(node_id))

def usage_series(
    node_id: str = Query(min_length=1, max_length=128),
    lifetime_id: str = Query(min_length=1, max_length=256),
    window: UsageWindow = "15m",
) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._usage_history_handlers().series(node_id, lifetime_id, window))

def usage_readings(
    node_id: str = Query(min_length=1, max_length=128),
    lifetime_id: str = Query(min_length=1, max_length=256),
    window: UsageWindow = "15m",
    limit: int = Query(default=500, ge=1, le=1000),
    cursor: str | None = Query(default=None, max_length=2048),
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _host()._usage_history_handlers().readings(node_id, lifetime_id, window, limit, cursor),
    )