"""Typed tensor costs, cache refusal and owned profile-process contracts."""

from copy import deepcopy
import json
import os
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.primacpp.entities import DeviceProfile, ProfileRequest
from kllm.modules.primacpp.interactors.kernel_profile import (decode_kernel_profile,
    kernel_settings, require_vulkan_profile_device)
from kllm.modules.primacpp.interactors.preparation import ProfileJobs
from kllm.modules.primacpp.interactors.profiles import ProfileCache, profile_key
from kllm.modules.runtimes.interactors.registry import RuntimeRegistry


def request():
    return ProfileRequest(runtime_id="a" * 64, model="fixture", mode="kernel", context=512,
                          threads=8, request_id=str(uuid4()))


def report(backend="vulkan"):
    accelerated = backend != "cpu"
    cpu_rates = {"q8_0": 10.0, "q4_K": 20.0, "f32": 20.0}
    gpu_rates = {"q8_0": 100.0, "q4_K": 200.0, "f32": 200.0} if accelerated else {}
    layers = []
    for index, (kind, operations) in enumerate((("q8_0", 2_000_000), ("q4_K", 1_000_000))):
        layers.append({"layer": index, "flops": {kind: operations, "f32": 1000},
                       "weights_bytes": 4096, "active_bytes": 2048, "kv_bytes": 2048,
                       "cpu_ms": operations / (cpu_rates[kind] * 1e6) + 0.00005,
                       "gpu_ms": operations / (gpu_rates[kind] * 1e6) + 0.000005 if accelerated else None})
    return {"schema_version": 2, "measurement_kind": "typed_roofline_estimate", "backend": backend,
            "context": 512, "history": 512, "threads": 8, "total_layers": 2,
            "cpu_gflops": cpu_rates, "gpu_gflops": gpu_rates, "layers": layers,
            "output": {key: value for key, value in layers[0].items() if key != "layer"},
            "input_ms": 0.0001, "tied_output": True, "ram_gbytes_s": 10.0,
            "gpu_read_gbytes_s": 100.0 if accelerated else None,
            "gpu_kv_copy_ms": 0.03 if accelerated else None, "memory_available_gib": 4.0,
            "disk_method": "serial_unbuffered_v1", "disk_queue_depth": 1,
            "disk_file_bytes": 64 * 1024 * 1024, "disk_seq_block_bytes": 1024 * 1024,
            "disk_random_block_bytes": 4096, "disk_read_seq_gbytes_s": 2.0,
            "disk_read_random_gbytes_s": 0.05, "disk_write_seq_gbytes_s": 1.0,
            "disk_write_random_gbytes_s": 0.02}


@pytest.mark.parametrize("fault", ["missing", "other_uuid", "other_driver", "other_index", "bool_index", "extra"])
def test_vulkan_profile_requires_exact_selected_physical_device(fault):
    selected = {"device_identity": "1" * 32, "driver_identity": "driver", "physical_index": 2}
    metrics = {"selected_device": dict(selected)}
    if fault == "missing":
        del metrics["selected_device"]
    elif fault == "other_uuid":
        metrics["selected_device"]["device_identity"] = "2" * 32
    elif fault == "other_driver":
        metrics["selected_device"]["driver_identity"] = "another"
    elif fault == "other_index":
        metrics["selected_device"]["physical_index"] = 1
    elif fault == "bool_index":
        metrics["selected_device"]["physical_index"] = True
    else:
        metrics["selected_device"]["name"] = "Vulkan0"
    with pytest.raises(ValueError, match="selected physical device"):
        require_vulkan_profile_device(metrics, selected)
    require_vulkan_profile_device({"selected_device": dict(selected)}, selected)


@pytest.mark.parametrize("backend", ["cpu", "vulkan"])
def test_mixed_tensor_types_preserve_absolute_layer_and_output_costs(backend):
    values = decode_kernel_profile(report(backend), request(), backend)
    assert values["cpu_layer_ms_by_index"] == pytest.approx([0.20005, 0.05005])
    assert values["cpu_layer_ms"] == pytest.approx(0.12505)
    assert values["weights_bytes_by_index"] == [4096, 4096]
    assert values["output_cpu_ms"] == pytest.approx(0.20005)
    assert values["disk_read_bytes_s"] == 2e9
    assert values["memory_available_bytes"] == 4 * 1024**3
    if backend == "vulkan":
        assert values["gpu_layer_ms_by_index"] == pytest.approx([0.020005, 0.005005])
    else:
        assert values["gpu_layer_ms_by_index"] == [] and values["output_gpu_ms"] is None


@pytest.mark.parametrize("fault", ["backend", "context", "history", "threads", "schema", "duplicate",
    "missing", "nan", "operations", "rate", "substitute", "estimate", "storage", "method", "alignment"])
def test_incomplete_or_mismatched_native_costs_cannot_be_cached(fault):
    value = report()
    if fault in ("backend", "context", "history", "threads", "schema"):
        key = "schema_version" if fault == "schema" else fault
        value[key] = "cuda" if fault == "backend" else 2.0 if fault == "schema" else 513
    elif fault == "duplicate":
        value["layers"][1]["layer"] = 0
    elif fault == "missing":
        value["layers"].pop()
    elif fault == "nan":
        value["layers"][0]["cpu_ms"] = float("nan")
    elif fault == "operations":
        value["layers"][0]["flops"]["q8_0"] = True
    elif fault == "rate":
        del value["gpu_gflops"]["q8_0"]
    elif fault == "substitute":
        value["gpu_gflops"] = {}
    elif fault == "estimate":
        value["layers"][0]["cpu_ms"] *= 2
    elif fault == "storage":
        value["layers"][0]["active_bytes"] = 8192
    elif fault == "method":
        value["disk_method"] = "cached"
    else:
        value["disk_random_block_bytes"] = 6000
    with pytest.raises(ValueError):
        decode_kernel_profile(value, request(), "vulkan")


def test_typed_cache_rejects_negative_or_incomplete_layer_vectors(tmp_path):
    value = DeviceProfile(runtime_id="a" * 64, model_sha256="b" * 64, node_id="node",
        hardware_digest="c" * 64, settings_digest="d" * 64, backend="cpu", measured_at=time.time(),
        **decode_kernel_profile(report("cpu"), request(), "cpu"))
    cache = ProfileCache(tmp_path)
    key = cache.put(value)
    identity = {name: getattr(value, name) for name in ("runtime_id", "model_sha256", "node_id",
        "hardware_digest", "settings_digest", "backend", "device_identity", "driver_identity")}
    assert cache.get(identity) == value
    path = tmp_path / (key + ".json")
    for bad in ([-1.0, 1.0], [1.0], [True, 1.0]):
        corrupted = value.model_dump()
        corrupted["cpu_layer_ms_by_index"] = bad
        path.write_text(json.dumps(corrupted))
        assert cache.get(identity) is None
    assert profile_key(value) == key


def handoff_report():
    value = report()
    value["gpu_host_transfer"] = {"method": "completed_f32_embedding_v1", "bytes": 4096,
        "upload_ms": [0.4, 0.1, 0.3, 0.2, 0.5], "download_ms": [0.2, 0.6, 0.3, 0.1, 0.4]}
    return value


def test_gpu_handoff_preserves_actual_payload_and_completed_medians():
    values = decode_kernel_profile(handoff_report(), request(), "vulkan")
    assert values["gpu_transfer_bytes"] == 4096
    assert values["gpu_upload_ms"] == values["gpu_download_ms"] == 0.3
    assert values["gpu_kv_copy_ms"] == 0.03


@pytest.mark.parametrize("fault", ["bytes", "method", "samples", "negative", "nan", "bool"])
def test_gpu_handoff_refuses_invalid_geometry_or_measurement(fault):
    value = handoff_report()
    handoff = value["gpu_host_transfer"]
    if fault == "bytes":
        handoff["bytes"] = 4097
    elif fault == "method":
        handoff["method"] = "vram_copy"
    elif fault == "samples":
        handoff["upload_ms"].pop()
    else:
        handoff["upload_ms"][0] = {"negative": -1.0, "nan": float("nan"), "bool": True}[fault]
    with pytest.raises(ValueError):
        decode_kernel_profile(value, request(), "vulkan")


def test_cpu_profile_cannot_claim_gpu_host_transfer():
    value = report("cpu")
    value["gpu_host_transfer"] = handoff_report()["gpu_host_transfer"]
    with pytest.raises(ValueError, match="method/backend"):
        decode_kernel_profile(value, request(), "cpu")


def logits_report():
    value = handoff_report()
    value["gpu_logits_transfer"] = {"method": "completed_f32_logits_v1", "bytes": 131072,
        "download_ms": [1.0, 0.5, 0.3, 0.1, 0.2]}
    return value


def test_logits_preserve_vocabulary_width_and_own_download_median():
    values = decode_kernel_profile(logits_report(), request(), "vulkan")
    assert values["gpu_logits_bytes"] == 131072
    assert values["gpu_logits_download_ms"] == 0.3
    assert values["gpu_transfer_bytes"] == 4096


@pytest.mark.parametrize("fault", ["width", "method", "samples", "nan", "negative", "cpu", "embedding"])
def test_logits_refuse_invalid_or_substituted_transfer_evidence(fault):
    value = logits_report()
    backend = "vulkan"
    if fault == "width":
        value["gpu_logits_transfer"]["bytes"] = 131073
    elif fault == "method":
        value["gpu_logits_transfer"]["method"] = "completed_f32_embedding_v1"
    elif fault == "samples":
        value["gpu_logits_transfer"]["download_ms"].pop()
    elif fault in ("nan", "negative"):
        value["gpu_logits_transfer"]["download_ms"][0] = float("nan") if fault == "nan" else -1.0
    elif fault == "cpu":
        transfer = value["gpu_logits_transfer"]
        value = report("cpu")
        value["gpu_logits_transfer"] = transfer
        backend = "cpu"
    else:
        del value["gpu_host_transfer"]
    with pytest.raises(ValueError):
        decode_kernel_profile(value, request(), backend)


@pytest.mark.parametrize("missing", [False, True])
def test_owned_gpu_job_requires_advertised_logits_evidence_before_cache(tmp_path, monkeypatch, missing):
    from kllm.modules.primacpp.interactors import preparation
    model = tmp_path / "model.gguf"
    model.write_bytes(b"controlled model bytes")
    device = {"backend": "vulkan", "name": "Vulkan0", "device_identity": "1" * 32,
        "driver_identity": "fixture-driver", "physical_index": 0, "memory_bytes": 2**30}
    payload = logits_report()
    payload["selected_device"] = {key: device[key] for key in
        ("device_identity", "driver_identity", "physical_index")}
    if missing:
        del payload["gpu_logits_transfer"]
    script = tmp_path / "profile.py"
    script.write_text("import json,os\nassert os.environ['KLLM_PRIMA_PROFILE_BACKEND']=='vulkan'\n"
                      + "print('KLLM_PRIMA_PROFILE='+json.dumps(" + repr(payload) + "))\n")
    if os.name == "nt":
        tool = tmp_path / "profile.cmd"
        tool.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        tool = tmp_path / "profile"
        tool.write_text(f'#!{sys.executable}\nexec(open({str(script)!r}).read())\n')
        tool.chmod(0o700)
    monkeypatch.setattr(preparation, "discover_vulkan_devices", lambda _: [device])
    state = SimpleNamespace(node_id="node", process_pid=None, members={"node": {"backend": "vulkan"}})
    runtime = {"engine": "primacpp", "backend": "vulkan", "profile_tool": str(tool),
        "capabilities": {"features": ["typed_kernel_profile_v2", "host_transfer_profile_v1",
                                    "logits_transfer_profile_v1", "vulkan_device_identity_v1",
                                    "vulkan_profile_device_identity_v1"]}}
    jobs = ProfileJobs(tmp_path / "jobs", lambda _: deepcopy(runtime), lambda _: model,
        lambda: state, RuntimeRegistry._kill)
    try:
        wanted = request().model_copy(update={"backend": "vulkan", "devices": [device["device_identity"]]})
        job = jobs.start(wanted)
        deadline = time.monotonic() + 5
        while jobs.job(job["job_id"])["phase"] not in {"prepared", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
        result = jobs.job(job["job_id"])
        if missing:
            assert result["phase"] == "failed" and "logits transfer capability" in result["error"]
            assert not jobs.cache.root.exists()
        else:
            assert result["phase"] == "prepared", result
            assert result["profile"]["gpu_logits_bytes"] == 131072
            assert result["profile"]["gpu_logits_download_ms"] == 0.3
    finally:
        jobs.close()
    assert not jobs.processes


@pytest.mark.parametrize("downgrade", [False, True])
def test_owned_process_honors_backend_and_typed_capability_before_publication(tmp_path, downgrade):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"controlled model bytes")
    payload = report("cpu") if not downgrade else {"schema_version": 1, "cpu_q4k_gflops": 10,
        "layer_q4k_flops": 10_000_000, "memory_available_gib": 4.0}
    script = tmp_path / "profile.py"
    script.write_text("import json,os\nassert os.environ['KLLM_PRIMA_PROFILE_BACKEND']=='cpu'\n"
                      + "print('KLLM_PRIMA_PROFILE='+json.dumps(" + repr(payload) + "))\n")
    if os.name == "nt":
        tool = tmp_path / "profile.cmd"
        tool.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        tool = tmp_path / "profile"
        tool.write_text(f'#!{sys.executable}\nexec(open({str(script)!r}).read())\n')
        tool.chmod(0o700)
    state = SimpleNamespace(node_id="node", process_pid=None, members={"node": {"backend": "cpu"}})
    runtime = {"engine": "primacpp", "backend": "cpu", "profile_tool": str(tool),
               "capabilities": {"features": ["typed_kernel_profile_v2"]}}
    jobs = ProfileJobs(tmp_path / "jobs", lambda _: deepcopy(runtime), lambda _: model,
                       lambda: state, RuntimeRegistry._kill)
    try:
        job = jobs.start(request())
        until = time.monotonic() + 5
        while jobs.job(job["job_id"])["phase"] not in {"prepared", "failed"} and time.monotonic() < until:
            time.sleep(0.01)
        result = jobs.job(job["job_id"])
        if downgrade:
            assert result["phase"] == "failed" and "incompatible profile" in result["error"]
            assert not jobs.cache.root.exists()
        else:
            assert result["phase"] == "prepared", result
            assert result["profile"]["kernel_profile_version"] == 2
            assert result["profile"]["cpu_layer_ms_by_index"] == pytest.approx([0.20005, 0.05005])
            assert jobs.cache.root.exists()
            assert kernel_settings(512, 8, []) != {"context": 512, "threads": 8}
    finally:
        jobs.close()
    assert not jobs.processes
