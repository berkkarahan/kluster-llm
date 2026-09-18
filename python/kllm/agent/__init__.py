from __future__ import annotations

import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import iterate_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kllm import config as kllm_config
from kllm import discovery, gateway, models as model_ops, native, netsec, runtime
from kllm.accel import allowed_accel_backends
from kllm.auth import ensure_api_token, verify_api_token
from kllm.hf_token import hf_token_status, save_hf_token
from kllm import model_settings as model_settings_store
from kllm.extra_args import sanitize_extra_args
from kllm import cluster_log
from kllm.run_planning import (
    build_master_launch,
    ensure_master_alive_after_spawn,
    ensure_rpc_peers_reachable,
    execute_plan,
    member_rpc_host,
    merge_and_sanitize_extra,
    rpc_server_endpoints,
    validate_vision_for_run,
)
from kllm.run_settings import PlanBody, RunBody
from kllm import membership as membership_mod
from kllm.state import (
    DEFAULT_CONTROL_PORT,
    DEFAULT_RPC_PORT,
    ClusterState,
    generate_join_key,
    models_dir,
    repo_root,
)

app = FastAPI(title="kllm-agent", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def _plan_run_validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path in ("/v1/plan", "/v1/run"):
        return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

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
    bind = STATE.rpc_bind
    port = STATE.rpc_port or DEFAULT_RPC_PORT
    if not bind:
        return
    STATE.netsec_warnings = netsec.refresh_rpc_firewall_warnings(port, bind)
    STATE.save()


_refresh_persisted_netsec_warnings()
# Ensure loopback inference token exists for harness connect
ensure_api_token()

_membership_lock = threading.Lock()
_membership_thread_started = False
_logs_thread_started = False


def _persist_last_settings(body: PlanBody | RunBody) -> None:
    data = body.model_dump()
    data.pop("tensor_split", None)
    STATE.last_settings = data


def _sync_master_liveness(*, shrink: bool = True) -> list[str]:
    """Sweep stale workers. Optionally replan-and-apply on shrink (master only)."""
    if STATE.role != "master":
        return []
    with _membership_lock:
        dropped = membership_mod.sweep_liveness(STATE)
        if dropped:
            STATE.save()
            if shrink:
                membership_mod.on_membership_shrink(STATE)
        return dropped


def _heartbeat_payload() -> dict[str, Any]:
    mem = STATE.members.get(STATE.node_id) or {}
    return {
        "node_id": STATE.node_id,
        "host": STATE.rpc_bind or mem.get("host") or mem.get("lan_host") or "",
        "control_port": STATE.control_port,
        "rpc_port": STATE.rpc_port,
        "total_ram_bytes": mem.get("total_ram_bytes") or 0,
        "available_ram_bytes": mem.get("available_ram_bytes") or 0,
        "vram_bytes": mem.get("vram_bytes"),
        "backend": mem.get("backend") or "unknown",
        "lan_host": mem.get("lan_host"),
        "wg_public_key": STATE.wg_public_key,
        "gpu_ids": STATE.gpu_ids,
    }


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
    import httpx

    # Re-ensure proxy if the helper died across agent restarts.
    try:
        _ensure_master_control_route(STATE.master_host, STATE.master_port)
    except HTTPException:
        membership_mod.mark_master_unreachable(STATE)
        return

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{_master_control_base()}/v1/heartbeat",
                json=_heartbeat_payload(),
                headers={"X-Kllm-Key": STATE.join_key},
            )
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
    import httpx

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
            start = time.perf_counter()
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(url)
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
        _probe_worker_latencies()
        STATE.save()
    elif STATE.role == "worker":
        _worker_post_heartbeat()


def _membership_loop() -> None:
    while True:
        time.sleep(membership_mod.HEARTBEAT_INTERVAL_S)
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
    if STATE.role == "master":
        cluster_log.HUB.ensure_journal_loaded()
    while True:
        time.sleep(cluster_log.FLUSH_INTERVAL_S)
        try:
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


if "pytest" not in sys.modules:
    _start_membership_thread()
    _start_logs_thread()


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
    lan_host: str | None = None
    wg_public_key: str | None = None
    wg_listen_port: int = netsec.WG_LISTEN_PORT
    gpu_ids: list[int] | None = None


class LogLineBody(BaseModel):
    ts: float | None = None
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
            f"{exc} On this machine run: python scripts/install.py "
            "(macOS cluster worker with a Vulkan master: "
            "uv run python scripts/install.py --backend vulkan; "
            "set KLLM_ACCEL_BACKEND=vulkan)",
        ) from exc


def _resolve_gpu_selection(gpu_ids: list[int] | None) -> dict[str, Any]:
    """Validate optional gpu_ids against list_gpus(); default = all indices.

    Returns dict with keys: gpu_ids, backend, gpus (selected device dicts),
    vram_bytes (sum of selected discrete VRAM, or None).
    """
    listed = native.list_gpus()
    backend = listed.get("backend") or "cpu"
    gpus = list(listed.get("gpus") or [])
    available = {int(g["index"]): g for g in gpus}

    if not available:
        if gpu_ids:
            raise HTTPException(400, f"no GPUs available; cannot select {gpu_ids}")
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
    if not gpu_ids:
        return {}
    csv = ",".join(str(i) for i in gpu_ids)
    if backend == "cuda":
        return {"CUDA_VISIBLE_DEVICES": csv}
    if backend == "vulkan":
        return {"GGML_VK_VISIBLE_DEVICES": csv}
    if backend == "rocm":
        return {
            "HIP_VISIBLE_DEVICES": csv,
            "ROCR_VISIBLE_DEVICES": csv,
        }
    return {}


class ConfigBody(BaseModel):
    device: Literal["cpu", "gpu"] = Field(default="gpu")
    gpu_ids: list[int] | None = None
    vram_percent: int | None = Field(default=None, ge=10, le=100)
    ram_percent: int | None = Field(default=None, ge=10, le=100)
    accel_backend: str | None = None


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
        }
    gpus = listed.get("gpus") or []
    probed = str(listed.get("probed_backend") or listed.get("backend") or "cpu").strip().lower()
    backend = str(listed.get("backend") or probed).strip().lower()
    out: dict[str, Any] = {
        "backend": backend,
        "probed_backend": probed,
        "allowed_backends": allowed_accel_backends(probed, gpus),
        "gpus": gpus,
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


class DistributeBody(BaseModel):
    name: str


class DeleteBody(BaseModel):
    name: str


class PullSource(BaseModel):
    host: str
    port: int


class PullBody(BaseModel):
    filename: str
    sha256: str
    size: int
    source: PullSource


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
    return {"ok": True, "role": STATE.role, "node_id": STATE.node_id, "pid": os.getpid()}


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


@app.post("/v1/models/download")
def download_model(body: DownloadBody) -> dict[str, Any]:
    try:
        path = model_ops.download_model(body.source, dest_name=body.name)
    except model_ops.DownloadInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "path": str(path), "name": path.name}


@app.post("/v1/models/distribute")
def distribute_model(body: DistributeBody) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "distribute requires master role")
    if not STATE.join_key:
        raise HTTPException(503, "master has no join key")
    try:
        path = model_ops.resolve_local_model(body.name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    workers = [m for m in STATE.members.values() if m.get("role") == "worker"]
    try:
        results = model_ops.distribute_to_workers(
            path,
            workers,
            STATE.join_key,
            default_port=STATE.control_port,
            master_host=_master_lan_host(),
            master_port=STATE.control_port,
        )
    except model_ops.DistributeInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "results": results}


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

    results: list[dict[str, Any]] = []
    if STATE.role == "master" and STATE.join_key:
        for m in STATE.members.values():
            if m.get("role") != "worker":
                continue
            ctrl_host = m.get("lan_host") or m.get("host")
            try:
                result = model_ops.delete_model_on_worker(
                    host=ctrl_host,
                    port=m.get("control_port", STATE.control_port),
                    join_key=STATE.join_key,
                    filename=deleted.name,
                )
                results.append({"host": ctrl_host, "ok": True, "result": result})
            except Exception as exc:
                results.append({"host": ctrl_host, "ok": False, "error": str(exc)})
    return {"ok": True, "name": deleted.name, "path": str(deleted), "results": results}


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    _: None = Depends(_require_api_token),
) -> Any:
    return await gateway.proxy_chat_completions(STATE.infer_port, request)


@app.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    _: None = Depends(_require_api_token),
) -> Any:
    return await gateway.proxy_anthropic_messages(STATE.infer_port, request)


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
            choice = body.accel_backend
            if choice is not None:
                choice = str(choice).strip().lower()
                gpus_info = _list_gpus_payload()
                allowed = gpus_info.get("allowed_backends") or []
                if choice not in allowed:
                    raise HTTPException(
                        400,
                        f"accel_backend {choice!r} not allowed on this host "
                        f"(allowed: {allowed or ['(none)']})",
                    )
            payload["accel_backend"] = choice
        return kllm_config.save_config(payload)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
        "distribute": model_ops.get_distribute_progress(),
        "receive": model_ops.get_receive_progress(),
        "capacity": capacity,
        "control_port": STATE.control_port,
        "rpc_port": STATE.rpc_port,
        "infer_port": STATE.infer_port,
        "inference_up": bool(STATE.process_pid and STATE.process_role == "master"),
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

    gpu_ids = body.gpu_ids if body.gpu_ids is not None else kllm_config.get_gpu_ids()
    gpu_sel = _resolve_gpu_selection(gpu_ids)
    STATE.gpu_ids = gpu_sel["gpu_ids"]

    cap = _probe_capacity_for_cluster()
    # Prefer selected VRAM sum when discrete GPUs were chosen
    vram = gpu_sel["vram_bytes"] if gpu_sel["gpus"] else cap.get("vram_bytes")
    backend = gpu_sel["backend"] if gpu_sel["gpus"] or gpu_sel["backend"] != "cpu" else cap.get("backend", "cpu")

    STATE.members[STATE.node_id] = {
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
        "gpu_ids": STATE.gpu_ids,
        "last_seen": time.time(),
        "latency_ms": 0.0,
    }

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
        native.stop_process(STATE.process_pid, force=True)
        STATE.process_pid = None
        STATE.process_role = None
        runtime.clear_children()
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
    try:
        overlay = netsec.normalize_overlay(body.overlay)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if overlay == netsec.OVERLAY_WIREGUARD and not netsec.wg_tooling_available():
        raise HTTPException(
            400,
            "WireGuard tooling not found (wg). Install WireGuard or use overlay=none",
        )

    master_host = body.master_host
    master_port = body.master_port
    if not master_host:
        found = discovery.browse_masters(timeout=2.5, key=body.key)
        if not found:
            raise HTTPException(
                404,
                "no matching master discovered on LAN; pass master_host or check join key",
            )
        if len(found) > 1:
            hosts = ", ".join(f"{m.host}:{m.port}" for m in found)
            raise HTTPException(
                409,
                f"multiple masters match join key; pass master_host ({hosts})",
            )
        master_host = found[0].host
        master_port = found[0].port

    gpu_ids = body.gpu_ids if body.gpu_ids is not None else kllm_config.get_gpu_ids()
    gpu_sel = _resolve_gpu_selection(gpu_ids)
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

    payload = {
        "node_id": STATE.node_id,
        "host": lan_host,
        "lan_host": lan_host,
        "control_port": STATE.control_port,
        "rpc_port": body.rpc_port,
        "total_ram_bytes": cap["total_ram_bytes"],
        "available_ram_bytes": cap["available_ram_bytes"],
        "vram_bytes": vram,
        "backend": backend,
        "gpu_ids": STATE.gpu_ids,
        "wg_public_key": wg_pub,
        "wg_listen_port": netsec.WG_LISTEN_PORT,
    }
    import httpx

    if STATE.cp_proxy_port:
        base = f"http://127.0.0.1:{int(STATE.cp_proxy_port)}"
    else:
        base = f"http://{master_host}:{master_port}"
    url = f"{base}/v1/register"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload, headers={"X-Kllm-Key": body.key})
    except httpx.TimeoutException as exc:
        from kllm import cp_proxy

        cp_proxy.stop_current()
        STATE.cp_proxy_port = None
        STATE.cp_proxy_pid = None
        raise HTTPException(
            504,
            f"master at {master_host}:{master_port} did not respond in time; "
            f"ensure `kllm start --key …` is running on the master and try "
            f"`curl http://{master_host}:{master_port}/health` from this machine",
        ) from exc
    except httpx.RequestError as exc:
        from kllm import cp_proxy

        cp_proxy.stop_current()
        STATE.cp_proxy_port = None
        STATE.cp_proxy_pid = None
        raise HTTPException(
            502,
            f"cannot reach master at {master_host}:{master_port}: {exc}",
        ) from exc
    if resp.status_code == 401:
        raise HTTPException(401, "invalid join key")
    if resp.status_code >= 400:
        try:
            err_payload = resp.json()
            detail = err_payload.get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    resp.raise_for_status()
    reg = resp.json()

    _apply_cluster_snapshot(reg)

    rpc_bind_host = lan_host
    # Master may correct a multi-NIC mis-pick to the TCP peer address that reached it.
    hint = reg.get("rpc_bind_hint")
    if (
        overlay == netsec.OVERLAY_NONE
        and not body.rpc_bind
        and isinstance(hint, str)
        and netsec.is_usable_lan_peer(hint)
        and hint != rpc_bind_host
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
    # in ~/.kllm/models (avoids GitHub SSL on some macOS venvs).
    try:
        from kllm.llama_pack import install_cached_llama_pack

        install_cached_llama_pack(backend=str(backend), force=False)
    except Exception:
        pass

    # Start local rpc-server bound to the RPC address (LAN or WG), not 0.0.0.0 by default
    root = repo_root()
    gpu_env = _gpu_visibility_env(backend, STATE.gpu_ids)
    try:
        launched = native.start_worker(
            host=rpc_bind_host,
            port=body.rpc_port,
            repo_root=str(root) if root else None,
            env=gpu_env or None,
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
        import httpx

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
        **body.model_dump(),
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
    response.update(membership_mod.snapshot_payload(STATE))
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
        update = body.model_dump()
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
        **membership_mod.snapshot_payload(STATE),
    }


def _logs_snapshot(n: int, node: str | None, source: str | None) -> dict[str, Any]:
    origin = "cluster" if STATE.role == "master" else "local"
    return {
        "lines": cluster_log.HUB.recent(n, node, source),
        "source": origin,
    }


def _logs_sse(
    n: int,
    follow: bool,
    node: str | None,
    source: str | None,
) -> StreamingResponse:
    return StreamingResponse(
        iterate_in_threadpool(cluster_log.HUB.iter_sse(n, follow, node, source)),
        media_type="text/event-stream",
    )


def _try_proxy_master_logs(
    n: int,
    follow: bool,
    node: str | None,
    source: str | None,
) -> dict[str, Any] | StreamingResponse | None:
    import httpx as httpx_mod

    params: dict[str, Any] = {"n": n, "follow": follow}
    if node:
        params["node"] = node
    if source:
        params["source"] = source
    headers = {}
    if STATE.join_key:
        headers["X-Kllm-Key"] = STATE.join_key
    url = f"{_master_control_base()}/v1/logs"
    try:
        if follow:
            client = httpx_mod.Client(timeout=None)
            try:
                resp = client.send(
                    client.build_request("GET", url, params=params, headers=headers),
                    stream=True,
                )
            except Exception:
                client.close()
                return None
            if resp.status_code != 200:
                resp.close()
                client.close()
                return None

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
        with httpx_mod.Client(timeout=2.0) as client:
            resp = client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


@app.post("/v1/logs")
def ingest_logs(
    body: LogsIngestBody,
    _: None = Depends(_require_key),
) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(403, "only the master accepts log ingest")
    events = [
        cluster_log.make_event(body.node_id, item.source, item.line, ts=item.ts)
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
):
    n = max(1, min(int(n), 5000))
    if source and source not in cluster_log.VALID_SOURCES:
        raise HTTPException(400, "source must be agent, llama, or rpc")
    if STATE.role == "master":
        cluster_log.HUB.ensure_journal_loaded()
    if STATE.role == "worker" and STATE.master_host:
        proxied = _try_proxy_master_logs(n, follow, node, source)
        if proxied is not None:
            return proxied
        warning = "master unreachable; showing local logs"
        if follow:
            def _gen_local():
                yield cluster_log.encode_sse(
                    cluster_log.make_event("kllm", "agent", warning)
                )
                yield from cluster_log.HUB.iter_sse(n, True, node, source)

            return StreamingResponse(
                iterate_in_threadpool(_gen_local()),
                media_type="text/event-stream",
            )
        snap = _logs_snapshot(n, node, source)
        snap["warning"] = warning
        snap["source"] = "local"
        return snap
    if follow:
        return _logs_sse(n, True, node, source)
    return _logs_snapshot(n, node, source)


@app.get("/v1/models/blob")
def get_model_blob(
    request: Request,
    x_kllm_key: str | None = Header(default=None),
    x_kllm_filename: str = Header(...),
):
    """Seed endpoint: Range-capable GGUF bytes (join-key auth)."""
    if STATE.join_key and x_kllm_key != STATE.join_key:
        raise HTTPException(401, "invalid join key")
    filename = Path(x_kllm_filename).name
    dest = models_dir() / filename
    if not dest.is_file():
        raise HTTPException(404, f"model not found: {filename}")
    file_size = dest.stat().st_size
    try:
        start, end, partial = model_ops.parse_bytes_range(
            request.headers.get("range"), file_size
        )
    except model_ops.RangeUnsatisfiable:
        raise HTTPException(
            416,
            "range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        ) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start),
        "X-Kllm-Filename": filename,
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end - 1}/{file_size}"
        status = 206
    else:
        status = 200
    return StreamingResponse(
        iterate_in_threadpool(model_ops.iter_file_range(dest, start, end)),
        status_code=status,
        media_type="application/octet-stream",
        headers=headers,
    )


@app.post("/v1/models/pull")
def pull_model(
    body: PullBody,
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Worker: pull a GGUF from one assigned seed via Range GETs."""
    if STATE.join_key and x_kllm_key != STATE.join_key:
        raise HTTPException(401, "invalid join key")
    filename = Path(body.filename).name
    dest = models_dir() / filename
    expected = (body.sha256 or "").lower()
    if dest.is_file():
        meta = model_ops.local_model_meta(dest)
        if (
            int(meta.get("size_bytes") or 0) == body.size
            and (meta.get("sha256") or "").lower() == expected
        ):
            return {
                "ok": True,
                "skipped": True,
                "path": str(dest),
                "sha256": expected,
            }
    try:
        return model_ops.pull_from_seed(
            filename=filename,
            size=body.size,
            digest=expected,
            source_host=body.source.host,
            source_port=body.source.port,
            join_key=x_kllm_key or "",
        )
    except model_ops.ReceiveInProgressError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/models/file")
def get_model_file_meta(
    x_kllm_key: str | None = Header(default=None),
    x_kllm_filename: str = Header(...),
) -> dict[str, Any]:
    """Probe whether a model exists locally (size + sidecar sha256)."""
    if STATE.join_key and x_kllm_key != STATE.join_key:
        raise HTTPException(401, "invalid join key")
    filename = Path(x_kllm_filename).name
    dest = models_dir() / filename
    if not dest.is_file():
        raise HTTPException(404, f"model not found: {filename}")
    meta = model_ops.local_model_meta(dest)
    meta["path"] = str(dest)
    return meta


@app.delete("/v1/models/file")
def delete_model_file(
    x_kllm_key: str | None = Header(default=None),
    x_kllm_filename: str = Header(...),
) -> dict[str, Any]:
    """Worker endpoint: delete a received model file (authenticated with join key)."""
    if STATE.join_key and x_kllm_key != STATE.join_key:
        raise HTTPException(401, "invalid join key")
    filename = Path(x_kllm_filename).name
    dest = models_dir() / filename
    if not dest.is_file():
        raise HTTPException(404, f"model not found: {filename}")
    try:
        deleted = model_ops.delete_local_model(filename)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400 if isinstance(exc, ValueError) else 404, str(exc)) from exc
    return {"ok": True, "path": str(deleted), "name": deleted.name}


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


def _guard_context(body: PlanBody | RunBody) -> None:
    if not body.context_auto and body.context < 512:
        raise HTTPException(400, detail={"context": "context must be >= 512 when context_auto is false"})


@app.post("/v1/plan")
def plan(body: PlanBody) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "plan requires master role")
    _guard_context(body)
    merge_and_sanitize_extra(body)
    result = execute_plan(body, STATE.members)
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
    rpc_servers = rpc_server_endpoints(STATE.members)
    ensure_rpc_peers_reachable(rpc_servers)
    root = repo_root()
    if STATE.process_pid:
        native.stop_process(STATE.process_pid, force=True)
        runtime.clear_children()
    gpu_env = _gpu_visibility_env(
        (STATE.members.get(STATE.node_id) or {}).get("backend") or "cpu",
        STATE.gpu_ids,
    )
    extra = sanitize_extra_args(body.extra_args) if body.extra_args else []
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
    launched: dict[str, Any] | None = None
    try:
        launched = native.start_master(**launch_cfg)
        ensure_master_alive_after_spawn(launched.get("pid"))
    except HTTPException:
        if launched and launched.get("pid"):
            native.stop_process(int(launched["pid"]), force=True)
            runtime.clear_children()
        raise
    except Exception as exc:
        if launched and launched.get("pid"):
            native.stop_process(int(launched["pid"]), force=True)
            runtime.clear_children()
        raise HTTPException(500, str(exc)) from exc
    STATE.process_pid = launched["pid"]
    STATE.process_role = "master"
    STATE.model_name = path.name
    STATE.last_plan = plan_result
    STATE.apply_error = None
    STATE.apply_warning = plan_result.get("warning") if plan_result.get("fit_status") == "tight" else None
    _persist_last_settings(body)
    if launched.get("pid"):
        runtime.register_child(int(launched["pid"]), "llama-server")
    STATE.save()
    _maybe_remember(body)
    out: dict[str, Any] = {"plan": plan_result, "launch": launched}
    if plan_result.get("fit_status") == "tight":
        out["warning"] = plan_result.get("warning") or plan_result.get("hint")
    return out


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
