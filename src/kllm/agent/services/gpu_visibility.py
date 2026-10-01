"""GPU visibility environment construction."""

from __future__ import annotations


def gpu_visibility_environment(
    backend: str, gpu_ids: list[int] | None
) -> dict[str, str]:
    """Return engine environment overrides for selected physical GPUs."""
    if not gpu_ids:
        return {}

    visible_devices = ",".join(str(device_id) for device_id in gpu_ids)
    if backend == "cuda":
        return {"CUDA_VISIBLE_DEVICES": visible_devices}
    if backend == "vulkan":
        return {"GGML_VK_VISIBLE_DEVICES": visible_devices}
    if backend == "rocm":
        return {
            "HIP_VISIBLE_DEVICES": visible_devices,
            "ROCR_VISIBLE_DEVICES": visible_devices,
        }
    return {}
