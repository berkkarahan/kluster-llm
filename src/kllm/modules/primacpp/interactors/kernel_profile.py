"""Validate typed native kernel estimates without treating them as generation timing."""

import math
from statistics import mean, median
from pathlib import Path

from kllm.modules.primacpp.interactors.devices import stable_device_inventory
from kllm.modules.primacpp.entities import StorageEvidence


def storage_identity(model):
    path = Path(model)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Profile model storage disappeared or changed")
    return StorageEvidence(filesystem_device=str(path.stat().st_dev), directory=str(path.parent.resolve()))


def kernel_hardware(host, devices, backend):
    observed = [{key: device[key] for key in ("name", "device_identity", "driver_identity",
                 "physical_index", "memory_bytes")} for device in stable_device_inventory(devices)]
    return {"host": host, "backend": backend, "devices": observed}


def kernel_settings(context, threads, devices):
    return {"context": context, "threads": threads, "mode": "kernel", "kernel_profile_version": 2,
            "history": context, "flash_attention": "off", "cache_type_k": "f16", "cache_type_v": "f16",
            "devices": [device["device_identity"] for device in devices], "disk_method": "serial_unbuffered_v1"}


def require_vulkan_profile_device(metrics: dict, selected: dict) -> None:
    """Bind completed profiler measurements to the selected physical Vulkan GPU."""
    expected = {key: selected[key] for key in ("device_identity", "driver_identity", "physical_index")}
    reported = metrics.get("selected_device") if isinstance(metrics, dict) else None
    if (not isinstance(reported, dict) or set(reported) != set(expected)
            or type(reported.get("physical_index")) is not int or reported != expected):
        raise ValueError("Native Vulkan profile did not measure the selected physical device")


def positive(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"Invalid typed kernel profile {label}")
    return float(value)


def integer(value, label, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError(f"Invalid typed kernel profile {label}")
    return value


def decode_kernel_profile(metrics, request, backend):
    if (not isinstance(metrics, dict) or type(metrics.get("schema_version")) is not int
            or metrics["schema_version"] != 2 or metrics.get("measurement_kind") != "typed_roofline_estimate"
            or metrics.get("backend") != backend or type(metrics.get("context")) is not int
            or metrics["context"] != request.context or type(metrics.get("history")) is not int
            or metrics["history"] != request.context or type(metrics.get("threads")) is not int
            or metrics["threads"] != request.threads):
        raise ValueError("Typed kernel profile does not match the requested backend/settings")
    total = integer(metrics.get("total_layers"), "layer count", minimum=1)
    layers = metrics.get("layers")
    if total > 4096 or not isinstance(layers, list) or len(layers) != total:
        raise ValueError("Incomplete typed kernel profile layers")
    rates = []
    for name in ("cpu_gflops", "gpu_gflops"):
        table = metrics.get(name)
        if not isinstance(table, dict) or len(table) > 64 or any(not isinstance(key, str) for key in table):
            raise ValueError("Invalid typed kernel profile rate table")
        rates.append({key: positive(value, name) for key, value in table.items()})
    if not rates[0] or (backend == "cpu" and rates[1]) or (backend != "cpu" and not rates[1]):
        raise ValueError("Typed kernel profile cannot substitute another backend")
    ram = positive(metrics.get("ram_gbytes_s"), "RAM rate")
    if backend == "cpu":
        if metrics.get("gpu_read_gbytes_s") is not None or metrics.get("gpu_kv_copy_ms") is not None:
            raise ValueError("CPU kernel profile reported GPU measurements")
        vram, copy = None, None
    else:
        vram = positive(metrics.get("gpu_read_gbytes_s"), "GPU memory rate")
        copy = positive(metrics.get("gpu_kv_copy_ms"), "GPU copy time")

    def cost(row):
        if not isinstance(row, dict) or not isinstance(row.get("flops"), dict) or not row["flops"]:
            raise ValueError("Incomplete typed kernel profile tensor costs")
        weights = integer(row.get("weights_bytes"), "tensor bytes", minimum=1)
        active = integer(row.get("active_bytes"), "active bytes", minimum=1)
        kv = integer(row.get("kv_bytes"), "KV bytes")
        if active > weights:
            raise ValueError("Invalid typed kernel profile active storage")
        flops = {key: integer(value, "tensor operations", minimum=1) for key, value in row["flops"].items()}
        values = []
        for name, table, bandwidth in (("cpu_ms", rates[0], ram), ("gpu_ms", rates[1], vram)):
            if bandwidth is None:
                if row.get(name) is not None:
                    raise ValueError("CPU profile cannot claim a GPU tensor estimate")
                values.append(None)
                continue
            if any(key not in table for key in flops):
                raise ValueError("Missing typed kernel profile rate for a model tensor")
            expected = max(sum(value / (table[key] * 1e6) for key, value in flops.items()),
                           (active + kv) / (bandwidth * 1e6))
            observed = positive(row.get(name), "tensor time")
            if not math.isclose(observed, expected, rel_tol=1e-6, abs_tol=1e-9):
                raise ValueError("Typed kernel profile tensor estimate disagrees with its measurements")
            values.append(observed)
        return values, weights

    cpu, gpu, weights = [], [], []
    for index, row in enumerate(layers):
        if not isinstance(row, dict) or type(row.get("layer")) is not int or row["layer"] != index:
            raise ValueError("Typed kernel profile has duplicate or unordered layers")
        times, size = cost(row)
        cpu.append(times[0])
        if times[1] is not None:
            gpu.append(times[1])
        weights.append(size)
    output, _ = cost(metrics.get("output"))
    if type(metrics.get("tied_output")) is not bool:
        raise ValueError("Invalid typed kernel profile output ownership")
    if (metrics.get("disk_method") != "serial_unbuffered_v1" or type(metrics.get("disk_queue_depth")) is not int
            or metrics["disk_queue_depth"] != 1 or metrics.get("disk_file_bytes") != 64 * 1024 * 1024
            or metrics.get("disk_seq_block_bytes") != 1024 * 1024):
        raise ValueError("Invalid typed kernel profile I/O method")
    block = integer(metrics.get("disk_random_block_bytes"), "I/O alignment", minimum=4096)
    if block > 65536 or block & (block - 1):
        raise ValueError("Invalid typed kernel profile I/O alignment")
    for key in ("disk_write_seq_gbytes_s", "disk_write_random_gbytes_s"):
        positive(metrics.get(key), key)
    handoff = metrics.get("gpu_host_transfer")
    transfers = {}
    if handoff is not None:
        if (backend == "cpu" or not isinstance(handoff, dict)
                or set(handoff) != {"method", "bytes", "upload_ms", "download_ms"}
                or handoff["method"] != "completed_f32_embedding_v1"):
            raise ValueError("Invalid GPU host transfer method/backend")
        size = integer(handoff["bytes"], "GPU handoff bytes", minimum=4)
        if size > 4194304 or size % 4:
            raise ValueError("Invalid GPU host transfer tensor width")
        transfers["gpu_transfer_bytes"] = size
        for key in ("upload_ms", "download_ms"):
            samples = handoff[key]
            if not isinstance(samples, list) or len(samples) != 5:
                raise ValueError("Incomplete GPU host transfer samples")
            transfers["gpu_" + key] = median(positive(v, "GPU host transfer elapsed time") for v in samples)
    logits = metrics.get("gpu_logits_transfer")
    if logits is not None:
        if (backend == "cpu" or handoff is None or not isinstance(logits, dict)
                or set(logits) != {"method", "bytes", "download_ms"}
                or logits["method"] != "completed_f32_logits_v1"):
            raise ValueError("Invalid GPU logits transfer method/backend")
        size = integer(logits["bytes"], "GPU logits bytes", minimum=4)
        samples = logits["download_ms"]
        if size > 4194304 or size % 4 or not isinstance(samples, list) or len(samples) != 5:
            raise ValueError("Incomplete or invalid GPU logits transfer samples")
        transfers["gpu_logits_bytes"] = size
        transfers["gpu_logits_download_ms"] = median(positive(v, "GPU logits elapsed time") for v in samples)
    return {**transfers, "kernel_profile_version": 2, "kernel_context": request.context, "kernel_threads": request.threads,
            "total_layers": total, "cpu_layer_ms": mean(cpu), "gpu_layer_ms": mean(gpu) if gpu else None,
            "cpu_layer_ms_by_index": cpu, "gpu_layer_ms_by_index": gpu, "weights_bytes_by_index": weights,
            "input_ms": positive(metrics.get("input_ms"), "input time"),
            "output_cpu_ms": output[0], "output_gpu_ms": output[1], "gpu_kv_copy_ms": copy,
            "disk_read_bytes_s": positive(metrics.get("disk_read_seq_gbytes_s"), "disk read rate") * 1e9,
            "disk_random_read_bytes_s": positive(metrics.get("disk_read_random_gbytes_s"), "random read rate") * 1e9,
            "disk_random_block_bytes": block,
            "memory_available_bytes": int(positive(metrics.get("memory_available_gib"), "available memory") * 1024**3)}
