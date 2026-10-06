"""HTTP handlers for leaving a cluster and stopping local cluster services."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from kllm import netsec
from kllm.agent.schemas import StartBody
from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.state import ClusterState


@dataclass(frozen=True)
class MasterStartDependencies:
    """External effects required to configure and advertise a master node."""

    state: Callable[[], ClusterState]
    normalize_overlay: Callable[[str], str]
    generate_join_key: Callable[[], str]
    pick_rpc_bind_host: Callable[[str | None], str]
    select_gpus: Callable[[StartBody], ResolvedGpuSelection]
    build_master_member: Callable[[str], dict[str, Any]]
    setup_overlay: Callable[[str, str], None]
    advertise: Callable[[str, int, str, str], None]


class MasterStartHandler:
    """Configure this node as cluster master and begin LAN advertisement."""

    def __init__(self, dependencies: MasterStartDependencies) -> None:
        self._deps = dependencies

    def start(self, body: StartBody) -> dict[str, Any]:
        state = self._deps.state()
        if state.role == "worker":
            raise HTTPException(409, "node is a worker; leave first")
        try:
            overlay = self._deps.normalize_overlay(body.overlay)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        state.role = "master"
        join_key = body.key or state.join_key or self._deps.generate_join_key()
        state.join_key = join_key
        state.control_port = body.control_port
        state.rpc_port = body.rpc_port
        state.master_host = None
        state.overlay = overlay
        state.netsec_warnings = []
        state.wg_address = None
        state.wg_public_key = None
        state.overlay_status = None
        state.apply_error = None
        state.apply_warning = None

        lan_host = self._deps.pick_rpc_bind_host(body.rpc_bind)
        state.rpc_bind = lan_host
        gpu = self._deps.select_gpus(body)
        state.gpu_ids = list(gpu.gpu_ids)
        state.members[state.node_id] = self._deps.build_master_member(lan_host)

        if overlay == netsec.OVERLAY_WIREGUARD:
            self._deps.setup_overlay(join_key, lan_host)
        else:
            state.overlay_status = "disabled"
        state.save()
        self._deps.advertise(
            f"kllm-{state.node_id}",
            state.control_port,
            join_key,
            lan_host,
        )
        return {
            "role": "master",
            "join_key": state.join_key,
            "node_id": state.node_id,
            "rpc_bind": state.rpc_bind,
            "overlay": state.overlay,
            "wg_address": state.wg_address,
            "overlay_status": state.overlay_status,
            "netsec_warnings": state.netsec_warnings,
            "gpu_ids": state.gpu_ids,
        }


class ClusterLifecycleHandlers:
    """Apply local cleanup workflows through injected process and network effects."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        end_usage: Callable[[str], None],
        stop_process: Callable[..., Any],
        clear_children: Callable[[], None],
        end_load_progress: Callable[[], None],
        idle_activity: Callable[[], dict[str, Any]],
        stop_advertiser: Callable[[], None],
        reset_network: Callable[[], None],
        clear_membership: Callable[[ClusterState], None],
        unregister_worker: Callable[[], None],
        before_network_reset: Callable[[], None] | None = None,
    ) -> None:
        self._state = state
        self._end_usage = end_usage
        self._stop_process = stop_process
        self._clear_children = clear_children
        self._end_load_progress = end_load_progress
        self._idle_activity = idle_activity
        self._stop_advertiser = stop_advertiser
        self._reset_network = reset_network
        self._clear_membership = clear_membership
        self._unregister_worker = unregister_worker
        self._before_network_reset = before_network_reset

    def stop(self) -> dict[str, str]:
        state = self._state()
        state.active_config = None
        if state.process_pid:
            try:
                self._end_usage(state.node_id)
            except (OSError, sqlite3.Error):
                pass
            self._stop_process(state.process_pid, force=True)
            state.process_pid = None
            state.process_role = None
            self._clear_children()
            self._end_load_progress()
            state.inference_activity = self._idle_activity()
        if state.role == "master":
            self._stop_advertiser()
            state.join_key = None
        if self._before_network_reset:
            self._before_network_reset()
        self._reset_network()
        self._clear_membership(state)
        state.role = "idle"
        state.master_host = None
        state.gpu_ids = None
        state.save()
        return {"role": "idle"}

    def leave(self) -> dict[str, str]:
        state = self._state()
        state.active_config = None
        if state.role == "worker" and state.master_host and state.join_key:
            try:
                self._unregister_worker()
            except Exception:
                pass
        if state.process_pid:
            self._stop_process(state.process_pid, force=True)
            state.process_pid = None
            state.process_role = None
            self._clear_children()
        if self._before_network_reset:
            self._before_network_reset()
        self._reset_network()
        self._clear_membership(state)
        state.role = "idle"
        state.join_key = None
        state.master_host = None
        state.gpu_ids = None
        state.save()
        return {"role": "idle"}
