"""Build detached status snapshots for HTTP and dashboard live-feed consumers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kllm.shared.state import ClusterState
from kllm.modules.logs.interactors.logging_control import overview as logging_overview

StatusPayload = dict[str, Any]
ConfigLoader = Callable[[], dict[str, Any]]
StatePlan = Callable[[ClusterState], dict[str, Any] | None]


class DashboardStatusService:
    """Compose public status payloads from injected application collaborators."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        *,
        load_config: ConfigLoader,
        inference_activity: Callable[[], dict[str, Any]],
        probe_capacity: Callable[[], Any],
        last_plan: StatePlan,
        download_progress: Callable[[], Any],
        rpc_cache_status: Callable[[], Any],
        update_progress: Callable[[], Any],
        software_identity: Callable[[], dict[str, Any]],
        default_device: str,
        managed_networks: Callable[[], list[dict[str, Any]]] | None = None,
        feature_flags: Callable[[], dict[str, bool]] | None = None,
        rpc_lifecycle: Callable[[], dict[str, Any]] | None = None,
        runtimes: Callable[[], dict[str, Any]] | None = None,
        network_directory: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._state = state
        self._load_config = load_config
        self._inference_activity = inference_activity
        self._probe_capacity = probe_capacity
        self._last_plan = last_plan
        self._download_progress = download_progress
        self._rpc_cache_status = rpc_cache_status
        self._update_progress = update_progress
        self._software_identity = software_identity
        self._default_device = default_device
        self._managed_networks = managed_networks
        self._feature_flags = feature_flags
        self._rpc_lifecycle = rpc_lifecycle
        self._runtimes = runtimes
        self._network_directory = network_directory

    def status(self) -> StatusPayload:
        """Build the full status response, including a fresh local capacity probe."""
        state = self._state()
        try:
            capacity = self._probe_capacity()
        except Exception as exc:
            capacity = {"error": str(exc)}
        config = self._load_config()
        activity = self._inference_activity()
        return {
            "role": state.role,
            **({"runtimes": self._runtimes()} if self._runtimes else {}),
            **({"network_directory": self._network_directory()} if self._network_directory else {}),
            "node_id": state.node_id,
            "join_key": state.join_key if state.role == "master" else None,
            "master_host": state.master_host,
            "master_port": state.master_port,
            "cp_proxy_port": state.cp_proxy_port,
            "members": state.members,
            "process_pid": state.process_pid,
            "process_role": state.process_role,
            "rpc_status": (state.members.get(state.node_id) or {}).get("rpc_status", "idle"),
            "last_plan": self._last_plan(state),
            "model_name": state.model_name,
            "download": self._download_progress(),
            "rpc_cache": self._rpc_cache_status(),
            "update": self._update_progress(),
            "software": self._software_identity(),
            "capacity": capacity,
            **self._common_state_fields(state),
            **({"rpc_lifecycle": self._rpc_lifecycle()} if self._rpc_lifecycle else {}),
            **({"managed_networks": self._managed_networks()} if self._managed_networks else {}),
            **({"feature_flags": self._feature_flags()} if self._feature_flags else {}),
            "inference_up": bool(activity.get("server_ready")),
            "inference_activity": activity,
            "device": config.get("device", self._default_device),
            "vram_percent": config.get("vram_percent"),
            "ram_percent": config.get("ram_percent"),
            "config_gpu_ids": config.get("gpu_ids"),
            "accel_backend": config.get("accel_backend"),
        }

    def live_status(self) -> StatusPayload:
        """Build a detached snapshot without hardware probes or software discovery."""
        state = self._state()
        config = self._load_config()
        member = dict(state.members.get(state.node_id) or {})
        capacity_fields = (
            "total_ram_bytes",
            "available_ram_bytes",
            "vram_bytes",
            "backend",
            "actual_backend",
            "devices",
            "rpc_devices",
            "unified_memory",
            "reclaimable_ram_bytes",
        )
        capacity = {key: member[key] for key in capacity_fields if key in member}
        activity = self._inference_activity()
        return {
            **({"runtimes": self._runtimes()} if self._runtimes else {}),
            **({"network_directory": self._network_directory()} if self._network_directory else {}),
            "role": state.role,
            "node_id": state.node_id,
            "join_key": state.join_key if state.role == "master" else None,
            "master_host": state.master_host,
            "master_port": state.master_port,
            "cp_proxy_port": state.cp_proxy_port,
            "members": {node_id: dict(value) for node_id, value in state.members.items()},
            "process_pid": state.process_pid,
            "process_role": state.process_role,
            "rpc_status": member.get("rpc_status", "idle"),
            "last_plan": self._last_plan(state),
            "model_name": state.model_name,
            "download": self._download_progress(),
            "rpc_cache": self._rpc_cache_status(),
            "update": self._update_progress(),
            "software": {
                key: member.get(key) for key in ("binary_version", "git_head", "llama_release")
            },
            "capacity": capacity,
            **self._common_state_fields(state),
            **({"rpc_lifecycle": self._rpc_lifecycle()} if self._rpc_lifecycle else {}),
            **({"managed_networks": self._managed_networks()} if self._managed_networks else {}),
            **({"feature_flags": self._feature_flags()} if self._feature_flags else {}),
            "inference_up": bool(activity.get("server_ready")),
            "inference_activity": activity,
            "device": config.get("device", self._default_device),
            "vram_percent": config.get("vram_percent"),
            "ram_percent": config.get("ram_percent"),
            "config_gpu_ids": config.get("gpu_ids"),
            "accel_backend": config.get(
                "accel_backend", member.get("actual_backend", member.get("backend"))
            ),
        }

    @staticmethod
    def _common_state_fields(state: ClusterState) -> StatusPayload:
        return {
            "active_config": state.active_config
            if state.process_pid
            and state.process_role == "master"
            and (state.active_config or {}).get("process_pid") == state.process_pid
            else None,
            "logging": logging_overview(state),
            "control_port": state.control_port,
            "rpc_port": state.rpc_port,
            "infer_port": state.infer_port,
            "gpu_ids": state.gpu_ids,
            "rpc_bind": state.rpc_bind,
            "overlay": state.overlay,
            "wg_address": state.wg_address,
            "overlay_status": state.overlay_status,
            "netsec_warnings": list(state.netsec_warnings or []),
            "apply_error": state.apply_error,
            "apply_warning": state.apply_warning,
        }
