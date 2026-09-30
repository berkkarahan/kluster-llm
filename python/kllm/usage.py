"""Scoped managed-child measurements and bounded, master-local usage history."""

from __future__ import annotations

import json
import base64
import binascii
import hashlib
import hmac
import math
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil
from kllm import native, runtime, usage_probe


WINDOW = timedelta(hours=24)
MAX_BYTES = 256 * 1024 * 1024
WINDOWS = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "24h": WINDOW}


class ResourceConflict(ValueError):
    def __init__(self, generation):
        super().__init__("resource generation conflict")
        self.generation = generation


class ResourceState:
    """Transient resource sessions; history remains owned by UsageStore."""
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.latest = {}
        self.received = {}

    def establish(self, node, request):
        if not isinstance(request, dict) or set(request) != {"request_id", "previous_generation"}:
            raise ValueError("invalid resource session request")
        ident, previous = request["request_id"], request["previous_generation"]
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident) or (previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{32}", previous))):
            raise ValueError("invalid resource session identity")
        with self.lock:
            current = self.sessions.get(node)
            if current and current["request_id"] == ident:
                if current["previous"] != previous:
                    raise ValueError("reset request_id reused with different previous generation")
                return current["generation"]
            active = current["generation"] if current else None
            if previous != active:
                raise ResourceConflict(active)
            generation = uuid.uuid4().hex
            self.sessions[node] = dict(generation=generation, request_id=ident, previous=previous, sequence=0)
            self.latest.pop(node, None)
            self.received.pop(node, None)
            return generation

    def validate_session(self, node, snapshot):
        current = self.sessions.get(node)
        if not current or snapshot["generation"] != current["generation"]:
            raise ResourceConflict(current["generation"] if current else None)
        if snapshot["sequence"] <= current["sequence"]:
            return False
        previous = self.latest.get(node)
        if previous:
            old = resource_observations(previous)
            if any(key in old and observed < old[key] for key, observed in resource_observations(snapshot).items()):
                return False
        return True

    def install(self, node, snapshot):
        with self.lock:
            if not self.validate_session(node, snapshot):
                return False
            self.latest[node] = snapshot
            self.received[node] = time.monotonic()
            self.sessions[node]["sequence"] = snapshot["sequence"]
            return True

    def prune(self, nodes):
        with self.lock:
            for mapping in (self.sessions, self.latest, self.received):
                for node in list(mapping):
                    if node not in nodes:
                        del mapping[node]


def resource_observations(snapshot):
    """Compare matching resource identities, never list order or reused numeric PIDs."""
    observations = {}
    def metrics(owner, value):
        if isinstance(value, dict):
            if "value" in value and "observed_at" in value:
                observations[owner] = parse_utc(value["observed_at"])
            else:
                for key, item in value.items():
                    metrics((*owner, key), item)
    metrics(("host",), snapshot.get("host"))
    for device in snapshot.get("devices", []):
        for view in device["memory"]:
            metrics(("device", device["device_key"], view["kind"]), view)
    for point in snapshot.get("processes", []):
        metrics(("process", point["lifetime_id"]), point)
    return observations


def validate_resources(node_id, snapshot, member, now):
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema_version", "node_id", "generation", "sequence", "snapshot_id", "requested_at", "completed_at",
        "host", "devices", "device_discovery", "processes", "totals", "collector",
    }:
        raise ValueError("invalid resource snapshot fields")
    seq, generation = snapshot["sequence"], snapshot["generation"]
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1 or snapshot["node_id"] != node_id or isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= 9007199254740991 or not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation) or snapshot["snapshot_id"] != f"{generation}:{seq}":
        raise ValueError("invalid resource snapshot identity")
    requested, completed = parse_utc(snapshot["requested_at"]), parse_utc(snapshot["completed_at"])
    if requested > completed or completed > now + timedelta(seconds=30) or requested < now - WINDOW:
        raise ValueError("invalid resource snapshot time")
    n = native.require_native()
    for source in ("host", "gpu"):
        part = dict(host=snapshot["host"] if source == "host" else None,
                    devices=snapshot["devices"] if source == "gpu" else [],
                    processes=snapshot["processes"] if source == "host" else [],
                    device_discovery=snapshot["device_discovery"] if source == "gpu" else None)
        n._validate_source_json(source, json.dumps(part, allow_nan=False))
    for point in snapshot["processes"]:
        if point["node_id"] != node_id or point["pid"] != member.get("process_pid") or point["role"] != member.get("process_role") or parse_utc(point["started_at"]) != parse_utc(member.get("process_started_at")) or (member.get("process_start_token") and point["start_token"] != member["process_start_token"]):
            raise ValueError("resource process does not match registered child")
    def timestamps(value):
        if isinstance(value, dict):
            if value.get("status") == "measured" and value.get("unit") == "percent_machine":
                start, end = parse_utc(value["interval_start"]), parse_utc(value["interval_end"])
                if start < now - WINDOW or end > now + timedelta(seconds=30):
                    raise ValueError("CPU observation interval outside accepted window")
            if isinstance(value.get("observed_at"), str):
                at = parse_utc(value["observed_at"])
                if at > now + timedelta(seconds=30) or at < now - WINDOW:
                    raise ValueError("resource observation outside accepted window")
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
        raise ValueError("resource totals do not match physical pools")
    if not isinstance(snapshot["collector"], dict) or snapshot["collector"] != dict(kind="native", platform=snapshot["collector"].get("platform")) or snapshot["collector"]["platform"] not in ("win32", "darwin", "linux"):
        raise ValueError("invalid resource collector metadata")


def resource_freshness(snapshot, connected, now, residence=0.0):
    if not snapshot:
        return None, "unavailable"
    observed = []
    def walk(value, key=""):
        if isinstance(value, dict):
            if value.get("status") == "measured" and value.get("value") is not None and key != "total":
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
    age = max(0.0, (now - min(observed)).total_seconds(),
              (parse_utc(snapshot["completed_at"]) - min(observed)).total_seconds() + residence)
    return age, "fresh" if connected and age < 10 else "stale"


def utc(value: datetime | float) -> str:
    if not isinstance(value, datetime):
        value = datetime.fromtimestamp(value, timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be ISO 8601 UTC with Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid UTC timestamp") from exc


def _parse_saved_utc(value: str) -> datetime:
    try:
        return parse_utc(value)
    except ValueError as exc:
        raise sqlite3.DatabaseError("usage history contains a damaged timestamp") from exc


def lifetime_id(node_id: str, pid: int, started: str) -> str:
    return f"{node_id}:{pid}:{started}"


def _decode_reading(payload: str) -> dict[str, Any]:
    try:
        point = json.loads(payload)
        if (not isinstance(point, dict) or not isinstance(point["process"], dict)
                or not isinstance(point["host"], dict) or not isinstance(point["devices"], list)
                or not {"cpu_percent_machine", "rss_bytes"} <= point["process"].keys()
                or not {"cpu_percent", "used_bytes", "total_bytes"} <= point["host"].keys()):
            raise ValueError("invalid reading")
        _parse_saved_utc(point["observed_at"])
        _parse_saved_utc(point["process_started_at"])
        return point
    except sqlite3.DatabaseError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise sqlite3.DatabaseError("usage history contains a damaged reading") from exc


def _number(value: Any, name: str, maximum: float | None = None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid {name}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} out of range")


def validate(node_id: str, point: dict[str, Any], *, now: datetime,
             expected_pid: int | None = None, expected_start: str | None = None,
             expected_role: str | None = None) -> None:
    if not isinstance(point, dict) or set(point) - {
        "lifetime_id", "pid", "process_started_at", "process_role", "observed_at",
        "process", "host", "devices", "availability", "collector_error",
    }:
        raise ValueError("unexpected reading fields")
    if not isinstance(node_id, str) or not node_id or len(node_id) > 128:
        raise ValueError("invalid node_id")
    pid = point.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or (expected_pid is not None and pid != expected_pid):
        raise ValueError("invalid tracked PID")
    start = point.get("process_started_at")
    began = parse_utc(start)
    observed = parse_utc(point.get("observed_at"))
    if began > observed or observed > now + timedelta(seconds=30) or observed < now - WINDOW:
        raise ValueError("reading timestamp outside accepted window")
    if expected_start is not None and parse_utc(expected_start) != began:
        raise ValueError("process start does not match registered process")
    role = point.get("process_role")
    if role not in ("master", "worker") or (expected_role is not None and role != expected_role):
        raise ValueError("invalid process role")
    if point.get("lifetime_id") != lifetime_id(node_id, pid, start):
        raise ValueError("lifetime identity does not match node, PID and start")
    for scope, names in (("process", {"cpu_percent_machine", "rss_bytes"}),
                         ("host", {"cpu_percent", "used_bytes", "total_bytes"})):
        data = point.get(scope)
        if not isinstance(data, dict) or set(data) != names:
            raise ValueError(f"invalid {scope} fields")
        for key, value in data.items():
            _number(value, key, 100 if "percent" in key else None)
    host = point["host"]
    if host["used_bytes"] is not None and host["total_bytes"] is not None and host["used_bytes"] > host["total_bytes"]:
        raise ValueError("used host memory exceeds capacity")
    devices = point.get("devices")
    if not isinstance(devices, list) or len(devices) > 32:
        raise ValueError("invalid device list")
    seen = set()
    for device in devices:
        if not isinstance(device, dict) or set(device) - {
            "device_id", "scope", "utilization_percent", "used_bytes", "total_bytes",
            "temperature_c", "power_w", "unavailable_reason",
        } or not isinstance(device.get("device_id"), str) or len(device["device_id"]) > 64 or not device["device_id"]:
            raise ValueError("invalid device fields")
        if device.get("scope") != "device" or device["device_id"] in seen:
            raise ValueError("invalid device scope or duplicate device")
        seen.add(device["device_id"])
        for key in ("utilization_percent", "used_bytes", "total_bytes", "power_w"):
            _number(device.get(key), key, 100 if "percent" in key else None)
        temp = device.get("temperature_c")
        if temp is not None and (isinstance(temp, bool) or not isinstance(temp, (int, float)) or not math.isfinite(temp) or not -50 <= temp <= 150):
            raise ValueError("temperature_c out of sensor range")
        if device.get("used_bytes") is not None and device.get("total_bytes") is not None and device["used_bytes"] > device["total_bytes"]:
            raise ValueError("used device memory exceeds capacity")
        if device.get("unavailable_reason") is not None and (not isinstance(device["unavailable_reason"], str) or len(device["unavailable_reason"]) > 256):
            raise ValueError("invalid unavailable reason")
        if any(device.get(key) is None for key in ("utilization_percent", "used_bytes", "total_bytes", "temperature_c", "power_w")) and not device.get("unavailable_reason"):
            raise ValueError("unavailable device fields require a reason")
    availability = point.get("availability")
    if not isinstance(availability, dict) or set(availability) - {"process_cpu", "process_rss", "host_cpu", "host_memory", "device_utilization"}:
        raise ValueError("invalid availability fields")
    if any(value not in ("measured", "initializing", "unsupported", "error") for value in availability.values()):
        raise ValueError("invalid availability status")
    for field, value in (("process_cpu", point["process"]["cpu_percent_machine"]),
                         ("process_rss", point["process"]["rss_bytes"])):
        if availability.get(field) != ("measured" if value is not None else availability.get(field)) or (value is None and availability.get(field) not in ("initializing", "unsupported", "error")):
            raise ValueError(f"{field} availability does not match reading")
    for field, value in (("host_cpu", host["cpu_percent"]),
                         ("host_memory", host["used_bytes"] if host["used_bytes"] is not None and host["total_bytes"] is not None else None)):
        status = availability.get(field)
        if (value is None and status not in ("initializing", "unsupported", "error")) or (value is not None and status not in (None, "measured")):
            raise ValueError(f"{field} availability does not match reading")
    if point.get("collector_error") is not None and (not isinstance(point["collector_error"], str) or len(point["collector_error"]) > 256):
        raise ValueError("invalid collector error")


class Sampler:
    def __init__(self, resource_probe=None):
        self._previous: tuple[int, str] | None = None
        self._process: psutil.Process | None = None
        self._resource_probe = resource_probe
        self.resources = None
        self._sensor_lock = threading.Lock()
        self._sensor_inflight = False
        self._sensor_values = []
        self._sensor_observed = None

    def _optional_sensors(self, state):
        # Only CUDA's legacy provider has extra sensors; avoid duplicate WDDM memory probes.
        if (state.members.get(state.node_id) or {}).get("backend") != "cuda":
            return [], None
        def collect():
            try:
                observed = utc(datetime.now(timezone.utc))
                devices = _devices(state)
                for device in devices:
                    device.update(used_bytes=None, total_bytes=None,
                                  unavailable_reason=((device.get("unavailable_reason") or "") + "; memory is reported by native device readings").strip("; ")[:256])
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

    def sample(self, state, *, now: datetime | None = None) -> dict[str, Any] | None:
        if self._resource_probe is not None:
            selections = []
            for child in runtime.load().children:
                if child.pid == state.process_pid and child.start_token and child.started_at and state.process_role in ("master", "worker"):
                    selections.append(dict(pid=child.pid, role=state.process_role, start_token=child.start_token, started_at=child.started_at))
            self.resources = self._resource_probe(node_id=state.node_id, managed_processes=selections)
            reconcile_resources(self.resources, (state.members.get(state.node_id) or {}).get("devices") or [])
            points = self.resources["processes"]
            if not points or not points[0]["identity_verified"] or points[0]["state"] != "running":
                return None
            point = points[0]
            host = self.resources["host"]
            metrics = [point["cpu"], point["rss"], host["cpu"], host["memory"]["used"], host["memory"]["total"]]
            observed = min((m["observed_at"] for m in metrics if m["value"] is not None), default=self.resources["completed_at"])
            mapped_ids = {d["device_id"] for d in project_resource_devices(self.resources, state)}
            devices, sensor_time = self._optional_sensors(state) if mapped_ids else ([], None)
            devices = [d for d in devices if d["device_id"] in mapped_ids]
            if sensor_time and parse_utc(sensor_time) >= parse_utc(point["started_at"]) and (datetime.now(timezone.utc) - parse_utc(sensor_time)).total_seconds() < 10:
                observed = min(observed, sensor_time)
            else:
                devices = []
            sensors = {d["device_id"]: d for d in devices}
            devices = project_resource_devices(self.resources, state, sensors)
            return dict(lifetime_id=point["lifetime_id"], pid=point["pid"], process_started_at=point["started_at"],
                process_role=point["role"], observed_at=observed,
                process=dict(cpu_percent_machine=point["cpu"]["value"], rss_bytes=point["rss"]["value"]),
                host=dict(cpu_percent=host["cpu"]["value"], used_bytes=host["memory"]["used"]["value"], total_bytes=host["memory"]["total"]["value"]),
                devices=devices, availability=dict(process_cpu=point["cpu"]["status"], process_rss=point["rss"]["status"], host_cpu=host["cpu"]["status"], host_memory=host["memory"]["used"]["status"]))
        if state.role not in ("master", "worker") or not state.process_pid or state.process_role not in ("master", "worker"):
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
        cpu = None if first else round(min(raw_cpu / max(psutil.cpu_count(logical=True) or 1, 1), 100), 3)
        rss = process.memory_info().rss
        try:
            host_cpu = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            host = {"cpu_percent": host_cpu, "used_bytes": memory.used, "total_bytes": memory.total}
        except Exception:
            host_cpu = None
            host = {"cpu_percent": None, "used_bytes": None, "total_bytes": None}
        return {
            "lifetime_id": lifetime_id(state.node_id, pid, started), "pid": pid,
            "process_started_at": started, "process_role": state.process_role,
            "observed_at": utc(now),
            "process": {"cpu_percent_machine": cpu, "rss_bytes": rss}, "host": host,
            "devices": _devices(state),
            "availability": {"process_cpu": "initializing" if first else "measured", "process_rss": "measured",
                             "host_cpu": "measured" if host_cpu is not None else "error",
                             "host_memory": "measured" if host["used_bytes"] is not None else "error"},
        }


def _normalized_device_name(name: object) -> str:
    text = re.sub(r"\((?:tm|r)\)", "", str(name or ""), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _copy_byte_metric(previous: dict, *, value: int | None, status: str, reason: str | None, source: str, detail: str | None) -> dict:
    metric = dict(previous)
    metric.update(value=value, unit="bytes", scope="device", status=status, reason=reason,
                  detail=None if detail is None else str(detail)[:256],
                  interval_start=None, interval_end=None, duration_ms=None, source=source)
    return metric


def reconcile_resources(snapshot, inventory) -> None:
    """Link native devices to llama inventory and use that total as capacity.

    A normalized display name (casefold, collapsed whitespace, ``(TM)``/``(R)`` removed)
    links only when it is unique on both sides. The previous OS total stays in ``detail``.
    """
    if not isinstance(snapshot, dict) or not isinstance(inventory, list):
        return
    devices = snapshot.get("devices")
    if not isinstance(devices, list):
        return
    inventory_names: dict[str, list] = {}
    for item in inventory:
        if isinstance(item, dict):
            key = _normalized_device_name(item.get("name"))
            if key:
                inventory_names.setdefault(key, []).append(item)
    snapshot_names: dict[str, list] = {}
    for device in devices:
        if isinstance(device, dict):
            key = _normalized_device_name(device.get("name"))
            if key:
                snapshot_names.setdefault(key, []).append(device)
    for device in devices:
        if not isinstance(device, dict):
            continue
        key = _normalized_device_name(device.get("name"))
        candidates = inventory_names.get(key, [])
        if len(candidates) != 1 or len(snapshot_names.get(key, [])) != 1:
            continue
        item = candidates[0]
        index = item.get("index")
        if type(index) is not int or index < 0:
            continue
        device["inventory_device_id"] = str(index)
        vram = item.get("vram_bytes")
        if type(vram) is not int or vram <= 0:
            continue
        for view in device.get("memory") or []:
            if not isinstance(view, dict) or view.get("kind") != "dedicated":
                continue
            total = view.get("total")
            if not isinstance(total, dict):
                continue
            view["total"] = _copy_byte_metric(
                total, value=vram, status="measured", reason=None,
                source="inventory.llama_list_devices",
                detail=f"{total.get('source')}={total.get('value')}")
            used = view.get("used") if isinstance(view.get("used"), dict) else {}
            used_value = used.get("value")
            previous_free = view.get("free") if isinstance(view.get("free"), dict) else total
            if type(used_value) is int and 0 <= used_value <= vram:
                view["free"] = _copy_byte_metric(
                    previous_free, value=vram - used_value, status="measured", reason=None,
                    source="inventory.capacity_minus_usage", detail=None)
            else:
                view["free"] = _copy_byte_metric(
                    previous_free, value=None, status="error", reason="ambiguous_pool",
                    source="inventory.capacity_minus_usage", detail=None)
    if isinstance(snapshot.get("host"), dict):
        snapshot["totals"] = usage_probe.memory_totals(snapshot["host"], snapshot)


def project_resource_devices(snapshot, state, sensors=None):
    """Require the inventory link from ``reconcile_resources``; a name match here is not proof."""
    inventory = (state.members.get(state.node_id) or {}).get("devices") or []
    result = []
    for device in snapshot["devices"]:
        link = device.get("inventory_device_id")
        if not isinstance(link, str) or not link:
            continue
        matches = [d for d in inventory if type(d.get("index")) is int and str(d["index"]) == link]
        if len(matches) != 1:
            continue
        index = matches[0].get("index")
        if type(index) is not int or index < 0 or sum(d.get("index") == index for d in inventory) != 1:
            continue
        device_id = str(index)
        dedicated = next((view for view in device["memory"] if view["kind"] == "dedicated"), None)
        optional = (sensors or {}).get(device_id) or {}
        row = dict(device_id=device_id, scope="device",
                   utilization_percent=optional.get("utilization_percent"),
                   temperature_c=optional.get("temperature_c"), power_w=optional.get("power_w"),
                   used_bytes=dedicated["used"]["value"] if dedicated else None,
                   total_bytes=dedicated["total"]["value"] if dedicated else None)
        row["unavailable_reason"] = "some device counters unavailable" if any(
            row[key] is None for key in ("utilization_percent", "temperature_c", "power_w", "used_bytes", "total_bytes")) else None
        result.append(row)
    return result


def _devices(state) -> list[dict[str, Any]]:
    member = state.members.get(state.node_id) or {}
    inventory = member.get("devices") or []
    if not inventory:
        return []
    values: dict[str, list[str]] = {}
    windows_used: dict[str, int] = {}
    if member.get("backend") == "cuda" and shutil.which("nvidia-smi"):
        try:
            output = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=0.8, check=True,
            ).stdout
            values = {row[0]: row[1:] for row in (list(map(str.strip, line.split(","))) for line in output.splitlines()) if len(row) == 6}
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform == "win32" and member.get("backend") in ("vulkan", "rocm"):
        windows_used = _windows_dedicated_usage(inventory)

    def parsed(value: str, scale: int = 1) -> float | None:
        try:
            number = float(value)
            return number * scale if math.isfinite(number) else None
        except ValueError:
            return None

    result = []
    for index, device in enumerate(inventory[:32]):
        device_id = str(device.get("index", index))
        row = values.get(device_id)
        utilization = parsed(row[0]) if row else None
        used = parsed(row[1], 1024**2) if row else windows_used.get(device_id)
        temperature = parsed(row[3]) if row else None
        power = parsed(row[4]) if row else None
        missing = ("Windows dedicated GPU memory counter unavailable or adapter not identified"
                   if sys.platform == "win32" and member.get("backend") in ("vulkan", "rocm")
                   else "device sensor unavailable")
        result.append({
            "device_id": device_id, "scope": "device",
            "utilization_percent": utilization,
            "used_bytes": used,
            "total_bytes": parsed(row[2], 1024**2) if row else device.get("vram_bytes"),
            "temperature_c": temperature,
            "power_w": power,
            "unavailable_reason": (missing if not row and used is None else "some device sensors unavailable"
                                    if None in (utilization, used, temperature, power) else None),
        })
    return result


def _windows_adapter_records() -> list[tuple[str, str]]:
    """Read DirectX adapter LUIDs and descriptions, without guessing GPU order."""
    import winreg

    records = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\DirectX") as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            key_name = winreg.EnumKey(root, i)
            if not re.fullmatch(r"\{?[0-9a-fA-F-]{36}\}?", key_name):
                continue
            with winreg.OpenKey(root, key_name) as key:
                name = winreg.QueryValueEx(key, "Description")[0]
                luid = winreg.QueryValueEx(key, "AdapterLuid")[0]
                if isinstance(luid, bytes) and len(luid) == 8:
                    luid = int.from_bytes(luid, "little")
                if isinstance(luid, int) and luid > 0 and isinstance(name, str) and name:
                    records.append((f"{luid:016x}", name))
    return records


def _windows_dedicated_usage(inventory: list[dict[str, Any]]) -> dict[str, int]:
    """Match WDDM device-wide dedicated bytes to an unambiguous inventory device."""
    script = (
        r"(Get-Counter -Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples | "
        "ForEach-Object { '{0}`t{1}' -f $_.Path, "
        "$_.CookedValue.ToString([Globalization.CultureInfo]::InvariantCulture) }"
    )
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=1.5, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        records = _windows_adapter_records()
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}

    counters: dict[str, list[int]] = {}
    for line in output.splitlines():
        match = re.search(r"luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})_phys_(\d+)\)\\dedicated usage\t(.+)$", line, re.I)
        if not match:
            continue
        try:
            used = float(match[4])
        except ValueError:
            continue
        if math.isfinite(used) and used >= 0 and used.is_integer():
            luid = (match[1] + match[2]).lower()
            counters.setdefault(luid, []).append(int(used))

    def normalized(name: str) -> str:
        cleaned = re.sub(r"\((?:tm|r)\)", "", name.lower())
        return " ".join(re.findall(r"[a-z0-9]+", cleaned.replace("™", "").replace("®", "")))

    result = {}
    inventory_names = [normalized(str(device.get("name") or "")) for device in inventory]
    for device in inventory:
        name = normalized(str(device.get("name") or ""))
        # Two matching adapters or two physical nodes behind one LUID cannot be attributed.
        matches = [luid for luid, description in records if normalized(description) == name and name]
        if inventory_names.count(name) != 1 or len(matches) != 1 or len(counters.get(matches[0], [])) != 1:
            continue
        used = counters[matches[0]][0]
        total = device.get("vram_bytes")
        if isinstance(total, (int, float)) and 0 < total and used <= total:
            result[str(device.get("index"))] = used
    return result


class UsageStore:
    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA auto_vacuum=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, host TEXT NOT NULL, role TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS lifetimes (
                lifetime_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, pid INTEGER NOT NULL, role TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, state TEXT NOT NULL, model TEXT);
            CREATE TABLE IF NOT EXISTS associations (
                lifetime_id TEXT NOT NULL, model TEXT NOT NULL, observed_from TEXT NOT NULL,
                observed_until TEXT, PRIMARY KEY(lifetime_id, observed_from));
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY, lifetime_id TEXT NOT NULL, node_id TEXT NOT NULL,
                observed_at TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(lifetime_id, observed_at));
            CREATE INDEX IF NOT EXISTS readings_time ON readings(observed_at, id);
            CREATE INDEX IF NOT EXISTS readings_filter ON readings(node_id, lifetime_id, observed_at, id);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if "context" not in {column[1] for column in self.db.execute("PRAGMA table_info(nodes)")}:
            self.db.execute("ALTER TABLE nodes ADD COLUMN context TEXT NOT NULL DEFAULT '{}' ")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('cursor_secret', ?)", (secrets.token_hex(32),))
        base_bytes = str(min(self._physical_bytes(), 64 * 1024))
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('base_bytes', ?)", (base_bytes,))
        self._base_bytes = int(self._meta("base_bytes"))
        self.recording = "interrupted"
        self.error: str | None = None
        self.last_successful_save = self._meta("last_successful_save")
        self._prune(datetime.now(timezone.utc))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self.lock:
            self.db.close()

    def _meta(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
        return row[0] if row else None

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    def latest(self, node_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT payload, received_at FROM readings WHERE node_id=? ORDER BY observed_at DESC,id DESC LIMIT 1", (node_id,)).fetchone()
            return {**_decode_reading(row[0]), "received_at": row[1]} if row else None

    def known_nodes(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {nid: {"host": host, "role": role, **json.loads(context)}
                    for nid, host, role, context in self.db.execute("SELECT node_id,host,role,context FROM nodes")}

    def coverage(self, node_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            condition = "WHERE node_id=?" if node_id is not None else ""
            row = self.db.execute(f"SELECT MIN(observed_at),MAX(observed_at) FROM readings {condition}",
                                  (node_id,) if node_id is not None else ()).fetchone()
            evicted_at = self._meta("storage_truncated_at")
            return {"earliest_retained": row[0], "latest_retained": row[1],
                    "coverage": "recorded" if row[0] else "unrecorded",
                    "storage_truncated": self._meta("storage_truncated") == "1" and (
                        evicted_at is None or _parse_saved_utc(evicted_at) >= datetime.now(timezone.utc) - WINDOW),
                    "recording": self.recording if row[0] or self.recording == "failed" else "interrupted",
                    "error": self.error, "last_successful_save": self.last_successful_save}

    def lifetimes(self, node_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT lifetime_id,pid,role,started_at,ended_at,state,model FROM lifetimes WHERE node_id=? ORDER BY started_at DESC",
                                   (node_id,)).fetchall()
            result = []
            for row in rows:
                life = dict(zip(("lifetime_id", "pid", "role", "started_at", "ended_at", "state", "model"), row))
                life["associations"] = [dict(zip(("model", "observed_from", "observed_until"), entry)) for entry in
                                        self.db.execute("SELECT model,observed_from,observed_until FROM associations WHERE lifetime_id=? ORDER BY observed_from", (row[0],))]
                result.append(life)
            return result

    def end_node(self, node_id: str, *, state: str = "ended") -> None:
        if state not in ("ended", "unobserved"):
            raise ValueError("invalid lifetime state")
        with self.lock, self.db:
            self.db.execute("UPDATE lifetimes SET state=?, ended_at=(SELECT MAX(observed_at) FROM readings WHERE readings.lifetime_id=lifetimes.lifetime_id) WHERE node_id=? AND state='running'", (state, node_id))
            self.db.execute("UPDATE associations SET observed_until=(SELECT ended_at FROM lifetimes WHERE lifetimes.lifetime_id=associations.lifetime_id) WHERE observed_until IS NULL AND lifetime_id IN (SELECT lifetime_id FROM lifetimes WHERE node_id=? AND state!= 'running')", (node_id,))

    def _filters(self, node_id: str, life_id: str, window: str, now: datetime) -> tuple[str, str]:
        if window not in WINDOWS:
            raise ValueError("window must be 15m, 1h or 24h")
        if not self.db.execute("SELECT 1 FROM lifetimes WHERE node_id=? AND lifetime_id=?", (node_id, life_id)).fetchone():
            raise LookupError("unknown node or process lifetime")
        return utc(now - WINDOWS[window]), utc(now)

    def _cursor_signature(self, data: dict[str, Any]) -> str:
        content = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(bytes.fromhex(self._meta("cursor_secret")), content, hashlib.sha256).hexdigest()

    def series(self, node_id: str, life_id: str, window: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            start, end = self._filters(node_id, life_id, window, now)
            rows = self.db.execute(
                "SELECT observed_at,payload FROM readings WHERE node_id=? AND lifetime_id=? AND observed_at>=? AND observed_at<=? ORDER BY observed_at,id",
                (node_id, life_id, start, end),
            ).fetchall()
            coverage = self.coverage(node_id)

        # JSON decoding and aggregation can be expensive at 24-hour scale; do
        # it after the immutable query snapshot is fetched so ingestion and
        # live reads are not blocked on chart preparation.
        step = {"15m": 2, "1h": 5, "24h": 120}[window]
        length = int(WINDOWS[window].total_seconds() / step)
        start_at = parse_utc(start)
        buckets = [{"start": utc(start_at + timedelta(seconds=i * step)),
                    "end": utc(start_at + timedelta(seconds=(i + 1) * step)),
                    "count": 0, "process_cpu_min": None, "process_cpu_max": None,
                    "process_rss_min": None, "process_rss_max": None,
                    "host_cpu_min": None, "host_cpu_max": None, "host_used_bytes_min": None,
                    "host_used_bytes_max": None, "devices": []} for i in range(length)]
        devices_by_bucket: list[dict[str, dict[str, Any]]] = [{} for _ in range(length)]
        for stamp, payload in rows:
            index = min(int((_parse_saved_utc(stamp) - start_at).total_seconds() // step), length - 1)
            if index < 0:
                continue
            bucket = buckets[index]
            point = _decode_reading(payload)
            bucket["count"] += 1
            for field, value in (("process_cpu", point["process"]["cpu_percent_machine"]),
                                 ("process_rss", point["process"]["rss_bytes"]),
                                 ("host_cpu", point["host"]["cpu_percent"]),
                                 ("host_used_bytes", point["host"]["used_bytes"])):
                if value is not None:
                    low, high = field + "_min", field + "_max"
                    bucket[low] = value if bucket[low] is None else min(value, bucket[low])
                    bucket[high] = value if bucket[high] is None else max(value, bucket[high])
            for device in point["devices"]:
                device_id = device["device_id"]
                entry = devices_by_bucket[index].get(device_id)
                if entry is None:
                    entry = {"device_id": device_id, "scope": "device", "total_bytes": None,
                             "unavailable_reason": None}
                    devices_by_bucket[index][device_id] = entry
                    bucket["devices"].append(entry)
                entry["total_bytes"] = device.get("total_bytes") or entry["total_bytes"]
                entry["unavailable_reason"] = device.get("unavailable_reason") or entry["unavailable_reason"]
                for field in ("utilization_percent", "used_bytes", "temperature_c", "power_w"):
                    value = device.get(field)
                    if value is not None:
                        low, high = field + "_min", field + "_max"
                        entry[low] = value if entry.get(low) is None else min(value, entry[low])
                        entry[high] = value if entry.get(high) is None else max(value, entry[high])
        return {"buckets": buckets, "window_start": start, "window_end": end, "time_zone": "UTC",
                **coverage}

    def readings(self, node_id: str, life_id: str, window: str, *, limit: int = 500,
                 cursor: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be 1–1000")
        now = now or datetime.now(timezone.utc)
        with self.lock:
            start, end = self._filters(node_id, life_id, window, now)
            seek = ("", 0)
            if cursor is not None:
                try:
                    if len(cursor) > 2048:
                        raise ValueError("cursor too long")
                    doc = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                    signature = doc.pop("sig")
                    if not isinstance(signature, str) or not hmac.compare_digest(signature, self._cursor_signature(doc)):
                        raise ValueError("cursor signature invalid")
                    if (doc["node"], doc["life"], doc["window"]) != (node_id, life_id, window):
                        raise ValueError("cursor does not match filters")
                    start, end = doc["start"], doc["end"]
                    parse_utc(start), parse_utc(end)
                    if (parse_utc(end) - parse_utc(start) != WINDOWS[window]
                            or parse_utc(end) > now + timedelta(seconds=30)
                            or parse_utc(start) < now - WINDOW - timedelta(minutes=1)):
                        raise ValueError("cursor window expired")
                    seek = (doc["last"], int(doc["id"]))
                    if (not start <= seek[0] <= end or seek[1] <= 0 or not self.db.execute(
                            "SELECT 1 FROM readings WHERE node_id=? AND lifetime_id=? AND observed_at=? AND id=?",
                            (node_id, life_id, *seek)).fetchone()):
                        raise ValueError("cursor position invalid")
                except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error):
                    raise ValueError("invalid or mismatched history cursor") from None
            rows = self.db.execute(
                "SELECT id,observed_at,payload,received_at FROM readings WHERE node_id=? AND lifetime_id=? AND observed_at>=? AND observed_at<=? AND (observed_at>? OR (observed_at=? AND id>?)) ORDER BY observed_at,id LIMIT ?",
                (node_id, life_id, start, end, seek[0], seek[0], seek[1], limit + 1),
            ).fetchall()
            page = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                data = {"node": node_id, "life": life_id, "window": window, "start": start,
                        "end": end, "last": page[-1][1], "id": page[-1][0]}
                data["sig"] = self._cursor_signature(data)
                next_cursor = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
            return {"readings": [{**_decode_reading(row[2]), "received_at": row[3]} for row in page],
                    "next_cursor": next_cursor, "window_start": start, "window_end": end,
                    "time_zone": "UTC", **self.coverage(node_id)}

    def _physical_bytes(self) -> int:
        return sum(file.stat().st_size for file in (self.path, Path(str(self.path) + "-journal"), Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")) if file.exists())

    def _write(self, node_id: str, host: str, point: dict[str, Any], received: str, model: str | None,
               context: dict[str, Any] | None = None) -> bool:
        before = self.db.total_changes
        self.db.execute("INSERT OR IGNORE INTO readings(lifetime_id,node_id,observed_at,received_at,payload) VALUES(?,?,?,?,?)",
                        (point["lifetime_id"], node_id, point["observed_at"], received, json.dumps(point, allow_nan=False)))
        if self.db.total_changes == before:
            return False
        self.db.execute("INSERT INTO nodes VALUES(?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET host=excluded.host,role=excluded.role,context=excluded.context",
                        (node_id, host, point["process_role"], json.dumps(context or {}, allow_nan=False)))
        prior = self.db.execute("SELECT lifetime_id,observed_at FROM readings WHERE node_id=? AND lifetime_id!=? ORDER BY observed_at DESC LIMIT 1",
                                (node_id, point["lifetime_id"])).fetchone()
        if prior and prior[1] < point["observed_at"]:
            self.db.execute("UPDATE lifetimes SET state='ended',ended_at=? WHERE lifetime_id=? AND state='running'", (prior[1], prior[0]))
            self.db.execute("UPDATE associations SET observed_until=? WHERE lifetime_id=? AND observed_until IS NULL", (prior[1], prior[0]))
        self.db.execute("INSERT INTO lifetimes VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(lifetime_id) DO UPDATE SET model=COALESCE(excluded.model,lifetimes.model)",
                        (point["lifetime_id"], node_id, point["pid"], point["process_role"], point["process_started_at"], None, "running", model))
        # A delayed observation may enrich an old lifetime, never make it current again.
        later = self.db.execute("SELECT 1 FROM readings WHERE node_id=? AND lifetime_id!=? AND observed_at>? LIMIT 1",
                                (node_id, point["lifetime_id"], point["observed_at"])).fetchone()
        if later:
            self.db.execute("UPDATE lifetimes SET state='ended', ended_at=(SELECT MAX(observed_at) FROM readings WHERE lifetime_id=?) WHERE lifetime_id=? AND state='running'",
                            (point["lifetime_id"], point["lifetime_id"]))
            self.db.execute("UPDATE associations SET observed_until=(SELECT ended_at FROM lifetimes WHERE lifetimes.lifetime_id=associations.lifetime_id) WHERE lifetime_id=? AND observed_until IS NULL", (point["lifetime_id"],))
        latest_observation = self.db.execute("SELECT MAX(observed_at) FROM readings WHERE lifetime_id=?", (point["lifetime_id"],)).fetchone()[0]
        if model and latest_observation == point["observed_at"]:
            previous = self.db.execute("SELECT model FROM associations WHERE lifetime_id=? ORDER BY observed_from DESC LIMIT 1", (point["lifetime_id"],)).fetchone()
            if previous is None or previous[0] != model:
                self.db.execute("UPDATE associations SET observed_until=? WHERE lifetime_id=? AND observed_until IS NULL", (point["observed_at"], point["lifetime_id"]))
                self.db.execute("INSERT INTO associations VALUES(?,?,?,NULL)", (point["lifetime_id"], model, point["observed_at"]))
        self.db.execute("INSERT INTO meta VALUES('last_successful_save',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (received,))
        return True

    def _prune(self, now: datetime) -> None:
        self.db.execute("DELETE FROM readings WHERE observed_at<?", (utc(now - WINDOW),))
        self.db.commit()
        # FULL auto-vacuum reclaims physical pages; leave headroom for rollback journal.
        while self._physical_bytes() > self.max_bytes:
            oldest = self.db.execute("SELECT id,observed_at FROM readings ORDER BY observed_at,id LIMIT 1").fetchone()
            if oldest is None:
                raise OSError("usage history budget cannot hold the database schema")
            self.db.execute("DELETE FROM readings WHERE id=?", (oldest[0],))
            self.db.execute("INSERT INTO meta VALUES('storage_truncated','1') ON CONFLICT(key) DO UPDATE SET value='1'")
            self.db.execute("INSERT INTO meta VALUES('storage_truncated_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (oldest[1],))
            self.db.commit()
        self.db.execute("DELETE FROM lifetimes WHERE lifetime_id NOT IN (SELECT DISTINCT lifetime_id FROM readings)")
        self.db.execute("DELETE FROM associations WHERE lifetime_id NOT IN (SELECT lifetime_id FROM lifetimes)")
        self.db.execute("DELETE FROM nodes WHERE node_id NOT IN (SELECT DISTINCT node_id FROM readings)")
        self.db.commit()

    def record(self, node_id: str, host: str, point: dict[str, Any], *, now: datetime | None = None,
               expected_pid: int | None = None, expected_start: str | None = None,
               expected_role: str | None = None, model: str | None = None,
               context: dict[str, Any] | None = None) -> bool:
        return bool(self.record_many(node_id, host, [point], now=now, expected_pid=expected_pid,
                                     expected_start=expected_start, expected_role=expected_role, model=model, context=context))

    def record_many(self, node_id: str, host: str, points: list[dict[str, Any]], *, now: datetime | None = None,
                    expected_pid: int | None = None, expected_start: str | None = None,
                    expected_role: str | None = None, model: str | None = None,
                    context: dict[str, Any] | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        for point in points:
            validate(node_id, point, now=now, expected_pid=expected_pid, expected_start=expected_start, expected_role=expected_role)
        context_bytes = len(json.dumps(context or {}, allow_nan=False).encode())
        if context_bytes > 8192:
            raise ValueError("node metadata exceeds 8 KiB")
        with self.lock:
            try:
                # Reserve an index page so a point too large to retain alone cannot retire good history.
                required = self._base_bytes + self.db.execute("PRAGMA page_size").fetchone()[0]
                required += context_bytes + max(len(json.dumps(point, allow_nan=False).encode()) for point in points)
                if required > self.max_bytes:
                    raise OSError("usage history budget cannot hold this reading")
                with self.db:
                    accepted = sum(self._write(node_id, host, point, utc(now), model, context) for point in points)
                if accepted:
                    self._prune(now)
                    if not any(self.db.execute(
                            "SELECT 1 FROM readings WHERE lifetime_id=? AND observed_at=?",
                            (point["lifetime_id"], point["observed_at"])).fetchone() for point in points):
                        raise OSError("usage history budget retired the entire new batch")
                    self.last_successful_save = self._meta("last_successful_save")
                    self.recording, self.error = "recording", None
                return accepted
            except (sqlite3.Error, OSError) as exc:
                self.db.rollback()
                self.recording, self.error = "failed", str(exc)
                raise
