"""Thin authenticated v2 resources; host policy stays in the network service."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from kllm.shared.auth import verify_api_token
from kllm.modules.managed_network.interactors.allocation import AdapterSelection, OperationRequest, StartRequest
from kllm.modules.managed_network.interactors.identity import IdentityError, strict_json
from kllm.modules.managed_network.entities import PeerIdentity, Record
from kllm.shared.platform.base import PlatformRefusal, is_admin
from kllm.modules.managed_network.gateways.store import StoreConflict
from kllm.modules.managed_network.interactors.bootstrap import BootstrapRequest, BootstrapCommit
from kllm.modules.managed_network.interactors.guided_setup import ADMIN_NOTICE, EnableRequest, GuidedRequest, RecoveryRequest
from kllm.shared.origin import _is_loopback, _origin_allowed, _request_origin
from kllm.modules.managed_network.interactors.service import ManagedNetworkService
from kllm.shared.exceptions import PersistenceError

T = TypeVar("T", bound=Record)


class PairRequest(PeerIdentity):
    replace: bool = False


async def body(request: Request, schema: type[T]) -> T:
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 65536:
            raise HTTPException(
                422, detail={"code": "INVALID_INPUT", "message": "Request exceeds 64 KiB"}
            )
    try:
        return schema.model_validate(strict_json(content.decode("utf-8")))
    except (ValueError, UnicodeError):
        raise HTTPException(
            422, detail={"code": "INVALID_INPUT", "message": "Invalid bounded request schema"}
        ) from None


def error_response(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(
            404, detail={"code": "NOT_FOUND", "message": "Network resource does not exist"}
        )
    if isinstance(error, PlatformRefusal):
        status = (
            409
            if error.code
            in (
                "NOT_MASTER",
                "INFERENCE_ACTIVE",
                "OPERATION_IN_PROGRESS",
                "RPC_BUSY",
                "CLEANUP_PENDING",
                "STALE_REVISION",
                "OWNERSHIP_CONFLICT",
            )
            else 422
        )
        return HTTPException(status, detail={"code": error.code, "message": str(error)})
    if isinstance(error, StoreConflict):
        return HTTPException(409, detail={"code": "STALE_REVISION", "message": str(error)})
    if isinstance(error, IdentityError):
        return HTTPException(409, detail={"code": "UNPAIRED_IDENTITY", "message": str(error)})
    return HTTPException(
        503,
        detail={
            "code": "UNSUPPORTED_CAPABILITY",
            "message": "Managed network resources are unavailable",
        },
    )


def build_router(
    service: Callable[[], ManagedNetworkService],
    token_verifier: Callable[[str | None], bool] = verify_api_token,
) -> APIRouter:
    router = APIRouter(prefix="/v2", tags=["managed-network-v1"])

    async def store_call(selected: ManagedNetworkService, callback, *args, **kwargs):
        try:
            return await selected.store.arun(callback, *args, **kwargs)
        except (OSError, sqlite3.Error, PersistenceError, RuntimeError) as error:
            raise error_response(error) from None

    def authorized(
        request: Request, *, local: bool = False, master: bool = False
    ) -> ManagedNetworkService:
        if not token_verifier(
            request.headers.get("authorization") or request.headers.get("x-api-key")
        ):
            raise HTTPException(
                401, detail={"code": "UNAUTHORIZED", "message": "Local API token required"}
            )
        peer = request.client.host if request.client else None
        origin = request.headers.get("origin")
        if local and not _is_loopback(peer):
            raise HTTPException(
                403,
                detail={"code": "LOCAL_ONLY", "message": "This change requires the local node app"},
            )
        if origin and not _origin_allowed(origin, _request_origin(request), peer):
            raise HTTPException(
                403,
                detail={"code": "INVALID_ORIGIN", "message": "Same-origin app request required"},
            )
        try:
            selected = service()
            if master and selected.state().role != "master":
                raise PlatformRefusal("NOT_MASTER", "This operation requires the current master")
            return selected
        except (PlatformRefusal, StoreConflict, IdentityError, OSError, RuntimeError) as error:
            raise error_response(error) from None

    @router.post("/node/network-bootstrap/challenge")
    async def bootstrap_challenge(request: Request) -> dict[str, Any]:
        if request.headers.get("origin"):
            raise HTTPException(
                403, detail={"code": "INVALID_ORIGIN", "message": "Agent pairing only"}
            )
        value = await body(request, BootstrapRequest)
        try:
            selected = service()
            return await store_call(selected, selected.bootstrap.challenge, value)
        except (
            IdentityError,
            StoreConflict,
            PlatformRefusal,
            sqlite3.Error,
            PersistenceError,
            RuntimeError,
            OSError,
        ) as error:
            raise error_response(error) from None

    @router.post("/node/network-bootstrap/commit")
    async def bootstrap_commit(request: Request) -> dict[str, Any]:
        if request.headers.get("origin"):
            raise HTTPException(
                403, detail={"code": "INVALID_ORIGIN", "message": "Agent pairing only"}
            )
        value = await body(request, BootstrapCommit)
        try:
            selected = service()
            return await store_call(selected, selected.bootstrap.commit, value)
        except (
            IdentityError,
            StoreConflict,
            PlatformRefusal,
            sqlite3.Error,
            PersistenceError,
            RuntimeError,
            OSError,
        ) as error:
            raise error_response(error) from None

    @router.get("/node/network-setup")
    async def local_setup(request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            status = await store_call(selected, selected.guided.local_status)
            return {**status, "administrator_notice": ADMIN_NOTICE}
        except (OSError, sqlite3.Error, PersistenceError, RuntimeError) as error:
            raise error_response(error) from None

    @router.get("/node/network-setup/recovery/{network_id}")
    async def helper_review(network_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        try:
            import asyncio

            return await asyncio.to_thread(
                selected.guided.helper.request, "review", network=str(network_id)
            )
        except PlatformRefusal as error:
            raise error_response(error) from None

    @router.post("/node/network-setup/recovery/{network_id}")
    async def helper_resolve(network_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        value = await body(request, RecoveryRequest)
        try:
            return await selected.guided.resolve(network_id, value)
        except (PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.get("/node/network-setup/recovery/{network_id}/prima/{run_id}")
    async def prima_listener_review(network_id: UUID, run_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        try:
            return await selected.guided.recover_prima_ports(network_id, run_id)
        except (PlatformRefusal, OSError, RuntimeError) as error:
            raise error_response(error) from None

    @router.post("/node/network-setup/recovery/{network_id}/prima/{run_id}")
    async def prima_listener_resolve(network_id: UUID, run_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        value = await body(request, RecoveryRequest)
        try:
            return await selected.guided.recover_prima_ports(network_id, run_id, value)
        except (PlatformRefusal, OSError, RuntimeError) as error:
            raise error_response(error) from None

    @router.post("/node/network-setup/administrator")
    async def enable_administrator(request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        value = await body(request, EnableRequest)
        try:
            return await selected.guided.enable(value.acknowledged)
        except (IdentityError, StoreConflict, PlatformRefusal, OSError, RuntimeError) as error:
            raise error_response(error) from None

    @router.get("/cluster/network-setup")
    async def assess_setup(request: Request) -> dict[str, Any]:
        selected = authorized(request, master=True)
        try:
            return await selected.guided.assess()
        except (
            IdentityError,
            StoreConflict,
            PlatformRefusal,
            OSError,
            RuntimeError,
            TimeoutError,
        ) as error:
            raise error_response(error) from None

    @router.delete("/node/network-setup/administrator")
    async def disable_administrator(request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        try:
            networks = await store_call(selected, selected.store.networks)
        except (OSError, sqlite3.Error, PersistenceError, RuntimeError) as error:
            raise error_response(error) from None
        if any(n.observed_state != "stopped" for n in networks):
            raise error_response(
                PlatformRefusal(
                    "OPERATION_IN_PROGRESS",
                    "Stop the private network before disabling administrator access",
                )
            )
        try:
            import asyncio

            return await asyncio.to_thread(selected.guided.helper.request, "revoke")
        except PlatformRefusal as error:
            raise error_response(error) from None

    @router.post("/cluster/network-setup", status_code=202)
    async def guided_start(request: Request) -> JSONResponse:
        selected = authorized(request, master=True)
        value = await body(request, GuidedRequest)
        try:
            return JSONResponse(await selected.guided.start(value), status_code=202)
        except (
            IdentityError,
            StoreConflict,
            PlatformRefusal,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            raise error_response(error) from None

    @router.get("/cluster/network-setup/operations/{operation_id}")
    async def guided_progress(operation_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request, master=True)
        try:
            from kllm.modules.managed_network.interactors.guided_setup import MESSAGES

            view = await store_call(selected, selected.operation_view, operation_id)
            terminal = view.get("terminal_result") or {}
            view["message"] = MESSAGES.get(
                terminal.get("error_code") or "", terminal.get("message", "")
            )
            latest = {r["node_id"]: r for r in view["results"]}
            view["participants"] = [
                {"node_id": r["node_id"], "status": r["status"], "stage": r["stage"]}
                for r in latest.values()
            ]
            return view
        except KeyError as error:
            raise error_response(error) from None

    @router.get("/node/network-identity")
    async def identity(request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            peers = await store_call(selected, selected.store.peers)
            state = selected.state()
            authority = (
                await store_call(selected, selected._authority) if state.role == "master" else None
            )
            binding = (
                authority.cluster_binding_id
                if authority is not None
                else next(
                    (p.cluster_binding_id for p in peers if p.expected_role == "master"), None
                )
            )
        except (OSError, sqlite3.Error, PersistenceError, RuntimeError) as error:
            raise error_response(error) from None
        return {
            **selected.identity.public(binding).model_dump(mode="json"),
            "paired_peers": [p.model_dump(mode="json") for p in peers],
            "capability": "managed-network-v1",
            "permission": is_admin(),
        }

    @router.put("/node/network-identity/peers/{node_id}")
    async def pair(node_id: str, request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        value = await body(request, PairRequest)
        if value.node_id != node_id:
            raise HTTPException(
                422, detail={"code": "INVALID_INPUT", "message": "Peer path and identity differ"}
            )
        state = selected.state()
        if state.role == "master":
            if (
                value.expected_role != "worker"
                or node_id not in state.members
                or value.cluster_binding_id
                != (await store_call(selected, selected._authority)).cluster_binding_id
            ):
                raise HTTPException(
                    409,
                    detail={
                        "code": "UNPAIRED_IDENTITY",
                        "message": "Pair a currently enrolled worker to the displayed master binding",
                    },
                )
        elif state.role != "worker" or value.expected_role != "master" or not state.master_host:
            raise HTTPException(
                409,
                detail={
                    "code": "UNPAIRED_IDENTITY",
                    "message": "Join the intended master before explicitly pairing it",
                },
            )
        try:
            peer = PeerIdentity.model_validate(value.model_dump(exclude={"replace"}))
            await store_call(selected, selected.store.pair, peer, replace=value.replace)
            return peer.model_dump(mode="json")
        except (
            ValueError,
            StoreConflict,
            OSError,
            sqlite3.Error,
            PersistenceError,
            RuntimeError,
        ) as error:
            raise error_response(error) from None

    @router.get("/node/network-adapters")
    async def adapters(request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            return await store_call(selected, selected.local_inventory)
        except (
            OSError,
            ValueError,
            PlatformRefusal,
            StoreConflict,
            sqlite3.Error,
            PersistenceError,
            RuntimeError,
        ) as error:
            raise error_response(error) from None

    @router.put("/node/network-selection")
    async def selection(request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        value = await body(request, AdapterSelection)
        try:
            return await store_call(selected, selected.select_adapter, value)
        except (OSError, ValueError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.get("/cluster/network-adapters")
    async def cluster_adapters(request: Request) -> dict[str, Any]:
        selected = authorized(request, master=True)
        return {"nodes": await selected.inventories()}

    @router.get("/cluster/networks")
    async def networks(request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            summaries = await store_call(selected, selected.summaries)
        except (OSError, sqlite3.Error, PersistenceError, RuntimeError) as error:
            raise error_response(error) from None
        return {
            "networks": summaries,
            "subnet_proposals": [
                "10.10.10.0/24",
                "192.168.234.0/24",
                "172.29.234.0/24",
                "10.234.234.0/24",
            ],
        }

    @router.post("/cluster/networks", status_code=202)
    async def start(request: Request) -> JSONResponse:
        selected = authorized(request, master=True)
        value = await body(request, StartRequest)
        try:
            return JSONResponse(await selected.begin_start(value), status_code=202)
        except (ValueError, PlatformRefusal, StoreConflict, IdentityError) as error:
            raise error_response(error) from None

    @router.get("/cluster/networks/{network_id}")
    async def network(network_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            return await store_call(selected, selected.network_view, network_id)
        except KeyError as error:
            raise error_response(error) from None

    @router.post("/cluster/networks/{network_id}/operations", status_code=202)
    async def operation(network_id: UUID, request: Request) -> JSONResponse:
        selected = authorized(request, master=True)
        value = await body(request, OperationRequest)
        try:
            return JSONResponse(await selected.begin_operation(network_id, value), status_code=202)
        except (KeyError, PlatformRefusal, StoreConflict, ValueError) as error:
            raise error_response(error) from None

    @router.get("/cluster/network-operations/{operation_id}")
    async def operation_status(operation_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            return await store_call(selected, selected.operation_view, operation_id)
        except KeyError as error:
            raise error_response(error) from None

    @router.get("/node/network-cleanup")
    async def cleanup(request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        try:
            journals = await store_call(selected, selected.store.journals)
        except (OSError, sqlite3.Error, PersistenceError, RuntimeError) as error:
            raise error_response(error) from None
        return {
            "journals": [
                j.model_dump(
                    mode="json", exclude={"pre_snapshot", "intended_delta", "command_result"}
                )
                for j in journals
                if j.phase != "removed_observed"
            ]
        }

    @router.get("/node/network-cleanup/{journal_id}")
    async def cleanup_review(journal_id: UUID, request: Request) -> dict[str, Any]:
        from kllm.modules.managed_network.interactors.recovery import review

        selected = authorized(request, local=True)
        try:
            return await review(selected, journal_id)
        except (KeyError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.post("/node/network-cleanup/{journal_id}/resolve")
    async def cleanup_resolve(journal_id: UUID, request: Request) -> dict[str, Any]:
        from kllm.modules.managed_network.interactors.recovery import CleanupDecision, resolve

        selected = authorized(request, local=True)
        value = await body(request, CleanupDecision)
        try:
            return await resolve(selected, journal_id, value)
        except (KeyError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.post("/cluster/network-operations/{operation_id}/cancel", status_code=202)
    async def cancel_operation(operation_id: UUID, request: Request) -> dict[str, Any]:
        from kllm.modules.managed_network.interactors.allocation import CancelRequest

        selected = authorized(request, master=True)
        value = await body(request, CancelRequest)
        try:
            return await selected.cancel(operation_id, value)
        except (KeyError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.websocket("/cluster/network-control")
    async def network_control(socket: WebSocket) -> None:
        try:
            selected = service()
            await selected.channels.server(socket)
        except Exception:
            await socket.close(code=1011)

    return router
