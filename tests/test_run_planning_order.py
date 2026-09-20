"""Plan/run ordering and placement settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from kllm.membership import cluster_snapshot
from kllm.run_planning import (
    build_master_launch,
    cluster_nodes,
    ensure_rpc_peers_reachable,
    member_rpc_host,
    ordered_members,
    rpc_server_endpoints,
)
from kllm.run_settings import RunBody, RunSettingsFields


def test_ordered_members_workers_then_master():
    members = {
        "m": {"node_id": "m", "role": "master", "host": "10.0.0.1"},
        "w1": {"node_id": "w1", "role": "worker", "host": "10.0.0.2", "rpc_port": 50052},
        "w2": {"node_id": "w2", "role": "worker", "host": "10.0.0.3", "rpc_port": 50053},
    }
    ordered = ordered_members(members)
    assert [m["node_id"] for m in ordered] == ["w1", "w2", "m"]
    rpc = rpc_server_endpoints(members)
    assert rpc == ["10.0.0.2:50052", "10.0.0.3:50053"]


def test_member_rpc_host_prefers_host_over_lan():
    assert member_rpc_host({"host": "10.8.0.2", "lan_host": "192.168.1.5"}) == "10.8.0.2"
    assert member_rpc_host({"lan_host": "192.168.1.5"}) == "192.168.1.5"
    assert member_rpc_host({}) == "127.0.0.1"


def test_cluster_nodes_carries_latency_and_master_flag():
    members = {
        "m": {
            "node_id": "m",
            "role": "master",
            "total_ram_bytes": 1,
            "latency_ms": 0.0,
        },
        "w": {
            "node_id": "w",
            "role": "worker",
            "total_ram_bytes": 2,
            "latency_ms": 12.5,
        },
    }
    nodes = cluster_nodes(members)
    assert nodes[0]["node_id"] == "w"
    assert nodes[0]["latency_ms"] == 12.5
    assert nodes[1]["is_master"] is True


def test_build_master_launch_rpc_aligns_with_worker_order():
    members = {
        "m": {"node_id": "m", "role": "master", "host": "10.0.0.1"},
        "w1": {"node_id": "w1", "role": "worker", "host": "10.0.0.2", "rpc_port": 50052},
        "w2": {"node_id": "w2", "role": "worker", "host": "10.0.0.3", "rpc_port": 50053},
    }
    body = RunBody(model="toy.gguf")
    plan = {
        "tensor_split_csv": "0.2000,0.3000,0.5000",
        "ngl": 32,
        "context_effective": 4096,
    }
    launch = build_master_launch(
        body,
        plan,
        model_path=Path("/tmp/toy.gguf"),
        rpc_servers=rpc_server_endpoints(members),
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    assert launch["rpc_servers"] == ["10.0.0.2:50052", "10.0.0.3:50053"]
    assert launch["tensor_split"] == "0.2000,0.3000,0.5000"
    assert len(launch["tensor_split"].split(",")) == len(ordered_members(members))
    assert launch["micro_batch"] == 128
    assert isinstance(launch["micro_batch"], int)


def test_ensure_rpc_peers_reachable_fails_closed_port():
    with pytest.raises(HTTPException) as ei:
        ensure_rpc_peers_reachable(["127.0.0.1:1"], timeout_s=0.2)
    assert ei.value.status_code == 503
    assert "unreachable" in str(ei.value.detail).lower()


def test_ensure_rpc_peers_reachable_uses_control_plane(monkeypatch):
    from kllm import http_clients
    import httpx
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"process_pid": 42, "process_role": "worker"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(http_clients, "control_client", lambda: client)
        ensure_rpc_peers_reachable(["127.0.0.1:50052"])
    assert calls == ["http://127.0.0.1:18765/v1/status"]


def test_run_settings_rejects_zero_weights():
    try:
        RunSettingsFields(model="x.gguf", vram_weight=0, latency_weight=0)
        assert False, "expected validation error"
    except ValueError as exc:
        assert "zero" in str(exc).lower()


def test_snapshot_includes_latency_ms():
    snap = cluster_snapshot(
        {"n1": {"node_id": "n1", "host": "h", "role": "worker", "latency_ms": 3.5}}
    )
    assert snap["n1"]["latency_ms"] == 3.5


def test_cli_payload_includes_weights():
    from kllm.cli_payload import build_plan_run_payload

    payload = build_plan_run_payload(model="m.gguf", vram_weight=0.7, latency_weight=0.3)
    assert payload["vram_weight"] == 0.7
    assert payload["latency_weight"] == 0.3


def test_native_knapsack_exposes_placement_fields():
    from kllm import native

    if not native.native_available():
        return
    gb = 1024**3
    plan = native.plan_split(
        nodes=[
            {
                "node_id": "w",
                "total_ram_bytes": 32 * gb,
                "available_ram_bytes": 16 * gb,
                "vram_bytes": 12 * gb,
                "backend": "vulkan",
                "latency_ms": 2.0,
                "is_master": False,
            },
            {
                "node_id": "m",
                "total_ram_bytes": 24 * gb,
                "available_ram_bytes": 12 * gb,
                "vram_bytes": int(24 * gb * 0.8),
                "backend": "metal",
                "unified_memory": True,
                "latency_ms": 0.0,
                "is_master": True,
            },
        ],
        model_size_bytes=4 * gb,
        device="gpu",
    )
    assert "node_alloc_bytes" in plan
    assert "node_scores" in plan
    assert plan["vram_alloc_bytes"] > 0
    assert plan["node_scores"][0] != plan["node_scores"][1] or plan["node_latency_ms"][0] != plan[
        "node_latency_ms"
    ][1]
