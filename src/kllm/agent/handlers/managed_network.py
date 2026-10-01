"""Thin authenticated v2 resources; host policy stays in the network service."""
from __future__ import annotations

from typing import Any, Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from kllm.auth import verify_api_token
from kllm.managed_network.allocation import AdapterSelection, OperationRequest, StartRequest
from kllm.managed_network.identity import IdentityError, strict_json
from kllm.managed_network.models import PeerIdentity, Record
from kllm.managed_network.platform.base import PlatformRefusal, is_admin
from kllm.managed_network.store import StoreConflict
from kllm.agent.origin import _is_loopback, _origin_allowed, _request_origin
from kllm.agent.services.managed_network import ManagedNetworkService

T = TypeVar('T', bound=Record)


class PairRequest(PeerIdentity):
    replace: bool = False


async def body(request: Request, schema: type[T]) -> T:
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 65536:
            raise HTTPException(422, detail={'code': 'INVALID_INPUT', 'message': 'Request exceeds 64 KiB'})
    try:
        return schema.model_validate(strict_json(content.decode('utf-8')))
    except (ValueError, UnicodeError):
        raise HTTPException(422, detail={'code': 'INVALID_INPUT', 'message': 'Invalid bounded request schema'}) from None


def error_response(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(404, detail={'code': 'NOT_FOUND', 'message': 'Network resource does not exist'})
    if isinstance(error, PlatformRefusal):
        status = 409 if error.code in ('NOT_MASTER', 'INFERENCE_ACTIVE', 'OPERATION_IN_PROGRESS', 'RPC_BUSY', 'CLEANUP_PENDING', 'STALE_REVISION', 'OWNERSHIP_CONFLICT') else 422
        return HTTPException(status, detail={'code': error.code, 'message': str(error)})
    if isinstance(error, StoreConflict):
        return HTTPException(409, detail={'code': 'STALE_REVISION', 'message': str(error)})
    if isinstance(error, IdentityError):
        return HTTPException(409, detail={'code': 'UNPAIRED_IDENTITY', 'message': str(error)})
    return HTTPException(503, detail={'code': 'UNSUPPORTED_CAPABILITY', 'message': 'Managed network resources are unavailable'})


def build_router(service: Callable[[], ManagedNetworkService], token_verifier: Callable[[str | None], bool] = verify_api_token) -> APIRouter:
    router = APIRouter(prefix='/v2', tags=['managed-network-v1'])

    def authorized(request: Request, *, local: bool = False, master: bool = False) -> ManagedNetworkService:
        if not token_verifier(request.headers.get('authorization') or request.headers.get('x-api-key')):
            raise HTTPException(401, detail={'code': 'UNAUTHORIZED', 'message': 'Local API token required'})
        peer = request.client.host if request.client else None
        origin = request.headers.get('origin')
        if local and not _is_loopback(peer):
            raise HTTPException(403, detail={'code': 'LOCAL_ONLY', 'message': 'This change requires the local node app'})
        if origin and not _origin_allowed(origin, _request_origin(request), peer):
            raise HTTPException(403, detail={'code': 'INVALID_ORIGIN', 'message': 'Same-origin app request required'})
        try:
            selected = service()
            if master and selected.state().role != 'master':
                raise PlatformRefusal('NOT_MASTER', 'This operation requires the current master')
            return selected
        except (PlatformRefusal, StoreConflict, IdentityError, OSError, RuntimeError) as error:
            raise error_response(error) from None

    @router.get('/node/network-identity')
    async def identity(request: Request) -> dict[str, Any]:
        selected = authorized(request)
        peers = selected.store.peers()
        binding = selected._authority().cluster_binding_id if selected.state().role == 'master' else next((p.cluster_binding_id for p in peers if p.expected_role == 'master'), None)
        return {**selected.identity.public(binding).model_dump(mode='json'), 'paired_peers': [p.model_dump(mode='json') for p in peers], 'capability': 'managed-network-v1', 'permission': is_admin()}

    @router.put('/node/network-identity/peers/{node_id}')
    async def pair(node_id: str, request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        value = await body(request, PairRequest)
        if value.node_id != node_id:
            raise HTTPException(422, detail={'code': 'INVALID_INPUT', 'message': 'Peer path and identity differ'})
        state = selected.state()
        if state.role == 'master':
            if value.expected_role != 'worker' or node_id not in state.members or value.cluster_binding_id != selected._authority().cluster_binding_id:
                raise HTTPException(409, detail={'code': 'UNPAIRED_IDENTITY', 'message': 'Pair a currently enrolled worker to the displayed master binding'})
        elif state.role != 'worker' or value.expected_role != 'master' or not state.master_host:
            raise HTTPException(409, detail={'code': 'UNPAIRED_IDENTITY', 'message': 'Join the intended master before explicitly pairing it'})
        try:
            peer = PeerIdentity.model_validate(value.model_dump(exclude={'replace'}))
            selected.store.pair(peer, replace=value.replace)
            return peer.model_dump(mode='json')
        except (ValueError, StoreConflict) as error:
            raise error_response(error) from None

    @router.get('/node/network-adapters')
    async def adapters(request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            return selected.local_inventory()
        except (OSError, ValueError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.put('/node/network-selection')
    async def selection(request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        value = await body(request, AdapterSelection)
        try:
            return selected.select_adapter(value)
        except (OSError, ValueError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.get('/cluster/network-adapters')
    async def cluster_adapters(request: Request) -> dict[str, Any]:
        selected = authorized(request, master=True)
        return {'nodes': await selected.inventories()}

    @router.get('/cluster/networks')
    async def networks(request: Request) -> dict[str, Any]:
        selected = authorized(request)
        return {'networks': selected.summaries(), 'subnet_proposals': ['192.168.234.0/24', '172.29.234.0/24', '10.234.234.0/24']}

    @router.post('/cluster/networks', status_code=202)
    async def start(request: Request) -> JSONResponse:
        selected = authorized(request, master=True)
        value = await body(request, StartRequest)
        try:
            return JSONResponse(await selected.begin_start(value), status_code=202)
        except (ValueError, PlatformRefusal, StoreConflict, IdentityError) as error:
            raise error_response(error) from None

    @router.get('/cluster/networks/{network_id}')
    async def network(network_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            return selected.network_view(network_id)
        except KeyError as error:
            raise error_response(error) from None

    @router.post('/cluster/networks/{network_id}/operations', status_code=202)
    async def operation(network_id: UUID, request: Request) -> JSONResponse:
        selected = authorized(request, master=True)
        value = await body(request, OperationRequest)
        try:
            return JSONResponse(await selected.begin_operation(network_id, value), status_code=202)
        except (KeyError, PlatformRefusal, StoreConflict, ValueError) as error:
            raise error_response(error) from None

    @router.get('/cluster/network-operations/{operation_id}')
    async def operation_status(operation_id: UUID, request: Request) -> dict[str, Any]:
        selected = authorized(request)
        try:
            return selected.operation_view(operation_id)
        except KeyError as error:
            raise error_response(error) from None

    @router.get('/node/network-cleanup')
    async def cleanup(request: Request) -> dict[str, Any]:
        selected = authorized(request, local=True)
        return {'journals': [j.model_dump(mode='json', exclude={'pre_snapshot', 'intended_delta', 'command_result'}) for j in selected.store.journals() if j.phase != 'removed_observed']}

    @router.get('/node/network-cleanup/{journal_id}')
    async def cleanup_review(journal_id: UUID, request: Request) -> dict[str, Any]:
        from kllm.managed_network.recovery import review
        selected = authorized(request, local=True)
        try:
            return await review(selected, journal_id)
        except (KeyError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.post('/node/network-cleanup/{journal_id}/resolve')
    async def cleanup_resolve(journal_id: UUID, request: Request) -> dict[str, Any]:
        from kllm.managed_network.recovery import CleanupDecision, resolve
        selected = authorized(request, local=True)
        value = await body(request, CleanupDecision)
        try:
            return await resolve(selected, journal_id, value)
        except (KeyError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.post('/cluster/network-operations/{operation_id}/cancel', status_code=202)
    async def cancel_operation(operation_id: UUID, request: Request) -> dict[str, Any]:
        from kllm.managed_network.allocation import CancelRequest
        selected = authorized(request, master=True)
        value = await body(request, CancelRequest)
        try:
            return await selected.cancel(operation_id, value)
        except (KeyError, PlatformRefusal, StoreConflict) as error:
            raise error_response(error) from None

    @router.websocket('/cluster/network-control')
    async def network_control(socket: WebSocket) -> None:
        try:
            selected = service()
            await selected.channels.server(socket)
        except Exception:
            await socket.close(code=1011)

    return router
