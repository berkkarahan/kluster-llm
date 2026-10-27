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

    def profiler():
        # Optional service is owned and closed by the runtime registry.
        from kllm.modules.primacpp.interactors.preparation import get_profile_jobs
        from kllm.modules import models as model_ops

        return get_profile_jobs(registry(), state, model_ops.resolve_local_model)

    def prima():
        c = control()
        return getattr(c, "prima", c)

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

    @router.get("/node/prima-devices")
    async def prima_devices(request: Request):
        """The selected Prima build's own physical Vulkan device report.

        The generic node inventory serves planning; this probe reads the exact
        physical identity (UUID/driver) the pinned Prima runtime itself sees.
        """
        authorize(request)
        from kllm.modules.primacpp.interactors.devices import (
            discover_vulkan_devices,
            stable_device_inventory,
        )

        def probe():
            try:
                runtime = registry().capture(None)
            except ValueError as error:
                return {"available": False, "reason": str(error)}
            if runtime.get("engine") != "primacpp":
                return {
                    "available": False,
                    "reason": "The selected runtime is not Prima; prepare a Prima build to probe its devices",
                }
            try:
                devices = stable_device_inventory(discover_vulkan_devices(runtime))
            except (ValueError, OSError) as error:
                return {"available": False, "reason": str(error)[:500]}
            capabilities = runtime.get("capabilities") or {}
            return {
                "available": True,
                "devices": devices,
                "features": capabilities.get("features", []),
                "backends": capabilities.get("backends", []),
            }

        return await work(probe)


    @router.post("/node/runtimes/{identity}/prima-plan")
    async def prima_plan(identity: str, request: Request):
        """Read-only topology preview; never starts ranks or runs profiling."""
        from kllm.modules.inference.entities import PlanBody

        authorize(request, master=True)
        value = await body(request, PlanBody)
        def inspect():
            from kllm.modules.runtimes.interactors.selection import use_runtime
            from kllm.modules.inference.interactors.effective_config import effective_configuration
            from kllm.modules.inference.interactors.deployment_review import DEPLOYMENT_REVIEW_VERSION

            captured = registry().capture(identity)
            prima_impl = prima()
            # Do not resolve legacy saved settings here alone: preparation and
            # Run must inspect the same fully materialized request settings.
            with use_runtime(captured):
                result = prima_impl.plan(identity, value)
                return {**result, "deployment_review_version": DEPLOYMENT_REVIEW_VERSION,
                        "effective_config": effective_configuration(value, result, prima_impl.resolve_model(value.model))}
        return await work(inspect)


    @router.post("/cluster/prima-preparations", status_code=202)
    async def prepare_prima(request: Request):
        from kllm.modules.inference.entities import PlanBody
        from kllm.modules.primacpp.entities import PreparationRequest
        authorize(request, mutation=True, master=True)
        raw = await body(request, PreparationRequest)
        try:
            settings = PlanBody.model_validate(raw.settings)
            return await work(lambda: prima().start_preparation(
                raw.runtime_id, settings, raw.plan_digest, raw.request_id,
                include_profile=raw.include_profile))
        except (KeyError, ValueError) as error:
            raise HTTPException(422, str(error)) from None

    @router.get("/cluster/prima-preparations/{identity}")
    async def prima_preparation(identity: str, request: Request):
        authorize(request, master=True)
        result = await work(lambda: prima().job(identity))
        if result is None:
            raise HTTPException(404, f"Preparation {identity} not found")
        return result

    @router.get("/cluster/prima-preparations/{identity}/coverage")
    async def prima_preparation_coverage(identity: str, request: Request):
        authorize(request, master=True)
        return await work(lambda: prima().preparation_coverage(identity))

    @router.post("/cluster/prima-preparations/{identity}/retry", status_code=202)
    async def retry_prima_preparation(
        identity: str,
        request: Request,
        stage: str | None = None,
        stage_name: str | None = None,
        node_id: str | None = None,
    ):
        from kllm.modules.primacpp.entities import ProfilingRetryRequest
        authorize(request, mutation=True, master=True)
        raw_bytes = await request.body()
        req_data = ProfilingRetryRequest()
        if raw_bytes.strip():
            try:
                req_data = ProfilingRetryRequest.model_validate_json(raw_bytes)
            except Exception:
                raise HTTPException(422, "Invalid retry request payload")
        target_stage = stage or stage_name or req_data.stage_name
        target_node = node_id or req_data.node_id
        return await work(lambda: prima().retry_preparation(
            identity, stage_name=target_stage, node_id=target_node
        ))

    @router.get("/cluster/prima-preparations/{identity}/logs")
    async def prima_preparation_logs(
        identity: str,
        request: Request,
        stage: str | None = None,
        node: str | None = None,
        level: str | None = None,
        limit: int = 500,
    ):
        import json
        authorize(request, master=True)
        job_data = await work(lambda: prima().job(identity))
        if job_data is None:
            raise HTTPException(404, f"Preparation {identity} not found")
        logs_data = await work(
            lambda: prima().preparation_logs(
                identity, stage=stage, node=node, level=level, limit=limit
            )
        )
        compat_summary = json.dumps(
            {
                "phase": job_data.get("phase"),
                "error": job_data.get("error"),
                "nodes": job_data.get("nodes", {}),
            },
            indent=2,
        )
        compat_text = (logs_data.get("text") or compat_summary)[-65536:]
        return {
            "text": compat_text,
            "logs": logs_data.get("logs", []),
            "total": len(logs_data.get("logs", [])),
            "run_id": identity,
        }

    @router.post("/cluster/prima-preparations/{identity}/cancel")
    async def cancel_prima_preparation(identity: str, request: Request):
        authorize(request, mutation=True, master=True)
        return await work(lambda: prima().cancel(identity))

    @router.post("/node/prima-profiles")
    async def profile(request: Request):
        from kllm.modules.primacpp.entities import ProfileRequest

        authorize(request, mutation=True)
        value = await body(request, ProfileRequest)
        return await work(lambda: profiler().start(value))

    @router.get("/node/prima-profiles/{identity}")
    async def profile_status(identity: str, request: Request):
        authorize(request)
        return await work(lambda: profiler().job(identity))

    @router.get("/node/prima-profiles/{identity}/logs")
    async def profile_logs(identity: str, request: Request):
        authorize(request)
        return {"text": await work(lambda: profiler().logs(identity))}

    @router.post("/node/prima-profiles/{identity}/cancel")
    async def cancel_profile(identity: str, request: Request):
        authorize(request, mutation=True)
        return await work(lambda: profiler().cancel(identity))

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

# Runtime installation belongs to scripts/install.py; the agent exposes no install routes.
LEGACY_V1: tuple[tuple[str, str, str], ...] = ()

CONTROL_V2: tuple[tuple[str, str, str], ...] = ()
