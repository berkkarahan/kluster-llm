"""Persisted-state compatibility."""

from __future__ import annotations

import json


def test_legacy_state_without_llama_warnings_loads(tmp_path, monkeypatch):
    """state.json written before llama_warnings existed must still load."""
    from kllm.shared import state

    state_file = tmp_path / "cluster.json"
    state_file.write_text(
        json.dumps({"node_id": "abcd1234", "netsec_warnings": ["old"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(state, "state_path", lambda: state_file)
    monkeypatch.setattr(state, "ensure_home", lambda: tmp_path)

    loaded = state.ClusterState.load()

    assert loaded.node_id == "abcd1234"
    assert loaded.llama_warnings == []
