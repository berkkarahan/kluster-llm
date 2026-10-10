from __future__ import annotations

from kllm.modules.cluster.presenters.heartbeat_payload import HeartbeatPayloadBuilder
from kllm.shared.state import ClusterState


def test_heartbeat_payload_builder_combines_state_and_runtime_identity():
    state = ClusterState(
        node_id="worker-1",
        role="worker",
        rpc_bind="10.0.0.2",
        wg_public_key="wg-key",
        gpu_ids=[1],
        process_pid=42,
        process_role="worker",
    )
    state.members[state.node_id] = {
        "host": "old-host",
        "lan_host": "192.168.1.2",
        "backend": "cuda",
        "available_ram_bytes": 0,
        "actual_backend": "cuda",
        "devices": [1],
        "unified_memory": None,
    }
    builder = HeartbeatPayloadBuilder(
        lambda: state,
        software_identity=lambda: {"git_head": "abc123", "llama_release": "v1.0"},
        process_started_at=lambda: "2026-01-01T00:00:00Z",
        process_start_token=lambda: "process-token",
    )

    payload = builder.build()

    assert payload["host"] == "10.0.0.2"
    assert payload["rpc_bind"] == "10.0.0.2"
    assert payload["lan_host"] == "10.0.0.2"
    assert payload["total_ram_bytes"] == 0
    assert payload["available_ram_bytes"] == 0
    assert payload["backend"] == "cuda"
    assert payload["actual_backend"] == "cuda"
    assert payload["devices"] == [1]
    assert "unified_memory" not in payload
    assert payload["wg_public_key"] == "wg-key"
    assert payload["git_head"] == "abc123"
    assert payload["llama_release"] == "v1.0"
    assert payload["process_started_at"] == "2026-01-01T00:00:00Z"
    assert payload["process_start_token"] == "process-token"
