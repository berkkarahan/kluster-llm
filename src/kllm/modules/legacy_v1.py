"""Legacy v1 control routes, aggregated so kllm.agent.legacy_v1_router stays stable."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter

from kllm.modules.cluster.router_http import (
    LEGACY_V1_DISCOVER,
    LEGACY_V1_LIFECYCLE,
    LEGACY_V1_STATUS,
)
from kllm.modules.http_routes import Endpoint, bind_routes
from kllm.modules.inference.router_http import LEGACY_V1_LOAD, LEGACY_V1_PROXY, LEGACY_V1_RUN
from kllm.modules.live.router_http import LEGACY_V1 as LIVE_V1
from kllm.modules.logs.router_http import LEGACY_V1 as LOGS_V1
from kllm.modules.models.router_http import LEGACY_V1_CATALOG, LEGACY_V1_DELETE
from kllm.modules.runtimes.router_http import LEGACY_V1 as RUNTIME_V1
from kllm.modules.settings.router_http import LEGACY_V1_CONFIG, LEGACY_V1_CREDENTIALS
from kllm.modules.updates.router_http import LEGACY_V1 as UPDATES_V1
from kllm.modules.usage.router_http import LEGACY_V1 as USAGE_V1

ROUTES = (
    *RUNTIME_V1,
    *LEGACY_V1_DISCOVER,
    *LEGACY_V1_CATALOG,
    *LEGACY_V1_LOAD,
    *UPDATES_V1,
    *LEGACY_V1_DELETE,
    *LEGACY_V1_PROXY,
    *LEGACY_V1_CREDENTIALS,
    *LEGACY_V1_CONFIG,
    *LEGACY_V1_STATUS,
    *LEGACY_V1_LIFECYCLE,
    *USAGE_V1,
    *LOGS_V1,
    *LIVE_V1,
    *LEGACY_V1_RUN,
)


def build_router(handlers: Mapping[str, Endpoint]) -> APIRouter:
    """Bind the legacy v1 paths to agent endpoint handlers."""
    router = APIRouter(tags=["legacy-v1"])
    bind_routes(router, ROUTES, handlers, version="v1")
    return router
