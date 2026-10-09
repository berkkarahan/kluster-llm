"""Worker launch inventory, separate from the hardware GPU inventory."""

from collections.abc import Sequence
from typing import Any

from kllm.core.gpu_selection import GpuDeviceInput


def worker_rpc_devices(gpus: Sequence[GpuDeviceInput], backend: str) -> list[dict[str, Any]]:
    """The explicit --device list fixes RPC indices across launch and restart."""
    devices = [
        {"index": i, "device": gpu.device, "backend": gpu.backend or backend, "memory_kind": "gpu"}
        for i, gpu in enumerate(gpu for gpu in gpus if gpu.device)
    ]
    devices.append({"index": len(devices), "device": "CPU", "backend": "cpu", "memory_kind": "ram"})
    return devices
