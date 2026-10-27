"""Explicit physical Vulkan selection across visibility ordinal remapping."""

from __future__ import annotations

import json
import os
import re
import subprocess


def stable_device_inventory(devices):
    """Capacity readings fluctuate during allocation; physical identity does not."""
    if not isinstance(devices, list):
        return devices
    return [{key: value for key, value in device.items() if key != "free_bytes"}
            if isinstance(device, dict) else device for device in devices]


def discover_vulkan_devices(runtime: dict) -> list[dict]:
    # A saved build's ordinals are observations, not the current device inventory.
    environment = {**os.environ, **runtime.get("runtime_env", {})}
    environment.pop("GGML_VK_VISIBLE_DEVICES", None)
    result = subprocess.run(
        [runtime["llama_server"], "--list-devices"],
        env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode:
        raise ValueError("Prima could not enumerate Vulkan devices; inspect runtime dependencies")
    reports = [line.split("=", 1)[1] for line in result.stdout.splitlines()
               if line.startswith("KLLM_PRIMA_CAPABILITIES=")]
    if len(reports) != 1:
        raise ValueError("Prima returned an invalid Vulkan device report")
    try:
        report = json.loads(reports[0])
        devices = report["devices"]
        if not isinstance(devices, list) or any(not isinstance(d, dict) for d in devices):
            raise ValueError("Invalid device list")
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("Prima returned an invalid Vulkan device report") from error
    return [dict(d) for d in devices if str(d.get("backend", "")).lower() == "vulkan"]


def select_vulkan_devices(devices: list[dict], selectors: list[str] | None = None) -> list[dict]:
    """Select by exact UUID, name or description; ambiguity never chooses device zero."""
    if not devices:
        raise ValueError("Requested Vulkan backend has no usable device; no substitution allowed")
    if selectors is None:
        if len(devices) != 1:
            raise ValueError("Select an explicit Vulkan device on a multi-GPU node")
        selected = [devices[0]]
    else:
        if not selectors:
            raise ValueError("Vulkan GPU execution requires a selected device")
        selected = []
        for selector in selectors:
            matches = [d for d in devices if selector in (
                d.get("device_identity"), d.get("name"), d.get("description"))]
            if len(matches) != 1:
                raise ValueError(f"Vulkan device selector {selector!r} is absent or ambiguous")
            selected.append(matches[0])
    for device in selected:
        if (not re.fullmatch(r"[0-9a-f]{32}", str(device.get("device_identity", "")))
                or type(device.get("physical_index")) is not int or device["physical_index"] < 0
                or not isinstance(device.get("driver_identity"), str) or not device["driver_identity"]):
            raise ValueError("Upgrade Prima for physical Vulkan device identity before GPU execution")
    if len({d["device_identity"] for d in selected}) != len(selected):
        raise ValueError("A physical Vulkan device was selected more than once")
    if len({d["physical_index"] for d in selected}) != len(selected):
        raise ValueError("Vulkan physical device indices disagree")
    return [dict(device) for device in selected]


def vulkan_environment(devices: list[dict]) -> dict[str, str]:
    selected = select_vulkan_devices(devices, [d["device_identity"] for d in devices])
    return {"GGML_VK_VISIBLE_DEVICES": ",".join(str(d["physical_index"]) for d in selected)}


def remapped_vulkan_names(devices: list[dict]) -> list[str]:
    # ggml's visible order, not the unfiltered logical ordinal, owns tensor_split.
    return [f"Vulkan{i}" for i in range(len(devices))]
