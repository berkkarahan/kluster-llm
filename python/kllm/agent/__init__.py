from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kllm import config as kllm_config
from kllm import discovery, gateway, models as model_ops, native, netsec
from kllm.auth import ensure_api_token, verify_api_token
from kllm.state import (
    DEFAULT_CONTROL_PORT,
    DEFAULT_RPC_PORT,
    ClusterState,
    generate_join_key,
    models_dir,
    repo_root,
)

app = FastAPI(title="kllm-agent", version="0.1.0")
_advertiser: discovery.MasterAdvertiser | None = None
STATE = ClusterState.load()
if not STATE.node_id:
    STATE.node_id = secrets.token_hex(4)
    STATE.save()
# Ensure loopback inference token exists for harness connect
ensure_api_token()


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


class JoinBody(BaseModel):
    key: str
    master_host: str
    master_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    rpc_bind: str | None = None
    overlay: str = netsec.OVERLAY_NONE


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


class RunBody(BaseModel):
    model: str
    context: int = 4096
    headroom: float = 0.80
    tensor_split: list[float] | None = None
    threads: int = 8
    micro_batch: int = 2
    device: Optional[Literal["cpu", "gpu"]] = None


class PlanBody(BaseModel):
    model: str
    context: int = 4096
    headroom: float = 0.80
    tensor_split: list[float] | None = None
    device: Optional[Literal["cpu", "gpu"]] = None


class ConfigBody(BaseModel):
    device: Literal["cpu", "gpu"] = Field(default="gpu")


class DownloadBody(BaseModel):
    source: str
    name: str | None = None


class DistributeBody(BaseModel):
    name: str


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "role": STATE.role, "node_id": STATE.node_id}


@app.get("/v1/discover")
def discover(timeout: float = 2.0) -> dict[str, Any]:
    found = discovery.browse_masters(timeout=timeout)
    return {
        "masters": [{"name": m.name, "host": m.host, "port": m.port} for m in found],
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


@app.post("/v1/models/download")
def download_model(body: DownloadBody) -> dict[str, Any]:
    try:
        path = model_ops.download_model(body.source, dest_name=body.name)
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
    results = []
    for m in STATE.members.values():
        if m.get("role") != "worker":
            continue
        # Control plane stays on LAN even when RPC rides WireGuard
        ctrl_host = m.get("lan_host") or m.get("host")
        try:
            result = model_ops.push_model_to_worker(
                host=ctrl_host,
                port=m.get("control_port", STATE.control_port),
                join_key=STATE.join_key,
                model_path=path,
            )
            results.append({"host": ctrl_host, "ok": True, "result": result})
        except Exception as exc:
            results.append({"host": ctrl_host, "ok": False, "error": str(exc)})
    return {"ok": True, "results": results}


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


@app.get("/v1/config")
def get_config() -> dict[str, Any]:
    return kllm_config.load_config()


@app.put("/v1/config")
def put_config(body: ConfigBody) -> dict[str, Any]:
    try:
        return kllm_config.save_config({"device": body.device})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/status")
def status() -> dict[str, Any]:
    capacity = None
    try:
        capacity = native.probe_capacity()
    except Exception as exc:
        capacity = {"error": str(exc)}
    return {
        "role": STATE.role,
        "node_id": STATE.node_id,
        "join_key": STATE.join_key if STATE.role == "master" else None,
        "master_host": STATE.master_host,
        "members": STATE.members,
        "process_pid": STATE.process_pid,
        "process_role": STATE.process_role,
        "last_plan": STATE.last_plan,
        "model_name": STATE.model_name,
        "capacity": capacity,
        "control_port": STATE.control_port,
        "rpc_port": STATE.rpc_port,
        "infer_port": STATE.infer_port,
        "inference_up": bool(STATE.process_pid and STATE.process_role == "master"),
        "device": kllm_config.get_device(),
        "rpc_bind": STATE.rpc_bind,
        "overlay": STATE.overlay,
        "wg_address": STATE.wg_address,
        "overlay_status": STATE.overlay_status,
        "netsec_warnings": list(STATE.netsec_warnings or []),
    }


def _reset_netsec_state() -> None:
    if STATE.overlay == netsec.OVERLAY_WIREGUARD:
        netsec.tear_down_wireguard()
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

    lan_host = netsec.pick_rpc_bind_host(body.rpc_bind)
    STATE.rpc_bind = lan_host
    fw = netsec.ensure_rpc_firewall(body.rpc_port, lan_host)
    if fw.get("warning"):
        STATE.netsec_warnings.append(str(fw["warning"]))

    cap = native.probe_capacity()
    STATE.members[STATE.node_id] = {
        "node_id": STATE.node_id,
        "host": lan_host,
        "lan_host": lan_host,
        "control_port": STATE.control_port,
        "rpc_port": STATE.rpc_port,
        "role": "master",
        "total_ram_bytes": cap["total_ram_bytes"],
        "available_ram_bytes": cap["available_ram_bytes"],
        "vram_bytes": cap["vram_bytes"],
        "backend": cap["backend"],
        "last_seen": time.time(),
    }

    if overlay == netsec.OVERLAY_WIREGUARD:
        _setup_master_overlay(STATE.join_key, lan_host)
    else:
        STATE.overlay_status = "disabled"

    STATE.save()

    if _advertiser:
        _advertiser.stop()
    _advertiser = discovery.MasterAdvertiser(f"kllm-{STATE.node_id}", STATE.control_port)
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
    }


@app.post("/v1/stop")
def stop() -> dict[str, Any]:
    global _advertiser
    if STATE.process_pid:
        native.stop_process(STATE.process_pid, force=True)
        STATE.process_pid = None
        STATE.process_role = None
    if STATE.role == "master":
        if _advertiser:
            _advertiser.stop()
            _advertiser = None
        STATE.members.clear()
        STATE.join_key = None
    _reset_netsec_state()
    STATE.role = "idle"
    STATE.master_host = None
    STATE.save()
    return {"role": "idle"}


@app.post("/v1/join")
def join_cluster(body: JoinBody) -> dict[str, Any]:
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

    cap = native.probe_capacity()
    lan_host = netsec.pick_rpc_bind_host(body.rpc_bind)
    STATE.rpc_bind = lan_host
    STATE.overlay = overlay
    STATE.netsec_warnings = []
    STATE.wg_address = None
    STATE.wg_public_key = None
    STATE.overlay_status = None

    fw = netsec.ensure_rpc_firewall(body.rpc_port, lan_host)
    if fw.get("warning"):
        STATE.netsec_warnings.append(str(fw["warning"]))

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
        "vram_bytes": cap["vram_bytes"],
        "backend": cap["backend"],
        "wg_public_key": wg_pub,
        "wg_listen_port": netsec.WG_LISTEN_PORT,
    }
    import httpx

    url = f"http://{body.master_host}:{body.master_port}/v1/register"
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(url, json=payload, headers={"X-Kllm-Key": body.key})
        if resp.status_code == 401:
            raise HTTPException(401, "invalid join key")
        resp.raise_for_status()
        reg = resp.json()

    rpc_bind_host = lan_host
    if overlay == netsec.OVERLAY_WIREGUARD:
        try:
            rpc_bind_host = _worker_bring_up_wg(body.key, body.master_host, reg)
        except Exception as exc:
            raise HTTPException(500, f"WireGuard overlay failed: {exc}") from exc

    # Start local rpc-server bound to the RPC address (LAN or WG), not 0.0.0.0 by default
    root = repo_root()
    try:
        launched = native.start_worker(
            host=rpc_bind_host,
            port=body.rpc_port,
            repo_root=str(root) if root else None,
        )
        STATE.process_pid = launched["pid"]
        STATE.process_role = "worker"
    except Exception as exc:
        # Allow join without binary for dry setups
        launched = {"error": str(exc), "pid": None}

    STATE.role = "worker"
    STATE.join_key = body.key
    STATE.master_host = body.master_host
    STATE.master_port = body.master_port
    STATE.rpc_port = body.rpc_port
    STATE.rpc_bind = rpc_bind_host
    STATE.save()
    return {
        "role": "worker",
        "master": body.master_host,
        "launch": launched,
        "rpc_bind": STATE.rpc_bind,
        "overlay": STATE.overlay,
        "wg_address": STATE.wg_address,
        "overlay_status": STATE.overlay_status,
        "netsec_warnings": STATE.netsec_warnings,
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
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    f"http://{STATE.master_host}:{STATE.master_port}/v1/unregister",
                    json={"node_id": STATE.node_id},
                    headers={"X-Kllm-Key": STATE.join_key},
                )
        except Exception:
            pass
    if STATE.process_pid:
        native.stop_process(STATE.process_pid, force=True)
        STATE.process_pid = None
        STATE.process_role = None
    _reset_netsec_state()
    STATE.role = "idle"
    STATE.master_host = None
    STATE.save()
    return {"role": "idle"}


@app.post("/v1/register")
def register(body: RegisterBody, _: None = Depends(_require_key)) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "not a master")

    lan_host = body.lan_host or body.host
    member: dict[str, Any] = {
        **body.model_dump(),
        "lan_host": lan_host,
        "role": "worker",
        "last_seen": time.time(),
    }

    response: dict[str, Any] = {"ok": True}

    if STATE.overlay == netsec.OVERLAY_WIREGUARD:
        if not body.wg_public_key:
            raise HTTPException(400, "wg_public_key required when master overlay=wireguard")
        wg_addr = netsec.next_wg_address(STATE.members, master=False)
        member["wg_address"] = wg_addr
        member["wg_public_key"] = body.wg_public_key
        member["host"] = wg_addr  # master dials RPC over overlay
        psk = netsec.derive_wg_psk(STATE.join_key or "")
        endpoint = f"{lan_host}:{body.wg_listen_port or netsec.WG_LISTEN_PORT}"
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
    response["members"] = len(STATE.members)
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
    STATE.members.pop(body.get("node_id", ""), None)
    STATE.save()
    return {"ok": True}


@app.post("/v1/heartbeat")
def heartbeat(body: RegisterBody, _: None = Depends(_require_key)) -> dict[str, Any]:
    if body.node_id in STATE.members:
        STATE.members[body.node_id].update(body.model_dump())
        STATE.members[body.node_id]["last_seen"] = time.time()
        STATE.save()
    return {"ok": True}


@app.put("/v1/models/receive")
async def receive_model(
    request: Request,
    x_kllm_key: str | None = Header(default=None),
    x_kllm_filename: str = Header(...),
    x_kllm_sha256: str = Header(...),
    x_kllm_size: int = Header(...),
) -> dict[str, Any]:
    # Workers receive from master — authenticate with the cluster key
    if STATE.join_key and x_kllm_key != STATE.join_key:
        raise HTTPException(401, "invalid join key")
    dest = models_dir() / Path(x_kllm_filename).name
    tmp = dest.with_suffix(dest.suffix + ".partial")
    size = 0
    with tmp.open("wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            size += len(chunk)
    if size != x_kllm_size:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"size mismatch: got {size} expected {x_kllm_size}")
    digest = model_ops.sha256_file(tmp)
    if digest != x_kllm_sha256:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "sha256 mismatch")
    tmp.replace(dest)
    return {"ok": True, "path": str(dest), "sha256": digest}


@app.post("/v1/plan")
def plan(body: PlanBody) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "plan requires master role")
    path = model_ops.resolve_local_model(body.model)
    nodes = []
    for m in STATE.members.values():
        nodes.append(
            {
                "node_id": m["node_id"],
                "total_ram_bytes": m.get("total_ram_bytes", 0),
                "available_ram_bytes": m.get("available_ram_bytes", 0),
                "vram_bytes": m.get("vram_bytes"),
                "backend": m.get("backend", "unknown"),
                "unified_memory": m.get("backend") == "metal",
            }
        )
    if not nodes:
        raise HTTPException(400, "no cluster members")
    device = kllm_config.resolve_device(body.device)
    try:
        result = native.plan_split(
            nodes=nodes,
            model_size_bytes=path.stat().st_size,
            context=body.context,
            headroom=body.headroom,
            tensor_split=body.tensor_split,
            device=device,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    STATE.last_plan = result
    STATE.model_name = path.name
    STATE.save()
    return result


@app.post("/v1/run")
def run(body: RunBody) -> dict[str, Any]:
    if STATE.role != "master":
        raise HTTPException(400, "run requires master role")
    plan_result = plan(
        PlanBody(
            model=body.model,
            context=body.context,
            headroom=body.headroom,
            tensor_split=body.tensor_split,
            device=body.device,
        )
    )
    path = model_ops.resolve_local_model(body.model)
    rpc_servers = []
    for m in STATE.members.values():
        if m.get("role") == "worker":
            rpc_servers.append(f"{m['host']}:{m.get('rpc_port', DEFAULT_RPC_PORT)}")
    root = repo_root()
    if STATE.process_pid:
        native.stop_process(STATE.process_pid, force=True)
    try:
        launched = native.start_master(
            model=str(path),
            tensor_split=plan_result["tensor_split_csv"],
            ngl=plan_result["ngl"],
            context=body.context,
            threads=body.threads,
            micro_batch=body.micro_batch,
            rpc_servers=rpc_servers,
            repo_root=str(root) if root else None,
            port=STATE.infer_port,
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    STATE.process_pid = launched["pid"]
    STATE.process_role = "master"
    STATE.model_name = path.name
    STATE.save()
    return {"plan": plan_result, "launch": launched}


def _mount_dashboard() -> None:
    dash = _dashboard_dir()
    if not dash:
        return
    assets = dash / "_app"
    if assets.is_dir():
        app.mount("/_app", StaticFiles(directory=str(assets)), name="dashboard-assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        # Do not shadow API — FastAPI matches more specific routes first when registered earlier.
        if full_path.startswith("v1/") or full_path in ("health",):
            raise HTTPException(404)
        candidate = dash / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = dash / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(404, "dashboard not built — run: cd dashboard && npm run build")


_mount_dashboard()


def create_app() -> FastAPI:
    return app
