"""Backend-independent physical GPU identities used by MPI admission.

Device names and backend ordinals are never treated as identity. Linux uses a
PCI sysfs address, Windows uses the Plug and Play instance ID, and a single
integrated Apple GPU uses its platform identity. Ambiguous inventories return
no devices, which keeps MPI admission closed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def _hex_vendor(value: str | int | None) -> str | None:
    if value is None:
        return None
    try:
        number = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None
    return f"0x{number:04x}"


def _linux_inventory(sys_root: Path = Path("/sys")) -> list[dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}
    for card in sorted((sys_root / "class" / "drm").glob("card[0-9]*")):
        device_link = card / "device"
        try:
            resolved = device_link.resolve(strict=True)
            pci_address = resolved.name.lower()
            try:
                uevent = (device_link / "uevent").read_text(encoding="ascii")
                slot = next(
                    line.split("=", 1)[1] for line in uevent.splitlines()
                    if line.startswith("PCI_SLOT_NAME=")
                )
                pci_address = slot.lower()
            except (OSError, StopIteration):
                pass
            if not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", pci_address):
                continue
            vendor = (device_link / "vendor").read_text(encoding="ascii").strip().lower()
            device = (device_link / "device").read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if not re.fullmatch(r"0x[0-9a-f]{4}", vendor):
            continue
        devices[pci_address] = {
            "physical_id": f"pci:{pci_address}",
            "vendor_id": vendor,
            "device_id": device,
            "platform_handle": card.name,
        }
    return [devices[key] for key in sorted(devices)]


def _parse_windows_inventory(output: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(output)
    except ValueError:
        return []
    rows = value if isinstance(value, list) else [value]
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        pnp = str(row.get("PNPDeviceID") or "").strip()
        match = re.search(r"(?:^|\\)VEN_([0-9A-Fa-f]{4})(?:&|\\)", pnp)
        if not pnp or not match:
            continue
        physical_id = "pnp:" + pnp.lower()
        if physical_id in seen:
            continue
        seen.add(physical_id)
        result.append({
            "physical_id": physical_id,
            "vendor_id": "0x" + match.group(1).lower(),
            "platform_handle": pnp,
            "name": str(row.get("Name") or ""),
        })
    return sorted(result, key=lambda item: item["physical_id"])


def _windows_inventory() -> list[dict[str, Any]]:
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,PNPDeviceID | ConvertTo-Json -Compress"
    )
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_windows_inventory(output.stdout) if output.returncode == 0 else []


def physical_inventory(
    local_gpus: list[dict[str, Any]] | None = None,
    *,
    platform: str | None = None,
    sys_root: Path = Path("/sys"),
) -> list[dict[str, Any]]:
    """Return only identities that can be mapped independently of a backend."""
    platform = platform or sys.platform
    if platform.startswith("linux"):
        return _linux_inventory(sys_root)
    if platform == "win32":
        return _windows_inventory()
    if platform == "darwin":
        gpus = list(local_gpus or [])
        # Current supported Apple Silicon hosts expose one integrated physical
        # GPU. Multiple/discrete Mac GPUs need an IORegistry mapping first.
        if len(gpus) != 1:
            return []
        return [{
            "physical_id": "apple-integrated:0",
            "vendor_id": "0x106b",
            "platform_handle": "integrated:0",
            "name": str(gpus[0].get("name") or "Apple GPU"),
        }]
    return []
