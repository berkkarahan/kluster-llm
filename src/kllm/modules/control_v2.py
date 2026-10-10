"""Version 2 control routes split across feature modules."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter

from kllm.modules.cluster.router_http import (
    CONTROL_V2_CLUSTER,
    CONTROL_V2_FAST,
    CONTROL_V2_MEMBERSHIP,
    CONTROL_V2_NODE_STATUS,
)
from kllm.modules.http_routes import Endpoint, bind_routes
from kllm.modules.inference.router_http import CONTROL_V2 as INFERENCE_V2
from kllm.modules.live.router_http import CONTROL_V2 as LIVE_V2
from kllm.modules.logs.router_http import CONTROL_V2 as LOGS_V2
from kllm.modules.logs.router_http import CONTROL_V2_POLICY
from kllm.modules.models.router_http import CONTROL_V2 as MODELS_V2
from kllm.modules.runtimes.router_http import CONTROL_V2 as RUNTIMES_V2
from kllm.modules.settings.router_http import CONTROL_V2_CREDENTIALS, CONTROL_V2_NODE
from kllm.modules.updates.router_http import CONTROL_V2 as UPDATES_V2
from kllm.modules.usage.router_http import CONTROL_V2 as USAGE_V2

ROUTES = (
    *CONTROL_V2_NODE_STATUS,
    *CONTROL_V2_NODE,
    *RUNTIMES_V2,
    *CONTROL_V2_CLUSTER,
    *CONTROL_V2_POLICY,
    *CONTROL_V2_MEMBERSHIP,
    *UPDATES_V2,
    *CONTROL_V2_FAST,
    *MODELS_V2,
    *INFERENCE_V2,
    *USAGE_V2,
    *LOGS_V2,
    *LIVE_V2,
    *CONTROL_V2_CREDENTIALS,
)


def build_router(handlers: Mapping[str, Endpoint]) -> APIRouter:
    """Bind versioned resource paths to the agent's typed endpoint handlers."""
    router = APIRouter(prefix="/v2", tags=["control-v2"])
    bind_routes(router, ROUTES, handlers, version="v2")
    return router
