"""Background sampling and delivery of managed-process usage."""

from __future__ import annotations

from kllm.shared.exceptions import PersistenceError

import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kllm.modules.usage.interactors import usage
from kllm.shared import process_registry as runtime
from kllm.shared.state import ClusterState


@dataclass(frozen=True)
class UsageCollectorDependencies:
    """Effects and state access required by the background usage collector."""

    state: Callable[[], ClusterState]
    stop: threading.Event
    sampler_factory: Callable[[], usage.Sampler]
    resource_probe_available: Callable[[], bool]
    resource_probe: Callable[..., dict[str, Any]]
    close_resource_probes: Callable[[], None]
    remote_resource_state: dict[str, Any]
    publish_resources: Callable[[dict[str, Any]], None]
    store: Callable[[], usage.UsageStore]
    assignment: Callable[[str], str | None]
    node_context: Callable[[dict[str, Any], str | None], dict[str, Any]]
    master_control_base: Callable[[], str]
    control_client: Callable[[], Any]
    set_worker_error: Callable[[str | None], None]
    get_worker_last_save: Callable[[], str | None]
    set_worker_last_save: Callable[[str | None], None]
    runtime_load: Callable[[], runtime.RuntimeInfo]
    runtime_save: Callable[[runtime.RuntimeInfo], None]
    platform: str = sys.platform


class UsageCollector:
    """Sample local usage, maintain process identity, and persist or forward it."""

    def __init__(self, dependencies: UsageCollectorDependencies) -> None:
        self._deps = dependencies

    def run(self) -> None:
        sampler = self._deps.sampler_factory()
        if (
            hasattr(sampler, "_resource_probe")
            and self._deps.resource_probe_available()
            and self._deps.platform in ("win32", "darwin")
        ):
            sampler._resource_probe = self._deps.resource_probe
        needs_end = True
        while not self._deps.stop.wait(2):
            state = self._deps.state()
            pid = state.process_pid
            try:
                self._configure_resource_probe(sampler, state.role)
                point = sampler.sample(state)
                resource_snapshot = getattr(sampler, "resources", None)
                if isinstance(resource_snapshot, dict):
                    resource_snapshot = self._remove_exited_processes(resource_snapshot, state, pid)
                    sampler.resources = resource_snapshot
                    self._deps.publish_resources(resource_snapshot)
                if point is None:
                    if state.role == "worker":
                        self._deps.set_worker_error(None)
                    if needs_end and state.role == "master" and not state.process_pid:
                        self._deps.store().end_node(state.node_id)
                        needs_end = False
                    continue
                needs_end = True
                if state.role == "master":
                    self._record_master(state, point)
                elif state.role == "worker" and state.master_host and state.join_key:
                    self._forward_worker(state, point)
            except Exception as exc:
                needs_end = self._handle_error(exc, state, pid, needs_end)

    def _configure_resource_probe(self, sampler: usage.Sampler, role: str) -> None:
        if not hasattr(sampler, "_resource_probe"):
            return
        remote = self._deps.remote_resource_state
        legacy_master = (
            role == "worker"
            and remote.get("supported") is False
            and time.monotonic() - remote.get("capability_checked_at", 0) < 30
        )
        use_native = (
            self._deps.resource_probe_available()
            and self._deps.platform in ("win32", "darwin")
            and not legacy_master
        )
        if sampler._resource_probe is not None and not use_native:
            self._deps.close_resource_probes()
        sampler._resource_probe = self._deps.resource_probe if use_native else None
        if not use_native:
            sampler.resources = None

    def _remove_exited_processes(
        self,
        resources: dict[str, Any],
        state: ClusterState,
        sample_pid: int | None,
    ) -> dict[str, Any]:
        exited = {
            (process["pid"], process["start_token"])
            for process in resources["processes"]
            if process["state"] == "exited"
        }
        if not exited:
            return resources
        tracked = self._deps.runtime_load()
        if state.process_pid == sample_pid and any(
            (child.pid, child.start_token) in exited for child in tracked.children
        ):
            tracked.children = [
                child for child in tracked.children if (child.pid, child.start_token) not in exited
            ]
            self._deps.runtime_save(tracked)
            state.process_pid = state.process_role = None
            state.active_config = None
            member = state.members.get(state.node_id)
            if member is not None:
                member.update(
                    process_pid=None,
                    process_role=None,
                    process_started_at=None,
                    process_start_token=None,
                )
            state.save()
        return dict(
            resources,
            processes=[
                process for process in resources["processes"] if process["state"] != "exited"
            ],
        )

    def _record_master(self, state: ClusterState, point: dict[str, Any]) -> None:
        member = state.members.get(state.node_id)
        if member is not None:
            member.update(
                process_pid=point["pid"],
                process_role=point["process_role"],
                process_started_at=point["process_started_at"],
            )
        self._deps.store().record(
            state.node_id,
            (member or {}).get("host") or state.node_id,
            point,
            expected_pid=state.process_pid,
            expected_start=point["process_started_at"],
            expected_role="master",
            model=state.model_name,
            context=self._deps.node_context(member or {}, self._deps.assignment(state.node_id)),
        )

    def _forward_worker(self, state: ClusterState, point: dict[str, Any]) -> None:
        response = self._deps.control_client().post(
            f"{self._deps.master_control_base()}/v2/telemetry/ingest",
            json={"node_id": state.node_id, "readings": [point]},
            headers={"X-Kllm-Key": state.join_key},
            timeout=1.5,
        )
        if response.status_code != 200:
            raise RuntimeError(f"usage ingest {response.status_code}")
        try:
            last_save = response.json().get("last_successful_save")
        except (ValueError, AttributeError):
            last_save = None
        self._deps.set_worker_last_save(last_save or self._deps.get_worker_last_save())
        self._deps.set_worker_error(None)

    def _handle_error(
        self,
        exc: Exception,
        state: ClusterState,
        sample_pid: int | None,
        needs_end: bool,
    ) -> bool:
        if state.role == "worker":
            self._deps.remote_resource_state.clear()
        if (
            isinstance(exc, (usage.psutil.NoSuchProcess, usage.psutil.ZombieProcess))
            and state.process_pid == sample_pid
        ):
            if state.role == "master":
                try:
                    self._deps.store().end_node(state.node_id)
                except (OSError, sqlite3.Error, PersistenceError):
                    pass
            state.process_pid = None
            state.active_config = None
            state.process_role = None
            member = state.members.get(state.node_id)
            if member is not None:
                member.update(process_pid=None, process_role=None, process_started_at=None)
            state.save()
            needs_end = False
        if state.role == "master":
            try:
                store = self._deps.store()
                if store.recording != "failed":
                    store.recording = "interrupted"
                    store.error = f"collection interrupted: {type(exc).__name__}"
            except (OSError, sqlite3.Error, PersistenceError):
                pass
        elif state.role == "worker":
            self._deps.set_worker_error(
                f"usage collection/ingest interrupted ({type(exc).__name__}); check private master connection and managed child"
            )
        return needs_end
