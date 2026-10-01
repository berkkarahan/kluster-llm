"""Application-scoped usage state, storage, and collector wiring."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
import sys
from typing import Any

from kllm import usage
from kllm.agent.services.resource_publisher import ResourcePublisher
from kllm.agent.services.usage_collector import (
    UsageCollector,
    UsageCollectorDependencies,
)
from kllm.agent.services.usage_overview import UsageOverviewService
from kllm.state import ClusterState


class UsageRuntime:
    """Own per-agent usage state and assemble its background collector."""

    def __init__(
        self,
        *,
        state: Callable[[], ClusterState],
        app_state: Callable[[], Any],
        home_dir: Callable[[], Path],
        public_last_plan: Callable[[ClusterState], dict[str, Any] | None],
        master_control_base: Callable[[], str],
        heartbeat_payload: Callable[[], dict[str, Any]],
        tracked_process_token: Callable[[], str | None],
        control_client: Callable[[], Any],
        stop: threading.Event,
        sampler_factory: Callable[[], usage.Sampler],
        resource_probe_available: Callable[[], bool],
        resource_probe: Callable[..., dict[str, Any]],
        close_resource_probes: Callable[[], None],
        runtime_load: Callable[[], Any],
        runtime_save: Callable[[Any], None],
        resources: usage.ResourceState,
        remote_resources: dict[str, Any],
        platform: str = sys.platform,
    ) -> None:
        self._state = state
        self._app_state = app_state
        self._home_dir = home_dir
        self._public_last_plan = public_last_plan
        self._master_control_base = master_control_base
        self._heartbeat_payload = heartbeat_payload
        self._tracked_process_token = tracked_process_token
        self._control_client = control_client
        self._stop = stop
        self._sampler_factory = sampler_factory
        self._resource_probe_available = resource_probe_available
        self._resource_probe = resource_probe
        self._close_resource_probes = close_resource_probes
        self._runtime_load = runtime_load
        self._runtime_save = runtime_save
        self._platform = platform
        self.resources = resources
        self.remote_resources = remote_resources
        self._worker_lock = threading.RLock()
        self._worker_error: str | None = None
        self._worker_last_save: str | None = None
        self._worker_last_success_at: str | None = None
        self.overview = UsageOverviewService(self.resources, self.assignment)

    @property
    def worker_error(self) -> str | None:
        with self._worker_lock:
            return self._worker_error

    @property
    def worker_last_save(self) -> str | None:
        with self._worker_lock:
            return self._worker_last_save

    @property
    def worker_last_success_at(self) -> str | None:
        with self._worker_lock:
            return self._worker_last_success_at

    def set_worker_error(self, value: str | None) -> None:
        with self._worker_lock:
            self._worker_error = value

    def set_worker_last_save(self, value: str | None) -> None:
        with self._worker_lock:
            self._worker_last_save = value

    def set_worker_last_success_at(self, value: str) -> None:
        with self._worker_lock:
            self._worker_last_success_at = value

    def reset(self) -> None:
        with self._worker_lock:
            self._worker_error = None
            self._worker_last_save = None
            self._worker_last_success_at = None

    def assignment(self, node_id: str) -> str | None:
        state = self._state()
        if node_id == state.node_id and state.process_pid and state.process_role == "master":
            return state.model_name
        plan = self._public_last_plan(state) or {}
        if node_id in (plan.get("node_ids") or []) or any(
            allocation.get("node_id") == node_id
            for allocation in (plan.get("device_allocations") or [])
            if isinstance(allocation, dict)
        ):
            return state.model_name
        return None

    def node_context(
        self, member: dict[str, Any], model: str | None
    ) -> dict[str, Any]:
        devices: list[dict[str, Any]] = []
        for device in (member.get("devices") or [])[:32]:
            if isinstance(device, dict):
                devices.append(
                    {
                        "index": device.get("index")
                        if isinstance(device.get("index"), int)
                        else len(devices),
                        "name": str(device.get("name") or "device")[:80],
                        "vram_bytes": device.get("vram_bytes")
                        if isinstance(device.get("vram_bytes"), int)
                        else None,
                    }
                )
        return {
            "backend": str(member.get("actual_backend") or member.get("backend") or "unknown")[:80],
            "devices": devices,
            "unified_memory": bool(member.get("unified_memory")),
            "total_ram_bytes": member.get("total_ram_bytes"),
            "vram_bytes": member.get("vram_bytes"),
            "assignment": model[:160] if model else None,
        }

    def store(self) -> usage.UsageStore:
        """Open or reuse the process-local history store attached to app state."""
        app_state = self._app_state()
        path = self._home_dir() / "usage.sqlite3"
        with self._worker_lock:
            store = getattr(app_state, "usage_store", None)
            if store is None or store.path != path:
                if store is not None:
                    store.close()
                store = usage.UsageStore(path)
                app_state.usage_store = store
            return store

    def close_store(self, app: Any) -> None:
        with self._worker_lock:
            store = getattr(app.state, "usage_store", None)
            if store is not None:
                store.close()
                del app.state.usage_store

    def clear_resources(self) -> None:
        with self.resources.lock:
            self.resources.sessions.clear()
            self.resources.latest.clear()
            self.resources.received.clear()

    def clear_remote_resources(self) -> None:
        self.remote_resources.clear()

    def publish_resources(self, snapshot: dict[str, Any]) -> None:
        ResourcePublisher(
            state=self._state,
            resources=self.resources,
            remote=self.remote_resources,
            master_control_base=self._master_control_base,
            heartbeat_payload=self._heartbeat_payload,
            tracked_process_token=self._tracked_process_token,
            control_client=self._control_client,
        ).publish(snapshot)

    def run_collector(self) -> None:
        collector = UsageCollector(
            UsageCollectorDependencies(
                state=self._state,
                stop=self._stop,
                sampler_factory=self._sampler_factory,
                resource_probe_available=self._resource_probe_available,
                resource_probe=self._resource_probe,
                close_resource_probes=self._close_resource_probes,
                remote_resource_state=self.remote_resources,
                publish_resources=self.publish_resources,
                store=self.store,
                assignment=self.assignment,
                node_context=self.node_context,
                master_control_base=self._master_control_base,
                control_client=self._control_client,
                set_worker_error=self.set_worker_error,
                get_worker_last_save=lambda: self.worker_last_save,
                set_worker_last_save=self.set_worker_last_save,
                runtime_load=self._runtime_load,
                runtime_save=self._runtime_save,
                platform=self._platform,
            )
        )
        collector.run()
