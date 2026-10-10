"""Observable GPU/RAM pooling and required-layer placement contracts."""

from pathlib import Path

import pytest

from kllm.shared import native
from kllm.modules.inference.interactors.rpc_devices import worker_rpc_devices
from kllm.modules.inference.interactors.contiguous_allocation import allocate_contiguous
from kllm.modules.inference.interactors.placement_models import PlacementDeviceCandidate, PlacementNodeInput
from kllm.modules.inference.interactors.gpu_selection import GpuDeviceInput
from kllm.modules.inference.interactors.run_launch import build_master_launch
from kllm.modules.inference.interactors.run_planning import resolve_launch_settings
from kllm.modules.inference.entities import RunBody

MIB = 1024**2


def node(nid, *, master=False, gpu=0, ram=4000, unified=False, rpc_cpu=True):
    devices = (
        [
            {
                "index": 0,
                "device": "Vulkan0",
                "backend": "vulkan",
                "vram_bytes": gpu * MIB,
                "free_bytes": gpu * MIB,
            }
        ]
        if gpu
        else []
    )
    rpc = [
        {"index": i, "device": d["device"], "backend": d["backend"], "memory_kind": "gpu"}
        for i, d in enumerate(devices)
    ]
    if rpc_cpu:
        rpc.append({"index": len(rpc), "device": "CPU", "backend": "cpu", "memory_kind": "ram"})
    return {
        "node_id": nid,
        "is_master": master,
        "backend": "vulkan" if gpu else "cpu",
        "total_ram_bytes": ram * MIB,
        "available_ram_bytes": ram * MIB,
        "unified_memory": unified,
        "rpc_endpoint": f"{nid}:50052",
        "devices": devices,
        "rpc_devices": rpc if not master else [],
    }


def plan(nodes, *, required=(), mode="gpu", weights=None, node_shares=None, priority=()):
    layout = {
        "architecture": "llama",
        "metadata": {
            "llama.embedding_length": 512,
            "llama.attention.head_count": 8,
            "llama.attention.head_count_kv": 2,
        },
        "layers": weights or [200 * MIB] * 4,
        "input_bytes": 10 * MIB,
        "output_bytes": 200 * MIB,
        "confidence": "metadata",
    }
    settings = resolve_launch_settings(RunBody(model="fixture.gguf", context=512)).to_mapping()
    return native.plan_split(
        nodes,
        1010 * MIB,
        context=512,
        headroom=1,
        device=mode,
        model_layout=layout,
        launch_settings=settings,
        required_nodes=required,
        node_shares=node_shares,
        node_priority=priority,
    )


def assert_accounting(p):
    assert p["estimated_bytes"] == sum(p["node_alloc_bytes"])
    assert p["estimated_bytes"] == p["vram_alloc_bytes"] + p["ram_alloc_bytes"]
    assert p["cpu_alloc_bytes"] == p["ram_alloc_bytes"]
    assert p["estimated_bytes"] == sum(
        p[key]
        for key in (
            "weights_estimate_bytes",
            "auxiliary_weights_bytes",
            "kv_estimate_bytes",
            "recurrent_state_bytes",
            "checkpoint_estimate_bytes",
            "compute_estimate_bytes",
            "auxiliary_runtime_bytes",
            "execution_overhead_bytes",
        )
    )


def test_required_workers_override_local_fit_without_equal_shares():
    p = plan(
        [node("master", master=True, gpu=4000), node("small", gpu=700), node("large", gpu=3000)],
        required=["master", "small", "large"],
    )
    assert p["fit_status"] != "overflow"
    assert p["cpu_layers"] == 0
    for nid in p["required_nodes"]:
        assert any(
            a["node_id"] == nid and a["layer_start"] < p["estimated_layers"]
            for a in p["device_allocations"]
        )
    assert p["node_workload_bytes"]["small"] < p["node_workload_bytes"]["master"]
    assert_accounting(p)


def test_gpu_pool_beats_master_cpu_spill_and_worker_ram():
    p = plan([node("master", master=True, gpu=700), node("worker", gpu=1000)])
    assert p["fit_status"] != "overflow"
    assert p["cpu_layers"] == 0
    assert all(a["memory_kind"] == "gpu" for a in p["device_allocations"])
    assert_accounting(p)


def test_worker_cpu_ram_is_real_rpc_allocation_and_launch_uses_its_index():
    p = plan(
        [node("master", master=True, ram=600), node("worker", gpu=100, ram=4000)],
        required=["worker"],
    )
    assert p["fit_status"] != "overflow"
    assert p["device_effective"] == "cpu"
    assert p["node_vram_alloc_bytes"]["worker"] == 0
    assert p["node_ram_alloc_bytes"]["worker"] > 0
    assert p["device_order"] == ["RPC1"]
    launch = build_master_launch(
        RunBody(model="fixture.gguf"),
        p,
        model_path=Path("missing.gguf"),
        rpc_servers=[],
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    assert launch["rpc_servers"] == ["worker:50052"]
    assert launch["extra_args"][-2:] == ["--device", "RPC1"]
    assert launch["ngl"] == 5  # RPC CPU layers still use upstream offload routing.
    assert_accounting(p)


def test_rpc_indices_count_unselected_cpu_devices_between_workers():
    p = plan(
        [node("master", master=True, ram=600), node("first", gpu=900), node("second", gpu=900)],
        required=["first", "second"],
    )
    assert p["fit_status"] != "overflow"
    assert p["cpu_layers"] == 0
    assert p["device_order"] == ["RPC0", "RPC2"]
    assert p["rpc_endpoints"] == ["first:50052", "second:50052"]
    assert_accounting(p)


def test_unified_worker_gpu_and_cpu_share_one_capacity():
    p = plan(
        [node("master", master=True, ram=300), node("worker", gpu=700, ram=1400, unified=True)],
        required=["worker"],
    )
    assert p["fit_status"] != "overflow"
    assert sum(p["node_ram_alloc_bytes"].values()) > 0
    assert p["node_ram_alloc_bytes"]["worker"] + p["node_vram_alloc_bytes"]["worker"] <= 1400 * MIB
    assert p["budget_bytes"] == 1700 * MIB
    exhausted = plan(
        [node("master", master=True, ram=300), node("worker", gpu=700, ram=1000, unified=True)],
        required=["worker"],
    )
    assert exhausted["fit_status"] == "overflow"
    assert_accounting(p)


def test_manual_unified_devices_use_host_pool_and_individual_device_limits():
    master = node("master", master=True, gpu=900, ram=4000, unified=True)
    master["devices"].append(master["devices"][0] | {"index": 1, "device": "Vulkan1"})
    p = plan([master], node_shares={"master": 1.0})
    assert p["fit_status"] != "overflow"
    # Reported 900+900 MiB is replaced by one 65% RAM pool, split across devices.
    from kllm.shared.accel import UNIFIED_VRAM_FRACTION_DEFAULT

    pool = int(master["total_ram_bytes"] * UNIFIED_VRAM_FRACTION_DEFAULT)
    assert p["node_gpu_budget_bytes"]["master"] == (pool // 2) * 2
    assert all(a["estimated_bytes"] <= a["budget_bytes"] for a in p["device_allocations"])
    assert_accounting(p)


def test_old_worker_ram_is_not_advertised_or_used():
    p = plan(
        [node("master", master=True, ram=300), node("old", gpu=100, ram=4000, rpc_cpu=False)],
        required=["old"],
    )
    assert p["fit_status"] == "overflow"
    assert p["node_ram_budget_bytes"]["old"] == 0
    assert "old" in p["hint"]


def test_cpu_mode_pools_workers_and_requires_master_model_layers():
    p = plan(
        [node("master", master=True, ram=600), node("worker", ram=4000)],
        required=["master", "worker"],
        mode="cpu",
    )
    assert p["fit_status"] != "overflow"
    assert p["cpu_layers"] > 0
    assert p["vram_alloc_bytes"] == 0
    assert p["node_workload_bytes"]["master"] > 0
    assert_accounting(p)


def test_unknown_required_node_and_incompatible_settings_are_rejected():
    with pytest.raises(ValueError, match="unavailable.*missing"):
        plan([node("master", master=True)], required=["missing"])
    for kwargs in ({"node_shares": {"master": 1}}, {"split_mode": "row"}, {"split_mode": "tensor"}):
        with pytest.raises(ValueError, match="automatic layer"):
            RunBody(model="fixture", required_nodes=["master"], **kwargs)
    for required in ([" "], ["master", "master"]):
        with pytest.raises(ValueError, match="required_nodes"):
            RunBody(model="fixture", required_nodes=required)
    body = RunBody(model="fixture", required_nodes=["master"])
    assert body.snapshot_for_remember()["required_nodes"] == ["master"]


def candidate(nid, budget):
    return PlacementDeviceCandidate(
        nid, nid, "vulkan", False, budget, 0, f"{nid}:50052", 0, 1, False
    )


def test_output_only_allocation_does_not_satisfy_required_node():
    result = allocate_contiguous(
        [candidate("small", 1), candidate("large", 100)],
        [10, 10, 1],
        cpu_budget=0,
        cpu_base=0,
        overhead=0,
        master_node_id="master",
        required_nodes=frozenset({"small"}),
        allow_cpu_spill=False,
    )
    assert result is None


def test_allocator_keeps_required_coverage_at_same_layer_boundary():
    result = allocate_contiguous(
        [candidate("optional", 10), candidate("required", 10), candidate("last", 20)],
        [10, 10, 10],
        cpu_budget=0,
        cpu_base=0,
        overhead=0,
        master_node_id="master",
        required_nodes=frozenset({"required"}),
        allow_cpu_spill=False,
    )
    assert result is not None
    assert any(a.node_id == "required" for a in result.allocations)


def test_rpc_inventory_follows_selected_gpu_order_and_appends_cpu():
    inventory = worker_rpc_devices(
        [
            GpuDeviceInput(index=2, device="Vulkan2", backend="vulkan"),
            GpuDeviceInput(index=0, device="Vulkan0", backend="vulkan"),
        ],
        "vulkan",
    )
    assert [d["device"] for d in inventory] == ["Vulkan2", "Vulkan0", "CPU"]
    assert [
        d.index
        for d in PlacementNodeInput.from_mapping(
            node("worker") | {"rpc_devices": inventory}
        ).rpc_devices
    ] == [0, 1, 2]
    with pytest.raises(ValueError, match="runtime order"):
        PlacementNodeInput.from_mapping(node("worker") | {"rpc_devices": inventory[::-1]})


def test_priority_overrides_local_shortcut_and_reorders_layer_distribution():
    nodes = [node("master", master=True, gpu=4000), node("worker", gpu=900)]
    remote_first = plan(nodes, priority=["worker", "master"])
    local_first = plan(nodes, priority=["master", "worker"])
    assert remote_first["fit_status"] != "overflow"
    assert remote_first["node_workload_bytes"]["worker"] > 0
    assert (
        remote_first["node_workload_bytes"]["worker"] > local_first["node_workload_bytes"]["worker"]
    )
    assert local_first["node_workload_bytes"]["worker"] == 0
    assert remote_first["cpu_layers"] == local_first["cpu_layers"] == 0
    assert remote_first["node_priority"] == ["worker", "master"]
    assert_accounting(remote_first)


def test_priority_respects_required_nodes_and_falls_back_past_exhausted_node():
    nodes = [node("master", master=True, gpu=4000), node("small", gpu=100), node("worker", gpu=900)]
    p = plan(nodes, priority=["small", "master", "worker"], required=["worker"])
    assert p["fit_status"] != "overflow"
    assert p["node_workload_bytes"]["small"] == 0
    assert p["node_workload_bytes"]["worker"] > 0
    assert p["cpu_layers"] == 0
    assert_accounting(p)


def test_priority_validation_and_remembered_order():
    with pytest.raises(ValueError, match="priority nodes are unavailable"):
        plan([node("master", master=True)], priority=["missing"])
    for order in (["master", "master"], [" "]):
        with pytest.raises(ValueError, match="node_priority"):
            RunBody(model="fixture", node_priority=order)
    for kwargs in ({"node_shares": {"master": 1}}, {"split_mode": "row"}):
        with pytest.raises(ValueError, match="node_priority"):
            RunBody(model="fixture", node_priority=["master"], **kwargs)
    assert RunBody(model="fixture", node_priority=["worker", "master"]).snapshot_for_remember()[
        "node_priority"
    ] == ["worker", "master"]
