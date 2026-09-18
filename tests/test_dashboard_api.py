from __future__ import annotations

import json
import os
import stat
import threading
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


def test_list_models_includes_max_context(client):
    c, _, _ = client
    import os
    import struct

    def write_string(buf: bytearray, s: str) -> None:
        b = s.encode("utf-8")
        buf += struct.pack("<Q", len(b))
        buf += b

    meta = bytearray()
    write_string(meta, "general.architecture")
    meta += struct.pack("<I", 8)
    write_string(meta, "qwen2")
    write_string(meta, "qwen2.context_length")
    meta += struct.pack("<I", 4)
    meta += struct.pack("<I", 32768)
    payload = struct.pack("<4sIQQ", b"GGUF", 3, 0, 2) + bytes(meta)

    models = Path(os.environ["KLLM_HOME"]) / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "ctx-model.gguf").write_bytes(payload)
    r = c.get("/v1/models")
    assert r.status_code == 200
    entry = next(m for m in r.json()["data"] if m["id"] == "ctx-model.gguf")
    assert entry["max_context"] == 32768


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
    body = r.json()
    assert body["device"] == "gpu"
    assert body["vram_percent"] == 80
    assert body["ram_percent"] == 80
    assert body.get("gpu_ids") is None

    r = c.put("/v1/config", json={"device": "cpu", "ram_percent": 55})
    assert r.status_code == 200
    assert r.json()["device"] == "cpu"
    assert r.json()["ram_percent"] == 55

    r = c.get("/v1/status")
    assert r.status_code == 200
    assert r.json()["device"] == "cpu"
    assert r.json()["ram_percent"] == 55
    assert "apply_error" in r.json()
    assert "apply_warning" in r.json()
    assert "download" in r.json()
    assert r.json()["download"] is None
    assert isinstance(r.json()["members"], dict)

    r = c.put(
        "/v1/config",
        json={"device": "gpu", "vram_percent": 70, "gpu_ids": [0, 1]},
    )
    assert r.status_code == 200
    assert r.json()["vram_percent"] == 70
    assert r.json()["gpu_ids"] == [0, 1]

    r = c.put("/v1/config", json={"device": "tpu"})
    assert r.status_code == 422


def test_delete_model_local(client):
    c, _, _ = client
    import os

    models = Path(os.environ["KLLM_HOME"]) / "models"
    models.mkdir(parents=True, exist_ok=True)
    gguf = models / "toy-del.gguf"
    gguf.write_bytes(b"gguf")

    r = c.post("/v1/models/delete", json={"name": "toy-del.gguf"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["name"] == "toy-del.gguf"
    assert not gguf.exists()

    r = c.get("/v1/models")
    assert "toy-del.gguf" not in [m["id"] for m in r.json()["data"]]


def test_delete_model_loaded_returns_409(client):
    c, agent, _ = client
    import os

    models = Path(os.environ["KLLM_HOME"]) / "models"
    models.mkdir(parents=True, exist_ok=True)
    gguf = models / "loaded.gguf"
    gguf.write_bytes(b"gguf")

    agent.STATE.model_name = "loaded.gguf"
    agent.STATE.process_pid = 1
    agent.STATE.process_role = "master"

    r = c.post("/v1/models/delete", json={"name": "loaded.gguf"})
    assert r.status_code == 409
    assert gguf.exists()


def test_delete_model_file_worker_auth(client):
    c, agent, _ = client
    import os

    models = Path(os.environ["KLLM_HOME"]) / "models"
    models.mkdir(parents=True, exist_ok=True)
    gguf = models / "worker-toy.gguf"
    gguf.write_bytes(b"gguf")
    agent.STATE.join_key = "secret-key"

    r = c.delete("/v1/models/file", headers={"X-Kllm-Filename": "worker-toy.gguf"})
    assert r.status_code == 401
    assert gguf.exists()

    r = c.delete(
        "/v1/models/file",
        headers={"X-Kllm-Key": "wrong", "X-Kllm-Filename": "worker-toy.gguf"},
    )
    assert r.status_code == 401
    assert gguf.exists()

    r = c.delete(
        "/v1/models/file",
        headers={"X-Kllm-Key": "secret-key", "X-Kllm-Filename": "worker-toy.gguf"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert not gguf.exists()


def test_delete_model_master_fanout(client):
    c, agent, _ = client
    import os

    models = Path(os.environ["KLLM_HOME"]) / "models"
    models.mkdir(parents=True, exist_ok=True)
    gguf = models / "fanout.gguf"
    gguf.write_bytes(b"gguf")

    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    agent.STATE.members = {
        "w1": {
            "node_id": "w1",
            "role": "worker",
            "lan_host": "127.0.0.1",
            "control_port": 18765,
        }
    }

    with patch("kllm.models.delete_model_on_worker", return_value={"ok": True}) as mocked:
        r = c.post("/v1/models/delete", json={"name": "fanout.gguf"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert not gguf.exists()
    assert len(body["results"]) == 1
    assert body["results"][0]["ok"] is True
    mocked.assert_called_once()


def _clear_hf_env(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)


def test_hf_token_get_never_returns_secret(client, monkeypatch):
    _clear_hf_env(monkeypatch)
    c, _, _ = client
    secret = "hf_super_secret_token"
    r = c.put("/v1/hf-token", json={"token": secret})
    assert r.status_code == 200
    put_body = r.json()
    assert put_body == {"configured": True, "source": "file"}
    assert secret not in r.text
    assert "token" not in put_body

    r = c.get("/v1/hf-token")
    assert r.status_code == 200
    body = r.json()
    assert body == {"configured": True, "source": "file"}
    assert set(body) == {"configured", "source"}
    assert secret not in r.text
    assert secret not in json.dumps(body)


def test_hf_token_put_save_and_clear(client, monkeypatch):
    _clear_hf_env(monkeypatch)
    c, _, _ = client
    r = c.get("/v1/hf-token")
    assert r.json() == {"configured": False, "source": None}

    r = c.put("/v1/hf-token", json={"token": "hf_saved"})
    assert r.status_code == 200
    assert r.json() == {"configured": True, "source": "file"}
    path = Path(os.environ["KLLM_HOME"]) / "hf_token"
    assert path.read_text(encoding="utf-8").strip() == "hf_saved"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    r = c.put("/v1/hf-token", json={"token": ""})
    assert r.status_code == 200
    assert r.json() == {"configured": False, "source": None}
    assert not path.exists()


def test_status_download_during_mocked_download(client, monkeypatch):
    _clear_hf_env(monkeypatch)
    c, _, _ = client
    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    class FakeResp:
        headers = {"Content-Length": "8"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size=8192):
            started.set()
            assert release.wait(timeout=5)
            yield b"abcdefgh"

    def run():
        try:
            with patch("kllm.models.httpx.stream", return_value=FakeResp()):
                from kllm import models as model_ops

                model_ops.download_model("https://example.com/model.gguf")
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    try:
        assert started.wait(timeout=5)
        r = c.get("/v1/status")
        assert r.status_code == 200
        dl = r.json()["download"]
        assert dl is not None
        assert dl["filename"] == "model.gguf"
        assert dl["total"] == 8
        assert dl["bytes"] >= 0
        assert "pct" in dl
    finally:
        release.set()
        t.join(timeout=5)

    assert errors == []
    r = c.get("/v1/status")
    assert r.status_code == 200
    assert r.json()["download"] is None


ORCA_REPO = "orcarouter/Qwen3.8-27B-Uncensored-GGUF"


class _SearchSibling:
    def __init__(self, rfilename: str, size: int):
        self.rfilename = rfilename
        self.size = size


class _SearchInfo:
    gated = False

    def __init__(self, siblings: list[_SearchSibling]):
        self.siblings = siblings


def _patch_hub_search(monkeypatch):
    def fake_list_models(**_kwargs):
        return [
            type("M", (), {"id": "someone/other-gguf"})(),
            type("M", (), {"id": ORCA_REPO})(),
        ]

    def fake_model_info(repo_id, **_kwargs):
        if repo_id == ORCA_REPO:
            return _SearchInfo(
                [
                    _SearchSibling("Qwen3.8-27B-Uncensored-Q4_K_M.gguf", 16_800_000_000),
                    _SearchSibling("Qwen3.8-27B-Uncensored-Q8_0.gguf", 30_000_000_000),
                    _SearchSibling("mmproj-Q4_K_M.gguf", 1000),
                ]
            )
        raise OSError("skip")

    monkeypatch.setattr("kllm.models.list_models", fake_list_models)
    monkeypatch.setattr("kllm.models.model_info", fake_model_info)


def test_models_search_empty_query_400(client):
    c, _, _ = client
    r = c.post("/v1/models/search", json={"query": "  "})
    assert r.status_code == 400
    assert "query" in r.json()["detail"]


def test_models_search_hub_error_400(client, monkeypatch):
    c, _, _ = client

    def boom(**_kwargs):
        raise RuntimeError("hub down")

    monkeypatch.setattr("kllm.models.list_models", boom)
    r = c.post("/v1/models/search", json={"query": "Qwen3.8-27B-Uncensored-GGUF"})
    assert r.status_code == 400
    assert "hub down" in r.json()["detail"]


def test_models_search_qwen_quant_fit(client, monkeypatch):
    c, agent, _ = client
    agent.STATE.role = "master"
    agent.STATE.members = {
        "n1": {"vram_bytes": 24_000_000_000},
        "n2": {"vram_bytes": 24_000_000_000},
    }
    _patch_hub_search(monkeypatch)
    r = c.post(
        "/v1/models/search",
        json={"query": "Qwen3.8-27B-Uncensored-GGUF:Q4_K_M"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "Qwen3.8-27B-Uncensored-GGUF:Q4_K_M"
    assert body["total_vram_bytes"] == 48_000_000_000
    assert len(body["results"]) == 1
    row = body["results"][0]
    assert row["repo_id"] == ORCA_REPO
    assert row["filename"] == "Qwen3.8-27B-Uncensored-Q4_K_M.gguf"
    assert row["quant"] == "Q4_K_M"
    assert row["size_bytes"] == 16_800_000_000
    assert row["source"] == f"{ORCA_REPO}:Qwen3.8-27B-Uncensored-Q4_K_M.gguf"
    assert row["fit"] == "fits"
    assert row["gated"] is False
