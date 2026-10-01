"""Worker-side cluster join orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm import netsec
from kllm.agent.join_flow import require_join_overlay, resolve_master_endpoint
from kllm.agent.schemas import JoinBody
from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.agent.services.rpc_devices import worker_rpc_devices
from kllm.core.gpu_selection import GpuDeviceInput
from kllm.state import ClusterState


@dataclass(frozen=True)
class ClusterJoinDependencies:
    """External effects used while preparing and joining as a worker."""

    state: Callable[[], ClusterState]
    gpu_selection: Callable[[JoinBody], ResolvedGpuSelection]
    probe_capacity: Callable[[], dict[str, Any]]
    pick_rpc_bind_host: Callable[..., str]
    ensure_rpc_firewall: Callable[[int, str], dict[str, Any]]
    ensure_master_control_route: Callable[[str, int], list[str]]
    load_or_create_keypair: Callable[[], tuple[str, str]]
    software_identity: Callable[[], dict[str, Any]]
    build_register_payload: Callable[..., dict[str, Any]]
    post_register: Callable[..., dict[str, Any]]
    drop_cp_proxy: Callable[[], None]
    apply_cluster_snapshot: Callable[[dict[str, Any]], None]
    worker_bring_up_wireguard: Callable[[str, dict[str, Any]], str]
    cached_llama_pack_warning: Callable[[str], str | None]
    gpu_visibility_env: Callable[[str, list[int] | None], dict[str, str]]
    repo_root: Callable[[], Path | None]
    start_worker: Callable[..., dict[str, Any]]
    register_child: Callable[[int, str], None]
    rpc_server_log_path: Callable[[], Path]


class ClusterJoinService:
    """Join the current node to a master and start its local RPC worker."""

    def __init__(self, dependencies: ClusterJoinDependencies) -> None:
        self._deps = dependencies

    def join(self, body: JoinBody) -> dict[str, Any]:
        state = self._deps.state()
        if state.role == "master":
            raise HTTPException(409, "node is master; stop first")
        overlay = require_join_overlay(body.overlay)
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
        state.overlay = overlay
        state.netsec_warnings = []
        state.llama_warnings = []
        state.wg_address = None
        state.wg_public_key = None
        state.overlay_status = None

        firewall = self._deps.ensure_rpc_firewall(body.rpc_port, lan_host)
        if firewall.get("warning"):
            state.netsec_warnings.append(str(firewall["warning"]))
        state.netsec_warnings.extend(
            self._deps.ensure_master_control_route(master_host, master_port)
        )

        wg_public_key: str | None = None
        if overlay == netsec.OVERLAY_WIREGUARD:
            try:
                _, wg_public_key = self._deps.load_or_create_keypair()
                state.wg_public_key = wg_public_key
            except Exception as exc:
                raise HTTPException(500, f"WireGuard keygen failed: {exc}") from exc

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
            wg_public_key=wg_public_key,
            git_head=identity.get("git_head"),
            llama_release=identity.get("llama_release"),
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

        rpc_bind_host = self._correct_rpc_bind(overlay, body, registration, lan_host, state)
        if overlay == netsec.OVERLAY_WIREGUARD:
            try:
                rpc_bind_host = self._deps.worker_bring_up_wireguard(
                    body.key, registration
                )
            except Exception as exc:
                raise HTTPException(500, f"WireGuard overlay failed: {exc}") from exc

        pack_warning = self._deps.cached_llama_pack_warning(str(backend))
        if pack_warning:
            state.llama_warnings = [*state.llama_warnings, pack_warning]
        launched = self._launch_worker(body, rpc_bind_host, backend, gpu.devices, state)

        state.role = "worker"
        state.join_key = body.key
        state.master_host = master_host
        state.master_port = master_port
        state.rpc_port = body.rpc_port
        state.rpc_bind = rpc_bind_host
        state.save()
        return {
            "role": "worker",
            "master": master_host,
            "launch": launched,
            "rpc_bind": state.rpc_bind,
            "overlay": state.overlay,
            "wg_address": state.wg_address,
            "overlay_status": state.overlay_status,
            "netsec_warnings": state.netsec_warnings,
            "llama_warnings": list(state.llama_warnings or []),
            "gpu_ids": state.gpu_ids,
            "cp_proxy_port": state.cp_proxy_port,
        }

    def _correct_rpc_bind(
        self,
        overlay: str,
        body: JoinBody,
        registration: dict[str, Any],
        lan_host: str,
        state: ClusterState,
    ) -> str:
        hint = registration.get("rpc_bind_hint")
        if (
            overlay != netsec.OVERLAY_NONE
            or body.rpc_bind
            or not isinstance(hint, str)
            or not netsec.should_apply_rpc_bind_hint(hint, lan_host)
        ):
            return lan_host
        state.netsec_warnings = list(state.netsec_warnings or []) + [
            f"rpc bind corrected to {hint} (master peer address)"
        ]
        return hint

    def _launch_worker(
        self,
        body: JoinBody,
        rpc_bind_host: str,
        backend: str,
        gpus: tuple[GpuDeviceInput, ...],
        state: ClusterState,
    ) -> dict[str, Any]:
        root = self._deps.repo_root()
        rpc_devices = worker_rpc_devices(gpus, backend)
        member = state.members.setdefault(state.node_id, {})
        member["rpc_devices"] = []
        try:
            launched = self._deps.start_worker(
                host=rpc_bind_host,
                port=body.rpc_port,
                repo_root=str(root) if root else None,
                # --device is authoritative. Visibility masks would renumber
                # CUDA/Vulkan devices and invalidate the advertised RPC indices.
                env=None,
                devices=[device["device"] for device in rpc_devices],
                log_path=str(self._deps.rpc_server_log_path()),
            )
            state.process_pid = launched["pid"]
            state.process_role = "worker"
            if launched.get("pid"):
                member["rpc_devices"] = rpc_devices
                self._deps.register_child(int(launched["pid"]), "rpc-server")
            return launched
        except Exception as exc:
            # A worker may join before its local RPC binary has been installed.
            return {"error": str(exc), "pid": None}
