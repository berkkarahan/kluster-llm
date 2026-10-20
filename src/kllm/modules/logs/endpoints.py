"""Logs HTTP endpoints for the agent composition root."""

from __future__ import annotations

from collections.abc import Callable
from kllm.shared import state_ref
import asyncio
import httpx
from typing import Any, cast
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
)
from kllm.modules.logs.interactors import logging_control
from kllm.shared.schemas import (
    LoggingBody,
    LoggingPolicyBody,
)
from kllm.shared.schemas import LogsIngestBody
from kllm.shared.state import DEFAULT_CONTROL_PORT

from kllm.modules.http_deps import require_key, require_api_token

_log_handlers_provider: Callable[[], Any] | None = None
_proxy_master_logs_provider: Callable[..., Any] | None = None


def set_logs_providers(
    *,
    handlers: Callable[[], Any] | None = None,
    proxy: Callable[..., Any] | None = None,
) -> None:
    global _log_handlers_provider, _proxy_master_logs_provider
    if handlers is not None:
        _log_handlers_provider = handlers
    if proxy is not None:
        _proxy_master_logs_provider = proxy


def _get_log_handlers() -> Any:
    if _log_handlers_provider is not None:
        return _log_handlers_provider()
    raise RuntimeError("log handlers provider is not configured")


def _get_proxy_master_logs() -> Any:
    if _proxy_master_logs_provider is not None:
        return _proxy_master_logs_provider
    return None


def get_logging() -> dict[str, Any]:
    return logging_control.overview(state_ref.current())


def apply_logging(
    body: LoggingPolicyBody, x_kllm_key: str | None = Header(default=None)
) -> dict[str, Any]:
    if state_ref.current().role != "worker" or not state_ref.current().join_key or x_kllm_key != state_ref.current().join_key:
        raise HTTPException(401, "invalid worker control key")
    logging_control.apply(state_ref.current(), body.model_dump())
    return logging_control.local_status(state_ref.current())


async def put_logging(
    body: LoggingBody, request: Request, _: None = Depends(require_api_token)
) -> dict[str, Any]:
    if state_ref.current().role != "master":
        raise HTTPException(409, "change logging on the master dashboard")
    policy = logging_control.change(state_ref.current(), body.enabled)

    async def update(node_id: str, member: dict[str, Any]) -> None:
        host = member.get("lan_host") or member.get("host")
        if not host:
            return
        try:
            response = await request.app.state.inference_client.put(
                f"http://{host}:{member.get('control_port') or DEFAULT_CONTROL_PORT}/v2/node/logging",
                json=policy,
                headers={"X-Kllm-Key": state_ref.current().join_key or ""},
                timeout=2,
            )
            if (
                response.status_code == 200
                and node_id in state_ref.current().members
                and state_ref.current().logging_generation == policy["generation"]
            ):
                state_ref.current().members[node_id]["logging_ack"] = response.json()
        except (httpx.HTTPError, ValueError):
            pass  # Heartbeats retry the persisted desired policy.

    # Existing membership traffic is the fallback if a node cannot be reached now.
    await asyncio.gather(
        *(
            update(nid, dict(member))
            for nid, member in list(state_ref.current().members.items())
            if nid != state_ref.current().node_id
        )
    )
    state_ref.current().save()
    return get_logging()


def ingest_logs(body: LogsIngestBody, _: None = Depends(require_key)) -> dict[str, Any]:
    return cast(dict[str, Any], _get_log_handlers().ingest(body))


def get_logs(
    n: int = 200,
    follow: bool = False,
    node: str | None = None,
    source: str | None = None,
    after: str | None = None,
) -> Any:
    return _get_log_handlers().read(n, follow, node, source, after, _get_proxy_master_logs())


def create_logs_router(
    handlers: Any = None,
    proxy: Any = None,
) -> APIRouter:
    router = APIRouter()

    def get_h() -> Any:
        if handlers is not None:
            return handlers() if callable(handlers) else handlers
        return _get_log_handlers()

    def get_prx() -> Any:
        if proxy is not None:
            return proxy
        return _get_proxy_master_logs()

    @router.get("/v1/logs/policy")
    @router.get("/node/logging")
    def _get_policy() -> dict[str, Any]:
        return get_logging()

    @router.post("/v1/logs/policy")
    @router.post("/node/logging")
    def _apply_policy(
        body: LoggingPolicyBody, x_kllm_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return apply_logging(body, x_kllm_key)

    @router.put("/v1/logs/policy")
    @router.put("/node/logging")
    async def _put_policy(
        body: LoggingBody, request: Request, _: None = Depends(require_api_token)
    ) -> dict[str, Any]:
        return await put_logging(body, request, _)

    @router.post("/v1/logs/ingest")
    @router.post("/logs/ingest")
    def _ingest(body: LogsIngestBody, _: None = Depends(require_key)) -> dict[str, Any]:
        return cast(dict[str, Any], get_h().ingest(body))

    @router.get("/v1/logs")
    @router.get("/logs")
    def _read(
        n: int = 200,
        follow: bool = False,
        node: str | None = None,
        source: str | None = None,
        after: str | None = None,
    ) -> Any:
        return get_h().read(n, follow, node, source, after, get_prx())

    return router


create_router = create_logs_router
