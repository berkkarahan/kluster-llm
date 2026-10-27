from __future__ import annotations

from kllm.shared import state_ref

from kllm.shared.exceptions import PersistenceError

from kllm.shared.persistence.operations import initialize

from collections.abc import AsyncIterator, Awaitable, Callable
import os
import asyncio
import sqlite3
from contextlib import asynccontextmanager
import httpx
from kllm.shared import http_clients, rpc_cache
from kllm.modules.runtimes.interactors.registry import RuntimeRegistry
from kllm.modules.runtimes.interactors.selection import snapshot as inference_runtime_snapshot
from kllm.modules.runtimes.interactors.runtime_control import RuntimeControlService
from kllm.modules.runtimes.router_http import build_router as build_runtime_router
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from kllm.shared import config as kllm_config
from kllm.modules.cluster.interactors import discovery
from kllm.modules.inference.interactors import gateway, inference_activity, load_progress  # noqa: F401
from kllm.modules.updates.interactors import cluster_update
from kllm.modules import models as model_ops
from kllm.modules.usage.interactors import usage
from kllm.shared import native, netsec, process_registry as runtime
from kllm.shared.auth import ensure_api_token, verify_api_token
from kllm.shared.hf_token import hf_token_status, save_hf_token
from kllm.modules.inference.interactors import model_settings as model_settings_store
from kllm.modules.inference.interactors.extra_args import sanitize_extra_args
from kllm.modules.logs.interactors import cluster_log
from kllm.modules.logs.interactors import logging_control
from kllm.modules.live.interactors.live import LiveFeed, SourceDenied, server_time as live_server_time
from kllm.modules.cluster.interactors.join_flow import build_register_payload, post_register
from kllm.agent.origin import (
    _is_loopback as _is_loopback,
    _live_request as _live_request,
    _origin as _origin,
    _origin_allowed as _origin_allowed,
    _request_origin as _request_origin,
    _websocket_origin as _websocket_origin,
)

from kllm.modules.live.interactors.live_ws import log_result_signature
from kllm.agent.dashboard import mount_dashboard as _mount_dashboard_routes
from kllm.modules.control_v2 import build_router as _build_v2_router
from kllm.modules.legacy_v1 import build_router as _build_v1_router
from kllm.modules.managed_network.router_http import build_router as build_network_router
from kllm.modules.inference.errors import (
    http_error_handler as _http_error_handler,
    plan_run_validation_handler as _plan_run_validation_handler,
)
from kllm.modules.models.router_http import ModelHandlers
from kllm.modules.updates.router_http import ClusterUpdateHandlers
from kllm.modules.cluster.interactors.fast_subnet_http import FastSubnetHandlers
from kllm.modules.usage.router_http import (
    UsageHistoryHandlers,
    UsageHistoryProxy,
    UsageIngestHandlers,
    UsageLiveHandlers,
)
from kllm.modules.settings.router_http import SettingsHandlers
from kllm.modules.inference.interactors.run_settings_http import RunSettingsHandlers
from kllm.modules.inference.router_http import InferenceDependencies, InferenceHandlers
from kllm.modules.logs.router_http import LogHandlers
from kllm.modules.logs.interactors.live_logs import LiveLogsHandler
from kllm.modules.cluster.router_http import MembershipHandlers
from kllm.modules.cluster.interactors.lifecycle import ClusterLifecycleHandlers, MasterStartDependencies, MasterStartHandler
from kllm.modules.live.router_http import (
    DashboardLiveWebSocket,
    LiveWebSocketDependencies,
    parse_subscription as _subscription,
    safe_control as _safe_control,
)
from kllm.agent.schemas import (
    JoinBody,
    StartBody,
    FastSubnetReconnectBody,
)
from kllm.modules.inference.interactors.run_route import finish_master_launch, spawn_master, stop_previous_master
from kllm.modules.models.interactors.model_service import ModelGateway, ModelService
from kllm.modules.updates.interactors.cluster_update_service import ClusterUpdateService
from kllm.modules.cluster.interactors.fast_subnet import FastSubnetService
from kllm.modules.settings.interactors.gpus import GpuSelectionService
from kllm.modules.settings.interactors.gpu_api import GpuApi
from kllm.modules.runtimes.interactors.runtime_api import build_runtime_setup_service
from kllm.modules.settings.interactors.gpus import ResolvedGpuSelection
from kllm.modules.cluster.interactors.membership_maintenance import MembershipMaintenance
from kllm.modules.cluster.interactors.membership_heartbeat import MembershipHeartbeatService
from kllm.modules.cluster.interactors.membership_loop import MembershipLoop
from kllm.modules.inference.interactors.capacity import CapacityService
from kllm.modules.usage.interactors.usage_runtime import UsageRuntime
from kllm.modules.cluster.interactors.cluster_join import ClusterJoinDependencies, ClusterJoinService
from kllm.modules.logs.interactors.log_relay import LogRelayService
from kllm.modules.cluster.presenters.dashboard_status import DashboardStatusService
from kllm.modules.live.interactors.dashboard_live import DashboardLiveService
from kllm.modules.cluster.interactors.background_tasks import CapacityRefreshScheduler
from kllm.modules.cluster.presenters.heartbeat_payload import HeartbeatPayloadBuilder
from kllm.modules.runtimes.interactors.runtime_setup import RuntimeSetupService
from kllm.modules.inference.interactors.template_validation import TemplateValidationService
from kllm.modules.managed_network.interactors.network_setup import NetworkStateService
from kllm.shared.legacy_tunnel import teardown_legacy_tunnel
from kllm.agent.application_lifecycle import AgentLifecycleDependencies, AgentLifespan
from kllm.agent.lifecycle_bus import LifecycleEvent, publish_lifecycle
from kllm.modules.inference.interactors.run_planning import (
    ensure_master_alive_after_spawn,
    ensure_rpc_peers_reachable,
    execute_plan,
    member_rpc_host,
    merge_and_sanitize_extra,
    validate_vision_for_run,
)
from kllm.modules.inference.interactors.run_launch import build_master_launch
from kllm.modules.inference.entities import PlanBody, RunBody
from kllm.modules.cluster.interactors import membership as membership_mod
from kllm.shared.state import (
    DEFAULT_RPC_PORT,
    ClusterState,
    generate_join_key,
    home_dir,
    logs_dir,
    models_dir,
    repo_root,
)

_background_stop = threading.Event()
_usage_runtime_lock = threading.Lock()
_usage_runtime_instance: UsageRuntime | None = None
_activity_lock = threading.Lock()
_activity_sampled_at = 0.0
_activity_cached: dict[str, Any] | None = None
# Compatibility alias retained for existing integrations and tests.
_resources = usage.ResourceState()
_resource_remote: dict[str, Any] = {}


def _usage_runtime() -> UsageRuntime:
    global _usage_runtime_instance
    if _usage_runtime_instance is None:
        with _usage_runtime_lock:
            if _usage_runtime_instance is None:
                _usage_runtime_instance = UsageRuntime(
                    state=lambda: state_ref.current(),
                    app_state=lambda: app.state,
                    home_dir=home_dir,
                    public_last_plan=membership_mod.public_last_plan,
                    master_control_base=lambda: _master_control_base(),
                    heartbeat_payload=lambda: _heartbeat_payload(),
                    tracked_process_token=lambda: _tracked_process_token(),
                    control_client=lambda: http_clients.control_client(),
                    stop=_background_stop,
                    sampler_factory=usage.Sampler,
                    resource_probe_available=lambda: native.resource_probe_available(),
                    resource_probe=lambda **kwargs: native.probe_resources(**kwargs),
                    close_resource_probes=lambda: native.close_resource_probes(),
                    runtime_load=runtime.load,
                    runtime_save=runtime.save,
                    resources=_resources,
                    remote_resources=_resource_remote,
                    platform=sys.platform,
                )
    return _usage_runtime_instance


def _usage_store() -> usage.UsageStore:
    return _usage_runtime().store()


def _usage_assignment(node_id: str) -> str | None:
    return _usage_runtime().assignment(node_id)


def _usage_node_context(member: dict[str, Any], model: str | None) -> dict[str, Any]:
    return _usage_runtime().node_context(member, model)


def _publish_resources(snapshot: dict[str, Any]) -> None:
    _usage_runtime().publish_resources(snapshot)


def _usage_loop() -> None:
    _usage_runtime().run_collector()


async def _sample_diagnostics() -> None:
    # Optional debug module; absent unless KLLM_DEBUG_HARDWARE is set. Kept lazy so a normal start does not import it.
    from kllm._dbg_a1ff77 import gpu_snapshot, llama_props, write

    while True:

        def sample() -> None:
            write(
                "hardware",
                "background",
                "device snapshot",
                {
                    "gpu": gpu_snapshot(state_ref.current().process_pid),
                    "llama_props": llama_props(state_ref.current().infer_port),
                },
            )

        await asyncio.to_thread(sample)
        await asyncio.sleep(10)


def _reset_usage_state() -> None:
    _usage_runtime().reset()


def _clear_resource_state() -> None:
    _usage_runtime().clear_resources()


async def _close_usage_store(app: FastAPI) -> None:
    await _usage_runtime().close_store(
        app, deadline=getattr(app.state, "sqlite_shutdown_deadline", None)
    )


async def _start_managed_network(app: FastAPI) -> None:
    from kllm.modules.managed_network.interactors.service import ManagedNetworkService
    from kllm.modules.inference.interactors.worker_rpc import WorkerRpcService
    from kllm.modules.inference.interactors.rpc_runs import RpcRunCoordinator

    service = None
    try:
        service = await initialize(
            lambda: ManagedNetworkService(home_dir(), lambda: state_ref.current(), _master_control_base),
            cleanup=lambda value: value.store.close(),
        )
        registry = _runtime_registry()
        service.runtimes = RuntimeControlService(
            service,
            registry,
            lambda: kllm_config.load_config().get("accel_backend") or "auto",
            native_supported=lambda: (
                native.native_available()
                and getattr(native.require_native(), "RUNTIME_SELECTION_VERSION", 0) == 1
            ),
            warm_capabilities=_schedule_local_capacity_refresh,
        )
        service.worker_rpc = WorkerRpcService(
            lambda: state_ref.current(),
            _runtime_setup_service,
            lock=_worker_launch_lock,
            before_stop=service.release_worker_bridges,
            registry=registry,
        )
        service.rpc_runs = RpcRunCoordinator(
            service,
            lambda body: _inference_handlers().run(body),
            lambda: membership_mod._stop_inference(state_ref.current()),
        )
        await service.start()
        if not state_ref.current().process_pid and registry.data.get("running"):
            registry.running(None)
        app.state.managed_network = service
    except asyncio.CancelledError:
        if service is not None:
            deadline = time.monotonic() + service.store._store.options.shutdown_timeout
            await asyncio.shield(service.aclose(deadline=deadline))
        raise
    except (OSError, ValueError, RuntimeError, sqlite3.Error, PersistenceError) as error:
        if service is not None:
            deadline = time.monotonic() + service.store._store.options.shutdown_timeout
            try:
                await service.aclose(deadline=deadline)
            except (OSError, RuntimeError, PersistenceError):
                pass  # The closing owner remains quarantined for safe recovery.
        app.state.managed_network_error = (
            f"Signed node runtime control is unavailable ({str(error)[:200]}); "
            "restart or recover the local agent"
        )


async def _stop_managed_network(app: FastAPI) -> None:
    service = getattr(app.state, "managed_network", None)
    if service is not None:
        try:
            if (
                service.rpc_runs is not None
                and service.rpc_runs.run_id is not None
                and state_ref.current().role == "master"
            ):
                await asyncio.to_thread(service.rpc_runs.stop, lambda: _inference_handlers().stop())
            if service.worker_rpc is not None:
                await asyncio.to_thread(service.worker_rpc.reconcile_startup)
        except (HTTPException, RuntimeError, ValueError):
            pass  # Ambiguous ownership stays durable for the next reconciliation.
        # A managed run is coupled to these bridge instances. Legacy runs keep
        # their existing lifecycle when no durable network reservation exists.
        for network in await service.store.arun(service.store.networks):
            runs = await service.store.arun(service.store.runs, network.network_id)
            for run in runs:
                if run.state == "active" and run.child_pid == state_ref.current().process_pid and run.child_pid:
                    registered = native.registered_process_identity(run.child_pid)
                    if registered and registered.get("start_token") == run.child_start_token:
                        await asyncio.to_thread(membership_mod._stop_inference, state_ref.current())
        try:
            await service.admission.release_stopped()
        except (RuntimeError, ValueError):
            # Abrupt/ambiguous process ownership remains durable for review.
            pass
        finally:
            await service.aclose(deadline=getattr(app.state, "sqlite_shutdown_deadline", None))
        del app.state.managed_network


def _managed_network_service():
    service = getattr(app.state, "managed_network", None)
    if service is None:
        raise HTTPException(
            503,
            detail={
                "code": "UNSUPPORTED_CAPABILITY",
                "message": "Managed network service has not started",
            },
        )
    return service


def _managed_network_summaries() -> list[dict[str, Any]]:
    service = getattr(app.state, "managed_network", None)
    return service.summaries() if service is not None else []


def _network_directory_sync() -> None:
    service = getattr(app.state, "managed_network", None)
    if service is not None:
        service.sync_directory()


def _network_directory() -> list[dict[str, Any]]:
    service = getattr(app.state, "managed_network", None)
    if service is None:
        return []
    # Refresh before serving so a fresh master without worker heartbeats still
    # lists its own identity; a worker sync is a no-op and reads its mirror.
    try:
        service.sync_directory()
    except (sqlite3.Error, PersistenceError):
        pass
    return service.directory_payload()


def _network_directory_remove(node_id: str) -> None:
    service = getattr(app.state, "managed_network", None)
    if service is not None:
        service.remove_directory(node_id)


def _rpc_lifecycle_status() -> dict[str, Any]:
    service = getattr(app.state, "managed_network", None)
    coordinator = service.rpc_runs if service is not None else None
    if state_ref.current().role == "master" and coordinator is not None:
        return {
            "status": coordinator.phase,
            "workers": list(coordinator.workers),
            "retries": coordinator.retries,
        }
    member = state_ref.current().members.get(state_ref.current().node_id) or {}
    return {"status": member.get("rpc_status", "idle"), "error": member.get("rpc_error")}


def _agent_lifespan() -> AgentLifespan:
    return AgentLifespan(
        AgentLifecycleDependencies(
            state=lambda: state_ref.current(),
            start_managed_network=_start_managed_network,
            stop_managed_network=_stop_managed_network,
            replace_state=state_ref.replace,
            upgrade_pending=ClusterState.upgrade_pending,
            stop_children=runtime.stop_children,
            load_state=lambda: ClusterState.load(migrate=True),
            reconcile_plan=membership_mod.reconcile_last_plan,
            reset_usage_state=_reset_usage_state,
            clear_resources=_clear_resource_state,
            clear_remote_resources=_usage_runtime().clear_remote_resources,
            control_client=http_clients.control_client,
            open_usage_store=_usage_store,
            background_stop=_background_stop,
            adopt_child_identity=runtime.adopt_child_identity,
            ensure_master_member=_ensure_master_member,
            create_live_feed=_new_live_feed,
            background_enabled=lambda: "pytest" not in sys.modules,
            start_membership=_start_membership_thread,
            start_log_relay=_start_logs_thread,
            usage_loop=_usage_loop,
            thread_factory=threading.Thread,
            close_resource_probes=native.close_resource_probes,
            sample_diagnostics=_sample_diagnostics,
            close_clients=http_clients.close,
            close_usage_store=_close_usage_store,
            stop_live_feed=lambda feed, *, deadline=None: feed.stop(deadline=deadline),
            platform=sys.platform,
            async_client_factory=httpx.AsyncClient,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with _agent_lifespan().run(app):
        yield


app = FastAPI(title="kllm-agent", version="0.1.0", lifespan=lifespan)


async def _request_validation_boundary(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    return await _plan_run_validation_handler(request, exc)


async def _http_error_boundary(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    return await _http_error_handler(request, exc)


app.add_exception_handler(RequestValidationError, _request_validation_boundary)
app.add_exception_handler(StarletteHTTPException, _http_error_boundary)


_advertiser: discovery.MasterAdvertiser | None = None
state_ref.replace(ClusterState.load(migrate=False))
_model_service = ModelService(
    cast(ModelGateway, model_ops),
    native,
    ThreadPoolExecutor(max_workers=1, thread_name_prefix="kllm-download"),
)
membership_mod.reconcile_last_plan(state_ref.current())
with state_ref.current().lock:
    if not state_ref.current().node_id:
        state_ref.current().node_id = secrets.token_hex(4)
        state_ref.current().save()


def _network_state_service() -> NetworkStateService:
    from kllm.shared import cp_proxy

    return NetworkStateService(
        state=lambda: state_ref.current(),
        reconcile_rpc_bind=netsec.reconcile_rpc_bind,
        refresh_firewall_warnings=netsec.refresh_rpc_firewall_warnings,
        wifi_path_warning=netsec.wifi_path_warning,
        stop_proxy=cp_proxy.stop_current,
        teardown_legacy_tunnel=teardown_legacy_tunnel,
        default_rpc_port=DEFAULT_RPC_PORT,
    )


def _refresh_persisted_netsec_warnings() -> None:
    """Drop stale firewall warnings after agent reload (e.g. code upgrade)."""
    _network_state_service().refresh_persisted_warnings()


_refresh_persisted_netsec_warnings()
# Ensure loopback inference token exists for harness connect
ensure_api_token()

_latency_probe_lock = threading.Lock()
_latency_probe_inflight = False


def _persist_last_settings(body: PlanBody | RunBody) -> None:
    data = body.model_dump()
    data.pop("tensor_split", None)
    state_ref.current().last_settings = data


def _relaunch_after_membership_change(state: ClusterState) -> None:
    membership_mod.on_membership_shrink(
        state,
        apply_serving=lambda _state, body, _plan: run(body),
        stop_serving=lambda _state: stop_inference(),
    )


_membership_maintenance = MembershipMaintenance(
    state=lambda: state_ref.current(),
    store=lambda: _usage_store(),
    shrink_cluster=_relaunch_after_membership_change,
)


def _sync_master_liveness(*, shrink: bool = True) -> list[str]:
    return _membership_maintenance.synchronize(shrink=shrink)


def _runtime_registry():
    registry = getattr(app.state, "runtime_registry", None)
    if registry is None or registry.closed or registry.root.parent != home_dir():
        if registry is not None:
            registry.close()
        registry = RuntimeRegistry(home_dir(), repo_root)
        app.state.runtime_registry = registry
    return registry


def _runtime_control():
    service = getattr(app.state, "managed_network", None)
    control = getattr(service, "runtimes", None)
    if control is None:
        message = getattr(app.state, "managed_network_error", None) or (
            "Signed node runtime control is unavailable; restart or recover the local agent"
        )
        raise HTTPException(503, detail={"code": "RUNTIME_CONTROL_UNAVAILABLE", "message": message})
    return control


def _runtime_status():
    service = getattr(app.state, "managed_network", None)
    control = getattr(service, "runtimes", None)
    if control is not None:
        value = control.status()
        value.setdefault("control", {"available": True})
        return value
    snapshot = _runtime_registry().snapshot()
    message = getattr(app.state, "managed_network_error", None)
    snapshot["control"] = {"available": False, **({"message": message} if message else {})}
    return snapshot


def _heartbeat_payload() -> dict[str, Any]:
    return _heartbeat_payload_builder().build()


def _heartbeat_payload_builder() -> HeartbeatPayloadBuilder:
    return HeartbeatPayloadBuilder(
        lambda: state_ref.current(),
        software_identity=cluster_update.software_identity,
        process_started_at=_tracked_process_start,
        process_start_token=_tracked_process_token,
        runtime_status=lambda: (
            _runtime_control().local_status()
            if getattr(getattr(app.state, "managed_network", None), "runtimes", None)
            else {}
        ),
    )


def _tracked_process_start() -> str | None:
    if not state_ref.current().process_pid:
        return None
    for child in runtime.load().children:
        if child.pid == state_ref.current().process_pid and child.start_token and child.started_at:
            return child.started_at
    import psutil

    try:
        return usage.utc(psutil.Process(state_ref.current().process_pid).create_time())
    except (OSError, ValueError, psutil.Error):
        return None


def _tracked_process_token() -> str | None:
    return next(
        (c.start_token for c in runtime.load().children if c.pid == state_ref.current().process_pid), None
    )


def _apply_directory_snapshot(value: Any) -> None:
    if not isinstance(value, list):
        return
    service = getattr(app.state, "managed_network", None)
    if service is None:
        return
    from kllm.modules.managed_network.entities import NodeNetworkIdentity

    items = []
    for entry in value[:256]:
        try:
            items.append(NodeNetworkIdentity.model_validate(entry))
        except ValueError:
            continue  # The master is trusted, but the boundary stays validated.
    try:
        service.store.put_directory_many(items, replace=True)
    except (sqlite3.Error, PersistenceError):
        pass  # The local identity mirror must not break membership heartbeats.


def _apply_cluster_snapshot(data: dict[str, Any]) -> None:
    if isinstance(data.get("logging"), dict):
        logging_control.apply(state_ref.current(), data["logging"])
    snapshot = data.get("cluster_members")
    if isinstance(snapshot, dict):
        with _worker_launch_lock:
            local = state_ref.current().members.get(state_ref.current().node_id) or {}
            state_ref.current().members = dict(snapshot)
            # Runtime ownership is local, never supplied by a cluster snapshot.
            if state_ref.current().node_id in state_ref.current().members:
                state_ref.current().members[state_ref.current().node_id] = dict(state_ref.current().members[state_ref.current().node_id])
                state_ref.current().members[state_ref.current().node_id].pop("rpc_owner", None)
                for key in (
                    "rpc_owner",
                    "rpc_status",
                    "rpc_error",
                    "rpc_devices",
                    "rpc_capabilities",
                    "rpc_control_version",
                ):
                    if key in local:
                        state_ref.current().members[state_ref.current().node_id][key] = local[key]
    _apply_directory_snapshot(data.get("node_directory"))
    if "last_plan" in data:
        state_ref.current().last_plan = data.get("last_plan")
    if "apply_error" in data:
        state_ref.current().apply_error = data.get("apply_error")
    if "apply_warning" in data:
        state_ref.current().apply_warning = data.get("apply_warning")
    if "inference_activity" in data:
        activity = data.get("inference_activity")
        state_ref.current().inference_activity = dict(activity) if isinstance(activity, dict) else None
    if "model_name" in data:
        model_name = data.get("model_name")
        state_ref.current().model_name = model_name if isinstance(model_name, str) and model_name else None


def _inference_activity() -> dict[str, Any]:
    """Read local master activity or return the master's latest worker mirror."""
    global _activity_sampled_at, _activity_cached
    if state_ref.current().role != "master":
        return state_ref.current().inference_activity or inference_activity.idle()
    with _activity_lock:
        now = time.monotonic()
        if _activity_cached is not None and now - _activity_sampled_at < 0.5:
            return dict(_activity_cached)
        running = bool(state_ref.current().process_pid and state_ref.current().process_role == "master")
        load = load_progress.sample(state_ref.current().infer_port) if running else None
        activity = inference_activity.sample(
            state_ref.current().infer_port,
            running=running,
            load=load,
            client=http_clients.control_client() if running else None,
        )
        state_ref.current().inference_activity = activity
        _activity_cached = dict(activity)
        _activity_sampled_at = now
        return dict(activity)


def _worker_local_leave() -> None:
    """Idle this worker locally without calling unregister (already dropped)."""
    if state_ref.current().process_pid:
        native.stop_process(cast(int, state_ref.current().process_pid), force=True)
        state_ref.current().process_pid = None
        state_ref.current().process_role = None
        runtime.clear_children()
    _managed_network_before_reset()
    _reset_netsec_state()
    with state_ref.current().lock:
        membership_mod.clear_membership_fields(state_ref.current())
        state_ref.current().role = "idle"
        state_ref.current().join_key = None
        state_ref.current().master_host = None
        state_ref.current().model_name = None
        state_ref.current().gpu_ids = None
        state_ref.current().save()


def _master_control_base() -> str:
    """HTTP base for worker → master control-plane (may be localhost proxy)."""
    if state_ref.current().cp_proxy_port:
        return f"http://127.0.0.1:{int(cast(int, state_ref.current().cp_proxy_port))}"
    return f"http://{state_ref.current().master_host}:{state_ref.current().master_port}"


def _ensure_master_control_route(master_host: str, master_port: int) -> list[str]:
    """Use direct LAN when possible; otherwise start a localhost TCP proxy.

    Returns netsec-style warnings for the caller to surface.
    """
    from kllm.shared import cp_proxy

    warnings: list[str] = []
    try:
        handle = cp_proxy.ensure_for_master(master_host, master_port)
    except (RuntimeError, OSError) as exc:
        raise HTTPException(502, str(exc)) from exc
    if handle is None:
        state_ref.current().cp_proxy_port = None
        state_ref.current().cp_proxy_pid = None
        return warnings
    state_ref.current().cp_proxy_port = handle.listen_port
    state_ref.current().cp_proxy_pid = handle.pid
    warnings.append(
        f"control-plane via 127.0.0.1:{handle.listen_port} "
        f"(agent Python cannot reach {master_host}:{master_port} on LAN; "
        f"helper={handle.helper})"
    )
    return warnings


_membership_heartbeats: MembershipHeartbeatService | None = None


def _membership_heartbeat_service() -> MembershipHeartbeatService:
    """Reuse one service so a missed heartbeat can be counted across ticks."""
    global _membership_heartbeats
    if _membership_heartbeats is None:
        _membership_heartbeats = MembershipHeartbeatService(
            state=lambda: state_ref.current(),
            ensure_control_route=lambda host, port: _ensure_master_control_route(host, port),
            control_base=_master_control_base,
            heartbeat_payload=_heartbeat_payload,
            control_client=lambda: http_clients.control_client(),
            mark_unreachable=lambda state: membership_mod.mark_master_unreachable(state),
            clear_unreachable=lambda state: membership_mod.clear_master_unreachable(state),
            worker_local_leave=_worker_local_leave,
            apply_snapshot=_apply_cluster_snapshot,
            member_rpc_host=member_rpc_host,
        )
    return _membership_heartbeats


def _worker_post_heartbeat() -> None:
    _membership_heartbeat_service().post_heartbeat()


_worker_launch_lock = threading.RLock()
_worker_runtime_retry = {"failures": 0, "next_attempt": 0.0}
_WORKER_RUNTIME_RETRY_BASE_S = 15.0
_WORKER_RUNTIME_RETRY_MAX_S = 300.0


def _worker_runtime_healthy() -> bool:
    if state_ref.current().process_role != "worker" or not state_ref.current().process_pid:
        return False
    try:
        return bool(native.process_alive(int(cast(int, state_ref.current().process_pid))))
    except Exception:
        return True


def _ensure_worker_runtime() -> None:
    """Compatibility entry point; membership never authorizes RPC startup."""
    # Runtime ownership is reconciled by the signed control service. Heartbeats
    # report membership and capacity even when the node is idle.
    return


def _probe_worker_latencies() -> None:
    _membership_heartbeat_service().probe_latencies()


def _schedule_worker_latency_probe() -> None:
    global _latency_probe_inflight
    with _latency_probe_lock:
        if _latency_probe_inflight:
            return
        _latency_probe_inflight = True

    def probe() -> None:
        global _latency_probe_inflight
        try:
            _probe_worker_latencies()
            state_ref.current().save()
        finally:
            with _latency_probe_lock:
                _latency_probe_inflight = False

    threading.Thread(target=probe, name="kllm-latency-probe", daemon=True).start()


def _schedule_local_capacity_refresh() -> None:
    """Refresh capacity without delaying the liveness control plane.

    ``llama-server --list-devices`` can stall while an accelerator initializes.
    Membership heartbeats must remain on their two-second cadence while that
    happens: a worker is evicted after 20 seconds without one. Only one probe
    may run at a time because all it does is refresh the latest capacity data.
    """
    _capacity_refresh_scheduler.schedule()


_membership_background = MembershipLoop(
    stop=_background_stop,
    state=lambda: state_ref.current(),
    interval_seconds=membership_mod.HEARTBEAT_INTERVAL_S,
    synchronize_master=lambda: _sync_master_liveness(shrink=True),
    post_worker_heartbeat=lambda: _worker_post_heartbeat(),
    schedule_worker_probe=lambda: _schedule_worker_latency_probe(),
    schedule_capacity_refresh=lambda: _schedule_local_capacity_refresh(),
)


def _tick_membership() -> None:
    _membership_background.tick()


def _membership_loop() -> None:
    _membership_background.run()


def _start_membership_thread() -> None:
    _membership_background.start()


_log_relay_service = LogRelayService(
    state=lambda: state_ref.current(),
    stop=_background_stop,
    master_control_base=_master_control_base,
)


def _start_logs_thread() -> None:
    _log_relay_service.start()


def _capacity_service() -> CapacityService:
    return CapacityService(
        state=lambda: state_ref.current(),
        list_gpus=native.list_gpus,
        probe_capacity=_probe_capacity_for_cluster,
        resolve_gpu_selection=_resolve_gpu_selection,
        pick_rpc_bind_host=netsec.pick_rpc_bind_host,
        tracked_process_start=_tracked_process_start,
        configured_gpu_ids=kllm_config.get_gpu_ids,
    )


def _build_master_member_record(lan_host: str) -> dict[str, Any]:
    return _capacity_service().master_member_record(lan_host)


def _ensure_master_member(*, persist: bool = False) -> bool:
    return _capacity_service().ensure_master_member(persist=persist)


def _refresh_local_capacity() -> None:
    _capacity_service().refresh_local()


_capacity_refresh_scheduler = CapacityRefreshScheduler(_refresh_local_capacity)


def _require_key(x_kllm_key: str | None = Header(default=None)) -> None:
    if state_ref.current().role != "master":
        return
    if not state_ref.current().join_key:
        raise HTTPException(503, "master has no join key")
    if not secrets.compare_digest(x_kllm_key or "", state_ref.current().join_key or ""):
        raise HTTPException(401, "invalid join key")


def _require_api_token(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> None:
    token = authorization or x_api_key
    # Localhost dashboard may omit token when talking same-origin without header;
    # prefer requiring token for inference routes (harnesses always send it).
    if not verify_api_token(token):
        # Allow unauthenticated only from missing header when no token file? Always require.
        raise HTTPException(401, "invalid or missing API token")


def _dashboard_dir() -> Path | None:
    root = repo_root()
    if root:
        built = root / "dashboard" / "build"
        if built.is_dir():
            return built
    # Fallback relative to this package's repo walk
    here = Path(__file__).resolve()
    for p in here.parents:
        candidate = p / "dashboard" / "build"
        if candidate.is_dir():
            return candidate
    return None


def _probe_capacity_for_cluster() -> dict[str, Any]:
    """Probe host capacity; map missing native runtime to a clear HTTP error."""
    try:
        return native.probe_capacity()
    except RuntimeError as exc:
        raise HTTPException(
            503,
            f"{exc} On this machine run: {native.install_recovery_command()}",
        ) from exc


def _gpu_api() -> GpuApi:
    return GpuApi(
        _gpu_service(),
        debug_hardware=os.getenv("KLLM_DEBUG_HARDWARE", "").lower() in ("1", "true", "yes"),
    )


def _gpu_inventory(
    listed: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    return _gpu_api().inventory(listed)


def _gpu_service() -> GpuSelectionService:
    return GpuSelectionService(
        list_gpus=native.list_gpus,
        configured_gpu_ids=kllm_config.get_gpu_ids,
        install_recovery_command=native.install_recovery_command,
    )


def _membership_gpu_ids(
    body_gpu_ids: list[int] | None,
    *,
    body_sent: bool,
    listed: dict[str, Any] | None = None,
) -> list[int] | None:
    """Resolve GPU defaults for membership requests through the host adapter."""
    return _gpu_api().membership_gpu_ids(
        body_gpu_ids,
        body_sent=body_sent,
        listed=listed,
    )


def _resolve_gpu_selection(
    gpu_ids: list[int] | None,
    *,
    listed: dict[str, Any] | None = None,
) -> ResolvedGpuSelection:
    """Validate optional GPU ids against the host inventory."""
    return _gpu_api().resolve(gpu_ids, listed=listed)


def _gpu_visibility_env(backend: str, gpu_ids: list[int] | None) -> dict[str, str]:
    return _gpu_api().visibility_environment(backend, gpu_ids)


def _list_gpus_payload() -> dict[str, Any]:
    """Enumerate GPUs with effective + probed backend and UI allowed list."""
    return _gpu_api().list_payload()


def _runtime_setup_service() -> RuntimeSetupService:
    return build_runtime_setup_service(
        state=lambda: state_ref.current(),
        repository_root=repo_root,
        gpu_visibility_env=_gpu_visibility_env,
        stop_process=native.stop_process,
        clear_children=runtime.clear_children,
        start_worker=native.start_worker,
        resolve_gpu_selection=_resolve_gpu_selection,
        register_child=runtime.register_child,
        rpc_log_path=cluster_log.rpc_server_log_path,
        process_alive=native.process_alive,
        stop_stray_rpc_servers=runtime.stop_stray_rpc_servers,
    )


@app.get("/health")
def health() -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "control_protocol": kllm_config.CONTROL_PROTOCOL_VERSION,
        "role": state_ref.current().role,
        "node_id": state_ref.current().node_id,
        "pid": os.getpid(),
    }
    if getattr(app.state, "managed_network", None) is not None:
        out["capabilities"] = ["managed-network-v1", "managed-network-v2", "rpc-control-v1"]
    # Non-secret fingerprint (same as mDNS ``kid``) so Join-by-key can HTTP-probe
    # when Bonjour/mDNS is unavailable on the LAN.
    if state_ref.current().role == "master" and state_ref.current().join_key:
        out["kid"] = discovery.join_key_id(cast(str, state_ref.current().join_key))
    return out


def _download_job_runner(source: str, name: str | None) -> None:
    """Compatibility wrapper for the injected model download worker."""
    _model_service.run_download_job(source, name)


def _model_handlers() -> ModelHandlers:
    """Build the HTTP adapter with current state and replaceable download runner."""
    return ModelHandlers(_model_service, lambda: state_ref.current(), _download_job_runner)


_cluster_update_service = ClusterUpdateService(cluster_update, _master_control_base)


def _cluster_update_handlers() -> ClusterUpdateHandlers:
    return ClusterUpdateHandlers(_cluster_update_service, lambda: state_ref.current())


def _fast_subnet_networks() -> list[dict[str, Any]]:
    service = getattr(app.state, "managed_network", None)
    if service is None:
        return []
    return service.summaries()


_fast_subnet_service = FastSubnetService(
    networks=_fast_subnet_networks,
    join=lambda body: join_cluster(body),
)


def _fast_subnet_handlers() -> FastSubnetHandlers:
    return FastSubnetHandlers(_fast_subnet_service, lambda: state_ref.current())


def fast_subnet_reachability() -> dict[str, Any]:
    """Master: probe each worker's private control port once."""
    return _fast_subnet_handlers().reachability()


def fast_subnet_reconnect(
    body: FastSubnetReconnectBody,
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Master: tell one worker to rejoin. Worker: rejoin the private master."""
    return _fast_subnet_handlers().reconnect(body, x_kllm_key)


async def _observed_inference(path: str, response: Awaitable[Any]) -> Any:
    """Publish request boundaries without letting a subscriber fail inference."""
    publish_lifecycle(LifecycleEvent.REQUEST_RECEIVED, path, isolate=True)
    result = await response
    publish_lifecycle(LifecycleEvent.RESPONSE_SENT, path, isolate=True)
    return result


def _active_config() -> dict[str, Any] | None:
    snapshot = state_ref.current().active_config
    if (
        snapshot
        and state_ref.current().process_role == "master"
        and snapshot.get("process_pid") == state_ref.current().process_pid
    ):
        return snapshot
    return None


def _connection_url(host: str, port: int, scheme: str = "http") -> str:
    """Format a reachable master endpoint, including IPv6 literals."""
    address = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{address}:{port}"


def _connection_request_url(request: Request) -> str | None:
    """Keep a non-loopback production dashboard origin when it can reach this agent."""
    host = request.url.hostname
    if not host or _is_loopback(host) or ("." not in host and ":" not in host):
        return None
    try:
        port = request.url.port
    except ValueError:
        return None
    scheme = request.url.scheme if request.url.scheme in ("http", "https") else "http"
    default_port = 443 if scheme == "https" else 80
    address = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{address}" + (f":{port}" if port and port != default_port else "")


def _settings_handlers() -> SettingsHandlers:
    return SettingsHandlers(
        lambda: state_ref.current(),
        ensure_api_token=ensure_api_token,
        hf_token_status=hf_token_status,
        save_hf_token=save_hf_token,
        config=kllm_config,
        list_gpus=_list_gpus_payload,
        home_dir=home_dir,
        models_dir=models_dir,
        logs_dir=logs_dir,
    )


def _dashboard_status_service() -> DashboardStatusService:
    return DashboardStatusService(
        state=lambda: state_ref.current(),
        load_config=kllm_config.load_config,
        inference_activity=_inference_activity,
        probe_capacity=native.probe_capacity,
        last_plan=membership_mod.public_last_plan,
        download_progress=model_ops.get_download_progress,
        rpc_cache_status=rpc_cache.status,
        update_progress=cluster_update.get_update_progress,
        software_identity=cluster_update.software_identity,
        default_device=kllm_config.DEFAULT_DEVICE,
        managed_networks=_managed_network_summaries,
        feature_flags=kllm_config.feature_flags,
        rpc_lifecycle=_rpc_lifecycle_status,
        runtimes=_runtime_status,
        network_directory=_network_directory,
    )


def _reset_netsec_state() -> None:
    _network_state_service().reset()


def _master_lan_host() -> str:
    mem = state_ref.current().members.get(state_ref.current().node_id) or {}
    return mem.get("lan_host") or mem.get("host") or discovery._local_ip()


def _master_gpu_selection(body: StartBody) -> ResolvedGpuSelection:
    listed = native.list_gpus()
    gpu_ids = _membership_gpu_ids(
        body.gpu_ids,
        body_sent="gpu_ids" in body.model_fields_set,
        listed=listed,
    )
    return _resolve_gpu_selection(gpu_ids, listed=listed)


def _advertise_master(name: str, port: int, join_key: str, lan_host: str) -> None:
    global _advertiser
    if _advertiser:
        _advertiser.stop()
    _advertiser = discovery.MasterAdvertiser(
        name,
        port,
        join_key=join_key,
        advertise_host=lan_host,
    )
    _advertiser.start()


def _master_start_handler() -> MasterStartHandler:
    return MasterStartHandler(
        MasterStartDependencies(
            state=lambda: state_ref.current(),
            generate_join_key=generate_join_key,
            pick_rpc_bind_host=netsec.pick_rpc_bind_host,
            select_gpus=_master_gpu_selection,
            build_master_member=_build_master_member_record,
            advertise=_advertise_master,
        )
    )


def _stop_advertiser() -> None:
    global _advertiser
    if _advertiser:
        _advertiser.stop()
        _advertiser = None


def _unregister_worker() -> None:
    _ensure_master_control_route(state_ref.current().master_host or "", state_ref.current().master_port)
    with httpx.Client(timeout=10.0) as client:
        client.post(
            f"{_master_control_base()}/v2/cluster/nodes/unregister",
            json={"node_id": state_ref.current().node_id},
            headers={"X-Kllm-Key": state_ref.current().join_key or ""},
        )


def _managed_network_before_reset() -> None:
    service = getattr(app.state, "managed_network", None)
    if service is not None:
        if state_ref.current().role == "master" and service.rpc_runs is not None:
            service.rpc_runs.stop(lambda: _inference_handlers().stop())
        elif service.worker_rpc is not None:
            service.worker_rpc.reconcile_startup()
        from kllm.modules.managed_network.interactors.recovery import before_reset

        service.admission.call(before_reset(service))


def _cluster_lifecycle_handlers() -> ClusterLifecycleHandlers:
    return ClusterLifecycleHandlers(
        state=lambda: state_ref.current(),
        end_usage=lambda node_id: _usage_store().end_node(node_id),
        stop_process=native.stop_process,
        clear_children=runtime.clear_children,
        end_load_progress=load_progress.end,
        idle_activity=inference_activity.idle,
        stop_advertiser=_stop_advertiser,
        reset_network=_reset_netsec_state,
        clear_membership=membership_mod.clear_membership_fields,
        unregister_worker=_unregister_worker,
        before_network_reset=lambda: _managed_network_before_reset(),
    )


def _join_gpu_selection(body: JoinBody) -> ResolvedGpuSelection:
    listed = native.list_gpus()
    gpu_ids = _membership_gpu_ids(
        body.gpu_ids,
        body_sent="gpu_ids" in body.model_fields_set,
        listed=listed,
    )
    return _resolve_gpu_selection(gpu_ids, listed=listed)


def _drop_cp_proxy() -> None:
    _network_state_service().drop_proxy()


def _cluster_join_service() -> ClusterJoinService:
    return ClusterJoinService(
        ClusterJoinDependencies(
            state=lambda: state_ref.current(),
            gpu_selection=_join_gpu_selection,
            probe_capacity=_probe_capacity_for_cluster,
            pick_rpc_bind_host=netsec.pick_rpc_bind_host,
            ensure_master_control_route=_ensure_master_control_route,
            software_identity=cluster_update.software_identity,
            build_register_payload=build_register_payload,
            post_register=post_register,
            drop_cp_proxy=_drop_cp_proxy,
            apply_cluster_snapshot=_apply_cluster_snapshot,
            post_worker_heartbeat=_worker_post_heartbeat,
            stop_process=native.stop_process,
            clear_children=runtime.clear_children,
        )
    )


def _join_cluster(body: JoinBody) -> dict[str, Any]:
    # Serialize with the background reconciler so a rejoin and a runtime
    # self-heal cannot start two rpc-servers on the same RPC port.
    with _worker_launch_lock:
        service = getattr(app.state, "managed_network", None)
        if service and service.worker_rpc:
            service.worker_rpc.reconcile_startup()
            if (state_ref.current().members.get(state_ref.current().node_id) or {}).get("rpc_status") == "cleanup_pending":
                raise HTTPException(409, "Reconcile the tracked RPC process locally before joining")
        return _cluster_join_service().join(body)


def _membership_handlers() -> MembershipHandlers:
    return MembershipHandlers(
        state=lambda: state_ref.current(),
        sync_liveness=_sync_master_liveness,
        resources=_usage_runtime().resources,
        resource_probe_available=native.resource_probe_available,
        store=_usage_store,
        inference_activity=_inference_activity,
        shrink_cluster=_relaunch_after_membership_change,
        directory_sync=_network_directory_sync,
        directory_snapshot=_network_directory,
        directory_remove=_network_directory_remove,
        lifecycle_publisher=publish_lifecycle,
    )


def _usage_ingest_handlers() -> UsageIngestHandlers:
    return UsageIngestHandlers(
        state=lambda: state_ref.current(),
        resources=_usage_runtime().resources,
        store=_usage_store,
        assignment=_usage_assignment,
        node_context=_usage_node_context,
        resource_validation_available=native.resource_probe_available,
    )


def _proxy_master_usage(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    proxy = UsageHistoryProxy(lambda: state_ref.current(), _master_control_base, http_clients.control_client)
    return proxy.get(path, params)


def _usage_history_handlers() -> UsageHistoryHandlers:
    return UsageHistoryHandlers(lambda: state_ref.current(), _usage_store, _proxy_master_usage)


def _set_worker_usage_last_save(value: str | None) -> None:
    _usage_runtime().set_worker_last_save(value)


def _set_worker_usage_last_success_at(value: str) -> None:
    _usage_runtime().set_worker_last_success_at(value)


def _usage_live_handlers() -> UsageLiveHandlers:
    return UsageLiveHandlers(
        state=lambda: state_ref.current(),
        proxy=_proxy_master_usage,
        overview=_usage_runtime().overview,
        store=_usage_store,
        sync_liveness=_sync_master_liveness,
        resource_schema_supported=native.resource_probe_available,
        worker_error=lambda: _usage_runtime().worker_error,
        last_save=lambda: _usage_runtime().worker_last_save,
        set_last_save=_set_worker_usage_last_save,
        set_last_success_at=_set_worker_usage_last_success_at,
        utc_now=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _usage_read(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Compatibility boundary for callers wrapping a one-off usage query."""
    return UsageHistoryHandlers._read(action)


def _log_handlers() -> LogHandlers:
    return LogHandlers(lambda: state_ref.current(), cluster_log.HUB, _master_control_base)


def _logs_snapshot(
    n: int, node: str | None, source: str | None, after: str | None = None
) -> dict[str, Any]:
    return _log_handlers().snapshot(n, node, source, after)


def _ensure_log_journal() -> None:
    _log_handlers().ensure_journal()


def _try_proxy_master_logs(
    n: int,
    follow: bool,
    node: str | None,
    source: str | None,
    after: str | None = None,
) -> Any:
    return _log_handlers().proxy_master(n, follow, node, source, after)


def _live_coverage() -> str:
    return (
        "cluster"
        if state_ref.current().role == "master"
        else "local_with_cluster_view"
        if state_ref.current().role == "worker"
        else "local"
    )


def _live_status() -> dict[str, Any]:
    """Fast detached status view; probes and software discovery stay off the feed path."""
    return _dashboard_status_service().live_status()


def _operation_records(kind: str, records: dict[str, Any]) -> dict[str, Any]:
    for key in ("current", "last_terminal"):
        operation = records.get(key)
        if not isinstance(operation, dict):
            continue
        operation = dict(operation)
        operation["kind"] = kind
        operation["node_id"] = state_ref.current().node_id
        if kind == "load":
            operation["state"] = (
                "running"
                if operation.get("phase") in {"starting", "loading"}
                else "complete"
                if operation.get("phase") == "ready"
                else "cancelled"
                if operation.get("phase") == "cancelled"
                else "error"
            )
        records[key] = operation
    return records


def _live_load() -> dict[str, Any]:
    return _operation_records("load", load_progress.records(state_ref.current().infer_port))


def _live_usage() -> dict[str, Any]:
    try:
        return usage_live()
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            raise SourceDenied from None
        raise


def _live_upstream_success(topic: str) -> str | None:
    return (
        _usage_runtime().worker_last_success_at
        if topic == "usage" and state_ref.current().role == "worker"
        else None
    )


def _live_download() -> dict[str, Any]:
    return _operation_records("download", model_ops.download_progress_records())


def _live_logs(after: str | None) -> dict[str, Any]:
    handler = LiveLogsHandler(
        state=lambda: state_ref.current(),
        app_state=app.state,
        snapshot=_logs_snapshot,
        proxy=_try_proxy_master_logs,
    )
    return handler.read(after)


def _dashboard_live_service() -> DashboardLiveService:
    return DashboardLiveService(
        app_state=app.state,
        state=lambda: state_ref.current(),
        status=lambda: _live_status(),
        coverage=lambda: _live_coverage(),
        usage=lambda: _live_usage(),
        load=lambda: _live_load(),
        download=lambda: _live_download(),
        logs=lambda after: _live_logs(after),
        upstream_success=lambda topic: _live_upstream_success(topic),
    )


def _new_live_feed() -> LiveFeed:
    return _dashboard_live_service().create()


def _live_feed() -> LiveFeed:
    return _dashboard_live_service().get()


def _live_websocket_handler() -> DashboardLiveWebSocket:
    return DashboardLiveWebSocket(
        LiveWebSocketDependencies(
            live_feed=_live_feed,
            websocket_origin=_websocket_origin,
            origin_allowed=_origin_allowed,
            server_time=live_server_time,
            log_signature=log_result_signature,
            subscription=_subscription,
            safe_control=_safe_control,
        )
    )


def _maybe_remember(body: PlanBody | RunBody) -> None:
    _run_settings_handlers().remember(body)


def _template_validation_service() -> TemplateValidationService:
    def resolve_server() -> str | None:
        root = repo_root()
        return native.resolve_llama_server(str(root) if root else None)

    return TemplateValidationService(resolve_server)


def _run_settings_handlers() -> RunSettingsHandlers:
    from kllm.modules.inference.entities import available_threads

    return RunSettingsHandlers(
        model_settings_store,
        model_ops.resolve_local_model,
        available_threads,
        capture_runtime=lambda identity: _runtime_registry().capture(identity),
    )


def _engine_settings_handlers():
    from kllm.modules.inference.gateways.engine_settings import EngineSettingsStore
    from kllm.modules.inference.interactors.engine_settings import EngineSettingsHandlers

    return EngineSettingsHandlers(
        EngineSettingsStore(home_dir() / "engine-model-settings.json"),
        model_settings_store, model_ops.resolve_local_model,
        lambda identity: _runtime_registry().capture(identity),
        lambda body, path: _run_settings_handlers().preflight(body, path),
    )


def _inference_handlers() -> InferenceHandlers:
    from kllm.shared.runtime_resources import node_resources

    service = getattr(app.state, "managed_network", None)
    return InferenceHandlers(
        InferenceDependencies(
            state=state_ref.current(),
            runtime_resources=(lambda: node_resources(service.runtimes.registry))
            if service is not None and service.runtimes is not None else None,
            runtime_context=(
                service.runtimes.operation
                if service is not None and service.runtimes is not None
                else None
            ),
            prima_run=(lambda body, path, plan, members: service.runtimes.prima.runs.launch(body, path, plan, members))
            if service is not None and service.runtimes is not None else None,
            prima_stop=(lambda: service.runtimes.prima.runs.stop())
            if service is not None and service.runtimes is not None else None,
            prima_plan=(lambda body, path, members: service.runtimes.prima.plan(
                inference_runtime_snapshot()["runtime_id"], body, model=path, members=members))
            if service is not None and service.runtimes is not None else None,
            resolve_settings=lambda body: _run_settings_handlers().resolve(body),
            preflight=lambda body, path: _run_settings_handlers().preflight(body, path),
            ensure_master_member=lambda: _ensure_master_member(persist=True),
            merge_extra=merge_and_sanitize_extra,
            execute_plan=execute_plan,
            resolve_model=model_ops.resolve_local_model,
            persist_last_settings=_persist_last_settings,
            remember=_maybe_remember,
            validate_vision=validate_vision_for_run,
            ensure_rpc_peers_reachable=ensure_rpc_peers_reachable,
            repository_root=repo_root,
            stop_previous_master=lambda: stop_previous_master(
                state_ref.current(), native, runtime, load_progress, inference_activity
            ),
            gpu_visibility_env=_gpu_visibility_env,
            sanitize_extra_args=sanitize_extra_args,
            build_master_launch=build_master_launch,
            spawn_master=lambda launch_config: spawn_master(
                native, runtime, launch_config, ensure_master_alive_after_spawn
            ),
            finish_master_launch=lambda path, plan_result, launched, persist, remember: (
                finish_master_launch(
                    state_ref.current(),
                    path,
                    plan_result,
                    launched,
                    runtime,
                    load_progress,
                    persist,
                    remember,
                )
            ),
            stop_managed_inference=lambda: membership_mod._stop_inference(state_ref.current()),
            inference_idle=inference_activity.idle,
            network_diagnostics=lambda network_id: _managed_network_service().admission.diagnostics(
                network_id
            ),
            network_members=lambda network_id: (
                _managed_network_service().prima_admission
                if (inference_runtime_snapshot() or {}).get("engine") == "primacpp"
                else _managed_network_service().admission
            ).members(
                network_id
            ),
            network_run=lambda network_id: (
                _managed_network_service().admission.context(network_id)
                if getattr(app.state, "managed_network", None) is not None
                else (
                    _managed_network_service()
                    if network_id
                    else __import__("contextlib").nullcontext(state_ref.current().members)
                )
            ),
            network_stop=lambda: (
                _managed_network_service().admission.stop_context()
                if getattr(app.state, "managed_network", None) is not None
                else __import__("contextlib").nullcontext()
            ),
            network_release=lambda: (
                _managed_network_service().admission.call(
                    _managed_network_service().admission.release_stopped(lock_held=True)
                )
                if getattr(app.state, "managed_network", None) is not None
                else None
            ),
            rpc_context=(
                service.rpc_runs.context
                if service is not None and service.rpc_runs is not None
                else None
            ),
            network_run_selected=(
                service.admission.context
                if service is not None and service.rpc_runs is not None
                else None
            ),
            lifecycle_publisher=publish_lifecycle,
        )
    )


def _guard_context(body: PlanBody | RunBody) -> None:
    if not body.context_auto and body.context < 512:
        raise HTTPException(
            400, detail={"context": "context must be >= 512 when context_auto is false"}
        )


def _mount_dashboard() -> None:
    _mount_dashboard_routes(app, _dashboard_dir())


from kllm.modules.cluster.endpoints import (  # noqa: E402
    discover,
    status,
    start_master,
    stop,
    join_cluster,
    leave,
    register,
    unregister,
    heartbeat,
    set_cluster_providers,
)
from kllm.modules.models.endpoints import (  # noqa: E402
    list_models,
    search_models,
    download_model,
    download_progress,
    cancel_download,
    delete_model,
    set_model_handlers_provider,
)
from kllm.modules.inference.endpoints import (  # noqa: E402
    get_engine_model_settings,
    put_engine_model_settings,
    load_progress_endpoint,
    chat_completions,
    embeddings,
    raw_embeddings,
    rerank,
    tokenize,
    detokenize,
    apply_template,
    text_completions,
    infill,
    raw_completions,
    chat_completion_control,
    anthropic_messages,
    responses,
    response_tokens,
    inference_models,
    connection_snapshot,
    get_model_settings,
    run_capabilities,
    validate_chat_template,
    put_model_settings,
    plan,
    run,
    stop_inference,
    legacy_plan,
    legacy_run,
    legacy_put_model_settings,
    set_inference_providers,
)
from kllm.modules.updates.endpoints import (  # noqa: E402
    cluster_update_endpoint,
    cluster_update_progress,
    set_cluster_update_handlers_provider,
)
from kllm.modules.settings.endpoints import (  # noqa: E402
    get_api_token,
    get_hf_token,
    put_hf_token,
    get_config,
    get_gpus,
    put_config,
    get_home,
    get_feature_flags,
    put_feature_flag,
    set_settings_handlers_provider,
)
from kllm.modules.logs.endpoints import (  # noqa: E402
    get_logging,
    apply_logging,
    put_logging,
    ingest_logs,
    get_logs,
    set_logs_providers,
)
from kllm.modules.usage.endpoints import (  # noqa: E402
    usage_ingest,
    usage_live,
    usage_lifetimes,
    usage_series,
    usage_readings,
    set_usage_handlers_provider,
)
from kllm.modules.live.endpoints import (  # noqa: E402
    dashboard_live,
    dashboard_live_websocket,
    set_live_providers,
)

_route_handlers: dict[str, Callable[..., Any]] = {
    "status": status,
    "get_gpus": get_gpus,
    "get_config": get_config,
    "put_config": put_config,
    "get_home": get_home,
    "get_feature_flags": get_feature_flags,
    "put_feature_flag": put_feature_flag,
    "start_master": start_master,
    "stop": stop,
    "join_cluster": join_cluster,
    "leave": leave,
    "register": register,
    "unregister": unregister,
    "heartbeat": heartbeat,
    "discover": discover,
    "cluster_update_endpoint": cluster_update_endpoint,
    "cluster_update_progress": cluster_update_progress,
    "fast_subnet_reachability": fast_subnet_reachability,
    "fast_subnet_reconnect": fast_subnet_reconnect,
    "list_models": list_models,
    "chat_completions": chat_completions,
    "embeddings": embeddings,
    "raw_embeddings": raw_embeddings,
    "rerank": rerank,
    "tokenize": tokenize,
    "detokenize": detokenize,
    "apply_template": apply_template,
    "text_completions": text_completions,
    "infill": infill,
    "raw_completions": raw_completions,
    "chat_completion_control": chat_completion_control,
    "anthropic_messages": anthropic_messages,
    "responses": responses,
    "response_tokens": response_tokens,
    "inference_models": inference_models,
    "connection_snapshot": connection_snapshot,
    "get_logging": get_logging,
    "put_logging": put_logging,
    "apply_logging": apply_logging,
    "search_models": search_models,
    "download_model": download_model,
    "download_progress": download_progress,
    "cancel_download": cancel_download,
    "delete_model": delete_model,
    "validate_chat_template": validate_chat_template,
    "run_capabilities": run_capabilities,
    "plan": plan,
    "run": run,
    "stop_inference": stop_inference,
    "load_progress_endpoint": load_progress_endpoint,
    "get_model_settings": get_model_settings,
    "put_model_settings": put_model_settings,
    "get_engine_model_settings": get_engine_model_settings,
    "put_engine_model_settings": put_engine_model_settings,
    "usage_live": usage_live,
    "usage_ingest": usage_ingest,
    "usage_lifetimes": usage_lifetimes,
    "usage_series": usage_series,
    "usage_readings": usage_readings,
    "get_logs": get_logs,
    "ingest_logs": ingest_logs,
    "dashboard_live": dashboard_live,
    "dashboard_live_websocket": dashboard_live_websocket,
    "get_api_token": get_api_token,
    "get_hf_token": get_hf_token,
    "put_hf_token": put_hf_token,
}

set_settings_handlers_provider(_settings_handlers)
set_cluster_update_handlers_provider(_cluster_update_handlers)
set_usage_handlers_provider(
    ingest=_usage_ingest_handlers,
    live=_usage_live_handlers,
    history=_usage_history_handlers,
)
set_live_providers(
    feed=_live_feed,
    websocket_handler=_live_websocket_handler,
)
set_logs_providers(
    handlers=_log_handlers,
    proxy=_try_proxy_master_logs,
)
set_cluster_providers(
    sync_master_liveness=_sync_master_liveness,
    dashboard_status_service=_dashboard_status_service,
    master_start_handler=_master_start_handler,
    cluster_lifecycle_handlers=_cluster_lifecycle_handlers,
    join_cluster=_join_cluster,
    membership_handlers=_membership_handlers,
)
set_model_handlers_provider(_model_handlers)
set_inference_providers(
    observed_inference=lambda *a, **k: _observed_inference(*a, **k),
    active_config=lambda: _active_config(),
    connection_url=lambda *a, **k: _connection_url(*a, **k),
    connection_request_url=lambda *a, **k: _connection_request_url(*a, **k),
    master_lan_host=lambda: _master_lan_host(),
    inference_activity=lambda: _inference_activity(),
    run_settings_handlers=lambda: _run_settings_handlers(),
    engine_settings_handlers=lambda: _engine_settings_handlers(),
    template_validation_service=lambda: _template_validation_service(),
    inference_handlers=lambda: _inference_handlers(),
    managed_network_service=lambda: getattr(app.state, "managed_network", None),
    managed_network_error=lambda: getattr(app.state, "managed_network_error", None),
)

legacy_v1_router = _build_v1_router(
    {
        **_route_handlers,
        "plan": legacy_plan,
        "run": legacy_run,
        "put_model_settings": legacy_put_model_settings,
    }
)
app.include_router(legacy_v1_router)
app.include_router(_build_v2_router(_route_handlers))
app.include_router(build_network_router(_managed_network_service))
app.include_router(
    build_runtime_router(lambda: _runtime_registry(), lambda: _runtime_control(), lambda: state_ref.current())
)
_mount_dashboard()


def create_app() -> FastAPI:
    return app
