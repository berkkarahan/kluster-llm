"""GPU inventory reconciliation and optional per-device sensor collection."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from typing import Any

from kllm import usage_probe
from kllm.state import ClusterState


def _normalized_device_name(name: object) -> str:
    text = re.sub(r"\((?:tm|r)\)", "", str(name or ""), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _copy_byte_metric(
    previous: dict,
    *,
    value: int | None,
    status: str,
    reason: str | None,
    source: str,
    detail: str | None,
) -> dict:
    metric = dict(previous)
    metric.update(
        value=value,
        unit="bytes",
        scope="device",
        status=status,
        reason=reason,
        detail=None if detail is None else str(detail)[:256],
        interval_start=None,
        interval_end=None,
        duration_ms=None,
        source=source,
    )
    return metric


def reconcile_resources(snapshot: Any, inventory: Any) -> None:
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
                total,
                value=vram,
                status="measured",
                reason=None,
                source="inventory.llama_list_devices",
                detail=f"{total.get('source')}={total.get('value')}",
            )
            used_value_record = view.get("used")
            used = used_value_record if isinstance(used_value_record, dict) else {}
            used_value = used.get("value")
            free_record = view.get("free")
            previous_free = free_record if isinstance(free_record, dict) else total
            if type(used_value) is int and 0 <= used_value <= vram:
                view["free"] = _copy_byte_metric(
                    previous_free,
                    value=vram - used_value,
                    status="measured",
                    reason=None,
                    source="inventory.capacity_minus_usage",
                    detail=None,
                )
            else:
                view["free"] = _copy_byte_metric(
                    previous_free,
                    value=None,
                    status="error",
                    reason="ambiguous_pool",
                    source="inventory.capacity_minus_usage",
                    detail=None,
                )
    if isinstance(snapshot.get("host"), dict):
        snapshot["totals"] = usage_probe.memory_totals(snapshot["host"], snapshot)


def project_resource_devices(
    snapshot: dict[str, Any],
    state: ClusterState,
    sensors: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
        if (
            type(index) is not int
            or index < 0
            or sum(d.get("index") == index for d in inventory) != 1
        ):
            continue
        device_id = str(index)
        dedicated = next((view for view in device["memory"] if view["kind"] == "dedicated"), None)
        optional = (sensors or {}).get(device_id) or {}
        row = dict(
            device_id=device_id,
            scope="device",
            utilization_percent=optional.get("utilization_percent"),
            temperature_c=optional.get("temperature_c"),
            power_w=optional.get("power_w"),
            used_bytes=dedicated["used"]["value"] if dedicated else None,
            total_bytes=dedicated["total"]["value"] if dedicated else None,
        )
        row["unavailable_reason"] = (
            "some device counters unavailable"
            if any(
                row[key] is None
                for key in (
                    "utilization_percent",
                    "temperature_c",
                    "power_w",
                    "used_bytes",
                    "total_bytes",
                )
            )
            else None
        )
        result.append(row)
    return result


def _devices(state: ClusterState) -> list[dict[str, Any]]:
    member = state.members.get(state.node_id) or {}
    inventory = member.get("devices") or []
    if not inventory:
        return []
    values: dict[str, list[str]] = {}
    windows_used: dict[str, int] = {}
    if member.get("backend") == "cuda" and shutil.which("nvidia-smi"):
        try:
            output = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=0.8,
                check=True,
            ).stdout
            values = {
                row[0]: row[1:]
                for row in (list(map(str.strip, line.split(","))) for line in output.splitlines())
                if len(row) == 6
            }
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
        missing = (
            "Windows dedicated GPU memory counter unavailable or adapter not identified"
            if sys.platform == "win32" and member.get("backend") in ("vulkan", "rocm")
            else "device sensor unavailable"
        )
        result.append(
            {
                "device_id": device_id,
                "scope": "device",
                "utilization_percent": utilization,
                "used_bytes": used,
                "total_bytes": parsed(row[2], 1024**2) if row else device.get("vram_bytes"),
                "temperature_c": temperature,
                "power_w": power,
                "unavailable_reason": (
                    missing
                    if not row and used is None
                    else "some device sensors unavailable"
                    if None in (utilization, used, temperature, power)
                    else None
                ),
            }
        )
    return result


def _windows_adapter_records() -> list[tuple[str, str]]:
    """Read DirectX adapter LUIDs and descriptions, without guessing GPU order."""
    import importlib

    winreg: Any = importlib.import_module("winreg")

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
            capture_output=True,
            text=True,
            timeout=1.5,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        records = _windows_adapter_records()
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}

    counters: dict[str, list[int]] = {}
    for line in output.splitlines():
        match = re.search(
            r"luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})_phys_(\d+)\)\\dedicated usage\t(.+)$", line, re.I
        )
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
        matches = [
            luid for luid, description in records if normalized(description) == name and name
        ]
        if (
            inventory_names.count(name) != 1
            or len(matches) != 1
            or len(counters.get(matches[0], [])) != 1
        ):
            continue
        used = counters[matches[0]][0]
        total = device.get("vram_bytes")
        if isinstance(total, (int, float)) and 0 < total and used <= total:
            result[str(device.get("index"))] = used
    return result
