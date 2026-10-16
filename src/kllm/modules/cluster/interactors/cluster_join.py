"""Worker-side cluster join orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from kllm.shared import netsec
from kllm.modules.cluster.interactors.join_flow import resolve_master_endpoint
from kllm.shared.schemas import JoinBody
from kllm.modules.settings.interactors.gpus import ResolvedGpuSelection
from kllm.modules.inference.interactors.rpc_devices import worker_rpc_devices
from kllm.shared.state import ClusterState


@dataclass(frozen=True)
class ClusterJoinDependencies:
    """External effects used while preparing and joining as a worker."""

    state: Callable[[], ClusterState]
    gpu_selection: Callable[[JoinBody], ResolvedGpuSelection]
    probe_capacity: Callable[[], dict[str, Any]]
    pick_rpc_bind_host: Callable[..., str]
    ensure_master_control_route: Callable[[str, int], list[str]]
    software_identity: Callable[[], dict[str, Any]]
    build_register_payload: Callable[..., dict[str, Any]]
    post_register: Callable[..., dict[str, Any]]
    drop_cp_proxy: Callable[[], None]
    apply_cluster_snapshot: Callable[[dict[str, Any]], None]
    post_worker_heartbeat: Callable[[], None]
    stop_process: Callable[..., Any] = lambda *_args, **_kwargs: None
    clear_children: Callable[[], None] = lambda: None


class ClusterJoinService:
    """Enroll the node and configure networking for later master commands."""

    def __init__(self, dependencies: ClusterJoinDependencies) -> None:
        self._deps = dependencies

    def join(self, body: JoinBody) -> dict[str, Any]:
        state = self._deps.state()
        if state.role == "master":
            raise HTTPException(409, "node is master; stop first")
        if state.overlay == "wireguard":
            raise HTTPException(409, "Leave the legacy WireGuard cluster before joining a LAN cluster")
        master_host, master_port = resolve_master_endpoint(
            body.master_host, body.master_port, body.key
        )
        gpu = self._deps.gpu_selection(body)
        state.gpu_ids = list(gpu.gpu_ids)

        try:
            capacity = self._deps.probe_capacity()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"probe_capacity failed: {exc}") from exc
        vram = gpu.vram_bytes if gpu.has_devices else capacity.get("vram_bytes")
        backend = (
            gpu.backend
            if gpu.has_devices or gpu.backend != "cpu"
            else capacity.get("backend", "cpu")
        )
        lan_host = self._deps.pick_rpc_bind_host(body.rpc_bind, toward=master_host)
        state.rpc_bind = lan_host
        state.overlay = "none"
        state.netsec_warnings = []
        state.llama_warnings = []
        state.wg_address = None
        state.wg_public_key = None
        state.overlay_status = None

        state.netsec_warnings.extend(
            self._deps.ensure_master_control_route(master_host, master_port)
        )

        identity = self._deps.software_identity()
        payload = self._deps.build_register_payload(
            node_id=state.node_id,
            lan_host=lan_host,
            control_port=state.control_port,
            rpc_port=body.rpc_port,
            cap=capacity,
            vram=vram,
            backend=backend,
            gpus=gpu.device_payloads(),
            gpu_ids=state.gpu_ids,
            git_head=identity.get("git_head"),
            llama_release=identity.get("llama_release"),
        )
        payload.update(
            rpc_capabilities=worker_rpc_devices(gpu.devices, backend),
            rpc_control_version=1,
            runtime_management_version=1,
            rpc_status="idle",
        )
        base = (
            f"http://127.0.0.1:{int(state.cp_proxy_port)}"
            if state.cp_proxy_port
            else f"http://{master_host}:{master_port}"
        )
        registration = self._deps.post_register(
            base,
            payload,
            body.key,
            master_host,
            master_port,
            self._deps.drop_cp_proxy,
        )
        self._deps.apply_cluster_snapshot(registration)

        rpc_bind_host = self._correct_rpc_bind(body, registration, lan_host, state)

        # Rejoining releases only the process already tracked by this agent.
        # Joining never starts a process or searches for unrelated RPC servers.
        if state.process_pid and state.process_role == "worker":
            self._deps.stop_process(state.process_pid, force=True)
            self._deps.clear_children()
        state.process_pid = None
        state.process_role = None

        state.role = "worker"
        state.join_key = body.key
        state.master_host = master_host
        state.master_port = master_port
        state.rpc_port = body.rpc_port
        state.rpc_bind = rpc_bind_host
        local = state.members.setdefault(state.node_id, {})
        local["host"] = rpc_bind_host
        local["lan_host"] = rpc_bind_host
        local["rpc_bind"] = rpc_bind_host
        local["rpc_capabilities"] = worker_rpc_devices(gpu.devices, backend)
        local["rpc_devices"] = []
        local["rpc_control_version"] = 1
        local["runtime_management_version"] = 1
        local["rpc_status"] = "idle"
        local.pop("rpc_owner", None)
        state.save()
        self._deps.post_worker_heartbeat()
        return {
            "role": "worker",
            "master": master_host,
            "launch": {"pid": None, "deferred": True},
            "rpc_status": "idle",
            "rpc_bind": state.rpc_bind,
            "netsec_warnings": state.netsec_warnings,
            "llama_warnings": list(state.llama_warnings or []),
            "gpu_ids": state.gpu_ids,
            "cp_proxy_port": state.cp_proxy_port,
        }

    def _correct_rpc_bind(
        self,
        body: JoinBody,
        registration: dict[str, Any],
        lan_host: str,
        state: ClusterState,
    ) -> str:
        hint = registration.get("rpc_bind_hint")
        if (
            body.rpc_bind
            or not isinstance(hint, str)
            or not netsec.should_apply_rpc_bind_hint(hint, lan_host)
        ):
            return lan_host
        state.netsec_warnings = list(state.netsec_warnings or []) + [
            f"rpc bind corrected to {hint} (master peer address)"
        ]
        return hint
