"""Cluster HTTP endpoints for the agent composition root."""

from __future__ import annotations

from kllm.shared import state_ref
from typing import Any, cast
from fastapi import (
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

def _host():
    from kllm.agent import runtime as host

    return host

def discover(timeout: float = 2.0, key: str | None = None) -> dict[str, Any]:
    found = discovery.browse_masters(timeout=timeout, key=key)
    return {
        "masters": [
            {"name": m.name, "host": m.host, "port": m.port, "key_id": m.key_id} for m in found
        ],
    }

def status() -> dict[str, Any]:
    _host()._sync_master_liveness(shrink=True)
    membership_mod.reconcile_last_plan(state_ref.current())
    return cast(dict[str, Any], _host()._dashboard_status_service().status())

def start_master(body: StartBody) -> dict[str, Any]:
    result = _host()._master_start_handler().start(body)
    publish_lifecycle(LifecycleEvent.CLUSTER_STARTED, state_ref.current().node_id or None)
    return cast(dict[str, Any], result)

def stop() -> dict[str, str]:
    was_master = state_ref.current().role == "master"
    try:
        result = _host()._cluster_lifecycle_handlers().stop()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if was_master:
        publish_lifecycle(LifecycleEvent.CLUSTER_STOPPED, state_ref.current().node_id or None)
    return cast(dict[str, str], result)

def join_cluster(body: JoinBody) -> dict[str, Any]:
    try:
        result = _host()._join_cluster(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"join failed: {exc}") from exc
    publish_lifecycle(LifecycleEvent.WORKER_JOINED, state_ref.current().node_id or None)
    return cast(dict[str, Any], result)

def leave() -> dict[str, str]:
    was_worker = state_ref.current().role == "worker"
    try:
        result = _host()._cluster_lifecycle_handlers().leave()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if was_worker:
        publish_lifecycle(LifecycleEvent.WORKER_LEFT, state_ref.current().node_id or None)
    return cast(dict[str, str], result)

def register(
    body: RegisterBody,
    request: Request,
    _: None = Depends(require_key),
) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._membership_handlers().register(body, request))

def unregister(body: UnregisterBody, _: None = Depends(require_key)) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._membership_handlers().unregister(body))

def heartbeat(
    body: RegisterBody,
    request: Request,
    _: None = Depends(require_key),
) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._membership_handlers().heartbeat(body, request))
