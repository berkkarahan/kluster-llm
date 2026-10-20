"""Cluster HTTP endpoints for the agent composition root."""

from __future__ import annotations

from collections.abc import Callable
from kllm.shared import state_ref
from typing import Any, cast
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from kllm.modules.cluster.interactors import discovery
from kllm.shared.schemas import (
    JoinBody,
    RegisterBody,
    StartBody,
    UnregisterBody,
)
from kllm.shared.lifecycle_bus import (
    LifecycleEvent,
    publish_lifecycle,
)
from kllm.modules.cluster.interactors import membership as membership_mod

from kllm.modules.http_deps import require_key

_sync_master_liveness_provider: Callable[..., Any] | None = None
_dashboard_status_service_provider: Callable[[], Any] | None = None
_master_start_handler_provider: Callable[[], Any] | None = None
_cluster_lifecycle_handlers_provider: Callable[[], Any] | None = None
_join_cluster_provider: Callable[[Any], Any] | None = None
_membership_handlers_provider: Callable[[], Any] | None = None


def set_cluster_providers(
    *,
    sync_master_liveness: Callable[..., Any] | None = None,
    dashboard_status_service: Callable[[], Any] | None = None,
    master_start_handler: Callable[[], Any] | None = None,
    cluster_lifecycle_handlers: Callable[[], Any] | None = None,
    join_cluster: Callable[[Any], Any] | None = None,
    membership_handlers: Callable[[], Any] | None = None,
) -> None:
    global _sync_master_liveness_provider, _dashboard_status_service_provider
    global _master_start_handler_provider, _cluster_lifecycle_handlers_provider
    global _join_cluster_provider, _membership_handlers_provider
    if sync_master_liveness is not None:
        _sync_master_liveness_provider = sync_master_liveness
    if dashboard_status_service is not None:
        _dashboard_status_service_provider = dashboard_status_service
    if master_start_handler is not None:
        _master_start_handler_provider = master_start_handler
    if cluster_lifecycle_handlers is not None:
        _cluster_lifecycle_handlers_provider = cluster_lifecycle_handlers
    if join_cluster is not None:
        _join_cluster_provider = join_cluster
    if membership_handlers is not None:
        _membership_handlers_provider = membership_handlers


def _get_status_service() -> Any:
    if _dashboard_status_service_provider is not None:
        return _dashboard_status_service_provider()
    raise RuntimeError("dashboard status service provider is not configured")


def _get_start_handler() -> Any:
    if _master_start_handler_provider is not None:
        return _master_start_handler_provider()
    raise RuntimeError("master start handler provider is not configured")


def _get_lifecycle_handlers() -> Any:
    if _cluster_lifecycle_handlers_provider is not None:
        return _cluster_lifecycle_handlers_provider()
    raise RuntimeError("cluster lifecycle handlers provider is not configured")


def _get_join_fn() -> Any:
    if _join_cluster_provider is not None:
        return _join_cluster_provider
    raise RuntimeError("join cluster provider is not configured")


def _get_membership_handlers() -> Any:
    if _membership_handlers_provider is not None:
        return _membership_handlers_provider()
    raise RuntimeError("membership handlers provider is not configured")


def discover(timeout: float = 2.0, key: str | None = None) -> dict[str, Any]:
    found = discovery.browse_masters(timeout=timeout, key=key)
    return {
        "masters": [
            {"name": m.name, "host": m.host, "port": m.port, "key_id": m.key_id} for m in found
        ],
    }


def status() -> dict[str, Any]:
    if _sync_master_liveness_provider is not None:
        _sync_master_liveness_provider(shrink=True)
    membership_mod.reconcile_last_plan(state_ref.current())
    return cast(dict[str, Any], _get_status_service().status())


def start_master(body: StartBody) -> dict[str, Any]:
    result = _get_start_handler().start(body)
    publish_lifecycle(LifecycleEvent.CLUSTER_STARTED, state_ref.current().node_id or None)
    return cast(dict[str, Any], result)


def stop() -> dict[str, str]:
    was_master = state_ref.current().role == "master"
    result = _get_lifecycle_handlers().stop()
    if was_master:
        publish_lifecycle(LifecycleEvent.CLUSTER_STOPPED, state_ref.current().node_id or None)
    return cast(dict[str, str], result)


def join_cluster(body: JoinBody) -> dict[str, Any]:
    try:
        result = _get_join_fn()(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"join failed: {exc}") from exc
    publish_lifecycle(LifecycleEvent.WORKER_JOINED, state_ref.current().node_id or None)
    return cast(dict[str, Any], result)


def leave() -> dict[str, str]:
    was_worker = state_ref.current().role == "worker"
    result = _get_lifecycle_handlers().leave()
    if was_worker:
        publish_lifecycle(LifecycleEvent.WORKER_LEFT, state_ref.current().node_id or None)
    return cast(dict[str, str], result)


def register(
    body: RegisterBody,
    request: Request,
    _: None = Depends(require_key),
) -> dict[str, Any]:
    return cast(dict[str, Any], _get_membership_handlers().register(body, request))


def unregister(body: UnregisterBody, _: None = Depends(require_key)) -> dict[str, Any]:
    return cast(dict[str, Any], _get_membership_handlers().unregister(body))


def heartbeat(
    body: RegisterBody,
    request: Request,
    _: None = Depends(require_key),
) -> dict[str, Any]:
    return cast(dict[str, Any], _get_membership_handlers().heartbeat(body, request))


def create_cluster_router(
    status_service: Any = None,
    start_handler: Any = None,
    lifecycle_handlers: Any = None,
    join_fn: Any = None,
    membership_handlers: Any = None,
    sync_liveness: Any = None,
) -> APIRouter:
    router = APIRouter()

    def get_status() -> Any:
        return status_service() if callable(status_service) else (status_service if status_service is not None else _get_status_service())

    def get_start() -> Any:
        return start_handler() if callable(start_handler) else (start_handler if start_handler is not None else _get_start_handler())

    def get_lifecycle() -> Any:
        return lifecycle_handlers() if callable(lifecycle_handlers) else (lifecycle_handlers if lifecycle_handlers is not None else _get_lifecycle_handlers())

    def get_join() -> Any:
        return join_fn if join_fn is not None else _get_join_fn()

    def get_membership() -> Any:
        return membership_handlers() if callable(membership_handlers) else (membership_handlers if membership_handlers is not None else _get_membership_handlers())

    @router.get("/v1/discover")
    @router.get("/cluster/discover")
    def _discover(timeout: float = 2.0, key: str | None = None) -> dict[str, Any]:
        return discover(timeout=timeout, key=key)

    @router.get("/v1/status")
    @router.get("/cluster/status")
    def _status() -> dict[str, Any]:
        if sync_liveness is not None:
            sync_liveness(shrink=True)
        elif _sync_master_liveness_provider is not None:
            _sync_master_liveness_provider(shrink=True)
        membership_mod.reconcile_last_plan(state_ref.current())
        return cast(dict[str, Any], get_status().status())

    @router.post("/v1/start")
    @router.post("/cluster/start")
    def _start(body: StartBody) -> dict[str, Any]:
        result = get_start().start(body)
        publish_lifecycle(LifecycleEvent.CLUSTER_STARTED, state_ref.current().node_id or None)
        return cast(dict[str, Any], result)

    @router.post("/v1/stop")
    @router.post("/cluster/stop")
    def _stop() -> dict[str, str]:
        was_master = state_ref.current().role == "master"
        result = get_lifecycle().stop()
        if was_master:
            publish_lifecycle(LifecycleEvent.CLUSTER_STOPPED, state_ref.current().node_id or None)
        return cast(dict[str, str], result)

    @router.post("/v1/join")
    @router.post("/cluster/join")
    def _join(body: JoinBody) -> dict[str, Any]:
        try:
            result = get_join()(body)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"join failed: {exc}") from exc
        publish_lifecycle(LifecycleEvent.WORKER_JOINED, state_ref.current().node_id or None)
        return cast(dict[str, Any], result)

    @router.post("/v1/leave")
    @router.post("/cluster/leave")
    def _leave() -> dict[str, str]:
        was_worker = state_ref.current().role == "worker"
        result = get_lifecycle().leave()
        if was_worker:
            publish_lifecycle(LifecycleEvent.WORKER_LEFT, state_ref.current().node_id or None)
        return cast(dict[str, str], result)

    @router.post("/v1/register")
    @router.post("/cluster/register")
    def _register(
        body: RegisterBody,
        request: Request,
        _: None = Depends(require_key),
    ) -> dict[str, Any]:
        return cast(dict[str, Any], get_membership().register(body, request))

    @router.post("/v1/unregister")
    @router.post("/cluster/unregister")
    def _unregister(body: UnregisterBody, _: None = Depends(require_key)) -> dict[str, Any]:
        return cast(dict[str, Any], get_membership().unregister(body))

    @router.post("/v1/heartbeat")
    @router.post("/cluster/heartbeat")
    def _heartbeat(
        body: RegisterBody,
        request: Request,
        _: None = Depends(require_key),
    ) -> dict[str, Any]:
        return cast(dict[str, Any], get_membership().heartbeat(body, request))

    return router


create_router = create_cluster_router