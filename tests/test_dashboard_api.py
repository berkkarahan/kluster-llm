from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.auth as auth_mod
    import kllm.state as state_mod
    import kllm.agent as agent_mod

    importlib.reload(state_mod)
    importlib.reload(auth_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    token = auth_mod.ensure_api_token()
    return TestClient(agent_mod.app), agent_mod, token


def test_list_models_empty(client):
    c, _, _ = client
    r = c.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_list_models_with_file(client, tmp_path, monkeypatch):
    c, agent, _ = client
    import os

    models = Path(os.environ["KLLM_HOME"]) / "models"
    models.mkdir(parents=True, exist_ok=True)
    gguf = models / "toy.gguf"
    gguf.write_bytes(b"gguf")
    r = c.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert "toy.gguf" in ids


def test_chat_requires_token(client):
    c, agent, _ = client
    agent.STATE.process_pid = 1
    agent.STATE.process_role = "master"
    r = c.post("/v1/chat/completions", json={"model": "x", "messages": []})
    assert r.status_code == 401


def test_chat_503_when_inference_down(client):
    c, agent, token = client
    # Avoid flaking when a real llama-server is bound to the default infer port.
    agent.STATE.infer_port = 39999
    r = c.post(
        "/v1/chat/completions",
        json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503


def test_api_token_endpoint(client):
    c, _, token = client
    r = c.get("/v1/api-token")
    assert r.status_code == 200
    assert r.json()["api_token"] == token


def test_anthropic_to_openai_mapping():
    from kllm.gateway import _anthropic_to_openai

    oai = _anthropic_to_openai(
        {
            "model": "m",
            "system": "sys",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 32,
            "tool_choice": {"type": "auto"},
        }
    )
    assert oai["messages"][0]["role"] == "system"
    assert oai["tool_choice"] == "auto"


def test_connect_plan_claude_no_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    from kllm.connect import plan_claude

    monkeypatch.setenv("PATH", str(tmp_path))
    claude = tmp_path / ("claude.exe" if __import__("sys").platform == "win32" else "claude")
    claude.write_text("", encoding="utf-8")
    if not claude.name.endswith(".exe"):
        claude.chmod(0o755)

    with patch("kllm.connect._which", return_value=str(claude)):
        plan = plan_claude("http://127.0.0.1:18765", "sk-test", "model.gguf", False, [])
    assert plan.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:18765"
    assert plan.env["ANTHROPIC_MODEL"] == "model.gguf"


def test_connect_plan_opencode_config():
    from kllm.connect import plan_opencode

    with patch("kllm.connect._which", return_value="/usr/bin/opencode"):
        plan = plan_opencode("http://127.0.0.1:18765", "sk-test", "m.gguf", True, ["run"])
    cfg = json.loads(plan.env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["model"].startswith("kllm/")
    assert "permission" in cfg


def test_config_get_put_and_status_device(client):
    c, _, _ = client
    r = c.get("/v1/config")
    assert r.status_code == 200
    assert r.json()["device"] == "gpu"

    r = c.put("/v1/config", json={"device": "cpu"})
    assert r.status_code == 200
    assert r.json()["device"] == "cpu"

    r = c.get("/v1/status")
    assert r.status_code == 200
    assert r.json()["device"] == "cpu"

    r = c.put("/v1/config", json={"device": "tpu"})
    assert r.status_code == 422
