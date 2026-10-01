"""Scoped managed-child measurements and bounded, master-local usage history."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Collection, Callable
from typing import Any

import psutil
from kllm import native, runtime, usage_probe
from kllm.exceptions import ConflictError, ValidationError
from kllm.state import ClusterState
from kllm.usage_support import (
    MAX_BYTES as MAX_BYTES,
    WINDOW as WINDOW,
    WINDOWS as WINDOWS,
    _decode_reading as _decode_reading,
    _number as _number,
    _parse_saved_utc as _parse_saved_utc,
    lifetime_id as lifetime_id,
    parse_utc as parse_utc,
    utc as utc,
    validate as validate,
)
from kllm.usage_store import UsageStore as UsageStore
from kllm.usage_devices import (
    _copy_byte_metric as _copy_byte_metric,
    _devices as _devices,
    _normalized_device_name as _normalized_device_name,
    _windows_adapter_records as _windows_adapter_records,
    _windows_dedicated_usage as _windows_dedicated_usage,
    project_resource_devices as project_resource_devices,
    reconcile_resources as reconcile_resources,
)


class ResourceConflict(ConflictError):
    def __init__(self, generation: str | None) -> None:
        super().__init__("resource generation conflict")
        self.generation = generation


class ResourceState:
    """Transient resource sessions; history remains owned by UsageStore."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.received: dict[str, float] = {}

    def establish(self, node: str, request: dict[str, Any]) -> str:
        if not isinstance(request, dict) or set(request) != {"request_id", "previous_generation"}:
            raise ValidationError("invalid resource session request")
        ident, previous = request["request_id"], request["previous_generation"]
        if (
            not isinstance(ident, str)
            or not re.fullmatch(r"[0-9a-f]{32}", ident)
            or (
                previous is not None
                and (not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{32}", previous))
            )
        ):
            raise ValidationError("invalid resource session identity")
        with self.lock:
            current = self.sessions.get(node)
            if current and current["request_id"] == ident:
                if current["previous"] != previous:
                    raise ValidationError(
                        "reset request_id reused with different previous generation"
                    )
                return str(current["generation"])
            active = current["generation"] if current else None
            if previous != active:
                raise ResourceConflict(active)
            generation = uuid.uuid4().hex
            self.sessions[node] = dict(
                generation=generation, request_id=ident, previous=previous, sequence=0
            )
            self.latest.pop(node, None)
            self.received.pop(node, None)
            return generation

    def validate_session(self, node: str, snapshot: dict[str, Any]) -> bool:
        current = self.sessions.get(node)
        if not current or snapshot["generation"] != current["generation"]:
            raise ResourceConflict(current["generation"] if current else None)
        if snapshot["sequence"] <= current["sequence"]:
            return False
        previous = self.latest.get(node)
        if previous:
            old = resource_observations(previous)
            if any(
                key in old and observed < old[key]
                for key, observed in resource_observations(snapshot).items()
            ):
                return False
        return True

    def install(self, node: str, snapshot: dict[str, Any]) -> bool:
        with self.lock:
            if not self.validate_session(node, snapshot):
                return False
            self.latest[node] = snapshot
            self.received[node] = time.monotonic()
            self.sessions[node]["sequence"] = snapshot["sequence"]
            return True

    def prune(self, nodes: Collection[str]) -> None:
        with self.lock:
            for mapping in (self.sessions, self.latest, self.received):
                for node in list(mapping):
                    if node not in nodes:
                        del mapping[node]


def resource_observations(snapshot: dict[str, Any]) -> dict[tuple[str, ...], datetime]:
    """Compare matching resource identities, never list order or reused numeric PIDs."""
    observations = {}

    def metrics(owner: tuple[str, ...], value: Any) -> None:
        if isinstance(value, dict):
            if "value" in value and "observed_at" in value:
                observations[owner] = parse_utc(value["observed_at"])
            else:
                for key, item in value.items():
                    metrics((*owner, key), item)

    metrics(("host",), snapshot.get("host"))
    for device in snapshot.get("devices", []):
        metrics(("device", device["device_key"], "utilization"), device.get("utilization"))
        for view in device["memory"]:
            metrics(("device", device["device_key"], view["kind"]), view)
    for point in snapshot.get("processes", []):
        metrics(("process", point["lifetime_id"]), point)
    return observations


def validate_resources(node_id: str, snapshot: Any, member: dict[str, Any], now: datetime) -> None:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema_version",
        "node_id",
        "generation",
        "sequence",
        "snapshot_id",
        "requested_at",
        "completed_at",
        "host",
        "devices",
        "device_discovery",
        "processes",
        "totals",
        "collector",
    }:
        raise ValidationError("invalid resource snapshot fields")
    seq, generation = snapshot["sequence"], snapshot["generation"]
    if (
        type(snapshot["schema_version"]) is not int
        or snapshot["schema_version"] not in (1, 2)
        or snapshot["node_id"] != node_id
        or isinstance(seq, bool)
        or not isinstance(seq, int)
        or not 1 <= seq <= 9007199254740991
        or not isinstance(generation, str)
        or not re.fullmatch(r"[0-9a-f]{32}", generation)
        or snapshot["snapshot_id"] != f"{generation}:{seq}"
    ):
        raise ValidationError("invalid resource snapshot identity")
    requested, completed = parse_utc(snapshot["requested_at"]), parse_utc(snapshot["completed_at"])
    if requested > completed or completed > now + timedelta(seconds=30) or requested < now - WINDOW:
        raise ValidationError("invalid resource snapshot time")
    n = native.require_native()
    if snapshot["schema_version"] == 1 and any("utilization" in device for device in snapshot["devices"]):
        raise ValidationError("utilization requires resource schema 2")
    for source in ("host", "gpu"):
        part = dict(
            host=snapshot["host"] if source == "host" else None,
            devices=snapshot["devices"] if source == "gpu" else [],
            processes=snapshot["processes"] if source == "host" else [],
            device_discovery=snapshot["device_discovery"] if source == "gpu" else None,
        )
        n._validate_source_json(source, json.dumps(part, allow_nan=False))
    for point in snapshot["processes"]:
        expected_start = member.get("process_started_at")
        if (
            point["node_id"] != node_id
            or point["pid"] != member.get("process_pid")
            or point["role"] != member.get("process_role")
            or not isinstance(expected_start, str)
            or parse_utc(point["started_at"]) != parse_utc(expected_start)
            or (
                member.get("process_start_token")
                and point["start_token"] != member["process_start_token"]
            )
        ):
            raise ValidationError("resource process does not match registered child")

    def timestamps(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("status") == "measured" and value.get("unit") == "percent_machine":
                start, end = parse_utc(value["interval_start"]), parse_utc(value["interval_end"])
                if start < now - WINDOW or end > now + timedelta(seconds=30):
                    raise ValidationError("CPU observation interval outside accepted window")
            if isinstance(value.get("observed_at"), str):
                at = parse_utc(value["observed_at"])
                if at > now + timedelta(seconds=30) or at < now - WINDOW:
                    raise ValidationError("resource observation outside accepted window")
            for item in value.values():
                timestamps(item)
        elif isinstance(value, list):
            for item in value:
                timestamps(item)

    timestamps(snapshot)
    # Totals are derived, never trusted as independent worker claims.
    expected = usage_probe.memory_totals(snapshot["host"], snapshot)
    if snapshot["totals"] != expected or any(
        total["value"] is not None and (type(total["value"]) is not int or total["value"] < 0)
        for total in snapshot["totals"].values()
    ):
        raise ValidationError("resource totals do not match physical pools")
    if (
        not isinstance(snapshot["collector"], dict)
        or snapshot["collector"]
        != dict(kind="native", platform=snapshot["collector"].get("platform"))
        or snapshot["collector"]["platform"] not in ("win32", "darwin", "linux")
    ):
        raise ValidationError("invalid resource collector metadata")


def resource_freshness(
    snapshot: dict[str, Any] | None,
    connected: bool,
    now: datetime,
    residence: float = 0.0,
) -> tuple[float | None, str]:
    if not snapshot:
        return None, "unavailable"
    observed = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            if (
                value.get("status") == "measured"
                and value.get("value") is not None
                and key != "total"
            ):
                observed.append(parse_utc(value["observed_at"]))
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for name in ("host", "devices", "processes"):
        walk(snapshot[name])
    if not observed:
        return None, "unavailable"
    age = max(
        0.0,
        (now - min(observed)).total_seconds(),
        (parse_utc(snapshot["completed_at"]) - min(observed)).total_seconds() + residence,
    )
    return age, "fresh" if connected and age < 10 else "stale"


class Sampler:
    def __init__(self, resource_probe: Callable[..., dict[str, Any]] | None = None) -> None:
        self._previous: tuple[int, str] | None = None
        self._process: psutil.Process | None = None
        self._resource_probe = resource_probe
        self.resources: dict[str, Any] | None = None
        self._sensor_lock = threading.Lock()
        self._sensor_inflight = False
        self._sensor_values: list[dict[str, Any]] = []
        self._sensor_observed: str | None = None

    def _optional_sensors(self, state: ClusterState) -> tuple[list[dict[str, Any]], str | None]:
        # Only CUDA's legacy provider has extra sensors; avoid duplicate WDDM memory probes.
        if (state.members.get(state.node_id) or {}).get("backend") != "cuda":
            return [], None

        def collect() -> None:
            try:
                observed = utc(datetime.now(timezone.utc))
                devices = _devices(state)
                for device in devices:
                    device.update(
                        used_bytes=None,
                        total_bytes=None,
                        unavailable_reason=(
                            (device.get("unavailable_reason") or "")
                            + "; memory is reported by native device readings"
                        ).strip("; ")[:256],
                    )
                with self._sensor_lock:
                    self._sensor_values = devices
                    self._sensor_observed = observed
            finally:
                with self._sensor_lock:
                    self._sensor_inflight = False

        with self._sensor_lock:
            if not self._sensor_inflight:
                self._sensor_inflight = True
                threading.Thread(target=collect, name="kllm-optional-sensors", daemon=True).start()
            return list(self._sensor_values), self._sensor_observed

    def sample(self, state: ClusterState, *, now: datetime | None = None) -> dict[str, Any] | None:
        if self._resource_probe is not None:
            selections = []
            for child in runtime.load().children:
                if (
                    child.pid == state.process_pid
                    and child.start_token
                    and child.started_at
                    and state.process_role in ("master", "worker")
                ):
                    selections.append(
                        dict(
                            pid=child.pid,
                            role=state.process_role,
                            start_token=child.start_token,
                            started_at=child.started_at,
                        )
                    )
            self.resources = self._resource_probe(
                node_id=state.node_id, managed_processes=selections
            )
            reconcile_resources(
                self.resources, (state.members.get(state.node_id) or {}).get("devices") or []
            )
            points = self.resources["processes"]
            if not points or not points[0]["identity_verified"] or points[0]["state"] != "running":
                return None
            point = points[0]
            host = self.resources["host"]
            metrics = [
                point["cpu"],
                point["rss"],
                host["cpu"],
                host["memory"]["used"],
                host["memory"]["total"],
            ]
            observed = min(
                (m["observed_at"] for m in metrics if m["value"] is not None),
                default=self.resources["completed_at"],
            )
            mapped_ids = {d["device_id"] for d in project_resource_devices(self.resources, state)}
            devices, sensor_time = self._optional_sensors(state) if mapped_ids else ([], None)
            devices = [d for d in devices if d["device_id"] in mapped_ids]
            if (
                sensor_time
                and parse_utc(sensor_time) >= parse_utc(point["started_at"])
                and (datetime.now(timezone.utc) - parse_utc(sensor_time)).total_seconds() < 10
            ):
                observed = min(observed, sensor_time)
            else:
                devices = []
            sensors = {d["device_id"]: d for d in devices}
            devices = project_resource_devices(self.resources, state, sensors)
            return dict(
                lifetime_id=point["lifetime_id"],
                pid=point["pid"],
                process_started_at=point["started_at"],
                process_role=point["role"],
                observed_at=observed,
                process=dict(
                    cpu_percent_machine=point["cpu"]["value"], rss_bytes=point["rss"]["value"]
                ),
                host=dict(
                    cpu_percent=host["cpu"]["value"],
                    used_bytes=host["memory"]["used"]["value"],
                    total_bytes=host["memory"]["total"]["value"],
                ),
                devices=devices,
                availability=dict(
                    process_cpu=point["cpu"]["status"],
                    process_rss=point["rss"]["status"],
                    host_cpu=host["cpu"]["status"],
                    host_memory=host["memory"]["used"]["status"],
                ),
            )
        if (
            state.role not in ("master", "worker")
            or not state.process_pid
            or state.process_role not in ("master", "worker")
        ):
            self._previous = None
            self._process = None
            return None
        now = now or datetime.now(timezone.utc)
        pid = state.process_pid
        observed = psutil.Process(pid)
        started = utc(observed.create_time())
        identity = (pid, started)
        first = identity != self._previous
        self._previous = identity
        # cpu_percent() is a delta on the *same* psutil.Process instance.
        if first or self._process is None:
            self._process = observed
        process = self._process
        raw_cpu = process.cpu_percent(interval=None)
        cpu = (
            None
            if first
            else round(min(raw_cpu / max(psutil.cpu_count(logical=True) or 1, 1), 100), 3)
        )
        rss = process.memory_info().rss
        try:
            host_cpu = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            host = {"cpu_percent": host_cpu, "used_bytes": memory.used, "total_bytes": memory.total}
        except Exception:
            host_cpu = None
            host = {"cpu_percent": None, "used_bytes": None, "total_bytes": None}
        return {
            "lifetime_id": lifetime_id(state.node_id, pid, started),
            "pid": pid,
            "process_started_at": started,
            "process_role": state.process_role,
            "observed_at": utc(now),
            "process": {"cpu_percent_machine": cpu, "rss_bytes": rss},
            "host": host,
            "devices": _devices(state),
            "availability": {
                "process_cpu": "initializing" if first else "measured",
                "process_rss": "measured",
                "host_cpu": "measured" if host_cpu is not None else "error",
                "host_memory": "measured" if host["used_bytes"] is not None else "error",
            },
        }
