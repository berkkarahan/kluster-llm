from __future__ import annotations

import os
import asyncio
import json
import logging
import math
import sqlite3
import ipaddress
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
import httpx
from kllm import http_clients, rpc_cache
import secrets
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import iterate_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
from kllm.accel import allowed_accel_backends
from kllm.auth import ensure_api_token, verify_api_token
from kllm.hf_token import hf_token_status, save_hf_token
from kllm import model_settings as model_settings_store
from kllm.extra_args import sanitize_extra_args
from kllm import cluster_log
from kllm.agent.live import LiveFeed, SourceDenied, TOPICS as LIVE_TOPICS, server_time as live_server_time
from kllm.agent.join_flow import (
    build_register_payload,
    cached_llama_pack_warning,
    post_register,
    require_join_overlay,
    resolve_master_endpoint,
)
from kllm.agent.live_ws import (
    apply_upstream_log_batch,
    log_result_signature,
    new_worker_log_state,
    worker_log_snapshot,
)
from kllm.agent.run_route import finish_master_launch, run_response, spawn_master, stop_previous_master
from kllm.run_planning import (
    build_master_launch,
    ensure_master_alive_after_spawn,
    ensure_rpc_peers_reachable,
    execute_plan,
    member_rpc_host,
    merge_and_sanitize_extra,
    validate_vision_for_run,
)
from kllm.run_settings import PlanBody, RunBody
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
_usage_lock = threading.Lock()
_activity_lock = threading.Lock()
_activity_sampled_at = 0.0
_activity_cached: dict[str, Any] | None = None
_worker_usage_error: str | None = None
_resources = usage.ResourceState()
_resource_remote: dict[str, Any] = {}
_worker_usage_last_save: str | None = None
_worker_usage_last_success_at: str | None = None


def _usage_store() -> usage.UsageStore:
    with _usage_lock:
        path = home_dir() / "usage.sqlite3"
        store = getattr(app.state, "usage_store", None)
        if store is None or store.path != path:
            if store is not None:
                store.close()
            store = usage.UsageStore(path)
            app.state.usage_store = store
        return store


def _usage_assignment(node_id: str) -> str | None:
    if node_id == STATE.node_id and STATE.process_pid and STATE.process_role == "master":
        return STATE.model_name
    plan = membership_mod.public_last_plan(STATE) or {}
    if (node_id in (plan.get("node_ids") or []) or any(
            allocation.get("node_id") == node_id for allocation in (plan.get("device_allocations") or [])
            if isinstance(allocation, dict))):
        return STATE.model_name
    return None


def _usage_node_context(member: dict[str, Any], model: str | None) -> dict[str, Any]:
    devices = []
    for device in (member.get("devices") or [])[:32]:
        if isinstance(device, dict):
            devices.append({"index": device.get("index") if isinstance(device.get("index"), int) else len(devices),
                            "name": str(device.get("name") or "device")[:80],
                            "vram_bytes": device.get("vram_bytes") if isinstance(device.get("vram_bytes"), int) else None})
    return {"backend": str(member.get("actual_backend") or member.get("backend") or "unknown")[:80],
            "devices": devices, "unified_memory": bool(member.get("unified_memory")),
            "total_ram_bytes": member.get("total_ram_bytes"), "vram_bytes": member.get("vram_bytes"),
            "assignment": model[:160] if model else None}


def _publish_resources(snapshot: dict[str, Any]) -> None:
    if STATE.role == "master":
        with _resources.lock:
            _resources.latest[STATE.node_id] = snapshot
            _resources.received[STATE.node_id] = time.monotonic()
        return
    if STATE.role != "worker" or not STATE.master_host or not STATE.join_key:
        return
    base = _master_control_base()
    session_key = (base, STATE.node_id, STATE.join_key)
    client = http_clients.control_client()
    headers = {"X-Kllm-Key": STATE.join_key}
    if _resource_remote.get("key") != session_key:
        _resource_remote.clear()
        _resource_remote["key"] = session_key
    if "supported" not in _resource_remote or time.monotonic() - _resource_remote.get("capability_checked_at", 0) >= 30:
        response = client.get(f"{base}/v1/usage/live", headers=headers, timeout=1.5)
        response.raise_for_status()
        versions = response.json().get("resource_schema_versions", [])
        if not isinstance(versions, list) or len(versions) > 8 or any(type(v) is not int for v in versions):
            raise ValueError("invalid resource capability response")
        _resource_remote["supported"] = 1 in versions
        _resource_remote["capability_checked_at"] = time.monotonic()
    if not _resource_remote["supported"]:
        return
    process_identity = (STATE.process_pid, _tracked_process_token())
    if _resource_remote.get("process_identity") != process_identity:
        heartbeat_response = client.post(f"{base}/v1/heartbeat", json=_heartbeat_payload(), headers=headers, timeout=1.5)
        heartbeat_response.raise_for_status()
        if not heartbeat_response.json().get("accepted"):
            raise RuntimeError("resource publisher requires worker registration")
        _resource_remote["process_identity"] = process_identity
    if _resource_remote.get("source_generation") != snapshot["generation"] or (_resource_remote.get("sequence", 0) >= 9007199254740991 and _resource_remote.get("last_snapshot_id") != snapshot["snapshot_id"]):
        _resource_remote.pop("generation", None)
        _resource_remote["source_generation"] = snapshot["generation"]
    if not _resource_remote.get("generation"):
        previous = None
        for _attempt in range(2):
            request_id = uuid.uuid4().hex
            request = dict(_heartbeat_payload(), resource_session=dict(request_id=request_id, previous_generation=previous))
            response = client.post(f"{base}/v1/register", json=request, headers=headers, timeout=1.5)
            if response.status_code == 409:
                previous = response.json().get("detail", {}).get("resource_generation")
                continue
            response.raise_for_status()
            ack = response.json()
            generation = ack.get("resource_generation")
            if ack.get("request_id") != request_id or not isinstance(generation, str) or len(generation) != 32 or any(c not in "0123456789abcdef" for c in generation):
                raise ValueError("resource session acknowledgement mismatch")
            _resource_remote.update(generation=generation, sequence=0, last_snapshot_id=None)
            break
        if not _resource_remote.get("generation"):
            raise RuntimeError("resource session conflict; retrying next sample")
    if _resource_remote.get("last_snapshot_id") != snapshot["snapshot_id"]:
        _resource_remote["sequence"] += 1
        _resource_remote["last_snapshot_id"] = snapshot["snapshot_id"]
    outgoing = dict(snapshot, generation=_resource_remote["generation"], sequence=_resource_remote["sequence"])
    outgoing["snapshot_id"] = f"{outgoing['generation']}:{outgoing['sequence']}"
    response = client.post(f"{base}/v1/usage/ingest", json=dict(node_id=STATE.node_id, readings=[], resources=outgoing), headers=headers, timeout=1.5)
    if response.status_code == 409:
        _resource_remote.pop("generation", None)
    response.raise_for_status()


def _usage_loop() -> None:
    global _worker_usage_error, _worker_usage_last_save
    sampler = usage.Sampler()
    if hasattr(sampler, "_resource_probe") and native.resource_probe_available() and sys.platform in ("win32", "darwin"):
        sampler._resource_probe = native.probe_resources
    needs_end = True
    while not _background_stop.wait(2):
        pid = STATE.process_pid
        try:
            if hasattr(sampler, "_resource_probe"):
                legacy_master = STATE.role == "worker" and _resource_remote.get("supported") is False and time.monotonic() - _resource_remote.get("capability_checked_at", 0) < 30
                use_native = native.resource_probe_available() and sys.platform in ("win32", "darwin") and not legacy_master
                if sampler._resource_probe is not None and not use_native:
                    native.close_resource_probes()
                sampler._resource_probe = native.probe_resources if use_native else None
                if not use_native:
                    sampler.resources = None
            point = sampler.sample(STATE)
            if getattr(sampler, "resources", None) is not None:
                ended = {(p["pid"], p["start_token"]) for p in sampler.resources["processes"] if p["state"] == "exited"}
                if ended:
                    tracked = runtime.load()
                    if STATE.process_pid == pid and any((c.pid, c.start_token) in ended for c in tracked.children):
                        tracked.children = [c for c in tracked.children if (c.pid, c.start_token) not in ended]
                        runtime.save(tracked)
                        STATE.process_pid = STATE.process_role = None
                        member = STATE.members.get(STATE.node_id)
                        if member is not None:
                            member.update(process_pid=None, process_role=None, process_started_at=None, process_start_token=None)
                        STATE.save()
                    sampler.resources = dict(sampler.resources, processes=[p for p in sampler.resources["processes"] if p["state"] != "exited"])
                _publish_resources(sampler.resources)
            if point is None:
                if STATE.role == "worker":
                    _worker_usage_error = None
                if needs_end and STATE.role == "master" and not STATE.process_pid:
                    _usage_store().end_node(STATE.node_id)
                    needs_end = False
                continue
            needs_end = True
            if STATE.role == "master":
                member = STATE.members.get(STATE.node_id)
                if member is not None:
                    member.update(process_pid=point["pid"], process_role=point["process_role"],
                                  process_started_at=point["process_started_at"])
                _usage_store().record(STATE.node_id, (member or {}).get("host") or STATE.node_id,
                                      point, expected_pid=STATE.process_pid,
                                      expected_start=point["process_started_at"], expected_role="master",
                                      model=STATE.model_name,
                                      context=_usage_node_context(member or {}, _usage_assignment(STATE.node_id)))
            elif STATE.role == "worker" and STATE.master_host and STATE.join_key:
                resp = http_clients.control_client().post(
                    f"{_master_control_base()}/v1/usage/ingest",
                    json={"node_id": STATE.node_id, "readings": [point]},
                    headers={"X-Kllm-Key": STATE.join_key}, timeout=1.5)
                if resp.status_code != 200:
                    raise RuntimeError(f"usage ingest {resp.status_code}")
                try:
                    _worker_usage_last_save = resp.json().get("last_successful_save") or _worker_usage_last_save
                except (ValueError, AttributeError):
                    pass
                _worker_usage_error = None
        except Exception as exc:
            if STATE.role == "worker":
                _resource_remote.clear()
            if isinstance(exc, (usage.psutil.NoSuchProcess, usage.psutil.ZombieProcess)) and STATE.process_pid == pid:
                if STATE.role == "master":
                    try:
                        _usage_store().end_node(STATE.node_id)
                    except (OSError, sqlite3.Error):
                        pass
                STATE.process_pid = None
                STATE.process_role = None
                member = STATE.members.get(STATE.node_id)
                if member is not None:
                    member.update(process_pid=None, process_role=None, process_started_at=None)
                STATE.save()
                needs_end = False
            if STATE.role == "master":
                try:
                    store = _usage_store()
                    if store.recording != "failed":
                        store.recording = "interrupted"
                        store.error = f"collection interrupted: {type(exc).__name__}"
                except (OSError, sqlite3.Error):
                    pass
            elif STATE.role == "worker":
                _worker_usage_error = f"usage collection/ingest interrupted ({type(exc).__name__}); check private master connection and managed child"

async def _sample_diagnostics():
    # Optional debug module; absent unless KLLM_DEBUG_HARDWARE is set. Kept lazy so a normal start does not import it.
    from kllm._dbg_a1ff77 import gpu_snapshot, llama_props, write
    while True:
        def sample():
            write("hardware", "background", "device snapshot",
                  {"gpu": gpu_snapshot(STATE.process_pid), "llama_props": llama_props(STATE.infer_port)})
        await asyncio.to_thread(sample)
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app):
    global _worker_usage_error, _worker_usage_last_save, _worker_usage_last_success_at
    _worker_usage_error = None
    _worker_usage_last_save = None
    _worker_usage_last_success_at = None
    with _resources.lock:
        _resources.sessions.clear()
        _resources.latest.clear()
        _resources.received.clear()
    _resource_remote.clear()
    http_clients.control_client()
    try:
        _usage_store()
    except (OSError, sqlite3.Error):
        pass  # The private history API reports 503; agent/inference must still start.
    _background_stop.clear()
    if sys.platform in ("win32", "darwin"):
        runtime.adopt_child_identity(STATE.process_pid, (STATE.members.get(STATE.node_id) or {}).get("process_started_at"))
    _ensure_master_member(persist=True)
    app.state.live_feed = _new_live_feed()
    usage_thread = None
    if "pytest" not in sys.modules:
        _start_membership_thread()
        _start_logs_thread()
        usage_thread = threading.Thread(target=_usage_loop, name="kllm-usage", daemon=True)
        usage_thread.start()
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5), trust_env=False) as client:
        app.state.inference_client = client
        sampler = asyncio.create_task(_sample_diagnostics()) if os.getenv("KLLM_DEBUG_HARDWARE", "").lower() in ("1", "true", "yes") else None
        try:
            yield
        finally:
            _background_stop.set()
            if usage_thread is not None:
                await asyncio.to_thread(native.close_resource_probes)
                await asyncio.to_thread(usage_thread.join, 4)
            if sampler:
                sampler.cancel()
                await asyncio.gather(sampler, return_exceptions=True)
            http_clients.close()
            with _usage_lock:
                store = getattr(app.state, "usage_store", None)
                if store is not None:
                    store.close()
                    del app.state.usage_store
            if hasattr(app.state, "live_feed"):
                await asyncio.to_thread(app.state.live_feed.stop)
                del app.state.live_feed


app = FastAPI(title="kllm-agent", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def _plan_run_validation_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    for error in errors:
        value = error.get("input")
        if isinstance(value, float) and not math.isfinite(value):
            error["input"] = str(value)
    if request.url.path in ("/v1/plan", "/v1/run"):
        return JSONResponse(status_code=400, content={"detail": jsonable_encoder(errors)})
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

_advertiser: discovery.MasterAdvertiser | None = None
STATE = ClusterState.load()
membership_mod.reconcile_last_plan(STATE)
if not STATE.node_id:
    STATE.node_id = secrets.token_hex(4)
    STATE.save()


def _refresh_persisted_netsec_warnings() -> None:
    """Drop stale firewall warnings after agent reload (e.g. code upgrade)."""
    if STATE.role not in ("master", "worker"):
        return
    warnings: list[str] = []
    bind, bind_warn = netsec.reconcile_rpc_bind(
        STATE.rpc_bind,
        toward=STATE.master_host,
        overlay=STATE.overlay or netsec.OVERLAY_NONE,
    )
    if bind_warn:
        STATE.rpc_bind = bind
        warnings.append(bind_warn)
    bind = STATE.rpc_bind
    port = STATE.rpc_port or DEFAULT_RPC_PORT
    if bind:
        warnings.extend(netsec.refresh_rpc_firewall_warnings(port, bind))
    peer = STATE.master_host if STATE.role == "worker" else None
    if STATE.role == "master":
        for member in STATE.members.values():
            if (member or {}).get("role") == "master":
                continue
            peer = (member or {}).get("lan_host") or (member or {}).get("host")
            if peer:
                break
    wifi = netsec.wifi_path_warning(peer)
    if wifi:
        warnings.append(wifi)
    STATE.netsec_warnings = warnings
    STATE.save()


_refresh_persisted_netsec_warnings()
# Ensure loopback inference token exists for harness connect
ensure_api_token()

_membership_lock = threading.Lock()
_membership_maintenance_lock = threading.Lock()
_membership_pending: dict[str, tuple[bool, tuple[str, str | None]]] = {}
_membership_maintenance_inflight = False
_latency_probe_lock = threading.Lock()
_latency_probe_inflight = False
_membership_thread_started = False
_logs_thread_started = False
_capacity_refresh_lock = threading.Lock()
_capacity_refresh_inflight = False


def _persist_last_settings(body: PlanBody | RunBody) -> None:
    data = body.model_dump()
    data.pop("tensor_split", None)
    STATE.last_settings = data


def _sync_master_liveness(*, shrink: bool = True) -> list[str]:
    """Sweep stale workers. Optionally replan-and-apply on shrink (master only)."""
    global _membership_maintenance_inflight
    if STATE.role != "master":
        return []
    with _membership_lock:
        dropped = membership_mod.sweep_liveness(STATE)
    if not dropped:
        return dropped
    with _membership_maintenance_lock:
        identity = (STATE.node_id, STATE.join_key)
        for node_id in dropped:
            previous = _membership_pending.get(node_id)
            _membership_pending[node_id] = (shrink or bool(previous and previous[0] and previous[1] == identity), identity)
        if _membership_maintenance_inflight:
            return dropped
        _membership_maintenance_inflight = True

    def maintain() -> None:
        global _membership_maintenance_inflight
        try:
            while True:
                with _membership_maintenance_lock:
                    pending = dict(_membership_pending)
                    _membership_pending.clear()
                    if not pending:
                        return
                try:
                    identity = (STATE.node_id, STATE.join_key)
                    if STATE.role != "master":
                        continue
                    removed = [node_id for node_id, (_, expected) in pending.items()
                               if expected == identity and node_id not in STATE.members]
                    if not removed:
                        continue
                    try:
                        store = _usage_store()
                        for node_id in removed:
                            if STATE.role != "master" or (STATE.node_id, STATE.join_key) != identity:
                                break
                            if node_id not in STATE.members:
                                store.end_node(node_id, state="unobserved")
                    except (OSError, sqlite3.Error) as exc:
                        try:
                            store = _usage_store()
                            store.recording = "failed"
                            reason = "disk full" if "disk full" in str(exc).lower() else "storage unavailable"
                            store.error = f"usage lifetime close failed: {reason}"
                        except (OSError, sqlite3.Error):
                            pass
                    if STATE.role != "master" or (STATE.node_id, STATE.join_key) != identity:
                        continue
                    STATE.save()
                    if any(pending[node_id][0] and node_id not in STATE.members for node_id in removed):
                        membership_mod.on_membership_shrink(STATE)
                except Exception as exc:
                    logging.getLogger(__name__).warning("membership maintenance failed (%s)", type(exc).__name__)
        finally:
            with _membership_maintenance_lock:
                restart = bool(_membership_pending)
                _membership_maintenance_inflight = restart
            if restart:
                threading.Thread(target=maintain, name="kllm-membership-maintenance", daemon=True).start()

    threading.Thread(target=maintain, name="kllm-membership-maintenance", daemon=True).start()
    return dropped


def _heartbeat_payload() -> dict[str, Any]:
    mem = STATE.members.get(STATE.node_id) or {}
    identity = cluster_update.software_identity()
    return {
        "node_id": STATE.node_id,
        "host": STATE.rpc_bind or mem.get("host") or mem.get("lan_host") or "",
        "control_port": STATE.control_port,
        "rpc_port": STATE.rpc_port,
        "total_ram_bytes": mem.get("total_ram_bytes") or 0,
        "available_ram_bytes": mem.get("available_ram_bytes") or 0,
        "vram_bytes": mem.get("vram_bytes"),
        "backend": mem.get("backend") or "unknown",
        **{key: mem.get(key) for key in ("actual_backend", "requested_backend", "devices", "unified_memory", "binary_version", "reclaimable_ram_bytes") if mem.get(key) is not None},
        "lan_host": mem.get("lan_host"),
        "wg_public_key": STATE.wg_public_key,
        "gpu_ids": STATE.gpu_ids,
        "git_head": identity.get("git_head"),
        "llama_release": identity.get("llama_release"),
        "process_pid": STATE.process_pid,
        "process_role": STATE.process_role,
        "process_started_at": _tracked_process_start(),
        "process_start_token": _tracked_process_token(),
    }


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
    return next((c.start_token for c in runtime.load().children if c.pid == STATE.process_pid), None)


def _apply_cluster_snapshot(data: dict[str, Any]) -> None:
    snapshot = data.get("cluster_members")
    if isinstance(snapshot, dict):
        STATE.members = dict(snapshot)
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
        activity = inference_activity.sample(STATE.infer_port, running=running, load=load)
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
    except RuntimeError as exc:
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


def _worker_post_heartbeat() -> None:
    if STATE.role != "worker" or not STATE.master_host or not STATE.join_key:
        return

    # Re-ensure proxy if the helper died across agent restarts.
    try:
        _ensure_master_control_route(STATE.master_host, STATE.master_port)
    except HTTPException:
        membership_mod.mark_master_unreachable(STATE)
        return

    try:
        resp = http_clients.control_client().post(
            f"{_master_control_base()}/v1/heartbeat", json=_heartbeat_payload(),
            headers={"X-Kllm-Key": STATE.join_key}, timeout=5.0)
    except Exception:
        membership_mod.mark_master_unreachable(STATE)
        return
    if resp.status_code != 200:
        membership_mod.mark_master_unreachable(STATE)
        return
    try:
        data = resp.json()
    except Exception:
        membership_mod.mark_master_unreachable(STATE)
        return
    membership_mod.clear_master_unreachable(STATE)
    snapshot = data.get("cluster_members")
    accepted = data.get("accepted")
    omitted = isinstance(snapshot, dict) and STATE.node_id not in snapshot
    if accepted is False or omitted:
        _worker_local_leave()
        return
    _apply_cluster_snapshot(data)
    STATE.save()


_LATENCY_EMA_ALPHA = 0.3


def _probe_worker_latencies() -> None:
    """Master: measure control-plane RTT to workers (EMA). Does not clobber on failure."""
    if STATE.role != "master":
        return
    master_id = STATE.node_id
    if master_id in STATE.members:
        STATE.members[master_id]["latency_ms"] = 0.0

    for nid, member in list(STATE.members.items()):
        if nid == master_id or (member or {}).get("role") == "master":
            continue
        raw = member or {}
        if not raw.get("host") and not raw.get("lan_host"):
            continue
        host = raw.get("lan_host") or member_rpc_host(raw)
        port = int(raw.get("control_port") or DEFAULT_CONTROL_PORT)
        url = f"http://{host}:{port}/health"
        try:
            client = http_clients.control_client()
            start = time.perf_counter()
            resp = client.get(url, timeout=1.0)
            if resp.status_code != 200:
                continue
            sample_ms = (time.perf_counter() - start) * 1000.0
        except Exception:
            continue
        prev = raw.get("latency_ms")
        if prev is None:
            smoothed = sample_ms
        else:
            smoothed = _LATENCY_EMA_ALPHA * sample_ms + (1.0 - _LATENCY_EMA_ALPHA) * float(prev)
        STATE.members[nid]["latency_ms"] = smoothed


def _tick_membership() -> None:
    if STATE.role == "master":
        _sync_master_liveness(shrink=True)
        _schedule_worker_latency_probe()
    elif STATE.role == "worker":
        _worker_post_heartbeat()
    _schedule_local_capacity_refresh()


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
    global _capacity_refresh_inflight
    with _capacity_refresh_lock:
        if _capacity_refresh_inflight:
            return
        _capacity_refresh_inflight = True

    def refresh() -> None:
        global _capacity_refresh_inflight
        try:
            _refresh_local_capacity()
        finally:
            with _capacity_refresh_lock:
                _capacity_refresh_inflight = False

    threading.Thread(
        target=refresh,
        name="kllm-capacity-refresh",
        daemon=True,
    ).start()


def _membership_loop() -> None:
    while not _background_stop.wait(membership_mod.HEARTBEAT_INTERVAL_S):
        try:
            _tick_membership()
        except Exception:
            pass


def _start_membership_thread() -> None:
    global _membership_thread_started
    if _membership_thread_started:
        return
    _membership_thread_started = True
    threading.Thread(target=_membership_loop, name="kllm-membership", daemon=True).start()


def _logs_loop() -> None:
    cluster_log.HUB.prime_offsets()
    while not _background_stop.wait(cluster_log.FLUSH_INTERVAL_S):
        try:
            if STATE.role == "master":
                cluster_log.HUB.ensure_journal_loaded()
            master_base = None
            if STATE.role == "worker" and STATE.master_host:
                master_base = _master_control_base()
            cluster_log.HUB.relay_tick(
                node_id=STATE.node_id or "unknown",
                role=STATE.role,
                master_base=master_base,
                join_key=STATE.join_key,
            )
        except Exception:
            pass


def _start_logs_thread() -> None:
    global _logs_thread_started
    if _logs_thread_started:
        return
    _logs_thread_started = True
    threading.Thread(target=_logs_loop, name="kllm-logs", daemon=True).start()


def _build_master_member_record(lan_host: str) -> dict[str, Any]:
    listed = native.list_gpus()
    gpu_sel = _resolve_gpu_selection(STATE.gpu_ids, listed=listed)
    cap = _probe_capacity_for_cluster()
    vram = gpu_sel["vram_bytes"] if gpu_sel["gpus"] else cap.get("vram_bytes")
    backend = gpu_sel["backend"] if gpu_sel["gpus"] or gpu_sel["backend"] != "cpu" else cap.get("backend", "cpu")
    return {
        "node_id": STATE.node_id,
        "host": lan_host,
        "lan_host": lan_host,
        "control_port": STATE.control_port,
        "rpc_port": STATE.rpc_port,
        "role": "master",
        "total_ram_bytes": cap["total_ram_bytes"],
        "available_ram_bytes": cap["available_ram_bytes"],
        "vram_bytes": vram,
        "backend": backend,
        "actual_backend": backend,
        "requested_backend": cap.get("requested_backend"),
        "devices": gpu_sel["gpus"],
        "unified_memory": cap.get("unified_memory", False),
        "binary_version": cap.get("binary_version"),
        "gpu_ids": STATE.gpu_ids,
        "last_seen": time.time(),
        "latency_ms": 0.0,
        "process_pid": STATE.process_pid,
        "process_role": STATE.process_role,
        "process_started_at": _tracked_process_start(),
    }


def _ensure_master_member(*, persist: bool = False) -> bool:
    """Reinsert the local master when persisted membership lost the master row."""
    if STATE.role != "master":
        return False
    nid = STATE.node_id
    mem = STATE.members.get(nid)
    if mem is not None and mem.get("role") == "master":
        return False
    try:
        lan_host = (
            (mem or {}).get("lan_host")
            or (mem or {}).get("host")
            or STATE.rpc_bind
            or netsec.pick_rpc_bind_host(None)
        )
        record = _build_master_member_record(str(lan_host))
        if mem:
            for key in ("wg_address", "wg_public_key"):
                if mem.get(key) is not None:
                    record[key] = mem[key]
            if STATE.overlay == netsec.OVERLAY_WIREGUARD and mem.get("host"):
                record["host"] = mem["host"]
        STATE.members[nid] = record
        if persist:
            STATE.save()
        return True
    except HTTPException:
        return False
    except Exception:
        return False


def _refresh_local_capacity() -> None:
    _ensure_master_member(persist=False)
    member = STATE.members.get(STATE.node_id)
    if member is None:
        return
    try:
        cap = native.probe_capacity()
        selected = _resolve_gpu_selection(STATE.gpu_ids)
        member.update({k: cap[k] for k in ("total_ram_bytes", "available_ram_bytes", "requested_backend", "unified_memory", "binary_version") if k in cap})
        member.update(backend=selected["backend"], actual_backend=selected["backend"], devices=selected["gpus"],
                      vram_bytes=selected["vram_bytes"])
        # RSS is measured from our own tracked child, never inferred from total RAM usage.
        member["reclaimable_ram_bytes"] = 0
        if STATE.process_pid:
            import psutil
            process = psutil.Process(STATE.process_pid)
            if process.name().lower() in ("llama-server", "llama-server.exe", "rpc-server", "rpc-server.exe"):
                member["reclaimable_ram_bytes"] = process.memory_info().rss
    except Exception:
        pass


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


class StartBody(BaseModel):
    key: str | None = None
    control_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    rpc_bind: str | None = None
    overlay: str = netsec.OVERLAY_NONE
    gpu_ids: list[int] | None = None


class JoinBody(BaseModel):
    key: str
    master_host: str | None = None
    master_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    rpc_bind: str | None = None
    overlay: str = netsec.OVERLAY_NONE
    gpu_ids: list[int] | None = None


class RegisterBody(BaseModel):
    node_id: str
    host: str
    control_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    total_ram_bytes: int = 0
    available_ram_bytes: int = 0
    vram_bytes: int | None = None
    backend: str = "unknown"
    actual_backend: str | None = None
    requested_backend: str | None = None
    devices: list[dict[str, Any]] = Field(default_factory=list)
    unified_memory: bool = False
    binary_version: str | None = None
    reclaimable_ram_bytes: int = 0
    lan_host: str | None = None
    wg_public_key: str | None = None
    wg_listen_port: int = netsec.WG_LISTEN_PORT
    gpu_ids: list[int] | None = None
    git_head: str | None = None
    llama_release: str | None = None
    process_pid: int | None = None
    process_role: str | None = None
    process_started_at: str | None = None
    process_start_token: str | None = None
    resource_session: dict[str, Any] | None = None


class UpdateProgressBody(BaseModel):
    node_id: str | None = None
    phase: str
    error: str | None = None
    git_head: str | None = None
    llama_release: str | None = None
    rebuild: str | None = None
    host: str | None = None


class LogLineBody(BaseModel):
    event_id: uuid.UUID | None = None
    ts: float | None = Field(default=None, allow_inf_nan=False)
    source: str = "agent"
    line: str


class LogsIngestBody(BaseModel):
    node_id: str
    lines: list[LogLineBody] = Field(default_factory=list)


def _probe_capacity_for_cluster() -> dict[str, Any]:
    """Probe host capacity; map missing native runtime to a clear HTTP error."""
    try:
        return native.probe_capacity()
    except RuntimeError as exc:
        raise HTTPException(
            503,
            f"{exc} On this machine run: {native.install_recovery_command()}",
        ) from exc


def _gpu_inventory(listed: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    listed = listed or native.list_gpus()
    gpus = list(listed.get("gpus") or [])
    available = {int(g["index"]): g for g in gpus}
    return listed, available


def _membership_gpu_ids(
    body_gpu_ids: list[int] | None,
    *,
    body_sent: bool,
    listed: dict[str, Any] | None = None,
) -> list[int] | None:
    """Resolve gpu_ids for start/join; ignore stale config when inventory is empty."""
    _, available = _gpu_inventory(listed)
    if available:
        if body_sent or body_gpu_ids is not None:
            return body_gpu_ids
        return kllm_config.get_gpu_ids()
    if body_sent:
        return body_gpu_ids
    return None


def _empty_gpu_inventory_detail(gpu_ids: list[int], listed: dict[str, Any]) -> str:
    detail = f"no GPUs available; cannot select {gpu_ids}"
    probe_err = listed.get("error")
    if probe_err:
        detail += f" ({probe_err})"
    detail += f". On this machine run: {native.install_recovery_command()}"
    return detail


def _resolve_gpu_selection(
    gpu_ids: list[int] | None,
    *,
    listed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate optional gpu_ids against list_gpus(); default = all indices.

    Returns dict with keys: gpu_ids, backend, gpus (selected device dicts),
    vram_bytes (sum of selected discrete VRAM, or None).
    """
    listed, available = _gpu_inventory(listed)
    backend = listed.get("backend") or "cpu"

    if not available:
        if gpu_ids:
            raise HTTPException(503, _empty_gpu_inventory_detail(gpu_ids, listed))
        return {
            "gpu_ids": [],
            "backend": backend,
            "gpus": [],
            "vram_bytes": None,
        }

    if gpu_ids is None:
        selected_ids = sorted(available.keys())
    else:
        if not gpu_ids:
            raise HTTPException(400, "gpu_ids must be non-empty when provided")
        unknown = [i for i in gpu_ids if i not in available]
        if unknown:
            raise HTTPException(
                400,
                f"unknown gpu_ids {unknown}; available={sorted(available.keys())}",
            )
        # Preserve order, drop duplicates
        seen: set[int] = set()
        selected_ids = []
        for i in gpu_ids:
            if i not in seen:
                seen.add(i)
                selected_ids.append(i)

    selected = [available[i] for i in selected_ids]
    vram_total: int | None = None
    for g in selected:
        v = g.get("vram_bytes")
        if v is not None:
            vram_total = (vram_total or 0) + int(v)
    return {
        "gpu_ids": selected_ids,
        "backend": backend,
        "gpus": selected,
        "vram_bytes": vram_total,
    }


def _gpu_visibility_env(backend: str, gpu_ids: list[int] | None) -> dict[str, str]:
    """Env vars so the engine only sees the selected physical GPUs."""
    env: dict[str, str] = {}
    if gpu_ids:
        csv = ",".join(str(i) for i in gpu_ids)
        if backend == "cuda":
            env = {"CUDA_VISIBLE_DEVICES": csv}
        elif backend == "vulkan":
            env = {"GGML_VK_VISIBLE_DEVICES": csv}
        elif backend == "rocm":
            env = {
                "HIP_VISIBLE_DEVICES": csv,
                "ROCR_VISIBLE_DEVICES": csv,
            }
    if os.getenv("KLLM_DEBUG_HARDWARE", "").lower() in ("1", "true", "yes"):
        try:
            # Optional debug module; import only when the hardware debug flag is set.
            from kllm._dbg_a1ff77 import write as _dbg_write

            _dbg_write(
                "H1",
                "agent/__init__.py:_gpu_visibility_env",
                "gpu visibility env",
                {"backend": backend, "gpu_ids": gpu_ids, "env": env},
            )
        except Exception:
            pass

    return env


class ConfigBody(BaseModel):
    device: Literal["cpu", "gpu"] = Field(default="gpu")
    gpu_ids: list[int] | None = None
    vram_percent: int | None = Field(default=None, ge=10, le=100)
    ram_percent: int | None = Field(default=None, ge=10, le=100)
    accel_backend: str | None = None
    split_mode: Literal["layer", "row", "tensor"] | None = None


def _list_gpus_payload() -> dict[str, Any]:
    """Enumerate GPUs with effective + probed backend and UI allowed list."""
    try:
        listed = native.list_gpus()
    except Exception as exc:
        return {
            "backend": "cpu",
            "probed_backend": "cpu",
            "allowed_backends": [],
            "gpus": [],
            "error": str(exc),
            "install_hint": native.install_recovery_command(),
        }
    gpus = listed.get("gpus") or []
    probed = str(listed.get("probed_backend") or listed.get("backend") or "cpu").strip().lower()
    backend = str(listed.get("backend") or probed).strip().lower()
    out: dict[str, Any] = {
        "backend": backend,
        "probed_backend": probed,
        "allowed_backends": listed.get("allowed_backends", allowed_accel_backends(probed, gpus)),
        "requested_backend": listed.get("requested_backend"),
        "actual_backend": listed.get("actual_backend", backend),
        "binary_version": listed.get("binary_version"),
        "unified_memory": listed.get("unified_memory", False),
        "backend_warning": listed.get("backend_warning"),
        "installed_backends": listed.get("installed_backends", []),
        "gpus": gpus,
        "install_hint": native.install_recovery_command(),
    }
    if listed.get("error"):
        out["error"] = listed["error"]
    return out


class DownloadBody(BaseModel):
    source: str
    name: str | None = None


class SearchBody(BaseModel):
    query: str


class HfTokenBody(BaseModel):
    token: str


class DeleteBody(BaseModel):
    name: str


class LlamaSetupBody(BaseModel):
    tag: str | None = None
    backend: str = "auto"
    force: bool = False


@app.post("/v1/llama/setup")
def llama_setup(
    body: LlamaSetupBody | None = None,
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Extract the pinned llama.cpp pack from ~/.kllm/models into vendor/bin."""
    if STATE.join_key and x_kllm_key != STATE.join_key:
        raise HTTPException(401, "invalid join key")
    payload = body or LlamaSetupBody()
    try:
        from kllm.llama_pack import install_cached_llama_pack

        result = install_cached_llama_pack(
            tag=payload.tag,
            backend=payload.backend,
            force=payload.force,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    relaunch: dict[str, Any] | None = None
    if STATE.role == "worker" and result.get("ok"):
        root = repo_root()
        gpu_env = _gpu_visibility_env(
            (STATE.members.get(STATE.node_id) or {}).get("backend") or "cpu",
            STATE.gpu_ids,
        )
        if STATE.process_pid:
            native.stop_process(STATE.process_pid, force=True)
            runtime.clear_children()
            STATE.process_pid = None
            STATE.process_role = None
        try:
            launched = native.start_worker(
                host=STATE.rpc_bind or "0.0.0.0",
                port=STATE.rpc_port or DEFAULT_RPC_PORT,
                repo_root=str(root) if root else None,
                env=gpu_env or None,
                devices=[d["device"] for d in _resolve_gpu_selection(STATE.gpu_ids)["gpus"] if d.get("device")],
                log_path=str(cluster_log.rpc_server_log_path()),
            )
            STATE.process_pid = launched.get("pid")
            STATE.process_role = "worker"
            if launched.get("pid"):
                runtime.register_child(int(launched["pid"]), "rpc-server")
            STATE.save()
            relaunch = launched
        except Exception as exc:
            relaunch = {"error": str(exc)}
    result["relaunch"] = relaunch
    return result


@app.get("/health")
def health() -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "role": STATE.role,
        "node_id": STATE.node_id,
        "pid": os.getpid(),
    }
    # Non-secret fingerprint (same as mDNS ``kid``) so Join-by-key can HTTP-probe
    # when Bonjour/mDNS is unavailable on the LAN.
    if STATE.role == "master" and STATE.join_key:
        out["kid"] = discovery.join_key_id(STATE.join_key)
    return out


@app.get("/v1/discover")
def discover(timeout: float = 2.0, key: str | None = None) -> dict[str, Any]:
    found = discovery.browse_masters(timeout=timeout, key=key)
    return {
        "masters": [
            {"name": m.name, "host": m.host, "port": m.port, "key_id": m.key_id} for m in found
        ],
    }


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    local = model_ops.list_local_models()
    data = list(local)
    if STATE.model_name and STATE.process_pid and STATE.process_role == "master":
        # Ensure loaded model appears OpenAI-shaped even if not under models dir name
        if not any(m["id"] == STATE.model_name for m in data):
            data.insert(
                0,
                {
                    "id": STATE.model_name,
                    "object": "model",
                    "owned_by": "kllm",
                    "filename": STATE.model_name,
                    "loaded": True,
                },
            )
        else:
            for m in data:
                if m["id"] == STATE.model_name:
                    m["loaded"] = True
    return {"object": "list", "data": data, "loaded": STATE.model_name}


def _search_total_vram_bytes() -> int | None:
    """Live cluster: sum member VRAM. Idle: sum local GPUs. Else unknown."""
    members = STATE.members or {}
    if members:
        return model_ops.sum_vram_bytes(members.values())
    try:
        listed = native.list_gpus()
    except Exception:
        return None
    return model_ops.sum_vram_bytes(listed.get("gpus") or [])


@app.post("/v1/models/search")
def search_models(body: SearchBody) -> dict[str, Any]:
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")
    try:
        return model_ops.search_hub_ggufs(
            query, total_vram_bytes=_search_total_vram_bytes()
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


_DOWNLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kllm-download")


def _download_job_runner(source: str, name: str | None) -> None:
    """Run the download off the request path; publish terminal progress.

    The operation ID prevents a late terminal publish from replacing a newer
    queued download.
    """
    operation_id = (model_ops.get_download_progress() or {}).get("operation_id")
    try:
        path = model_ops.download_model(source, dest_name=name)
    except model_ops.DownloadCancelledError:
        return  # cancel already published phase=cancelled
    except Exception as exc:
        snap = model_ops.get_download_progress()
        filename = (snap or {}).get("filename") or (name or Path(source).name)
        try:
            model_ops.set_download_terminal(filename, None, "error", str(exc), operation_id)
        except Exception:
            pass
        return
    total = path.stat().st_size
    try:
        model_ops.set_download_terminal(path.name, total, "complete", operation_id=operation_id)
    except OSError:
        pass


@app.post("/v1/models/download")
def download_model(body: DownloadBody) -> dict[str, Any]:
    try:
        # Validate the source up front (resolves HF filename / quant) so a bad
        # ref fails fast with 400 instead of surfacing mid-transfer.
        if ":" in body.source and not body.source.startswith(("http://", "https://")):
            repo_id, suffix = body.source.split(":", 1)
            model_ops.resolve_hf_filename(repo_id, suffix, token=model_ops.resolve_hf_token())
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        operation = model_ops.begin_download(body.source, body.name)
    except model_ops.DownloadInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc
    _DOWNLOAD_EXECUTOR.submit(_download_job_runner, body.source, body.name)
    return {
        "ok": True,
        "accepted": True,
        "source": body.source,
        "kind": "download",
        "node_id": STATE.node_id,
        "state": "running",
        **{key: operation[key] for key in ("operation_id", "started_at", "updated_at", "finished_at")},
    }


@app.get("/v1/models/download-progress")
def download_progress() -> dict[str, Any]:
    snap = model_ops.download_job_snapshot()
    if snap is None:
        return {"state": "idle"}
    return snap


@app.post("/v1/models/download/cancel")
def cancel_download() -> dict[str, Any]:
    cancelled = model_ops.cancel_download()
    if not cancelled:
        raise HTTPException(409, "no download in progress")
    return {"ok": True}


@app.get("/v1/load-progress")
def load_progress_endpoint() -> dict[str, Any]:
    """Active inference load state for the UI (phase machine + RSS fraction)."""
    return load_progress.sample(STATE.infer_port)


@app.post("/v1/cluster/update")
def cluster_update_endpoint(
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Master: fan out update to workers. Worker: accept and spawn detached updater."""
    if STATE.role == "master":
        if not STATE.join_key:
            raise HTTPException(503, "master has no join key")
        if x_kllm_key is not None and x_kllm_key != STATE.join_key:
            raise HTTPException(401, "invalid join key")
        workers = [m for m in STATE.members.values() if m.get("role") == "worker"]
        try:
            return cluster_update.fanout_worker_updates(
                workers,
                STATE.join_key,
                default_port=STATE.control_port,
            )
        except cluster_update.UpdateInProgressError as exc:
            raise HTTPException(409, str(exc)) from exc

    if STATE.role == "worker":
        if STATE.join_key and x_kllm_key != STATE.join_key:
            raise HTTPException(401, "invalid join key")
        try:
            return cluster_update.accept_local_update(
                role=STATE.role,
                node_id=STATE.node_id,
                join_key=STATE.join_key,
                master_host=STATE.master_host,
                master_port=STATE.master_port,
                control_port=STATE.control_port,
                rpc_port=STATE.rpc_port,
                rpc_bind=STATE.rpc_bind,
                overlay=STATE.overlay,
                gpu_ids=STATE.gpu_ids,
                master_base=_master_control_base() if STATE.master_host else None,
            )
        except cluster_update.UpdateError as exc:
            raise HTTPException(400, str(exc)) from exc

    raise HTTPException(400, "cluster update requires master or worker role")


@app.post("/v1/cluster/update-progress")
def cluster_update_progress(
    body: UpdateProgressBody,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    """Worker updater → master: report pull/rebuild/rejoin phase."""
    if STATE.role != "master":
        raise HTTPException(400, "update progress requires master role")
    snap = cluster_update.apply_worker_progress(
        node_id=body.node_id,
        host=body.host,
        phase=body.phase,
        error=body.error,
        git_head=body.git_head,
        llama_release=body.llama_release,
        rebuild=body.rebuild,
    )
    return {"ok": True, "update": snap or cluster_update.get_update_progress()}


@app.post("/v1/models/delete")
def delete_model(body: DeleteBody) -> dict[str, Any]:
    try:
        path = model_ops.resolve_local_model(body.name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if (
        STATE.model_name
        and STATE.process_pid
        and STATE.process_role == "master"
        and path.name == STATE.model_name
    ):
        raise HTTPException(409, f"model is currently loaded: {STATE.model_name}")
    try:
        deleted = model_ops.delete_local_model(path.name)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400 if isinstance(exc, ValueError) else 404, str(exc)) from exc
    return {"ok": True, "name": deleted.name, "path": str(deleted), "results": []}


@app.post("/v1/chat/completions")
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
    return await gateway.proxy_chat_completions(
        STATE.infer_port, request, model_path=model_path
    )


@app.post("/v1/messages")
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
    return await gateway.proxy_anthropic_messages(
        STATE.infer_port, request, model_path=model_path
    )


@app.get("/v1/api-token")
def get_api_token() -> dict[str, Any]:
    """Return loopback API token (local operator helper)."""
    return {"api_token": ensure_api_token(), "base_url": f"http://127.0.0.1:{STATE.control_port}"}


@app.get("/v1/hf-token")
def get_hf_token() -> dict[str, Any]:
    """Whether an HF token is configured. Never returns the secret."""
    return hf_token_status()


@app.put("/v1/hf-token")
def put_hf_token(body: HfTokenBody) -> dict[str, Any]:
    """Save ~/.kllm/hf_token (chmod 600). Empty string deletes the file."""
    return save_hf_token(body.token)


@app.get("/v1/config")
def get_config() -> dict[str, Any]:
    return kllm_config.load_config()


@app.get("/v1/gpus")
def get_gpus() -> dict[str, Any]:
    """Enumerate local GPUs for pre-start selection."""
    return _list_gpus_payload()


@app.put("/v1/config")
def put_config(body: ConfigBody) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = {"device": body.device}
        # Include gpu_ids when the client sent the field (including explicit null = all).
        if "gpu_ids" in body.model_fields_set:
            payload["gpu_ids"] = body.gpu_ids
        if body.vram_percent is not None:
            payload["vram_percent"] = body.vram_percent
        if body.ram_percent is not None:
            payload["ram_percent"] = body.ram_percent
        if "accel_backend" in body.model_fields_set:
            payload["accel_backend"] = body.accel_backend
        if "split_mode" in body.model_fields_set:
            payload["split_mode"] = body.split_mode
        return kllm_config.save_config(payload)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/home")
def get_home() -> dict[str, Any]:
    """Data-directory layout for the System Settings > Data directory panel."""
    return {
        "home": str(home_dir()),
        "models": str(models_dir()),
        "logs": str(logs_dir()),
    }


@app.get("/v1/status")
def status() -> dict[str, Any]:
    _sync_master_liveness(shrink=True)
    membership_mod.reconcile_last_plan(STATE)
    capacity = None
    try:
        capacity = native.probe_capacity()
    except Exception as exc:
        capacity = {"error": str(exc)}
    cfg = kllm_config.load_config()
    activity = _inference_activity()
    return {
        "role": STATE.role,
        "node_id": STATE.node_id,
        "join_key": STATE.join_key if STATE.role == "master" else None,
        "master_host": STATE.master_host,
        "master_port": STATE.master_port,
        "cp_proxy_port": STATE.cp_proxy_port,
        "members": STATE.members,
        "process_pid": STATE.process_pid,
        "process_role": STATE.process_role,
        "last_plan": membership_mod.public_last_plan(STATE),
        "model_name": STATE.model_name,
        "download": model_ops.get_download_progress(),
        "rpc_cache": rpc_cache.status(),
        "update": cluster_update.get_update_progress(),
        "software": cluster_update.software_identity(),
        "capacity": capacity,
        "control_port": STATE.control_port,
        "rpc_port": STATE.rpc_port,
        "infer_port": STATE.infer_port,
        "inference_up": bool(activity.get("server_ready")),
        "inference_activity": activity,
        "device": cfg.get("device", kllm_config.DEFAULT_DEVICE),
        "vram_percent": cfg.get("vram_percent"),
        "ram_percent": cfg.get("ram_percent"),
        "config_gpu_ids": cfg.get("gpu_ids"),
        "accel_backend": cfg.get("accel_backend"),
        "gpu_ids": STATE.gpu_ids,
        "rpc_bind": STATE.rpc_bind,
        "overlay": STATE.overlay,
        "wg_address": STATE.wg_address,
        "overlay_status": STATE.overlay_status,
        "netsec_warnings": list(STATE.netsec_warnings or []),
        "apply_error": STATE.apply_error,
        "apply_warning": STATE.apply_warning,
    }


def _reset_netsec_state() -> None:
    if STATE.overlay == netsec.OVERLAY_WIREGUARD:
        netsec.tear_down_wireguard()
    from kllm import cp_proxy

    cp_proxy.stop_current()
    STATE.cp_proxy_port = None
    STATE.cp_proxy_pid = None
    STATE.rpc_bind = None
    STATE.overlay = netsec.OVERLAY_NONE
    STATE.wg_address = None
    STATE.wg_public_key = None
    STATE.overlay_status = None
    STATE.netsec_warnings = []


def _master_lan_host() -> str:
    mem = STATE.members.get(STATE.node_id) or {}
    return mem.get("lan_host") or mem.get("host") or discovery._local_ip()


def _setup_master_overlay(join_key: str, lan_host: str) -> None:
    """Bring up WireGuard on master; raises HTTPException on hard failure."""
    if not netsec.wg_tooling_available():
        raise HTTPException(
            400,
            "WireGuard tooling not found (wg). Install WireGuard or use overlay=none",
        )
    try:
        priv, pub = netsec.load_or_create_keypair()
        wg_addr = netsec.next_wg_address(STATE.members, master=True)
        psk = netsec.derive_wg_psk(join_key)
        conf = netsec.build_wg_conf(
            private_key=priv,
            address=wg_addr,
            listen_port=netsec.WG_LISTEN_PORT,
            psk=psk,
            peers=[],
        )
        netsec.bring_up_wireguard(conf)
        STATE.wg_address = wg_addr
        STATE.wg_public_key = pub
        STATE.overlay_status = f"up {wg_addr} (listen {netsec.WG_LISTEN_PORT})"
        # Keep LAN host for control plane / mDNS; RPC peers will use WG IPs.
        STATE.members[STATE.node_id]["lan_host"] = lan_host
        STATE.members[STATE.node_id]["wg_address"] = wg_addr
        STATE.members[STATE.node_id]["wg_public_key"] = pub
        STATE.members[STATE.node_id]["host"] = wg_addr
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"WireGuard overlay failed: {exc}") from exc


@app.post("/v1/start")
def start_master(body: StartBody) -> dict[str, Any]:
    global _advertiser
    if STATE.role == "worker":
        raise HTTPException(409, "node is a worker; leave first")
    try:
        overlay = netsec.normalize_overlay(body.overlay)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    STATE.role = "master"
    STATE.join_key = body.key or STATE.join_key or generate_join_key()
    STATE.control_port = body.control_port
    STATE.rpc_port = body.rpc_port
    STATE.master_host = None
    STATE.overlay = overlay
    STATE.netsec_warnings = []
    STATE.wg_address = None
    STATE.wg_public_key = None
    STATE.overlay_status = None
    STATE.apply_error = None
    STATE.apply_warning = None

    lan_host = netsec.pick_rpc_bind_host(body.rpc_bind)
    STATE.rpc_bind = lan_host
    fw = netsec.ensure_rpc_firewall(body.rpc_port, lan_host)
    if fw.get("warning"):
        STATE.netsec_warnings.append(str(fw["warning"]))

    listed = native.list_gpus()
    gpu_ids = _membership_gpu_ids(
        body.gpu_ids,
        body_sent="gpu_ids" in body.model_fields_set,
        listed=listed,
    )
    gpu_sel = _resolve_gpu_selection(gpu_ids, listed=listed)
    STATE.gpu_ids = gpu_sel["gpu_ids"]

    STATE.members[STATE.node_id] = _build_master_member_record(lan_host)

    if overlay == netsec.OVERLAY_WIREGUARD:
        _setup_master_overlay(STATE.join_key, lan_host)
    else:
        STATE.overlay_status = "disabled"

    STATE.save()

    if _advertiser:
        _advertiser.stop()
    _advertiser = discovery.MasterAdvertiser(
        f"kllm-{STATE.node_id}",
        STATE.control_port,
        join_key=STATE.join_key,
        advertise_host=lan_host,
    )
    _advertiser.start()
    return {
        "role": "master",
        "join_key": STATE.join_key,
        "node_id": STATE.node_id,
        "rpc_bind": STATE.rpc_bind,
        "overlay": STATE.overlay,
        "wg_address": STATE.wg_address,
        "overlay_status": STATE.overlay_status,
        "netsec_warnings": STATE.netsec_warnings,
        "gpu_ids": STATE.gpu_ids,
    }


@app.post("/v1/stop")
def stop() -> dict[str, Any]:
    global _advertiser
    if STATE.process_pid:
        try:
            _usage_store().end_node(STATE.node_id)
        except (OSError, sqlite3.Error):
            pass
        native.stop_process(STATE.process_pid, force=True)
        STATE.process_pid = None
        STATE.process_role = None
        runtime.clear_children()
        load_progress.end()
        STATE.inference_activity = inference_activity.idle()
    if STATE.role == "master":
        if _advertiser:
            _advertiser.stop()
            _advertiser = None
        STATE.join_key = None
    _reset_netsec_state()
    membership_mod.clear_membership_fields(STATE)
    STATE.role = "idle"
    STATE.master_host = None
    STATE.gpu_ids = None
    STATE.save()
    return {"role": "idle"}


@app.post("/v1/join")
def join_cluster(body: JoinBody) -> dict[str, Any]:
    try:
        return _join_cluster(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"join failed: {exc}") from exc


def _join_cluster(body: JoinBody) -> dict[str, Any]:
    if STATE.role == "master":
        raise HTTPException(409, "node is master; stop first")
    overlay = require_join_overlay(body.overlay)
    master_host, master_port = resolve_master_endpoint(body.master_host, body.master_port, body.key)

    listed = native.list_gpus()
    gpu_ids = _membership_gpu_ids(
        body.gpu_ids,
        body_sent="gpu_ids" in body.model_fields_set,
        listed=listed,
    )
    gpu_sel = _resolve_gpu_selection(gpu_ids, listed=listed)
    STATE.gpu_ids = gpu_sel["gpu_ids"]

    try:
        cap = _probe_capacity_for_cluster()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"probe_capacity failed: {exc}") from exc
    vram = gpu_sel["vram_bytes"] if gpu_sel["gpus"] else cap.get("vram_bytes")
    backend = gpu_sel["backend"] if gpu_sel["gpus"] or gpu_sel["backend"] != "cpu" else cap.get("backend", "cpu")
    lan_host = netsec.pick_rpc_bind_host(body.rpc_bind, toward=master_host)
    STATE.rpc_bind = lan_host
    STATE.overlay = overlay
    STATE.netsec_warnings = []
    STATE.llama_warnings = []
    STATE.wg_address = None
    STATE.wg_public_key = None
    STATE.overlay_status = None

    fw = netsec.ensure_rpc_firewall(body.rpc_port, lan_host)
    if fw.get("warning"):
        STATE.netsec_warnings.append(str(fw["warning"]))

    # Prefer direct LAN; if this interpreter cannot open LAN sockets (macOS TCC /
    # Zscaler), relay control-plane HTTP via a helper-python localhost proxy.
    STATE.netsec_warnings.extend(_ensure_master_control_route(master_host, master_port))

    wg_pub: str | None = None
    if overlay == netsec.OVERLAY_WIREGUARD:
        try:
            _, wg_pub = netsec.load_or_create_keypair()
            STATE.wg_public_key = wg_pub
        except Exception as exc:
            raise HTTPException(500, f"WireGuard keygen failed: {exc}") from exc

    identity = cluster_update.software_identity()
    payload = build_register_payload(
        node_id=STATE.node_id,
        lan_host=lan_host,
        control_port=STATE.control_port,
        rpc_port=body.rpc_port,
        cap=cap,
        vram=vram,
        backend=backend,
        gpus=gpu_sel["gpus"],
        gpu_ids=STATE.gpu_ids,
        wg_public_key=wg_pub,
        git_head=identity.get("git_head"),
        llama_release=identity.get("llama_release"),
    )

    def _drop_cp_proxy() -> None:
        from kllm import cp_proxy

        cp_proxy.stop_current()
        STATE.cp_proxy_port = None
        STATE.cp_proxy_pid = None

    if STATE.cp_proxy_port:
        base = f"http://127.0.0.1:{int(STATE.cp_proxy_port)}"
    else:
        base = f"http://{master_host}:{master_port}"
    reg = post_register(base, payload, body.key, master_host, master_port, _drop_cp_proxy)

    _apply_cluster_snapshot(reg)

    rpc_bind_host = lan_host
    # Master may correct a multi-NIC mis-pick to the TCP peer address that reached it.
    hint = reg.get("rpc_bind_hint")
    if (
        overlay == netsec.OVERLAY_NONE
        and not body.rpc_bind
        and isinstance(hint, str)
        and netsec.should_apply_rpc_bind_hint(hint, rpc_bind_host)
    ):
        rpc_bind_host = hint
        STATE.netsec_warnings = list(STATE.netsec_warnings or []) + [
            f"rpc bind corrected to {hint} (master peer address)"
        ]
    if overlay == netsec.OVERLAY_WIREGUARD:
        try:
            rpc_bind_host = _worker_bring_up_wg(body.key, master_host, reg)
        except Exception as exc:
            raise HTTPException(500, f"WireGuard overlay failed: {exc}") from exc

    # Align llama.cpp with the committed llama-release pin when a pack is already
    # in ~/.kllm/models (avoids GitHub SSL on some macOS venvs). A pack must
    # never change which backends are installed, so a refusal is surfaced rather
    # than silently swallowed on platforms with a multi-backend contract.
    pack_warning = cached_llama_pack_warning(str(backend))
    if pack_warning:
        STATE.llama_warnings = [*STATE.llama_warnings, pack_warning]

    # Start local rpc-server bound to the RPC address (LAN or WG), not 0.0.0.0 by default
    root = repo_root()
    gpu_env = _gpu_visibility_env(backend, STATE.gpu_ids)
    try:
        launched = native.start_worker(
            host=rpc_bind_host,
            port=body.rpc_port,
            repo_root=str(root) if root else None,
            env=gpu_env or None,
            devices=[d["device"] for d in gpu_sel["gpus"] if d.get("device")],
            log_path=str(cluster_log.rpc_server_log_path()),
        )
        STATE.process_pid = launched["pid"]
        STATE.process_role = "worker"
        if launched.get("pid"):
            runtime.register_child(int(launched["pid"]), "rpc-server")
    except Exception as exc:
        # Allow join without binary for dry setups
        launched = {"error": str(exc), "pid": None}

    STATE.role = "worker"
    STATE.join_key = body.key
    STATE.master_host = master_host
    STATE.master_port = master_port
    STATE.rpc_port = body.rpc_port
    STATE.rpc_bind = rpc_bind_host
    STATE.save()
    return {
        "role": "worker",
        "master": master_host,
        "launch": launched,
        "rpc_bind": STATE.rpc_bind,
        "overlay": STATE.overlay,
        "wg_address": STATE.wg_address,
        "overlay_status": STATE.overlay_status,
        "netsec_warnings": STATE.netsec_warnings,
        "llama_warnings": list(STATE.llama_warnings or []),
        "gpu_ids": STATE.gpu_ids,
        "cp_proxy_port": STATE.cp_proxy_port,
    }


def _worker_bring_up_wg(join_key: str, master_lan: str, reg: dict[str, Any]) -> str:
    wg_addr = reg.get("wg_address")
    peers = reg.get("wg_peers") or []
    if not wg_addr:
        raise RuntimeError("master did not assign a WireGuard address (is master overlay=wireguard?)")
    priv, _pub = netsec.load_or_create_keypair()
    psk = netsec.derive_wg_psk(join_key)
    conf = netsec.build_wg_conf(
        private_key=priv,
        address=wg_addr,
        listen_port=netsec.WG_LISTEN_PORT,
        psk=psk,
        peers=peers,
    )
    netsec.bring_up_wireguard(conf)
    STATE.wg_address = wg_addr
    STATE.overlay_status = f"up {wg_addr}"
    return wg_addr


@app.post("/v1/leave")
def leave() -> dict[str, Any]:
    if STATE.role == "worker" and STATE.master_host and STATE.join_key:
        try:
            _ensure_master_control_route(STATE.master_host, STATE.master_port)
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    f"{_master_control_base()}/v1/unregister",
                    json={"node_id": STATE.node_id},
                    headers={"X-Kllm-Key": STATE.join_key},
                )
        except Exception:
            pass
    if STATE.process_pid:
        native.stop_process(STATE.process_pid, force=True)
        STATE.process_pid = None
        STATE.process_role = None
        runtime.clear_children()
    _reset_netsec_state()
    membership_mod.clear_membership_fields(STATE)
    STATE.role = "idle"
    STATE.join_key = None
    STATE.master_host = None
    STATE.gpu_ids = None
    STATE.save()
    return {"role": "idle"}


@app.post("/v1/register")
def register(
    body: RegisterBody,
    request: Request,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "not a master")
    _sync_master_liveness(shrink=True)
    _resources.prune(STATE.members)
    if body.resource_session is not None:
        if body.node_id not in STATE.members or body.node_id == STATE.node_id or not native.resource_probe_available():
            raise HTTPException(400, "resource session requires a registered worker and native support")
        try:
            generation = _resources.establish(body.node_id, body.resource_session)
        except usage.ResourceConflict as exc:
            raise HTTPException(409, {"message": str(exc), "resource_generation": exc.generation}) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "request_id": body.resource_session["request_id"], "resource_generation": generation}
    lan_host = body.lan_host or body.host
    peer = request.client.host if request.client else None
    response: dict[str, Any] = {"ok": True}

    # Multi-NIC workers sometimes advertise a Hyper-V/VPN address the master cannot
    # reach. Prefer the TCP peer address that actually completed this register call.
    if (
        STATE.overlay == netsec.OVERLAY_NONE
        and netsec.is_usable_lan_peer(peer)
        and peer != lan_host
    ):
        response["rpc_bind_hint"] = peer
        lan_host = peer

    member: dict[str, Any] = {
        **body.model_dump(exclude={"resource_session"}),
        "host": lan_host,
        "lan_host": lan_host,
        "role": "worker",
        "last_seen": time.time(),
    }

    if STATE.overlay == netsec.OVERLAY_WIREGUARD:
        if not body.wg_public_key:
            raise HTTPException(400, "wg_public_key required when master overlay=wireguard")
        wg_addr = netsec.next_wg_address(STATE.members, master=False)
        member["wg_address"] = wg_addr
        member["wg_public_key"] = body.wg_public_key
        member["host"] = wg_addr  # master dials RPC over overlay
        member["lan_host"] = body.lan_host or body.host
        psk = netsec.derive_wg_psk(STATE.join_key or "")
        endpoint = f"{member['lan_host']}:{body.wg_listen_port or netsec.WG_LISTEN_PORT}"
        live = netsec.add_peer_to_running(
            public_key=body.wg_public_key,
            allowed_ips=f"{wg_addr}/32",
            endpoint=endpoint,
            psk=psk,
        )
        if not live.get("ok") and live.get("warning"):
            STATE.netsec_warnings = list(STATE.netsec_warnings or []) + [str(live["warning"])]

        # Rebuild master conf with all peers so tunnel survives restart
        _rewrite_master_wg_peers()

        master_lan = _master_lan_host()
        response["wg_address"] = wg_addr
        response["wg_peers"] = [
            {
                "public_key": STATE.wg_public_key or "",
                "endpoint": f"{master_lan}:{netsec.WG_LISTEN_PORT}",
                "allowed_ips": f"{STATE.wg_address}/32",
                "address": STATE.wg_address or "",
            }
        ]
        response["wg_listen_port"] = netsec.WG_LISTEN_PORT
    else:
        member["host"] = lan_host

    STATE.members[body.node_id] = member
    STATE.save()
    membership_mod.on_membership_grow(STATE)
    response["members"] = len(STATE.members)
    response.update(membership_mod.snapshot_payload(STATE, _inference_activity()))
    return response


def _rewrite_master_wg_peers() -> None:
    """Rewrite and re-apply master WireGuard conf including all worker peers."""
    if STATE.overlay != netsec.OVERLAY_WIREGUARD or not STATE.wg_address or not STATE.join_key:
        return
    try:
        priv, _pub = netsec.load_or_create_keypair()
        psk = netsec.derive_wg_psk(STATE.join_key)
        peers: list[dict[str, str]] = []
        for m in STATE.members.values():
            if m.get("role") != "worker":
                continue
            pub = m.get("wg_public_key")
            addr = m.get("wg_address")
            if not pub or not addr:
                continue
            lan = m.get("lan_host") or m.get("host")
            peer: dict[str, str] = {
                "public_key": pub,
                "allowed_ips": f"{addr}/32",
                "address": addr,
            }
            if lan and not str(lan).startswith(netsec.WG_PREFIX):
                peer["endpoint"] = f"{lan}:{m.get('wg_listen_port', netsec.WG_LISTEN_PORT)}"
            peers.append(peer)
        conf = netsec.build_wg_conf(
            private_key=priv,
            address=STATE.wg_address,
            listen_port=netsec.WG_LISTEN_PORT,
            psk=psk,
            peers=peers,
        )
        netsec.bring_up_wireguard(conf)
    except Exception as exc:
        STATE.netsec_warnings = list(STATE.netsec_warnings or []) + [f"wg peer rewrite: {exc}"]


@app.post("/v1/unregister")
def unregister(body: dict[str, str], _: None = Depends(_require_key)) -> dict[str, Any]:
    _sync_master_liveness(shrink=True)
    nid = body.get("node_id", "")
    if nid and nid != STATE.node_id and nid in STATE.members:
        STATE.members.pop(nid, None)
        _resources.prune(STATE.members)
        membership_mod.on_membership_shrink(STATE)
    else:
        STATE.save()
    return {"ok": True}


@app.post("/v1/heartbeat")
def heartbeat(
    body: RegisterBody,
    request: Request,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    _sync_master_liveness(shrink=True)
    accepted = body.node_id in STATE.members
    if accepted:
        update = body.model_dump(exclude={"resource_session"})
        if not update.get("process_pid") and STATE.members[body.node_id].get("process_pid"):
            try:
                _usage_store().end_node(body.node_id)
            except (OSError, sqlite3.Error):
                pass
        peer = request.client.host if request.client else None
        # Keep dial address aligned with the path that reaches the master (LAN).
        if (
            STATE.overlay == netsec.OVERLAY_NONE
            and netsec.is_usable_lan_peer(peer)
            and (update.get("host") != peer or update.get("lan_host") != peer)
        ):
            update["host"] = peer
            update["lan_host"] = peer
        STATE.members[body.node_id].update(update)
        STATE.members[body.node_id]["last_seen"] = time.time()
        STATE.save()
    return {
        "ok": True,
        "accepted": accepted,
        **membership_mod.snapshot_payload(STATE, _inference_activity()),
    }


@app.post("/v1/usage/ingest")
async def usage_ingest(request: Request, _: None = Depends(_require_key)) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "usage ingest requires master role")
    _resources.prune(STATE.members)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise HTTPException(400, "usage batch exceeds 64 KiB")
    try:
        body = usage.usage_probe.decode(bytes(raw), framed=False)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, "invalid usage JSON") from None
    if not isinstance(body, dict) or set(body) not in ({"node_id", "readings"}, {"node_id", "readings", "resources"}):
        raise HTTPException(400, "usage batch requires node_id, readings and optional resources")
    node_id = body["node_id"]
    member = STATE.members.get(node_id) if isinstance(node_id, str) else None
    if not member or node_id == STATE.node_id or member.get("role") != "worker":
        raise HTTPException(400, "unregistered worker node")
    points = body["readings"]
    snapshot = body.get("resources")
    if not isinstance(points, list) or not (0 if snapshot is not None else 1) <= len(points) <= 8:
        raise HTTPException(400, "usage batch must contain 1–8 readings")
    now = datetime.now(timezone.utc)
    try:
        if snapshot is not None:
            if not native.resource_probe_available():
                raise ValueError("native resource validation unavailable")
            usage.validate_resources(node_id, snapshot, member, now)
        if points and (not member.get("process_pid") or not member.get("process_started_at") or member.get("process_role") != "worker"):
            raise ValueError("worker has no registered managed process")
        for point in points:
            usage.validate(node_id, point, now=now, expected_pid=member["process_pid"],
                           expected_start=member["process_started_at"], expected_role="worker")
        model = _usage_assignment(node_id)
        with _resources.lock:
            if snapshot is not None:
                _resources.validate_session(node_id, snapshot)
            accepted = _usage_store().record_many(
                node_id, member.get("host") or node_id, points, now=now,
                expected_pid=member["process_pid"], expected_start=member["process_started_at"],
                expected_role="worker", model=model, context=_usage_node_context(member, model)) if points else 0
            resources_accepted = _resources.install(node_id, snapshot) if snapshot is not None else False
    except usage.ResourceConflict as exc:
        raise HTTPException(409, {"message": str(exc), "resource_generation": exc.generation}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "usage recording unavailable; previous history retained") from None
    result = {"accepted": accepted, "duplicates": len(points) - accepted,
              "last_successful_save": _usage_store().last_successful_save if points else None}
    if snapshot is not None:
        result["resources_accepted"] = resources_accepted
    return result


def _proxy_master_usage(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not STATE.master_host or not STATE.join_key:
        raise HTTPException(503, "master usage history unavailable")
    try:
        response = http_clients.control_client().get(
            f"{_master_control_base()}{path}", params=params,
            headers={"X-Kllm-Key": STATE.join_key}, timeout=5.0)
        if response.status_code != 200:
            status = response.status_code if response.status_code in (400, 401, 403, 404, 422) else 503
            raise HTTPException(status, "master usage access denied" if status in (401, 403)
                                else "master usage history unavailable")
        return response.json()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "master usage history unavailable") from None


def _with_resources(node_id, node, connected, now):
    with _resources.lock:
        snapshot = _resources.latest.get(node_id)
        residence = max(0.0, time.monotonic() - _resources.received.get(node_id, time.monotonic()))
    age, freshness = usage.resource_freshness(snapshot, connected, now, residence)
    node.update(resources=snapshot, resource_age_seconds=age, resource_freshness=freshness)
    return node


@app.get("/v1/usage/live")
def usage_live() -> dict[str, Any]:
    if STATE.role == "worker":
        global _worker_usage_last_save, _worker_usage_last_success_at
        try:
            data = _proxy_master_usage("/v1/usage/live")
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise HTTPException(503, {"message": "master usage history unavailable; check private control connection",
                                          "recording": "interrupted", "error": _worker_usage_error or "master unavailable",
                                          "last_successful_save": _worker_usage_last_save}) from None
            raise
        _worker_usage_last_save = data.get("recording", {}).get("last_successful_save") or _worker_usage_last_save
        _worker_usage_last_success_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if _worker_usage_error:
            if STATE.node_id in data.get("nodes", {}):
                data["nodes"][STATE.node_id]["error"] = _worker_usage_error
            data["recording"] = {**data["recording"], "recording": "interrupted", "error": _worker_usage_error}
        return data
    _sync_master_liveness(shrink=True)
    try:
        store = _usage_store()
        members = dict(STATE.members) if STATE.role == "master" else {}
        _resources.prune(members)
        known = store.known_nodes()
        now = datetime.now(timezone.utc)
        nodes = {}
        for node_id in members.keys() | known.keys():
            member = members.get(node_id)
            meta = member or known[node_id]
            point = store.latest(node_id)
            age = max(0, (now - usage.parse_utc(point["observed_at"])).total_seconds()) if point else None
            connected = member is not None
            current = bool(member and member.get("process_pid") and member.get("process_started_at")
                           and point and point["pid"] == member["process_pid"]
                           and usage.parse_utc(point["process_started_at"]) == usage.parse_utc(member["process_started_at"]))
            nodes[node_id] = {
                "node_id": node_id, "host": meta.get("host") or node_id,
                "role": meta.get("role", "worker"), "backend": meta.get("actual_backend") or meta.get("backend"),
                "devices": meta.get("devices") or [], "unified_memory": bool(meta.get("unified_memory")),
                "total_ram_bytes": meta.get("total_ram_bytes"), "vram_bytes": meta.get("vram_bytes"),
                "assignment": _usage_assignment(node_id) if member else meta.get("assignment"),
                "connected": connected, "process_state": "running" if current else "no managed process" if connected else "unobserved",
                "reading": point if current or not connected else None,
                "age_seconds": round(age, 1) if point and (current or not connected) else None,
                "freshness": "fresh" if connected and current and age is not None and age < 10 else "stale" if point and (current or not connected) else "unavailable",
            }
            _with_resources(node_id, nodes[node_id], connected, now)
            if nodes[node_id]["resources"] and member and member.get("process_pid"):
                verified = any(p["pid"] == member["process_pid"] and p["identity_verified"] and p["state"] == "running"
                               for p in nodes[node_id]["resources"]["processes"])
                nodes[node_id]["process_state"] = "running" if verified else "unavailable"
        relationships = [
            {"from_node_id": STATE.node_id, "to_node_id": node_id, "kind": "master-worker"}
            for node_id, member in members.items()
            if node_id != STATE.node_id and member.get("role") == "worker"
        ]
        recording = store.coverage()
        if STATE.role != "master" and recording["recording"] != "failed":
            recording["recording"] = "interrupted"
        return {"nodes": nodes, "relationships": relationships, "recording": recording,
                "resource_schema_versions": [1] if native.resource_probe_available() else []}
    except (OSError, sqlite3.Error):
        with _resources.lock:
            has_resources = any(node in STATE.members for node in _resources.latest)
        if has_resources:
            now = datetime.now(timezone.utc)
            nodes = {node: _with_resources(node, dict(node_id=node, host=member.get("host") or node,
                role=member.get("role", "worker"), connected=True, reading=None, freshness="unavailable",
                process_state="unavailable" if member.get("process_pid") else "no managed process"), True, now)
                for node, member in STATE.members.items()}
            return {"nodes": nodes, "relationships": [], "resource_schema_versions": [1],
                    "recording": {"recording": "failed", "error": "usage history unavailable; live resources retained", "last_successful_save": None}}
        raise HTTPException(503, "usage history unavailable; previously saved records preserved") from None


def _usage_read(action) -> dict[str, Any]:
    try:
        return action()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "usage history unavailable; previously saved records preserved") from None


@app.get("/v1/usage/lifetimes")
def usage_lifetimes(node_id: str = Query(min_length=1, max_length=128)) -> dict[str, Any]:
    if STATE.role == "worker":
        return _proxy_master_usage("/v1/usage/lifetimes", {"node_id": node_id})

    def query():
        store = _usage_store()
        known = store.known_nodes()
        member = STATE.members.get(node_id)
        if node_id not in known and not member:
            raise LookupError("unknown node")
        meta = member or known[node_id]
        return {"node_id": node_id, "host": meta.get("host") or node_id,
                "connected": member is not None, "lifetimes": store.lifetimes(node_id),
                "retained_node_ids": sorted(known), **store.coverage(node_id)}

    return _usage_read(query)


@app.get("/v1/usage/series")
def usage_series(node_id: str = Query(min_length=1, max_length=128),
                 lifetime_id: str = Query(min_length=1, max_length=256),
                 window: Literal["15m", "1h", "24h"] = "15m") -> dict[str, Any]:
    if STATE.role == "worker":
        return _proxy_master_usage("/v1/usage/series", {"node_id": node_id, "lifetime_id": lifetime_id, "window": window})
    return _usage_read(lambda: _usage_store().series(node_id, lifetime_id, window))


@app.get("/v1/usage/readings")
def usage_readings(node_id: str = Query(min_length=1, max_length=128),
                   lifetime_id: str = Query(min_length=1, max_length=256),
                   window: Literal["15m", "1h", "24h"] = "15m",
                   limit: int = Query(default=500, ge=1, le=1000),
                   cursor: str | None = Query(default=None, max_length=2048)) -> dict[str, Any]:
    params = {"node_id": node_id, "lifetime_id": lifetime_id, "window": window, "limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    if STATE.role == "worker":
        return _proxy_master_usage("/v1/usage/readings", params)
    return _usage_read(lambda: _usage_store().readings(node_id, lifetime_id, window, limit=limit, cursor=cursor))


def _logs_snapshot(
    n: int,
    node: str | None,
    source: str | None,
    after: str | None = None,
) -> dict[str, Any]:
    origin = "cluster" if STATE.role == "master" else "local"
    local_node = node
    if STATE.role == "worker" and not local_node:
        local_node = STATE.node_id
    try:
        snap = cluster_log.HUB.snapshot(n, local_node, source, after)
    except cluster_log.CursorError as exc:
        raise HTTPException(400, f"invalid log cursor: {exc}") from exc
    snap["source"] = origin
    return snap


def _ensure_log_journal() -> None:
    try:
        cluster_log.HUB.ensure_journal_loaded()
    except OSError as exc:
        raise HTTPException(503, "cluster log journal is temporarily unavailable; retry") from exc


def _logs_sse(
    n: int,
    follow: bool,
    node: str | None,
    source: str | None,
) -> StreamingResponse:
    local_node = node
    if STATE.role == "worker" and not local_node:
        local_node = STATE.node_id
    return StreamingResponse(
        iterate_in_threadpool(cluster_log.HUB.iter_sse(n, follow, local_node, source)),
        media_type="text/event-stream",
    )


def _try_proxy_master_logs(
    n: int,
    follow: bool,
    node: str | None,
    source: str | None,
    after: str | None = None,
) -> dict[str, Any] | StreamingResponse | None:
    params: dict[str, Any] = {"n": n, "follow": follow}
    if node:
        params["node"] = node
    if source:
        params["source"] = source
    if after:
        params["after"] = after
    headers = {}
    if STATE.join_key:
        headers["X-Kllm-Key"] = STATE.join_key
    url = f"{_master_control_base()}/v1/logs"
    if follow:
        client = httpx.Client(timeout=None)
        try:
            resp = client.send(
                client.build_request("GET", url, params=params, headers=headers),
                stream=True,
            )
        except Exception:
            client.close()
            return None
        if resp.status_code != 200:
            status = resp.status_code
            resp.close()
            client.close()
            raise HTTPException(status, f"master log request returned HTTP {status}")

        def _gen():
            try:
                for chunk in resp.iter_bytes():
                    yield chunk
            finally:
                resp.close()
                client.close()

        return StreamingResponse(
            iterate_in_threadpool(_gen()),
            media_type="text/event-stream",
        )
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url, params=params, headers=headers)
    except Exception:
        return None
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"master log request returned HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        raise HTTPException(502, "master returned an invalid log response") from None
    if not isinstance(data, dict):
        raise HTTPException(502, "master returned an invalid log response")
    return data


@app.post("/v1/logs")
def ingest_logs(
    body: LogsIngestBody,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(403, "only the master accepts log ingest")
    _ensure_log_journal()
    events = [
        cluster_log.make_event(
            body.node_id, item.source, item.line, ts=item.ts, event_id=item.event_id
        )
        for item in body.lines
    ]
    ingested = cluster_log.HUB.ingest(events, persist=True)
    return {"ok": True, "ingested": ingested}


@app.get("/v1/logs")
def get_logs(
    n: int = 200,
    follow: bool = False,
    node: str | None = None,
    source: str | None = None,
    after: str | None = None,
):
    n = max(1, min(int(n), 5000))
    if source and source not in cluster_log.VALID_SOURCES:
        raise HTTPException(400, "source must be agent, llama, or rpc")
    if after is not None:
        if follow:
            raise HTTPException(400, "after cannot be combined with follow")
        try:
            cluster_log.parse_cursor(after)
        except cluster_log.CursorError as exc:
            raise HTTPException(400, f"invalid log cursor: {exc}") from exc
    if STATE.role == "master":
        _ensure_log_journal()
    if STATE.role == "worker" and STATE.master_host:
        proxied = _try_proxy_master_logs(n, follow, node, source, after)
        if proxied is not None:
            if isinstance(proxied, dict):
                try:
                    cluster_log.parse_cursor(proxied.get("cursor"))
                except cluster_log.CursorError as exc:
                    raise HTTPException(
                        502,
                        "master does not support valid incremental log snapshots",
                    ) from exc
                if not isinstance(proxied.get("lines"), list) or any(
                    not isinstance(line, dict)
                    or cluster_log.normalize_event_id(line.get("event_id")) is None
                    for line in proxied["lines"]
                ):
                    raise HTTPException(
                        502,
                        "master does not support identified incremental log entries",
                    )
            return proxied
        warning = "master unreachable; showing local logs"
        if follow:
            def _gen_local():
                yield cluster_log.encode_sse(
                    cluster_log.make_event("kllm", "agent", warning)
                )
                yield from cluster_log.HUB.iter_sse(
                    n, True, node or STATE.node_id, source
                )

            return StreamingResponse(
                iterate_in_threadpool(_gen_local()),
                media_type="text/event-stream",
            )
        snap = _logs_snapshot(n, node, source, after)
        snap["warning"] = warning
        snap["source"] = "local"
        return snap
    if follow:
        return _logs_sse(n, True, node, source)
    return _logs_snapshot(n, node, source, after)


def _live_coverage() -> str:
    return "cluster" if STATE.role == "master" else "local_with_cluster_view" if STATE.role == "worker" else "local"


def _live_status() -> dict[str, Any]:
    """Fast detached status view; probes and software discovery stay off the feed path."""
    config = kllm_config.load_config()
    member = dict(STATE.members.get(STATE.node_id) or {})
    capacity = {key: member[key] for key in (
        "total_ram_bytes", "available_ram_bytes", "vram_bytes", "backend", "actual_backend",
        "devices", "unified_memory", "reclaimable_ram_bytes",
    ) if key in member}
    activity = _inference_activity()
    return {
        "role": STATE.role, "node_id": STATE.node_id,
        "join_key": STATE.join_key if STATE.role == "master" else None,
        "master_host": STATE.master_host, "master_port": STATE.master_port,
        "cp_proxy_port": STATE.cp_proxy_port,
        "members": {node_id: dict(value) for node_id, value in STATE.members.items()},
        "process_pid": STATE.process_pid, "process_role": STATE.process_role,
        "last_plan": membership_mod.public_last_plan(STATE), "model_name": STATE.model_name,
        "download": model_ops.get_download_progress(), "rpc_cache": rpc_cache.status(),
        "update": cluster_update.get_update_progress(),
        "software": {key: member.get(key) for key in ("binary_version", "git_head", "llama_release")},
        "capacity": capacity, "control_port": STATE.control_port, "rpc_port": STATE.rpc_port,
        "infer_port": STATE.infer_port, "inference_up": bool(activity.get("server_ready")),
        "inference_activity": activity, "device": config.get("device", kllm_config.DEFAULT_DEVICE),
        "vram_percent": config.get("vram_percent"), "ram_percent": config.get("ram_percent"),
        "config_gpu_ids": config.get("gpu_ids"),
        "accel_backend": config.get("accel_backend", member.get("actual_backend", member.get("backend"))),
        "gpu_ids": STATE.gpu_ids, "rpc_bind": STATE.rpc_bind, "overlay": STATE.overlay,
        "wg_address": STATE.wg_address, "overlay_status": STATE.overlay_status,
        "netsec_warnings": list(STATE.netsec_warnings or []),
        "apply_error": STATE.apply_error, "apply_warning": STATE.apply_warning,
    }


def _operation_records(kind: str, records: dict[str, Any]) -> dict[str, Any]:
    for key in ("current", "last_terminal"):
        operation = records.get(key)
        if not isinstance(operation, dict):
            continue
        operation = dict(operation)
        operation["kind"] = kind
        operation["node_id"] = STATE.node_id
        if kind == "load":
            operation["state"] = ("running" if operation.get("phase") in {"starting", "loading"}
                                   else "complete" if operation.get("phase") == "ready"
                                   else "cancelled" if operation.get("phase") == "cancelled" else "error")
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
    return _worker_usage_last_success_at if topic == "usage" and STATE.role == "worker" else None


def _live_download() -> dict[str, Any]:
    return _operation_records("download", model_ops.download_progress_records())


def _live_logs(after: str | None) -> dict[str, Any]:
    if after is not None:
        cluster_log.parse_cursor(after)
    if STATE.role != "worker":
        snapshot = _logs_snapshot(n=200, node=None, source=None, after=after)
        continuity_id = getattr(app.state, "live_log_continuity", None)
        if continuity_id is None:
            continuity_id = str(uuid.uuid4())
            app.state.live_log_continuity = continuity_id
        snapshot.update(source="cluster", continuity_id=continuity_id, discontinuity=None)
        return snapshot

    state = getattr(app.state, "live_log_state", None)
    if state is None:
        state = new_worker_log_state()
        app.state.live_log_state = state

    with state["lock"]:
        flight = state["flight"]
        retry_delay = 30.0 if state.get("error") == "denied" else 0.5
        owner = flight is None and time.monotonic() - state["last_acquired"] >= retry_delay
        if owner:
            flight = threading.Event()
            state["flight"] = flight

    if owner:
        upstream = None
        failure: Exception | None = None
        try:
            try:
                upstream = _try_proxy_master_logs(500, False, None, None, state["upstream_cursor"])
            except HTTPException as exc:
                if exc.status_code in (401, 403):
                    raise SourceDenied from None
                raise RuntimeError("Master log source returned an error") from None
            apply_upstream_log_batch(state, upstream)
        except Exception as exc:
            failure = exc
            with state["lock"]:
                state["error"] = "denied" if isinstance(exc, SourceDenied) else "unavailable"
                state["last_acquired"] = time.monotonic()
        finally:
            with state["lock"]:
                state["flight"] = None
                flight.set()
        if failure is not None:
            raise failure
    elif flight is not None:
        flight.wait(2)

    return worker_log_snapshot(state, after, STATE.node_id)


def _new_live_feed() -> LiveFeed:
    return LiveFeed(
        {
            "status": (_live_status, _live_coverage),
            "usage": (_live_usage, _live_coverage),
            "load": (_live_load, "local"),
            "download": (_live_download, "local"),
        },
        _live_logs,
        upstream_success=_live_upstream_success,
        scope=lambda: (STATE.role, STATE.node_id, STATE.master_host, STATE.join_key, STATE.process_pid),
    )


def _live_feed() -> LiveFeed:
    feed = getattr(app.state, "live_feed", None)
    if feed is None:
        feed = _new_live_feed()
        app.state.live_feed = feed
    feed.start()
    return feed


def _origin(value: str, *, limit: int = 512) -> tuple[str, str, int] | None:
    if not value or len(value) > limit:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path or parsed.query or parsed.fragment:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.rstrip(".").lower(), port


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def _request_origin(request: Request) -> tuple[str, str, int] | None:
    parsed = urlsplit(str(request.url))
    return _origin(f"{parsed.scheme}://{parsed.netloc}")


def _websocket_origin(websocket: WebSocket) -> tuple[str, str, int] | None:
    parsed = urlsplit(str(websocket.url))
    scheme = "https" if parsed.scheme == "wss" else "http"
    return _origin(f"{scheme}://{parsed.netloc}")


def _origin_allowed(value: str, target: tuple[str, str, int] | None, peer: str | None) -> bool:
    origin = _origin(value)
    if origin is None or target is None:
        return False
    if origin == target:
        return True
    return origin[0] == "http" and origin[2] == 5173 and _is_loopback(origin[1]) and _is_loopback(target[1]) and _is_loopback(peer)


def _live_request(
    topics: str | None,
    logs: str,
    after: str | None,
    continuity_id: str | None,
    page_origin: str | None,
    origin: str | None,
    request: Request,
) -> tuple[list[str], bool, str | None, str | None]:
    if topics is None:
        selected = list(LIVE_TOPICS)
    elif topics == "":
        selected = []
    else:
        selected = topics.split(",")
        if len(selected) != len(set(selected)) or any(topic not in LIVE_TOPICS for topic in selected):
            raise HTTPException(422, "topics must be a unique subset of status, usage, load, download")
    wants_logs = logs == "1"
    if (after is not None or continuity_id is not None) and not wants_logs:
        raise HTTPException(422, "after and continuity_id require logs=1")
    if not selected and not wants_logs:
        raise HTTPException(422, "request at least one topic or logs")
    if after is not None:
        try:
            cluster_log.parse_cursor(after)
        except cluster_log.CursorError:
            raise HTTPException(400, "invalid log cursor") from None
    if continuity_id is not None:
        try:
            uuid.UUID(continuity_id)
        except ValueError:
            raise HTTPException(422, "invalid continuity_id") from None
    target = _request_origin(request)
    if page_origin is not None:
        if _origin(page_origin) is None:
            raise HTTPException(422, "invalid page_origin")
        if not _origin_allowed(page_origin, target, request.client.host if request.client else None):
            raise HTTPException(403, "page origin is not permitted")
    if origin is not None and not _origin_allowed(origin, target, request.client.host if request.client else None):
        raise HTTPException(403, "origin is not permitted")
    return selected, wants_logs, after, continuity_id


@app.get("/v1/dashboard/live")
def dashboard_live(
    request: Request,
    topics: str | None = Query(default=None, max_length=256),
    logs: Literal["0", "1"] = "0",
    after: str | None = Query(default=None, max_length=128),
    continuity_id: str | None = Query(default=None, max_length=64),
    page_origin: str | None = Query(default=None, max_length=512),
    origin: str | None = Header(default=None),
) -> JSONResponse:
    selected, wants_logs, cursor, continuity = _live_request(topics, logs, after, continuity_id, page_origin, origin, request)
    try:
        payload = _live_feed().snapshot(selected, logs=wants_logs, after=cursor, continuity_id=continuity)
    except OverflowError:
        raise HTTPException(503, "dashboard snapshot is temporarily unavailable") from None
    except ValueError:
        raise HTTPException(400, "invalid log cursor") from None
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


def _safe_control(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**53


def _subscription(control: dict[str, Any], generation: int) -> tuple[int, list[str], str | None, str | None] | None:
    if set(control) != {"type", "version", "generation", "topics", "logs"} or control.get("type") != "subscribe" or control.get("version") != 1:
        return None
    next_generation = control.get("generation")
    topics = control.get("topics")
    logs = control.get("logs")
    if not _safe_control(next_generation) or next_generation <= generation or not isinstance(topics, list):
        return None
    if any(not isinstance(topic, str) or topic not in LIVE_TOPICS for topic in topics):
        return None
    if len(topics) != len(set(topics)):
        return None
    if logs is None:
        cursor = None
    elif isinstance(logs, dict) and set(logs) == {"after", "continuity_id"}:
        cursor = logs.get("after")
        continuity_id = logs.get("continuity_id")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 128):
            return None
        if continuity_id is not None:
            try:
                uuid.UUID(continuity_id)
            except (ValueError, TypeError):
                return None
    else:
        return None
    if not topics and logs is None:
        return None
    try:
        if cursor is not None:
            cluster_log.parse_cursor(cursor)
    except cluster_log.CursorError:
        return None
    return next_generation, topics, cursor, continuity_id if logs is not None else None


@app.websocket("/v1/dashboard/live")
async def dashboard_live_websocket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    target = _websocket_origin(websocket)
    peer = websocket.client.host if websocket.client else None
    if origin is None or not _origin_allowed(origin, target, peer):
        await websocket.close(code=1008)
        return
    feed = _live_feed()
    viewer_id = str(uuid.uuid4())
    serving_epoch = feed.current_epoch()
    await websocket.accept()
    await websocket.send_json({"type": "hello", "version": 1, "epoch": serving_epoch,
                               "server_time": live_server_time(), "heartbeat_ms": 2000})
    generation = 0
    topics: list[str] = []
    logs_requested = False
    log_cursor: str | None = None
    log_continuity: str | None = None
    outstanding: tuple[int, float, str | None, str | None, tuple[Any, ...] | None] | None = None
    sent_revisions: dict[str, int] = {}
    sent_log_cursor: str | None = None
    sent_log_signature: tuple[Any, ...] | None = None
    batch = 0
    next_ping = time.monotonic() + 2
    next_refresh = time.monotonic()
    peer_seen = time.monotonic()
    control_times: list[float] = []
    ping_id = 0
    pending_ping: int | None = None

    async def fail(code: str, message: str, close_code: int = 1008) -> None:
        await websocket.send_json({"type": "error", "area": "connection", "code": code, "message": message})
        await websocket.close(code=close_code)

    try:
        while True:
            now = time.monotonic()
            if feed.current_epoch() != serving_epoch:
                await websocket.close(code=1012)
                return
            if now - peer_seen > 4:
                await websocket.close(code=1013)
                return
            if outstanding is not None and now - outstanding[1] > 5:
                await websocket.close(code=1013)
                return
            if generation and outstanding is None and now >= next_refresh:
                try:
                    snapshot = await asyncio.to_thread(feed.snapshot, topics, logs=logs_requested,
                                                       after=log_cursor, continuity_id=log_continuity, lease=False)
                except ValueError:
                    await fail("invalid_cursor", "Invalid log cursor")
                    return
                if feed.current_epoch() != serving_epoch:
                    await websocket.close(code=1012)
                    return
                changed = {topic: record for topic, record in snapshot["areas"].items() if sent_revisions.get(topic) != record["revision"]}
                log_result = snapshot["logs"]
                current_cursor = (log_result or {}).get("snapshot", {}).get("cursor") if log_result else None
                current_signature = log_result_signature(log_result) if log_result else None
                if changed or (log_result is not None and current_signature != sent_log_signature):
                    batch += 1
                    await websocket.send_json({"type": "data", "generation": generation, "batch": batch, "initial": False,
                                               "epoch": snapshot["epoch"], "server_time": live_server_time(), "areas": changed, "logs": log_result})
                    current_continuity = (log_result or {}).get("snapshot", {}).get("continuity_id") if log_result else None
                    outstanding = (batch, time.monotonic(), current_cursor, current_continuity, current_signature)
                    sent_revisions.update({topic: record["revision"] for topic, record in changed.items()})
                next_refresh = time.monotonic() + 1
            timeout = min(max(0.01, next_ping - now), max(0.01, next_refresh - now), 1.0)
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout)
            except TimeoutError:
                if time.monotonic() >= next_ping:
                    ping_id += 1
                    pending_ping = ping_id
                    await websocket.send_json({"type": "ping", "id": ping_id})
                    next_ping = time.monotonic() + 2
                continue
            if message["type"] == "websocket.disconnect":
                return
            raw = message.get("text")
            if raw is None:
                await fail("invalid_request", "JSON text controls are required")
                return
            if len(raw.encode()) > 4096:
                await websocket.close(code=1009)
                return
            try:
                control = json.loads(raw)
            except ValueError:
                await fail("invalid_request", "Invalid JSON control")
                return
            now = time.monotonic()
            control_times = [item for item in control_times if now - item < 1]
            control_times.append(now)
            if len(control_times) > 20 or not isinstance(control, dict):
                await fail("invalid_request", "Invalid control")
                return
            peer_seen = now
            kind = control.get("type")
            if kind == "subscribe":
                parsed = _subscription(control, generation)
                if parsed is None:
                    await fail("invalid_request", "Invalid subscription")
                    return
                generation, topics, log_cursor, log_continuity = parsed
                logs_requested = control["logs"] is not None
                feed.subscribe(viewer_id, topics)
                sent_revisions = {}
                sent_log_cursor = None
                sent_log_signature = None
                try:
                    snapshot = await asyncio.to_thread(feed.snapshot, topics, logs=logs_requested,
                                                       after=log_cursor, continuity_id=log_continuity, lease=False)
                except ValueError:
                    await fail("invalid_cursor", "Invalid log cursor")
                    return
                if feed.current_epoch() != serving_epoch:
                    await websocket.close(code=1012)
                    return
                batch += 1
                current_cursor = (snapshot["logs"] or {}).get("snapshot", {}).get("cursor") if snapshot["logs"] else None
                await websocket.send_json({"type": "data", "generation": generation, "batch": batch, "initial": True,
                                           "epoch": snapshot["epoch"], "server_time": live_server_time(), "areas": snapshot["areas"], "logs": snapshot["logs"]})
                current_continuity = (snapshot["logs"] or {}).get("snapshot", {}).get("continuity_id") if snapshot["logs"] else None
                logs = snapshot["logs"]
                current_signature = log_result_signature(logs)
                outstanding = (batch, time.monotonic(), current_cursor, current_continuity, current_signature)
                sent_revisions = {topic: record["revision"] for topic, record in snapshot["areas"].items()}
                next_refresh = time.monotonic() + 1
            elif kind == "ack" and set(control) == {"type", "generation", "batch"} and _safe_control(control.get("generation")) and _safe_control(control.get("batch")):
                if outstanding is not None and control["generation"] == generation and control["batch"] == outstanding[0]:
                    log_cursor = outstanding[2]
                    sent_log_cursor = outstanding[2]
                    log_continuity = outstanding[3]
                    sent_log_signature = outstanding[4]
                    outstanding = None
                elif control["generation"] > generation or (control["generation"] == generation and (outstanding is None or control["batch"] > outstanding[0])):
                    await fail("invalid_request", "Invalid acknowledgement")
                    return
            elif kind == "ping" and set(control) == {"type", "id"} and _safe_control(control.get("id")):
                await websocket.send_json({"type": "pong", "id": control["id"], "server_time": live_server_time()})
            elif kind == "pong" and set(control) == {"type", "id"} and control.get("id") == pending_ping:
                pending_ping = None
            else:
                await fail("invalid_request", "Invalid control")
                return
    except WebSocketDisconnect:
        return
    finally:
        feed.unsubscribe(viewer_id)


def _maybe_remember(body: PlanBody | RunBody) -> None:
    if not body.remember:
        return
    model_settings_store.upsert_model_settings(body.model, body.snapshot_for_remember())


@app.get("/v1/model-settings/{model}")
def get_model_settings(model: str) -> dict[str, Any]:
    saved = model_settings_store.get_model_settings(model)
    if saved:
        return saved
    from kllm.run_settings import RunSettingsFields

    return {
        "model": model,
        "parameter_set": RunSettingsFields.default_snapshot(model),
        "updated_at": None,
    }


@app.get("/v1/run-capabilities")
def run_capabilities(model: str | None = None) -> dict[str, Any]:
    from kllm.chat_templates import PRESETS
    from kllm.gguf_meta import read_inference_metadata
    from kllm.run_settings import available_threads

    metadata = read_inference_metadata(model_ops.resolve_local_model(model), include_template=True) if model else None
    return {"threads_max": available_threads(),
            "template_embedded": bool(metadata and metadata.get("has_chat_template")),
            "template_text": (metadata or {}).get("chat_template_text"),
            "templates": [{"id": key, "label": label} for key, (label, _) in PRESETS.items()]}


class TemplateCheckBody(BaseModel):
    text: str


@app.post("/v1/chat-template/validate")
def validate_chat_template(body: TemplateCheckBody) -> dict[str, Any]:
    from kllm.chat_templates import MAX_TEMPLATE_BYTES
    import subprocess
    import tempfile

    if not body.text or len(body.text.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise HTTPException(400, "template must be between 1 byte and 64 KiB")
    binary = native.resolve_llama_server(str(repo_root()) if repo_root() else None)
    if not binary:
        raise HTTPException(503, "llama-server unavailable")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "draft.jinja"
        path.write_text(body.text, encoding="utf-8")
        try:
            check = subprocess.run([binary, "--jinja", "--chat-template-file", str(path), "--help"],
                                   capture_output=True, text=True, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, f"template check unavailable: {exc}") from exc
    if check.returncode:
        raise HTTPException(400, (check.stderr or check.stdout)[-2000:])
    return {"valid": True, "detail": "Engine parsed a sample message; model compatibility is unverified."}


class ModelSettingsBody(BaseModel):
    parameter_set: dict[str, Any] = Field(default_factory=dict)


@app.put("/v1/model-settings/{model}")
def put_model_settings(model: str, body: ModelSettingsBody) -> dict[str, Any]:
    """Persist per-model run settings (context, KV dtype, speculative, ...).

    Validated against ``RunSettingsFields`` so a malformed set is rejected
    with field-level errors instead of poisoning the store. The stored set is
    applied by the dashboard on model selection and merged into plan/run
    payloads — no cluster reload is required for it to take effect.
    """
    from kllm.run_settings import RunSettingsFields

    params = dict(body.parameter_set)
    params["model"] = model
    try:
        validated = RunSettingsFields.model_validate(params)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    stored = validated.model_dump(exclude={"model", "tensor_split"})
    return model_settings_store.upsert_model_settings(model, stored)


def _guard_context(body: PlanBody | RunBody) -> None:
    if not body.context_auto and body.context < 512:
        raise HTTPException(400, detail={"context": "context must be >= 512 when context_auto is false"})


@app.post("/v1/plan")
def plan(body: PlanBody, preview: bool = False) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "plan requires master role")
    _ensure_master_member(persist=True)
    _guard_context(body)
    merge_and_sanitize_extra(body)
    result = execute_plan(body, STATE.members)
    if preview:
        return result
    if STATE.process_pid:
        _maybe_remember(body)
        return result
    path = model_ops.resolve_local_model(body.model)
    STATE.last_plan = result
    STATE.model_name = path.name
    if result.get("fit_status") == "overflow":
        STATE.apply_error = result.get("hint") or "model and context exceed cluster budget (overflow)"
        STATE.apply_warning = None
    elif result.get("fit_status") == "tight":
        STATE.apply_error = None
        STATE.apply_warning = result.get("warning") or result.get("hint")
    else:
        STATE.apply_error = None
        STATE.apply_warning = None
    _persist_last_settings(body)
    STATE.save()
    _maybe_remember(body)
    return result


@app.post("/v1/run")
def run(body: RunBody) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "run requires master role")
    _ensure_master_member(persist=True)
    _guard_context(body)
    merge_and_sanitize_extra(body)
    validate_vision_for_run(body)
    plan_result = execute_plan(body, STATE.members)
    if plan_result.get("fit_status") == "overflow":
        raise HTTPException(
            400,
            plan_result.get("hint") or "model and context exceed cluster budget (overflow)",
        )
    path = model_ops.resolve_local_model(body.model)
    rpc_servers = list(plan_result.get("rpc_endpoints") or [])
    ensure_rpc_peers_reachable(rpc_servers)
    root = repo_root()
    stop_previous_master(STATE, native, runtime, load_progress, inference_activity)
    gpu_env = _gpu_visibility_env(
        (STATE.members.get(STATE.node_id) or {}).get("backend") or "cpu",
        STATE.gpu_ids,
    )
    extra = sanitize_extra_args(body.extra_args) if body.extra_args else []
    if os.getenv("KLLM_DEBUG_HARDWARE", "").lower() in ("1", "true", "yes"):
        try:
            # Optional debug module; import only when the hardware debug flag is set.
            from kllm._dbg_a1ff77 import write as _dbg_write

            _dbg_write(
                "H1,H2,H4",
                "agent/__init__.py:run",
                "run launch inputs",
                {
                    "backend": (STATE.members.get(STATE.node_id) or {}).get("backend"),
                    "gpu_ids": STATE.gpu_ids,
                    "gpu_env": gpu_env,
                    "rpc_servers": rpc_servers,
                    "tensor_split": plan_result.get("tensor_split_csv"),
                    "node_ids": plan_result.get("node_ids"),
                    "ngl": plan_result.get("ngl"),
                    "node_devices": plan_result.get("node_devices"),
                    "node_alloc_bytes": plan_result.get("node_alloc_bytes"),
                    "device_effective": plan_result.get("device_effective"),
                },
            )
        except Exception:
            pass

    launch_cfg = build_master_launch(
        body,
        plan_result,
        model_path=path,
        rpc_servers=rpc_servers,
        infer_port=STATE.infer_port,
        repo_root=str(root) if root else None,
        gpu_env=gpu_env,
        extra_args=extra,
    )
    launched = spawn_master(native, runtime, launch_cfg, ensure_master_alive_after_spawn)
    finish_master_launch(
        STATE,
        path,
        plan_result,
        launched,
        runtime,
        load_progress,
        lambda: _persist_last_settings(body),
        lambda: _maybe_remember(body),
    )
    return run_response(plan_result, launched)


@app.post("/v1/inference/stop")
def stop_inference() -> dict[str, Any]:
    """Stop llama-server and drop the loaded model. The cluster stays up."""
    if STATE.role != "master":
        raise HTTPException(400, "stop inference requires master role")
    membership_mod._stop_inference(STATE)
    STATE.model_name = None
    STATE.inference_activity = inference_activity.idle()
    STATE.save()
    return {"ok": True, "role": STATE.role, "model_name": None}


def _mount_dashboard() -> None:
    dash = _dashboard_dir()
    if not dash:
        return
    assets = dash / "_app"
    if assets.is_dir():
        app.mount("/_app", StaticFiles(directory=str(assets)), name="dashboard-assets")

    # Accept all methods so missing API routes return 404 instead of Starlette's
    # 405 Method Not Allowed when only a GET SPA catch-all matched the path.
    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def spa_fallback(full_path: str, request: Request) -> FileResponse:
        # Do not shadow API — FastAPI matches more specific routes first when registered earlier.
        if full_path.startswith("v1/") or full_path in ("health",):
            raise HTTPException(404, "not found")
        if request.method not in ("GET", "HEAD"):
            raise HTTPException(404, "not found")
        candidate = dash / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = dash / "index.html"
        if index.is_file():
            return FileResponse(
                index,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                },
            )
        raise HTTPException(404, "dashboard not built — run: cd dashboard && npm run build")


_mount_dashboard()


def create_app() -> FastAPI:
    return app
