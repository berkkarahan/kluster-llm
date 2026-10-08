"""Resource-oriented control API routes for protocol version 2."""

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter

Endpoint = Callable[..., Any]


def build_router(handlers: Mapping[str, Endpoint]) -> APIRouter:
    """Bind versioned resource paths to the agent's typed endpoint handlers."""
    router = APIRouter(prefix="/v2", tags=["control-v2"])
    routes = (
        ("/node", "GET", "status"),
        ("/node/devices", "GET", "get_gpus"),
        ("/node/config", "GET", "get_config"),
        ("/node/config", "PUT", "put_config"),
        ("/node/home", "GET", "get_home"),
        ("/node/runtime", "POST", "llama_setup"),
        ("/cluster", "GET", "status"),
        ("/cluster", "POST", "start_master"),
        ("/cluster", "DELETE", "stop"),
        ("/cluster/logging", "GET", "get_logging"),
        ("/cluster/logging", "PUT", "put_logging"),
        ("/node/logging", "PUT", "apply_logging"),
        ("/cluster/membership", "POST", "join_cluster"),
        ("/cluster/membership", "DELETE", "leave"),
        ("/cluster/nodes", "GET", "status"),
        ("/cluster/nodes/register", "POST", "register"),
        ("/cluster/nodes/unregister", "POST", "unregister"),
        ("/cluster/nodes/heartbeat", "POST", "heartbeat"),
        ("/cluster/discovery", "GET", "discover"),
        ("/cluster/updates", "POST", "cluster_update_endpoint"),
        ("/cluster/updates/progress", "POST", "cluster_update_progress"),
        ("/cluster/fast-subnet/reachability", "GET", "fast_subnet_reachability"),
        ("/cluster/fast-subnet/reconnect", "POST", "fast_subnet_reconnect"),
        ("/models", "GET", "list_models"),
        ("/models/search", "POST", "search_models"),
        ("/models/downloads", "POST", "download_model"),
        ("/models/downloads/current", "GET", "download_progress"),
        ("/models/downloads/cancel", "POST", "cancel_download"),
        ("/models/deletions", "POST", "delete_model"),
        ("/models/chat-template/validation", "POST", "validate_chat_template"),
        ("/inference/capabilities", "GET", "run_capabilities"),
        ("/inference/models", "GET", "inference_models"),
        ("/inference/plans", "POST", "plan"),
        ("/inference/runs", "POST", "run"),
        ("/inference/runs/stop", "POST", "stop_inference"),
        ("/inference/load", "GET", "load_progress_endpoint"),
        ("/inference/model-settings/{model}", "GET", "get_model_settings"),
        ("/inference/model-settings/{model}", "PUT", "put_model_settings"),
        ("/telemetry", "GET", "usage_live"),
        ("/telemetry/ingest", "POST", "usage_ingest"),
        ("/telemetry/lifetimes", "GET", "usage_lifetimes"),
        ("/telemetry/series", "GET", "usage_series"),
        ("/telemetry/readings", "GET", "usage_readings"),
        ("/logs", "GET", "get_logs"),
        ("/logs", "POST", "ingest_logs"),
        ("/events", "GET", "dashboard_live"),
        ("/events", "WEBSOCKET", "dashboard_live_websocket"),
        ("/credentials/api-token", "GET", "get_api_token"),
        ("/credentials/huggingface", "GET", "get_hf_token"),
        ("/credentials/huggingface", "PUT", "put_hf_token"),
    )

    for path, method, name in routes:
        endpoint = handlers.get(name)
        if endpoint is None:
            continue
        operation_id = f"v2_{name}_{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
        if method == "WEBSOCKET":
            router.add_api_websocket_route(path, endpoint, name=operation_id)
        else:
            router.add_api_route(path, endpoint, methods=[method], operation_id=operation_id)
    return router


def build_network_router(service: Callable[..., Any]) -> APIRouter:
    """Keep the paired node socket and operator resources on v2 only."""
    from kllm.agent.handlers.managed_network import build_router as build_network

    return build_network(service)
