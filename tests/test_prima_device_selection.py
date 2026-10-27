from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors import control, launch
from kllm.modules.primacpp.interactors.evidence import verify_vulkan_execution
from kllm.modules.primacpp.interactors.execution import Asset, ExecutionManifest, Segment


def allocation(devices, ngl=2):
    return RankAllocation(node_id="node", role="master", rank=0, address="127.0.0.1", next_node_id="node",
                          layers=[LayerRange(start=0, end=4)], ngl=ngl, devices=devices,
                          tensor_split=[1 / len(devices)] * len(devices) if devices else [])


def test_uuid_selection_pins_visibility_and_native_order(monkeypatch, tmp_path):
    a, b = "a" * 32, "b" * 32
    devices = [{"name": "Vulkan0", "device_identity": a, "driver_identity": "1", "physical_index": 0},
               {"name": "Vulkan1", "device_identity": b, "driver_identity": "1", "physical_index": 5}]
    runtime = {"backend": "vulkan", "artifact_dir": str(tmp_path), "llama_server": str(tmp_path / "server.exe"),
               "capabilities": {"features": ["explicit_devices_v1"]}}
    calls = []
    monkeypatch.setattr(control, "discover_vulkan_devices", lambda _: devices)
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--device"})
    monkeypatch.setattr(control.native, "require_native", lambda: SimpleNamespace(
        start_prima_rank=lambda value: calls.append(value) or {"pid": 17, "process_start_token": "owned"}))
    monkeypatch.setattr(control.native, "_launch_record", lambda value: value)
    # This test pins device selection only; settings translation is covered elsewhere.
    monkeypatch.setattr(control, "rank_arguments", lambda *args, **kwargs: [])
    from kllm.shared import process_registry
    monkeypatch.setattr(process_registry, "register_child", lambda *args: None)
    # RankSupervisor.start stages execution.json before spawning; mirror that contract.
    rank = allocation([b, a])
    manifest = ExecutionManifest(runtime_id="a" * 64, run_id=str(uuid4()), plan_digest="b" * 64,
                                 total_layers=4, ranks=[rank],
                                 assets=[Asset(name="model.gguf", kind="model", sha256="c" * 64, size=1)],
                                 settings={}, inventory_digest="d" * 64,
                                 segments=[Segment(start=0, end=4, node_id="node")],
                                 backends={"node": "vulkan"})
    (tmp_path / "execution.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    control.spawn_rank(runtime, rank, tmp_path / "model.gguf", tmp_path / "execution.json", tmp_path / "rank.log", {})
    assert calls[0]["env"]["GGML_VK_VISIBLE_DEVICES"] == "5,0"
    assert calls[0]["extra_args"] == ["--device", "Vulkan0,Vulkan1"]


def test_cpu_rank_does_not_initialize_unselected_gpu_backends(monkeypatch):
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--device"})
    assert launch.device_arguments(Path("binary"), {"capabilities": {"features": ["explicit_devices_v1"]}},
                                   allocation([], ngl=0), selected=[]) == ["--device", "none"]


def test_device_capability_requires_matching_binary_and_selected_device_support(monkeypatch):
    rank = allocation(["Vulkan0", "Vulkan1"])
    with pytest.raises(ValueError, match="selected-device"):
        launch.device_arguments(Path("binary"), {}, rank, selected=[])
    with pytest.raises(ValueError, match="selected-device"):
        launch.device_arguments(Path("binary"), {}, allocation(["Vulkan0"]), selected=[])
    monkeypatch.setattr(launch, "help_flags", lambda _: set())
    with pytest.raises(ValueError, match="disagrees"):
        launch.device_arguments(Path("binary"), {"capabilities": {"features": ["explicit_devices_v1"]}}, rank, selected=[])
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--device"})
    with pytest.raises(ValueError, match="strict device KV"):
        launch.device_arguments(
            Path("binary"),
            {"capabilities": {"features": ["explicit_devices_v1", "strict_device_kv_v1"]}},
            rank,
            selected=[],
        )
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--device", "--strict-device-kv"})
    assert launch.device_arguments(
        Path("binary"),
        {"capabilities": {"features": ["explicit_devices_v1", "strict_device_kv_v1"]}},
        rank,
        selected=[],
    ) == ["--device", "Vulkan0,Vulkan1", "--strict-device-kv"]


def execution_fixture(split, layers=4):
    rank = RankAllocation(node_id="node", role="master", rank=0, address="127.0.0.1", next_node_id="node",
                          layers=[LayerRange(start=0, end=layers)], ngl=layers,
                          devices=[f"Vulkan{i}" for i in range(len(split))], tensor_split=split)
    manifest = SimpleNamespace(runtime_id="runtime", digest="manifest", settings={})
    selected = [{"name": f"Vulkan{i}", "device_identity": str(i), "driver_identity": "driver"} for i in range(len(split))]
    weights = {"Vulkan0": [f"blk.{layer}.{suffix}.weight" for layer in range(layers)
                           for suffix in ("attn_q", "attn_k", "attn_v", "attn_output", "ffn_gate", "ffn_up", "ffn_down")]}
    reports = [{"schema_version": 1, "completed": True, "runtime_id": "runtime", "manifest_digest": "manifest",
                "rank": 0, "phase": phase, "tokens": tokens, "devices": selected[:1], "executed_weights": weights}
               for phase, tokens in (("prefill", 8), ("decode", 1))]
    return rank, manifest, selected, reports


def test_execution_requires_exact_weighted_device_ownership():
    rank, manifest, selected, reports = execution_fixture([3.0, 1.0])
    weights = reports[0]["executed_weights"]
    weights["Vulkan1"] = [weight for weight in weights["Vulkan0"] if weight.startswith("blk.3.")]
    weights["Vulkan0"] = [weight for weight in weights["Vulkan0"] if not weight.startswith("blk.3.")]
    for report in reports:
        report["devices"] = selected
    assert verify_vulkan_execution(reports, manifest, rank, selected)["status"] == "passed"
    weights["Vulkan0"], weights["Vulkan1"] = weights["Vulkan1"], weights["Vulkan0"]
    with pytest.raises(ValueError, match="another backend"):
        verify_vulkan_execution(reports, manifest, rank, selected)


def test_execution_reports_only_devices_with_assigned_work():
    rank, manifest, selected, reports = execution_fixture([100.0, 1.0], layers=1)
    assert verify_vulkan_execution(reports, manifest, rank, selected)["status"] == "passed"
    reports[0]["devices"] = selected
    with pytest.raises(ValueError, match="different physical"):
        verify_vulkan_execution(reports, manifest, rank, selected)


def test_execution_supports_selected_nonzero_visible_ordinal():
    rank, manifest, selected, reports = execution_fixture([1.0])
    for report in reports:
        report["executed_weights"] = {"Vulkan1": report["executed_weights"]["Vulkan0"]}
        report["devices"] = [{**selected[0], "name": "Vulkan1"}]
    assert verify_vulkan_execution(reports, manifest, rank, selected, logical_names=["Vulkan1"])["status"] == "passed"
