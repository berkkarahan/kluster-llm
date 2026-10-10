"""Master membership heal and heterogeneous RPC placement."""

from __future__ import annotations

import kllm.agent.runtime as agent_runtime

from unittest.mock import patch


from kllm.modules.inference.interactors.placement import MIB, plan_devices
from kllm.modules.inference.interactors.placement_models import ModelLayout, PlacementSettings
from kllm.modules.inference.interactors.run_planning import cluster_nodes
from kllm.modules.inference.interactors.run_planning import resolve_launch_settings
from kllm.modules.inference.entities import RunBody


def _layout():
    weight = 100 * MIB
    return dict(
        architecture="llama",
        metadata={"llama.attention.head_count_kv": 1},
        layers=[weight] * 4,
        output_bytes=weight,
        input_bytes=10 * MIB,
        confidence="metadata",
    )


def _settings():
    return resolve_launch_settings(RunBody(model="test.gguf")).to_mapping()


def test_cluster_nodes_synthesizes_unified_vulkan_device():
    members = {
        "w": {
            "node_id": "w",
            "role": "worker",
            "host": "10.0.0.2",
            "rpc_port": 50052,
            "unified_memory": True,
            "vram_bytes": 500 * MIB,
            "requested_backend": "vulkan",
            "backend": "cpu",
            "devices": [],
        },
        "m": {
            "node_id": "m",
            "role": "master",
            "host": "10.0.0.1",
            "rpc_port": 50052,
            "total_ram_bytes": 8 * MIB,
            "available_ram_bytes": 8 * MIB,
            "devices": [
                {
                    "index": 0,
                    "device": "Vulkan0",
                    "backend": "vulkan",
                    "vram_bytes": 600 * MIB,
                    "free_bytes": 600 * MIB,
                }
            ],
        },
    }
    nodes = cluster_nodes(members)
    assert nodes[0].devices[0].device == "Vulkan0"
    assert nodes[1].is_master is True


def test_plan_with_healed_master_and_mixed_workers():
    members = {
        "mac_metal": {
            "node_id": "mac_metal",
            "role": "worker",
            "host": "10.0.0.2",
            "rpc_port": 50052,
            "unified_memory": True,
            "vram_bytes": 510 * MIB,
            "requested_backend": "metal",
            "backend": "cpu",
            "devices": [],
            "latency_ms": 5.0,
        },
        "mac_vk": {
            "node_id": "mac_vk",
            "role": "worker",
            "host": "10.0.0.3",
            "rpc_port": 50052,
            "unified_memory": True,
            "vram_bytes": 510 * MIB,
            "requested_backend": "vulkan",
            "backend": "cpu",
            "devices": [],
            "latency_ms": 6.0,
        },
        "win": {
            "node_id": "win",
            "role": "master",
            "host": "10.0.0.1",
            "rpc_port": 50052,
            "total_ram_bytes": 8 * 1024 * MIB,
            "available_ram_bytes": 8 * 1024 * MIB,
            "devices": [
                {
                    "index": 0,
                    "device": "Vulkan0",
                    "backend": "vulkan",
                    "vram_bytes": 510 * MIB,
                    "free_bytes": 510 * MIB,
                }
            ],
            "latency_ms": 0.0,
        },
    }
    nodes = cluster_nodes(members)
    result = plan_devices(
        tuple(nodes),
        500 * MIB,
        ModelLayout.from_mapping(_layout()),
        PlacementSettings.from_mapping(_settings()),
        context=512,
        headroom=1,
        device="gpu",
        vram_weight=0.6,
        latency_weight=0.4,
    )
    assert result.fit_status != "overflow"
    assert any(n.is_master for n in nodes)


def test_ensure_master_member_repairs_missing_row(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.shared.state as state_mod
    import kllm.agent as agent_mod

    importlib.reload(state_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    agent = agent_mod
    agent.STATE.role = "master"
    agent.STATE.members = {
        "w1": {"node_id": "w1", "role": "worker", "host": "10.0.0.2", "last_seen": 0},
    }
    cap = {
        "total_ram_bytes": 16 * MIB,
        "available_ram_bytes": 8 * MIB,
        "vram_bytes": 6 * MIB,
        "backend": "vulkan",
        "unified_memory": False,
        "requested_backend": "vulkan",
        "binary_version": "test",
    }
    gpus = {
        "backend": "vulkan",
        "gpus": [
            {
                "index": 0,
                "device": "Vulkan0",
                "backend": "vulkan",
                "vram_bytes": 6 * MIB,
            }
        ],
    }
    with (
        patch.object(agent.native, "probe_capacity", return_value=cap),
        patch.object(agent.native, "list_gpus", return_value=gpus),
    ):
        assert agent_runtime._ensure_master_member(persist=False) is True
    assert agent.STATE.node_id in agent.STATE.members
    assert agent.STATE.members[agent.STATE.node_id]["role"] == "master"
    assert "w1" in agent.STATE.members
