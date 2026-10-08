"""Install the pinned llama.cpp runtime and refresh a worker's RPC process."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kllm.agent.schemas import LlamaSetupBody
from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.agent.services.rpc_devices import worker_rpc_devices
from kllm.exceptions import NotFoundError, ValidationError
from kllm.run_settings import cpu_batch_threads
from kllm.state import DEFAULT_RPC_PORT, ClusterState

PackInstaller = Callable[[str | None, str, bool], dict[str, Any]]
WorkerStarter = Callable[..., dict[str, Any]]
GpuSelection = Callable[[list[int] | None], ResolvedGpuSelection]

# rpc-server loads backends before binding the RPC port, so a failed bind is not
# visible immediately after spawn. Sample a few times before declaring success.
_POST_SPAWN_ALIVE_CHECKS = 4
_POST_SPAWN_ALIVE_INTERVAL_S = 0.15
_LAUNCH_FAILED_MESSAGE = (
    "rpc-server exited immediately after start; check the RPC port is free and the "
    "rpc-server binary is installed"
)


@dataclass(frozen=True)
class RuntimeSetupResult:
    """Outcome of pack installation and any required worker relaunch."""

    installation: dict[str, Any]
    relaunch: dict[str, Any] | None

    def as_payload(self) -> dict[str, Any]:
        """Return the stable HTTP response payload."""
        return {**self.installation, "relaunch": self.relaunch}


class RuntimeSetupService:
    """Coordinate local runtime installation and worker process replacement."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        *,
        install_pack: PackInstaller,
        repository_root: Callable[[], Path | None],
        gpu_visibility_env: Callable[[str, list[int] | None], dict[str, str]],
        stop_process: Callable[..., Any],
        clear_children: Callable[[], None],
        start_worker: WorkerStarter,
        resolve_gpu_selection: GpuSelection,
        register_child: Callable[[int, str], None],
        rpc_log_path: Callable[[], Path],
        process_alive: Callable[[int], bool] = lambda _pid: True,
        stop_stray_rpc_servers: Callable[[], int] = lambda: 0,
    ) -> None:
        self._state = state
        self._install_pack = install_pack
        self._repository_root = repository_root
        self._gpu_visibility_env = gpu_visibility_env
        self._stop_process = stop_process
        self._clear_children = clear_children
        self._start_worker = start_worker
        self._resolve_gpu_selection = resolve_gpu_selection
        self._register_child = register_child
        self._rpc_log_path = rpc_log_path
        self._process_alive = process_alive
        self._stop_stray_rpc_servers = stop_stray_rpc_servers

    def setup(self, body: LlamaSetupBody) -> RuntimeSetupResult:
        """Install the selected pack and restart a worker RPC server if needed."""
        state = self._state()
        if state.process_pid and state.process_role == "worker" and self._process_alive(state.process_pid):
            raise ValidationError("Stop the model on its master before replacing the worker runtime")
        try:
            result = self._install_pack(body.tag, body.backend, body.force)
        except FileNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        except Exception as exc:
            raise ValidationError(str(exc)) from exc

        return RuntimeSetupResult(result, None)

    def ensure_worker(self, state: ClusterState) -> dict[str, Any] | None:
        """Restart a joined worker's rpc-server when its process is missing or dead.

        Returns the launch result when a restart was attempted, otherwise ``None``
        for a healthy or non-worker node. This makes a connected cluster recover
        instead of staying "connected" with no RPC worker to serve on.
        """
        # Kept for callers upgrading from the old worker reconciler. Membership
        # and installation cannot grant permission to launch an RPC process.
        return None

    def start_commanded_worker(self, state: ClusterState) -> dict[str, Any]:
        """Start RPC only after the signed runtime service grants ownership."""
        return self._relaunch_worker(state)

    def _relaunch_worker(self, state: ClusterState) -> dict[str, Any]:
        launched = None
        try:
            root = self._repository_root()
            member = state.members.setdefault(state.node_id, {})
            member["rpc_devices"] = []
            selection = self._resolve_gpu_selection(state.gpu_ids)
            rpc_devices = worker_rpc_devices(selection.devices, selection.backend)
            if state.process_pid:
                self._stop_process(state.process_pid, force=True)
                self._clear_children()
            state.process_pid = None
            state.process_role = None
            launched = self._start_worker(
                host=state.rpc_bind or "127.0.0.1",
                port=state.rpc_port or DEFAULT_RPC_PORT,
                repo_root=str(root) if root else None,
                env=None,
                devices=[device["device"] for device in rpc_devices],
                # rpc-server's own default is 8 threads for the CPU device.
                threads=cpu_batch_threads(),
                log_path=str(self._rpc_log_path()),
            )
            pid = launched.get("pid")
            if not pid or not self._await_alive(int(pid)):
                if pid:
                    self._stop_process(int(pid), force=True)
                state.process_pid = None
                state.process_role = None
                member["rpc_devices"] = []
                state.save()
                return {"error": _LAUNCH_FAILED_MESSAGE}
            state.process_pid = pid
            state.process_role = "worker"
            member["rpc_devices"] = rpc_devices
            self._register_child(int(pid), "rpc-server")
            state.save()
            return launched
        except Exception as exc:
            if launched and launched.get("pid"):
                self._stop_process(int(launched["pid"]), force=True)
                self._clear_children()
            state.process_pid = state.process_role = None
            state.members.setdefault(state.node_id, {})["rpc_devices"] = []
            state.save()
            return {"error": str(exc)}

    def _await_alive(self, pid: int) -> bool:
        for _ in range(_POST_SPAWN_ALIVE_CHECKS):
            if not self._process_alive(pid):
                return False
            time.sleep(_POST_SPAWN_ALIVE_INTERVAL_S)
        return self._process_alive(pid)
