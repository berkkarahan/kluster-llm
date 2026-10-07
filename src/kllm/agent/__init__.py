from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import os
import asyncio
import sqlite3
from contextlib import asynccontextmanager
import httpx
from kllm import http_clients, rpc_cache
from kllm.runtimes.registry import RuntimeRegistry
from kllm.agent.services.runtime_control import RuntimeControlService
from kllm.agent.handlers.runtimes import build_router as build_runtime_router
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from kllm import config as kllm_config
from kllm import (
    cluster_update,
    discovery,
    gateway,
    inference_activity,
    load_progress,
    models as model_ops,
    native,
    netsec,
    runtime,
    usage,
)
from kllm.auth import ensure_api_token, verify_api_token
from kllm.hf_token import hf_token_status, save_hf_token
from kllm import model_settings as model_settings_store
from kllm.extra_args import sanitize_extra_args
from kllm import cluster_log
from kllm import logging_control
from kllm.agent.schemas import LoggingBody, LoggingPolicyBody
from kllm.agent.live import (
    LiveFeed,
    SourceDenied,
    server_time as live_server_time,
)
from kllm.agent.join_flow import (
    build_register_payload,
    post_register,
)
from .origin import (
    _is_loopback as _is_loopback,
    _live_request as _live_request,
    _origin as _origin,
    _origin_allowed as _origin_allowed,
    _request_origin as _request_origin,
    _websocket_origin as _websocket_origin,
)

from kllm.agent.live_ws import (
    log_result_signature,
)
from kllm.agent.routes.v2 import build_router as _build_v2_router, build_network_router
from kllm.agent.routes.dashboard import mount_dashboard as _mount_dashboard_routes
from kllm.agent.routes.v1 import build_router as _build_v1_router
from kllm.agent.errors import (
    http_error_handler as _http_error_handler,
    plan_run_validation_handler as _plan_run_validation_handler,
)
from kllm.agent.handlers.models import ModelHandlers
from kllm.agent.handlers.cluster_updates import ClusterUpdateHandlers
from kllm.agent.handlers.usage import (
    UsageHistoryHandlers,
    UsageHistoryProxy,
    UsageIngestHandlers,
    UsageLiveHandlers,
    UsageWindow,
)
from kllm.agent.handlers.settings import SettingsHandlers
from kllm.agent.handlers.run_settings import RunSettingsHandlers
from kllm.agent.handlers.inference import InferenceDependencies, InferenceHandlers
from kllm.agent.handlers.logs import LogHandlers
from kllm.agent.handlers.live_logs import LiveLogsHandler
from kllm.agent.handlers.membership import MembershipHandlers
from kllm.agent.handlers.lifecycle import (
    ClusterLifecycleHandlers,
    MasterStartDependencies,
    MasterStartHandler,
)
from kllm.agent.handlers.live_websocket import (
    DashboardLiveWebSocket,
    LiveWebSocketDependencies,
    parse_subscription as _subscription,
    safe_control as _safe_control,
)
from kllm.exceptions import (
    ExternalServiceError,
    ResourceUnavailableError,
    ValidationError,
)
from kllm.agent.schemas import (
    ConfigBody,
    DeleteBody,
    DownloadBody,
    HfTokenBody,
    JoinBody,
    LlamaSetupBody,
    LogsIngestBody,
    ModelSettingsBody,
    LegacyModelSettingsBody,
    RegisterBody,
    SearchBody,
    StartBody,
    TemplateCheckBody,
    UnregisterBody,
    UpdateProgressBody,
)
from kllm.agent.run_route import (
    finish_master_launch,
    spawn_master,
    stop_previous_master,
)
from kllm.agent.services.models import ModelService
from kllm.agent.services.cluster_updates import ClusterUpdateService
from kllm.agent.services.gpus import GpuSelectionService
from kllm.agent.gpu_api import GpuApi
from kllm.agent.runtime_api import (
    build_runtime_setup_service,
    setup_runtime,
)
from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.agent.services.membership_maintenance import MembershipMaintenance
from kllm.agent.services.membership_heartbeat import MembershipHeartbeatService
from kllm.agent.services.membership_loop import MembershipLoop
from kllm.agent.services.capacity import CapacityService
from kllm.agent.usage_runtime import UsageRuntime
from kllm.agent.services.cluster_join import ClusterJoinDependencies, ClusterJoinService
from kllm.agent.services.log_relay import LogRelayService
from kllm.agent.services.dashboard_status import DashboardStatusService
from kllm.agent.services.dashboard_live import DashboardLiveService
from kllm.agent.services.background_tasks import CapacityRefreshScheduler
from kllm.agent.services.heartbeat_payload import HeartbeatPayloadBuilder
from kllm.agent.services.runtime_setup import RuntimeSetupService
from kllm.agent.services.template_validation import TemplateValidationService
from kllm.agent.services.network_setup import (
    MasterOverlaySetupService,
    NetworkStateService,
    WorkerOverlaySetupService,
)
from kllm.agent.application_lifecycle import AgentLifecycleDependencies, AgentLifespan
from kllm.models.schemas import DownloadAccepted, ModelCatalog, ModelDeletion
from kllm.run_planning import (
    ensure_master_alive_after_spawn,
    ensure_rpc_peers_reachable,
    execute_plan,
    member_rpc_host,
    merge_and_sanitize_extra,
    validate_vision_for_run,
)
from kllm.run_launch import build_master_launch
from kllm.run_settings import PlanBody, RunBody, LegacyPlanBody, LegacyRunBody
from kllm import membership as membership_mod
from kllm.state import (
    DEFAULT_CONTROL_PORT,
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
                    state=lambda: STATE,
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
                    "gpu": gpu_snapshot(STATE.process_pid),
                    "llama_props": llama_props(STATE.infer_port),
                },
            )

        await asyncio.to_thread(sample)
        await asyncio.sleep(10)


def _replace_state(state: ClusterState) -> None:
    global STATE
    STATE = state


def _reset_usage_state() -> None:
    _usage_runtime().reset()


def _clear_resource_state() -> None:
    _usage_runtime().clear_resources()


def _close_usage_store(app: FastAPI) -> None:
    _usage_runtime().close_store(app)


async def _start_managed_network(app: FastAPI) -> None:
    from kllm.agent.services.managed_network import ManagedNetworkService
    from kllm.agent.services.worker_rpc import WorkerRpcService
    from kllm.agent.services.rpc_runs import RpcRunCoordinator
    try:
        service = ManagedNetworkService(home_dir(), lambda: STATE, _master_control_base)
        registry = _runtime_registry()
        service.runtimes = RuntimeControlService(service, registry,
            lambda: kllm_config.load_config().get('accel_backend') or 'auto',
            native_supported=lambda: native.native_available() and getattr(native.require_native(), 'RUNTIME_SELECTION_VERSION', 0) == 1)
        service.worker_rpc = WorkerRpcService(lambda: STATE, _runtime_setup_service, lock=_worker_launch_lock, before_stop=service.release_worker_bridges, registry=registry)
        service.rpc_runs = RpcRunCoordinator(service, lambda body: _inference_handlers().run(body), lambda: membership_mod._stop_inference(STATE))
        await service.start()
        if not STATE.process_pid and registry.data.get('running'):
            registry.running(None)
        app.state.managed_network = service
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        app.state.managed_network_error = "Managed network identity/store is unavailable; local recovery required"


async def _stop_managed_network(app: FastAPI) -> None:
    service = getattr(app.state, 'managed_network', None)
    if service is not None:
        try:
            if service.rpc_runs is not None and service.rpc_runs.run_id is not None and STATE.role == 'master':
                await asyncio.to_thread(service.rpc_runs.stop, lambda: _inference_handlers().stop())
            if service.worker_rpc is not None:
                await asyncio.to_thread(service.worker_rpc.reconcile_startup)
        except (HTTPException, RuntimeError, ValueError):
            pass  # Ambiguous ownership stays durable for the next reconciliation.
        # A managed run is coupled to these bridge instances. Legacy runs keep
        # their existing lifecycle when no durable network reservation exists.
        for network in service.store.networks():
            for run in service.store.runs(network.network_id):
                if run.state == 'active' and run.child_pid == STATE.process_pid and run.child_pid:
                    registered = native.registered_process_identity(run.child_pid)
                    if registered and registered.get('start_token') == run.child_start_token:
                        await asyncio.to_thread(membership_mod._stop_inference, STATE)
        try:
            await service.admission.release_stopped()
        except (RuntimeError, ValueError):
            # Abrupt/ambiguous process ownership remains durable for review.
            pass
        finally:
            await service.close()
        del app.state.managed_network


def _managed_network_service():
    service = getattr(app.state, 'managed_network', None)
    if service is None:
        raise HTTPException(503, detail={'code': 'UNSUPPORTED_CAPABILITY', 'message': 'Managed network service has not started'})
    return service


def _managed_network_summaries() -> list[dict[str, Any]]:
    service = getattr(app.state, 'managed_network', None)
    return service.summaries() if service is not None else []


def _rpc_lifecycle_status() -> dict[str, Any]:
    service = getattr(app.state, 'managed_network', None)
    coordinator = service.rpc_runs if service is not None else None
    if STATE.role == 'master' and coordinator is not None:
        return {'status': coordinator.phase, 'workers': list(coordinator.workers), 'retries': coordinator.retries}
    member = STATE.members.get(STATE.node_id) or {}
    return {'status': member.get('rpc_status', 'idle'), 'error': member.get('rpc_error')}


def _agent_lifespan() -> AgentLifespan:
    return AgentLifespan(
        AgentLifecycleDependencies(
            state=lambda: STATE,
            start_managed_network=_start_managed_network,
            stop_managed_network=_stop_managed_network,
            replace_state=_replace_state,
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
            stop_live_feed=lambda feed: feed.stop(),
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
STATE = ClusterState.load(migrate=False)
_model_service = ModelService(
    model_ops,
    native,
    ThreadPoolExecutor(max_workers=1, thread_name_prefix="kllm-download"),
)
membership_mod.reconcile_last_plan(STATE)
if not STATE.node_id:
    STATE.node_id = secrets.token_hex(4)
    STATE.save()


def _network_state_service() -> NetworkStateService:
    from kllm import cp_proxy

    return NetworkStateService(
        state=lambda: STATE,
        reconcile_rpc_bind=netsec.reconcile_rpc_bind,
        refresh_firewall_warnings=netsec.refresh_rpc_firewall_warnings,
        wifi_path_warning=netsec.wifi_path_warning,
        stop_proxy=cp_proxy.stop_current,
        teardown_wireguard=netsec.tear_down_wireguard,
        overlay_wireguard=netsec.OVERLAY_WIREGUARD,
        overlay_none=netsec.OVERLAY_NONE,
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
    STATE.last_settings = data


def _relaunch_after_membership_change(state: ClusterState) -> None:
    membership_mod.on_membership_shrink(
        state,
        apply_serving=lambda _state, body, _plan: run(body),
        stop_serving=lambda _state: stop_inference(),
    )


_membership_maintenance = MembershipMaintenance(
    state=lambda: STATE,
    store=lambda: _usage_store(),
    shrink_cluster=_relaunch_after_membership_change,
)


def _sync_master_liveness(*, shrink: bool = True) -> list[str]:
    return _membership_maintenance.synchronize(shrink=shrink)


def _runtime_registry():
    registry = getattr(app.state, 'runtime_registry', None)
    if registry is None or registry.closed or registry.root.parent != home_dir():
        if registry is not None:
            registry.close()
        registry = RuntimeRegistry(home_dir(), repo_root)
        app.state.runtime_registry = registry
    return registry


def _runtime_control():
    service = getattr(app.state, 'managed_network', None)
    control = getattr(service, 'runtimes', None)
    if control is None:
        raise HTTPException(503, 'Signed node runtime control is unavailable; restart or recover the local agent')
    return control


def _runtime_status():
    service = getattr(app.state, 'managed_network', None)
    control = getattr(service, 'runtimes', None)
    return control.status() if control is not None else _runtime_registry().snapshot()


def _heartbeat_payload() -> dict[str, Any]:
    return _heartbeat_payload_builder().build()


def _heartbeat_payload_builder() -> HeartbeatPayloadBuilder:
    return HeartbeatPayloadBuilder(
        lambda: STATE,
        software_identity=cluster_update.software_identity,
        process_started_at=_tracked_process_start,
        process_start_token=_tracked_process_token,
        runtime_status=lambda: _runtime_control().local_status() if getattr(getattr(app.state, 'managed_network', None), 'runtimes', None) else {},
    )


def _tracked_process_start() -> str | None:
    if not STATE.process_pid:
        return None
    for child in runtime.load().children:
        if child.pid == STATE.process_pid and child.start_token and child.started_at:
            return child.started_at
    import psutil

    try:
        return usage.utc(psutil.Process(STATE.process_pid).create_time())
    except (OSError, ValueError, psutil.Error):
        return None


def _tracked_process_token() -> str | None:
    return next(
        (c.start_token for c in runtime.load().children if c.pid == STATE.process_pid), None
    )


def _apply_cluster_snapshot(data: dict[str, Any]) -> None:
    if isinstance(data.get("logging"), dict):
        logging_control.apply(STATE, data["logging"])
    snapshot = data.get("cluster_members")
    if isinstance(snapshot, dict):
        with _worker_launch_lock:
            local = STATE.members.get(STATE.node_id) or {}
            STATE.members = dict(snapshot)
            # Runtime ownership is local, never supplied by a cluster snapshot.
            if STATE.node_id in STATE.members:
                STATE.members[STATE.node_id] = dict(STATE.members[STATE.node_id])
                STATE.members[STATE.node_id].pop('rpc_owner', None)
                for key in ('rpc_owner', 'rpc_status', 'rpc_error', 'rpc_devices', 'rpc_capabilities', 'rpc_control_version'):
                    if key in local:
                        STATE.members[STATE.node_id][key] = local[key]
    if "last_plan" in data:
        STATE.last_plan = data.get("last_plan")
    if "apply_error" in data:
        STATE.apply_error = data.get("apply_error")
    if "apply_warning" in data:
        STATE.apply_warning = data.get("apply_warning")
    if "inference_activity" in data:
        activity = data.get("inference_activity")
        STATE.inference_activity = dict(activity) if isinstance(activity, dict) else None


def _inference_activity() -> dict[str, Any]:
    """Read local master activity or return the master's latest worker mirror."""
    global _activity_sampled_at, _activity_cached
    if STATE.role != "master":
        return STATE.inference_activity or inference_activity.idle()
    with _activity_lock:
        now = time.monotonic()
        if _activity_cached is not None and now - _activity_sampled_at < 0.5:
            return dict(_activity_cached)
        running = bool(STATE.process_pid and STATE.process_role == "master")
        load = load_progress.sample(STATE.infer_port) if running else None
        activity = inference_activity.sample(
            STATE.infer_port,
            running=running,
            load=load,
            client=http_clients.control_client() if running else None,
        )
        STATE.inference_activity = activity
        _activity_cached = dict(activity)
        _activity_sampled_at = now
        return dict(activity)


def _worker_local_leave() -> None:
    """Idle this worker locally without calling unregister (already dropped)."""
    if STATE.process_pid:
        native.stop_process(STATE.process_pid, force=True)
        STATE.process_pid = None
        STATE.process_role = None
        runtime.clear_children()
    _managed_network_before_reset()
    _reset_netsec_state()
    membership_mod.clear_membership_fields(STATE)
    STATE.role = "idle"
    STATE.join_key = None
    STATE.master_host = None
    STATE.gpu_ids = None
    STATE.save()


def _master_control_base() -> str:
    """HTTP base for worker → master control-plane (may be localhost proxy)."""
    if STATE.cp_proxy_port:
        return f"http://127.0.0.1:{int(STATE.cp_proxy_port)}"
    return f"http://{STATE.master_host}:{STATE.master_port}"


def _ensure_master_control_route(master_host: str, master_port: int) -> list[str]:
    """Use direct LAN when possible; otherwise start a localhost TCP proxy.

    Returns netsec-style warnings for the caller to surface.
    """
    from kllm import cp_proxy

    warnings: list[str] = []
    try:
        handle = cp_proxy.ensure_for_master(master_host, master_port)
    except (RuntimeError, OSError) as exc:
        raise HTTPException(502, str(exc)) from exc
    if handle is None:
        STATE.cp_proxy_port = None
        STATE.cp_proxy_pid = None
        return warnings
    STATE.cp_proxy_port = handle.listen_port
    STATE.cp_proxy_pid = handle.pid
    warnings.append(
        f"control-plane via 127.0.0.1:{handle.listen_port} "
        f"(agent Python cannot reach {master_host}:{master_port} on LAN; "
        f"helper={handle.helper})"
    )
    return warnings


def _membership_heartbeat_service() -> MembershipHeartbeatService:
    return MembershipHeartbeatService(
        state=lambda: STATE,
        ensure_control_route=_ensure_master_control_route,
        control_base=_master_control_base,
        heartbeat_payload=_heartbeat_payload,
        control_client=http_clients.control_client,
        mark_unreachable=lambda state: membership_mod.mark_master_unreachable(state),
        clear_unreachable=lambda state: membership_mod.clear_master_unreachable(state),
        worker_local_leave=_worker_local_leave,
        apply_snapshot=_apply_cluster_snapshot,
        member_rpc_host=member_rpc_host,
    )


def _worker_post_heartbeat() -> None:
    _membership_heartbeat_service().post_heartbeat()


_worker_launch_lock = threading.RLock()
_worker_runtime_retry = {"failures": 0, "next_attempt": 0.0}
_WORKER_RUNTIME_RETRY_BASE_S = 15.0
_WORKER_RUNTIME_RETRY_MAX_S = 300.0


def _worker_runtime_healthy() -> bool:
    if STATE.process_role != "worker" or not STATE.process_pid:
        return False
    try:
        return bool(native.process_alive(int(STATE.process_pid)))
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
            STATE.save()
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
    state=lambda: STATE,
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
    state=lambda: STATE,
    stop=_background_stop,
    master_control_base=_master_control_base,
)


def _start_logs_thread() -> None:
    _log_relay_service.start()


def _capacity_service() -> CapacityService:
    return CapacityService(
        state=lambda: STATE,
        list_gpus=native.list_gpus,
        probe_capacity=_probe_capacity_for_cluster,
        resolve_gpu_selection=_resolve_gpu_selection,
        pick_rpc_bind_host=netsec.pick_rpc_bind_host,
        tracked_process_start=_tracked_process_start,
    )


def _build_master_member_record(lan_host: str) -> dict[str, Any]:
    return _capacity_service().master_member_record(lan_host)


def _ensure_master_member(*, persist: bool = False) -> bool:
    return _capacity_service().ensure_master_member(persist=persist)


def _refresh_local_capacity() -> None:
    _capacity_service().refresh_local()


_capacity_refresh_scheduler = CapacityRefreshScheduler(_refresh_local_capacity)


def _require_key(x_kllm_key: str | None = Header(default=None)) -> None:
    if STATE.role != "master":
        return
    if not STATE.join_key:
        raise HTTPException(503, "master has no join key")
    if x_kllm_key != STATE.join_key:
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


def llama_setup(
    body: LlamaSetupBody | None = None,
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Extract the pinned llama.cpp pack from ~/.kllm/models into vendor/bin."""
    return setup_runtime(
        body,
        x_kllm_key,
        state=STATE,
        service=_runtime_setup_service,
    )


def _runtime_setup_service() -> RuntimeSetupService:
    return build_runtime_setup_service(
        state=lambda: STATE,
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
        "role": STATE.role,
        "node_id": STATE.node_id,
        "pid": os.getpid(),
    }
    if getattr(app.state, 'managed_network', None) is not None:
        out['capabilities'] = ['managed-network-v1', 'managed-network-v2', 'rpc-control-v1']
    # Non-secret fingerprint (same as mDNS ``kid``) so Join-by-key can HTTP-probe
    # when Bonjour/mDNS is unavailable on the LAN.
    if STATE.role == "master" and STATE.join_key:
        out["kid"] = discovery.join_key_id(STATE.join_key)
    return out


def discover(timeout: float = 2.0, key: str | None = None) -> dict[str, Any]:
    found = discovery.browse_masters(timeout=timeout, key=key)
    return {
        "masters": [
            {"name": m.name, "host": m.host, "port": m.port, "key_id": m.key_id} for m in found
        ],
    }


def list_models() -> ModelCatalog:
    return _model_handlers().catalog()


def search_models(body: SearchBody) -> dict[str, Any]:
    return _model_handlers().search(body)


def _download_job_runner(source: str, name: str | None) -> None:
    """Compatibility wrapper for the injected model download worker."""
    _model_service.run_download_job(source, name)


def _model_handlers() -> ModelHandlers:
    """Build the HTTP adapter with current state and replaceable download runner."""
    return ModelHandlers(_model_service, lambda: STATE, _download_job_runner)


def download_model(body: DownloadBody) -> DownloadAccepted:
    return _model_handlers().download(body)


def download_progress() -> dict[str, Any]:
    return _model_handlers().progress()


def cancel_download() -> dict[str, Any]:
    return _model_handlers().cancel()


def load_progress_endpoint() -> dict[str, Any]:
    """Active inference load state for the UI (phase machine + RSS fraction)."""
    return load_progress.sample(STATE.infer_port)


_cluster_update_service = ClusterUpdateService(cluster_update, _master_control_base)


def _cluster_update_handlers() -> ClusterUpdateHandlers:
    return ClusterUpdateHandlers(_cluster_update_service, lambda: STATE)


def cluster_update_endpoint(
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Master: fan out update to workers. Worker: accept and spawn detached updater."""
    return _cluster_update_handlers().request(x_kllm_key)


def cluster_update_progress(
    body: UpdateProgressBody,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    """Worker updater → master: report pull/rebuild/rejoin phase."""
    return _cluster_update_handlers().progress(body)


def delete_model(body: DeleteBody) -> ModelDeletion:
    return _model_handlers().delete(body)


async def chat_completions(
    request: Request,
    _: None = Depends(_require_api_token),
) -> Any:
    model_path = None
    if STATE.model_name:
        try:
            model_path = model_ops.resolve_local_model(STATE.model_name)
        except FileNotFoundError:
            model_path = None
    return await gateway.proxy_chat_completions(STATE.infer_port, request, model_path=model_path,
                                                active_config=_active_config())


async def anthropic_messages(
    request: Request,
    _: None = Depends(_require_api_token),
) -> Any:
    model_path = None
    if STATE.model_name:
        try:
            model_path = model_ops.resolve_local_model(STATE.model_name)
        except FileNotFoundError:
            model_path = None
    return await gateway.proxy_anthropic_messages(STATE.infer_port, request, model_path=model_path,
                                                  active_config=_active_config())


async def responses(request: Request, _: None = Depends(_require_api_token)) -> Any:
    return await gateway.proxy_responses(STATE.infer_port, request, active_config=_active_config())


def _active_config() -> dict[str, Any] | None:
    snapshot = STATE.active_config
    if snapshot and STATE.process_role == "master" and snapshot.get("process_pid") == STATE.process_pid:
        return snapshot
    return None


async def response_tokens(request: Request, _: None = Depends(_require_api_token)) -> Any:
    return await gateway.proxy_responses(STATE.infer_port, request, count_tokens=True)


async def inference_models(request: Request, _: None = Depends(_require_api_token)) -> Any:
    return await gateway.served_models(STATE.infer_port, request.app.state.inference_client)


def get_logging() -> dict[str, Any]:
    return logging_control.overview(STATE)


def apply_logging(body: LoggingPolicyBody, x_kllm_key: str | None = Header(default=None)) -> dict[str, Any]:
    if STATE.role != "worker" or not STATE.join_key or x_kllm_key != STATE.join_key:
        raise HTTPException(401, "invalid worker control key")
    logging_control.apply(STATE, body.model_dump())
    return logging_control.local_status(STATE)


async def put_logging(body: LoggingBody, request: Request, _: None = Depends(_require_api_token)) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(409, "change logging on the master dashboard")
    policy = logging_control.change(STATE, body.enabled)

    async def update(node_id: str, member: dict[str, Any]) -> None:
        host = member.get("lan_host") or member.get("host")
        if not host:
            return
        try:
            response = await request.app.state.inference_client.put(
                f"http://{host}:{member.get('control_port') or DEFAULT_CONTROL_PORT}/v2/node/logging",
                json=policy, headers={"X-Kllm-Key": STATE.join_key or ""}, timeout=2)
            if response.status_code == 200 and node_id in STATE.members and STATE.logging_generation == policy["generation"]:
                STATE.members[node_id]["logging_ack"] = response.json()
        except (httpx.HTTPError, ValueError):
            pass  # Heartbeats retry the persisted desired policy.

    # Existing membership traffic is the fallback if a node cannot be reached now.
    await asyncio.gather(*(update(nid, dict(member)) for nid, member in list(STATE.members.items()) if nid != STATE.node_id))
    STATE.save()
    return get_logging()


def _settings_handlers() -> SettingsHandlers:
    return SettingsHandlers(
        lambda: STATE,
        ensure_api_token=ensure_api_token,
        hf_token_status=hf_token_status,
        save_hf_token=save_hf_token,
        config=kllm_config,
        list_gpus=_list_gpus_payload,
        home_dir=home_dir,
        models_dir=models_dir,
        logs_dir=logs_dir,
    )


def get_api_token() -> dict[str, Any]:
    """Return loopback API token (local operator helper)."""
    return _settings_handlers().api_token()


def get_hf_token() -> dict[str, Any]:
    """Whether an HF token is configured. Never returns the secret."""
    return _settings_handlers().hf_token()


def put_hf_token(body: HfTokenBody) -> dict[str, Any]:
    """Save ~/.kllm/hf_token (chmod 600). Empty string deletes the file."""
    return _settings_handlers().save_hf_token(body)


def get_config() -> dict[str, Any]:
    return _settings_handlers().get_config()


def get_gpus() -> dict[str, Any]:
    """Enumerate local GPUs for pre-start selection."""
    return _settings_handlers().get_gpus()


def put_config(body: ConfigBody) -> dict[str, Any]:
    return _settings_handlers().save_config(body)


def get_home() -> dict[str, Any]:
    """Data-directory layout for the System Settings > Data directory panel."""
    return _settings_handlers().home()


def _dashboard_status_service() -> DashboardStatusService:
    return DashboardStatusService(
        state=lambda: STATE,
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
        rpc_lifecycle=_rpc_lifecycle_status,
        runtimes=_runtime_status,
    )


def status() -> dict[str, Any]:
    _sync_master_liveness(shrink=True)
    membership_mod.reconcile_last_plan(STATE)
    return _dashboard_status_service().status()


def _reset_netsec_state() -> None:
    _network_state_service().reset()


def _master_lan_host() -> str:
    mem = STATE.members.get(STATE.node_id) or {}
    return mem.get("lan_host") or mem.get("host") or discovery._local_ip()


def _setup_master_overlay(join_key: str, lan_host: str) -> None:
    """Translate master overlay service errors into the HTTP contract."""
    try:
        _master_overlay_setup_service().setup(join_key, lan_host)
    except ResourceUnavailableError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ExternalServiceError as exc:
        raise HTTPException(500, f"WireGuard overlay failed: {exc}") from exc


def _master_overlay_setup_service() -> MasterOverlaySetupService:
    return MasterOverlaySetupService(
        state=lambda: STATE,
        tooling_available=netsec.wg_tooling_available,
        load_keypair=netsec.load_or_create_keypair,
        next_address=netsec.next_wg_address,
        derive_psk=netsec.derive_wg_psk,
        build_config=netsec.build_wg_conf,
        bring_up=netsec.bring_up_wireguard,
        listen_port=netsec.WG_LISTEN_PORT,
    )


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
            state=lambda: STATE,
            normalize_overlay=netsec.normalize_overlay,
            generate_join_key=generate_join_key,
            pick_rpc_bind_host=netsec.pick_rpc_bind_host,
            select_gpus=_master_gpu_selection,
            build_master_member=_build_master_member_record,
            setup_overlay=_setup_master_overlay,
            advertise=_advertise_master,
        )
    )


def start_master(body: StartBody) -> dict[str, Any]:
    return _master_start_handler().start(body)


def _stop_advertiser() -> None:
    global _advertiser
    if _advertiser:
        _advertiser.stop()
        _advertiser = None


def _unregister_worker() -> None:
    _ensure_master_control_route(STATE.master_host or "", STATE.master_port)
    with httpx.Client(timeout=10.0) as client:
        client.post(
            f"{_master_control_base()}/v2/cluster/nodes/unregister",
            json={"node_id": STATE.node_id},
            headers={"X-Kllm-Key": STATE.join_key or ""},
        )


def _managed_network_before_reset() -> None:
    service = getattr(app.state, 'managed_network', None)
    if service is not None:
        if STATE.role == 'master' and service.rpc_runs is not None:
            service.rpc_runs.stop(lambda: _inference_handlers().stop())
        elif service.worker_rpc is not None:
            service.worker_rpc.reconcile_startup()
        from kllm.managed_network.recovery import before_reset
        service.admission.call(before_reset(service))


def _cluster_lifecycle_handlers() -> ClusterLifecycleHandlers:
    return ClusterLifecycleHandlers(
        state=lambda: STATE,
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


def stop() -> dict[str, str]:
    return _cluster_lifecycle_handlers().stop()


def join_cluster(body: JoinBody) -> dict[str, Any]:
    try:
        return _join_cluster(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"join failed: {exc}") from exc


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
            state=lambda: STATE,
            gpu_selection=_join_gpu_selection,
            probe_capacity=_probe_capacity_for_cluster,
            pick_rpc_bind_host=netsec.pick_rpc_bind_host,
            ensure_master_control_route=_ensure_master_control_route,
            load_or_create_keypair=netsec.load_or_create_keypair,
            software_identity=cluster_update.software_identity,
            build_register_payload=build_register_payload,
            post_register=post_register,
            drop_cp_proxy=_drop_cp_proxy,
            apply_cluster_snapshot=_apply_cluster_snapshot,
            worker_bring_up_wireguard=_worker_bring_up_wg,
            post_worker_heartbeat=_worker_post_heartbeat,
            stop_process=native.stop_process,
            clear_children=runtime.clear_children,
        )
    )


def _join_cluster(body: JoinBody) -> dict[str, Any]:
    # Serialize with the background reconciler so a rejoin and a runtime
    # self-heal cannot start two rpc-servers on the same RPC port.
    with _worker_launch_lock:
        service = getattr(app.state, 'managed_network', None)
        if service and service.worker_rpc:
            service.worker_rpc.reconcile_startup()
            if (STATE.members.get(STATE.node_id) or {}).get('rpc_status') == 'cleanup_pending':
                raise HTTPException(409, 'Reconcile the tracked RPC process locally before joining')
        return _cluster_join_service().join(body)


def _worker_bring_up_wg(join_key: str, reg: dict[str, Any]) -> str:
    return _worker_overlay_setup_service().setup(join_key, reg)


def _worker_overlay_setup_service() -> WorkerOverlaySetupService:
    return WorkerOverlaySetupService(
        state=lambda: STATE,
        load_keypair=netsec.load_or_create_keypair,
        derive_psk=netsec.derive_wg_psk,
        build_config=netsec.build_wg_conf,
        bring_up=netsec.bring_up_wireguard,
        listen_port=netsec.WG_LISTEN_PORT,
    )


def leave() -> dict[str, str]:
    return _cluster_lifecycle_handlers().leave()


def _membership_handlers() -> MembershipHandlers:
    return MembershipHandlers(
        state=lambda: STATE,
        sync_liveness=_sync_master_liveness,
        resources=_usage_runtime().resources,
        resource_probe_available=native.resource_probe_available,
        store=_usage_store,
        master_lan_host=_master_lan_host,
        inference_activity=_inference_activity,
        shrink_cluster=_relaunch_after_membership_change,
    )


def register(
    body: RegisterBody,
    request: Request,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    return _membership_handlers().register(body, request)


def _rewrite_master_wg_peers() -> None:
    _membership_handlers().rewrite_master_wg_peers()


def unregister(body: UnregisterBody, _: None = Depends(_require_key)) -> dict[str, Any]:
    return _membership_handlers().unregister(body)


def heartbeat(
    body: RegisterBody,
    request: Request,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    return _membership_handlers().heartbeat(body, request)


def _usage_ingest_handlers() -> UsageIngestHandlers:
    return UsageIngestHandlers(
        state=lambda: STATE,
        resources=_usage_runtime().resources,
        store=_usage_store,
        assignment=_usage_assignment,
        node_context=_usage_node_context,
        resource_validation_available=native.resource_probe_available,
    )


async def usage_ingest(request: Request, _: None = Depends(_require_key)) -> dict[str, Any]:
    return await _usage_ingest_handlers().ingest(request)


def _proxy_master_usage(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    proxy = UsageHistoryProxy(lambda: STATE, _master_control_base, http_clients.control_client)
    return proxy.get(path, params)


def _usage_history_handlers() -> UsageHistoryHandlers:
    return UsageHistoryHandlers(lambda: STATE, _usage_store, _proxy_master_usage)


def _set_worker_usage_last_save(value: str | None) -> None:
    _usage_runtime().set_worker_last_save(value)


def _set_worker_usage_last_success_at(value: str) -> None:
    _usage_runtime().set_worker_last_success_at(value)


def _usage_live_handlers() -> UsageLiveHandlers:
    return UsageLiveHandlers(
        state=lambda: STATE,
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


def usage_live() -> dict[str, Any]:
    return _usage_live_handlers().live()


def _usage_read(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Compatibility boundary for callers wrapping a one-off usage query."""
    return UsageHistoryHandlers._read(action)


def usage_lifetimes(node_id: str = Query(min_length=1, max_length=128)) -> dict[str, Any]:
    return _usage_history_handlers().lifetimes(node_id)


def usage_series(
    node_id: str = Query(min_length=1, max_length=128),
    lifetime_id: str = Query(min_length=1, max_length=256),
    window: UsageWindow = "15m",
) -> dict[str, Any]:
    return _usage_history_handlers().series(node_id, lifetime_id, window)


def usage_readings(
    node_id: str = Query(min_length=1, max_length=128),
    lifetime_id: str = Query(min_length=1, max_length=256),
    window: UsageWindow = "15m",
    limit: int = Query(default=500, ge=1, le=1000),
    cursor: str | None = Query(default=None, max_length=2048),
) -> dict[str, Any]:
    return _usage_history_handlers().readings(node_id, lifetime_id, window, limit, cursor)


def _log_handlers() -> LogHandlers:
    return LogHandlers(lambda: STATE, cluster_log.HUB, _master_control_base)


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


def ingest_logs(body: LogsIngestBody, _: None = Depends(_require_key)) -> dict[str, Any]:
    return _log_handlers().ingest(body)


def get_logs(
    n: int = 200,
    follow: bool = False,
    node: str | None = None,
    source: str | None = None,
    after: str | None = None,
) -> Any:
    return _log_handlers().read(n, follow, node, source, after, _try_proxy_master_logs)


def _live_coverage() -> str:
    return (
        "cluster"
        if STATE.role == "master"
        else "local_with_cluster_view"
        if STATE.role == "worker"
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
        operation["node_id"] = STATE.node_id
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
    return _operation_records("load", load_progress.records(STATE.infer_port))


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
        if topic == "usage" and STATE.role == "worker"
        else None
    )


def _live_download() -> dict[str, Any]:
    return _operation_records("download", model_ops.download_progress_records())


def _live_logs(after: str | None) -> dict[str, Any]:
    handler = LiveLogsHandler(
        state=lambda: STATE,
        app_state=app.state,
        snapshot=_logs_snapshot,
        proxy=_try_proxy_master_logs,
    )
    return handler.read(after)


def _dashboard_live_service() -> DashboardLiveService:
    return DashboardLiveService(
        app_state=app.state,
        state=lambda: STATE,
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


def dashboard_live(
    request: Request,
    topics: str | None = Query(default=None, max_length=256),
    logs: Literal["0", "1"] = "0",
    after: str | None = Query(default=None, max_length=128),
    continuity_id: str | None = Query(default=None, max_length=64),
    page_origin: str | None = Query(default=None, max_length=512),
    origin: str | None = Header(default=None),
) -> JSONResponse:
    selected, wants_logs, cursor, continuity = _live_request(
        topics, logs, after, continuity_id, page_origin, origin, request
    )
    try:
        payload = _live_feed().snapshot(
            selected, logs=wants_logs, after=cursor, continuity_id=continuity
        )
    except OverflowError:
        raise HTTPException(503, "dashboard snapshot is temporarily unavailable") from None
    except ValueError:
        raise HTTPException(400, "invalid log cursor") from None
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


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


async def dashboard_live_websocket(websocket: WebSocket) -> None:
    await _live_websocket_handler().handle(websocket)


def _maybe_remember(body: PlanBody | RunBody) -> None:
    _run_settings_handlers().remember(body)


def get_model_settings(model: str) -> dict[str, Any]:
    return _run_settings_handlers().get(model)


def run_capabilities(model: str | None = None) -> dict[str, Any]:
    return _run_settings_handlers().capabilities(model)


def validate_chat_template(body: TemplateCheckBody) -> dict[str, Any]:
    try:
        return _template_validation_service().validate(body.text).as_payload()
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ExternalServiceError, ResourceUnavailableError) as exc:
        raise HTTPException(503, str(exc)) from exc


def _template_validation_service() -> TemplateValidationService:
    def resolve_server() -> str | None:
        root = repo_root()
        return native.resolve_llama_server(str(root) if root else None)

    return TemplateValidationService(resolve_server)


def put_model_settings(model: str, body: ModelSettingsBody) -> dict[str, Any]:
    """Persist per-model run settings (context, KV dtype, speculative, ...).

    Validated against ``RunSettingsFields`` so a malformed set is rejected
    with field-level errors instead of poisoning the store. The stored set is
    applied by the dashboard on model selection and merged into plan/run
    payloads — no cluster reload is required for it to take effect.
    """
    return _run_settings_handlers().save(model, body)


def _run_settings_handlers() -> RunSettingsHandlers:
    from kllm.run_settings import available_threads

    return RunSettingsHandlers(
        model_settings_store,
        model_ops.resolve_local_model,
        available_threads,
    )


def _inference_handlers() -> InferenceHandlers:
    service = getattr(app.state, 'managed_network', None)
    return InferenceHandlers(
        InferenceDependencies(
            state=STATE,
            runtime_context=(service.runtimes.operation if service is not None and service.runtimes is not None else None),
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
                STATE, native, runtime, load_progress, inference_activity
            ),
            gpu_visibility_env=_gpu_visibility_env,
            sanitize_extra_args=sanitize_extra_args,
            build_master_launch=build_master_launch,
            spawn_master=lambda launch_config: spawn_master(
                native, runtime, launch_config, ensure_master_alive_after_spawn
            ),
            finish_master_launch=lambda path, plan_result, launched, persist, remember: (
                finish_master_launch(
                    STATE,
                    path,
                    plan_result,
                    launched,
                    runtime,
                    load_progress,
                    persist,
                    remember,
                )
            ),
            stop_managed_inference=lambda: membership_mod._stop_inference(STATE),
            inference_idle=inference_activity.idle,
            network_diagnostics=lambda network_id: _managed_network_service().admission.diagnostics(network_id),
            network_members=lambda network_id: _managed_network_service().admission.members(network_id),
            network_run=lambda network_id: (
                _managed_network_service().admission.context(network_id)
                if getattr(app.state, 'managed_network', None) is not None
                else (_managed_network_service() if network_id else __import__('contextlib').nullcontext(STATE.members))
            ),
            network_stop=lambda: (
                _managed_network_service().admission.stop_context()
                if getattr(app.state, 'managed_network', None) is not None else __import__('contextlib').nullcontext()
            ),
            network_release=lambda: (
                _managed_network_service().admission.call(_managed_network_service().admission.release_stopped(lock_held=True))
                if getattr(app.state, 'managed_network', None) is not None else None
            ),
            rpc_context=(service.rpc_runs.context if service is not None and service.rpc_runs is not None else None),
            network_run_selected=(service.admission.context if service is not None and service.rpc_runs is not None else None),
        )
    )


def _guard_context(body: PlanBody | RunBody) -> None:
    if not body.context_auto and body.context < 512:
        raise HTTPException(
            400, detail={"context": "context must be >= 512 when context_auto is false"}
        )


def plan(body: PlanBody, preview: bool = False) -> dict[str, Any]:
    return _inference_handlers().plan(body, preview)


def run(body: RunBody) -> dict[str, Any]:
    service = getattr(app.state, 'managed_network', None)
    if service is None and getattr(app.state, 'managed_network_error', None):
        raise HTTPException(503, 'Recover signed node/runtime control before starting inference')
    return service.rpc_runs.launch(body) if service is not None and service.rpc_runs is not None else _inference_handlers().run(body)


def legacy_plan(body: LegacyPlanBody, preview: bool = False) -> dict[str, Any]:
    return _inference_handlers().plan(body, preview)


def legacy_run(body: LegacyRunBody) -> dict[str, Any]:
    return run(body)


def legacy_put_model_settings(model: str, body: LegacyModelSettingsBody) -> dict[str, Any]:
    from kllm.run_settings import RunSettingsFields
    from kllm.parameter_catalog import canonicalize_parameters
    values = {key: value for key, value in body.parameter_set.items() if key in RunSettingsFields.model_fields}
    return put_model_settings(model, ModelSettingsBody(parameter_set=canonicalize_parameters(values, legacy=True)))


def stop_inference() -> dict[str, Any]:
    """Stop llama-server and drop the loaded model. The cluster stays up."""
    service = getattr(app.state, 'managed_network', None)
    return service.rpc_runs.stop(lambda: _inference_handlers().stop()) if service is not None and service.rpc_runs is not None else _inference_handlers().stop()


def _mount_dashboard() -> None:
    _mount_dashboard_routes(app, _dashboard_dir())


_route_handlers: dict[str, Callable[..., Any]] = {
    "status": status,
    "get_gpus": get_gpus,
    "get_config": get_config,
    "put_config": put_config,
    "get_home": get_home,
    "llama_setup": llama_setup,
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
    "list_models": list_models,
    "chat_completions": chat_completions,
    "anthropic_messages": anthropic_messages,
    "responses": responses,
    "response_tokens": response_tokens,
    "inference_models": inference_models,
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
legacy_v1_router = _build_v1_router({**_route_handlers, "plan": legacy_plan, "run": legacy_run,
                                  "put_model_settings": legacy_put_model_settings})
app.include_router(legacy_v1_router)
app.include_router(_build_v2_router(_route_handlers))
app.include_router(build_network_router(_managed_network_service))
app.include_router(build_runtime_router(lambda: _runtime_registry(), lambda: _runtime_control(), lambda: STATE))
_mount_dashboard()


def create_app() -> FastAPI:
    return app
