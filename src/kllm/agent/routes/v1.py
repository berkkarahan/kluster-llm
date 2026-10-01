"""Legacy v1 HTTP routes kept during the control API migration."""

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter

Endpoint = Callable[..., Any]

ROUTES: tuple[tuple[str, str, str], ...] = (
    ("/v1/llama/setup", "POST", "llama_setup"),
    ("/v1/discover", "GET", "discover"),
    ("/v1/models", "GET", "list_models"),
    ("/v1/models/search", "POST", "search_models"),
    ("/v1/models/download", "POST", "download_model"),
    ("/v1/models/download-progress", "GET", "download_progress"),
    ("/v1/models/download/cancel", "POST", "cancel_download"),
    ("/v1/load-progress", "GET", "load_progress_endpoint"),
    ("/v1/cluster/update", "POST", "cluster_update_endpoint"),
    ("/v1/cluster/update-progress", "POST", "cluster_update_progress"),
    ("/v1/models/delete", "POST", "delete_model"),
    ("/v1/chat/completions", "POST", "chat_completions"),
    ("/v1/messages", "POST", "anthropic_messages"),
    ("/v1/api-token", "GET", "get_api_token"),
    ("/v1/hf-token", "GET", "get_hf_token"),
    ("/v1/hf-token", "PUT", "put_hf_token"),
    ("/v1/config", "GET", "get_config"),
    ("/v1/gpus", "GET", "get_gpus"),
    ("/v1/config", "PUT", "put_config"),
    ("/v1/home", "GET", "get_home"),
    ("/v1/status", "GET", "status"),
    ("/v1/start", "POST", "start_master"),
    ("/v1/stop", "POST", "stop"),
    ("/v1/join", "POST", "join_cluster"),
    ("/v1/leave", "POST", "leave"),
    ("/v1/register", "POST", "register"),
    ("/v1/unregister", "POST", "unregister"),
    ("/v1/heartbeat", "POST", "heartbeat"),
    ("/v1/usage/ingest", "POST", "usage_ingest"),
    ("/v1/usage/live", "GET", "usage_live"),
    ("/v1/usage/lifetimes", "GET", "usage_lifetimes"),
    ("/v1/usage/series", "GET", "usage_series"),
    ("/v1/usage/readings", "GET", "usage_readings"),
    ("/v1/logs", "POST", "ingest_logs"),
    ("/v1/logs", "GET", "get_logs"),
    ("/v1/dashboard/live", "GET", "dashboard_live"),
    ("/v1/dashboard/live", "WEBSOCKET", "dashboard_live_websocket"),
    ("/v1/model-settings/{model}", "GET", "get_model_settings"),
    ("/v1/run-capabilities", "GET", "run_capabilities"),
    ("/v1/chat-template/validate", "POST", "validate_chat_template"),
    ("/v1/model-settings/{model}", "PUT", "put_model_settings"),
    ("/v1/plan", "POST", "plan"),
    ("/v1/run", "POST", "run"),
    ("/v1/inference/stop", "POST", "stop_inference"),
)


def build_router(handlers: Mapping[str, Endpoint]) -> APIRouter:
    """Bind the legacy v1 paths to agent endpoint handlers."""
    router = APIRouter(tags=["legacy-v1"])
    for path, method, name in ROUTES:
        endpoint = handlers.get(name)
        if endpoint is None:
            continue
        operation_id = f"v1_{name}_{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
        if method == "WEBSOCKET":
            router.add_api_websocket_route(path, endpoint, name=operation_id)
        else:
            router.add_api_route(path, endpoint, methods=[method], operation_id=operation_id)
    return router
