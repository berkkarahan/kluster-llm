"""Tests for the coordinated protocol upgrade's transient-state reset."""

import json
from dataclasses import asdict

import pytest

from kllm import runtime, state


def test_legacy_cluster_state_resets_transient_data_and_keeps_settings(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    path = tmp_path / "cluster.json"
    monkeypatch.setattr(state, "state_path", lambda: path)
    legacy = state.ClusterState(
        role="master",
        node_id="node-1",
        join_key="old-key",
        members={"node-1": {"role": "master"}},
        process_pid=123,
        process_role="master",
        last_plan={"node_ids": ["node-1"]},
        model_name="model.gguf",
        last_settings={"model": "model.gguf", "context": 4096},
    )
    data = asdict(legacy)
    data.pop("schema_version")
    path.write_text(json.dumps(data), encoding="utf-8")

    assert state.ClusterState.upgrade_pending() is True
    migrated = state.ClusterState.load()

    assert migrated.role == "idle"
    assert migrated.join_key is None
    assert migrated.members == {}
    assert migrated.process_pid is None
    assert migrated.last_plan is None
    assert migrated.model_name is None
    assert migrated.node_id == "node-1"
    assert migrated.last_settings == {"model": "model.gguf", "context": 4096}
    assert migrated.schema_version == state.CLUSTER_STATE_SCHEMA_VERSION
    assert state.ClusterState.upgrade_pending() is False


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        (b"\0" * 128, "invalid JSON"),
        (b"{broken", "invalid JSON"),
        (b"[]", "JSON object"),
        (b'{"schema_version": 0}', "positive integer"),
        (b"\xff", "valid UTF-8"),
    ],
)
def test_corrupt_cluster_state_fails_closed_without_exposing_contents(
    tmp_path, monkeypatch, contents, reason
) -> None:
    path = tmp_path / "cluster.json"
    path.write_bytes(contents)
    monkeypatch.setattr(state, "state_path", lambda: path)

    for read in (state.ClusterState.load, state.ClusterState.upgrade_pending):
        with pytest.raises(state.ClusterStateLoadError) as raised:
            read()
        message = str(raised.value)
        assert str(path) in message
        assert reason in message
        assert "broken" not in message
        assert path.read_bytes() == contents


def test_stop_children_keeps_agent_pid_and_stops_only_tracked_children(monkeypatch) -> None:
    info = runtime.RuntimeInfo(
        agent_pid=10,
        children=[runtime.ChildProc(pid=20, kind="llama-server")],
    )
    stopped: list[int] = []
    monkeypatch.setattr(runtime, "load", lambda: info)
    monkeypatch.setattr(runtime, "_alive", lambda _pid: True)
    monkeypatch.setattr(runtime, "save", lambda _info: None)
    monkeypatch.setattr(runtime.native, "stop_process", lambda pid, force: stopped.append(pid))

    assert runtime.stop_children() is True
    assert stopped == [20]
    assert info.agent_pid == 10
    assert info.children == []
