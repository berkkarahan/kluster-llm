"""Authenticated local library and master-owned target resources."""

from __future__ import annotations

import asyncio
from typing import Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from kllm.shared.auth import verify_api_token
from kllm.shared.origin import _is_loopback, _origin_allowed, _request_origin
from kllm.modules.managed_network.router_http import body
from kllm.modules.runtimes.entities import BuildRequest, SourceRequest, RuntimeVersion, TargetRequest
from kllm.modules.runtimes.interactors.source import discover, resolve


def build_router(
    registry: Callable, control: Callable, state: Callable, token_verifier=verify_api_token
):
    router = APIRouter(prefix="/v2", tags=["llama.cpp-runtimes"])

    def authorize(request: Request, *, mutation=False, master=False):
        if not token_verifier(
            request.headers.get("authorization") or request.headers.get("x-api-key")
        ):
            raise HTTPException(401, "Local API token required")
        peer = request.client.host if request.client else None
        if request.headers.get("origin") and not _origin_allowed(
            request.headers["origin"], _request_origin(request), peer
        ):
            raise HTTPException(403, "Same-origin app request required")
        if mutation and not _is_loopback(peer):
            raise HTTPException(403, "Runtime library changes require the local node app")
        if master and state().role != "master":
            raise HTTPException(409, "Change the cluster target on the master")

    async def work(callback):
        try:
            return await asyncio.to_thread(callback)
        except KeyError:
            raise HTTPException(404, "Runtime or operation does not exist") from None
        except (ValueError, OSError) as error:
            raise HTTPException(
                409, detail={"code": "RUNTIME_NOT_READY", "message": str(error)}
            ) from None

    def cluster_status():
        """Cluster-aware status when signed control is live; local library otherwise.

        Saving, building and selecting local versions must not depend on the
        signed node channel. Reads therefore degrade explicitly with
        ``control.available false`` instead of hiding a local library behind 503.
        """
        try:
            selected = control()
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            snapshot = registry().snapshot()
            snapshot["control"] = {
                "available": False,
                "message": detail.get("message") or str(error.detail),
            }
            return snapshot
        value = selected.status()
        value.setdefault("control", {"available": True})
        return value

    @router.get("/node/runtimes")
    async def library(request: Request):
        authorize(request)
        return await work(cluster_status)

    @router.get("/node/runtimes/sources")
    async def sources(request: Request, repository: str):
        authorize(request)
        return await work(lambda: discover(repository))

    @router.post("/node/runtimes/resolve")
    async def resolve_source(request: Request):
        authorize(request)
        value = await body(request, SourceRequest)
        return await work(lambda: resolve(value).model_dump())

    @router.post("/node/runtimes")
    async def save(request: Request):
        authorize(request, mutation=True)
        value = await body(request, RuntimeVersion)
        # Persist the reviewed snapshot; never re-resolve a moving branch on Save.
        return await work(lambda: registry().add(value))

    @router.delete("/node/runtimes/{identity}")
    async def remove(identity: str, request: Request):
        authorize(request, mutation=True)
        await work(lambda: registry().remove(identity))
        return {"ok": True}

    @router.post("/node/runtime-builds")
    async def build(request: Request):
        authorize(request, mutation=True)
        value = await body(request, BuildRequest)
        return await work(lambda: registry().build(value))

    @router.get("/node/runtime-builds/{identity}")
    async def job(identity: str, request: Request):
        authorize(request)
        return await work(lambda: registry().job(identity))

    @router.get("/node/runtime-builds/{identity}/logs")
    async def logs(identity: str, request: Request):
        authorize(request)
        return {"text": await work(lambda: registry().logs(identity))}

    @router.post("/node/runtime-builds/{identity}/cancel")
    async def cancel(identity: str, request: Request):
        authorize(request, mutation=True)
        return await work(lambda: registry().cancel(identity))

    @router.put("/node/runtime-selection")
    async def select(request: Request):
        authorize(request, mutation=True)
        value = await body(request, TargetRequest)
        if state().role != "idle":
            raise HTTPException(409, "Enrolled nodes use the master’s cluster target")
        await work(lambda: registry().select(value.runtime_id))
        return registry().snapshot()

    @router.get("/cluster/runtime-target")
    async def target(request: Request):
        authorize(request)
        return await work(cluster_status)

    @router.put("/cluster/runtime-target")
    async def set_target(request: Request):
        authorize(request, mutation=True, master=True)
        value = await body(request, TargetRequest)
        return await work(lambda: control().change_target(value))

    @router.post("/cluster/runtime-target/nodes/{node_id}/retry")
    async def retry(node_id: str, request: Request):
        authorize(request, mutation=True, master=True)
        selected = control()
        target = selected.target()
        if target is None:
            raise HTTPException(409, "Select a cluster target first")
        if node_id == state().node_id:
            return registry().build(
                BuildRequest(
                    runtime_id=target["version"]["runtime_id"],
                    backend=selected.backend(),
                    request_id=str(uuid4()),
                )
            )
        session = selected.service.channels.sessions.get(node_id)
        if session is None:
            raise HTTPException(
                409,
                detail={
                    "code": "CHANNEL_UNAVAILABLE",
                    "message": "This worker has no paired signed control channel; re-pair it before forcing a sync",
                },
            )
        return await session.request_runtime(
            uuid4(), "prepare_runtime", body={"target": target, "retry": True}
        )

    @router.get("/cluster/runtime-target/nodes/{node_id}/builds/{job_id}/logs")
    async def remote_logs(node_id: str, job_id: str, request: Request):
        authorize(request, master=True)
        session = control().service.channels.sessions.get(node_id)
        if session is None:
            raise HTTPException(
                409,
                detail={
                    "code": "CHANNEL_UNAVAILABLE",
                    "message": "This worker has no paired signed control channel; re-pair it to read remote build logs",
                },
            )
        return await session.request_runtime(uuid4(), "runtime_build_logs", body={"job_id": job_id})

    @router.post("/cluster/runtime-target/nodes/{node_id}/builds/{job_id}/cancel")
    async def remote_cancel(node_id: str, job_id: str, request: Request):
        authorize(request, mutation=True, master=True)
        session = control().service.channels.sessions.get(node_id)
        if session is None:
            raise HTTPException(
                409,
                detail={
                    "code": "CHANNEL_UNAVAILABLE",
                    "message": "This worker has no paired signed control channel; re-pair it to cancel remote builds",
                },
            )
        return await session.request_runtime(
            uuid4(), "cancel_runtime_build", body={"job_id": job_id}
        )

    return router

LEGACY_V1: tuple[tuple[str, str, str], ...] = (
    ('/v1/llama/setup', 'POST', 'llama_setup'),
)

CONTROL_V2: tuple[tuple[str, str, str], ...] = (
    ('/node/runtime', 'POST', 'llama_setup'),
)
