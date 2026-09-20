"""Tests for the async download job, load-progress, and per-model settings routes."""

from __future__ import annotations

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


# ---------------------------------------------------------------- downloads

def test_download_progress_idle(client):
    c, _, _ = client
    r = c.get("/v1/models/download-progress")
    assert r.status_code == 200
    assert r.json() == {"state": "idle"}


def test_download_start_and_complete(client, monkeypatch):
    c, agent, _ = client
    monkeypatch.setattr(
        agent,
        "_download_job_runner",
        lambda source, name: None,
    )
    # Simulate a live job mid-transfer.
    from kllm import models as model_ops

    model_ops._DOWNLOAD_PROGRESS = {
        "filename": "toy.gguf",
        "bytes": 4,
        "total": 8,
        "pct": 50,
        "phase": "downloading",
        "rate_bps": None,
        "eta_seconds": None,
    }
    r = c.post("/v1/models/download", json={"source": "https://example.com/x.gguf"})
    assert r.status_code == 409

    model_ops._DOWNLOAD_PROGRESS = None
    r = c.post("/v1/models/download", json={"source": "https://example.com/toy.gguf"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "accepted": True, "source": "https://example.com/toy.gguf"}

    # Runner (mocked) finished: terminal snapshot visible to pollers.
    model_ops.set_download_terminal("toy.gguf", 8, "complete")
    r = c.get("/v1/models/download-progress")
    body = r.json()
    assert body["state"] == "complete"
    assert body["phase"] == "complete"
    assert body["pct"] == 100

    # Cancel with no active job → 409.
    model_ops._DOWNLOAD_PROGRESS = None
    r = c.post("/v1/models/download/cancel")
    assert r.status_code == 409


def test_download_bad_ref_fails_fast(client, monkeypatch):
    c, agent, _ = client
    with patch("kllm.models.resolve_hf_filename", side_effect=ValueError("no such quant")):
        r = c.post("/v1/models/download", json={"source": "org/repo:Q9_Z"})
    assert r.status_code == 400
    assert "no such quant" in r.json()["detail"]


def test_download_cancel_mid_transfer(client, monkeypatch):
    c, agent, _ = client
    started = threading.Event()
    release = threading.Event()

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
        with patch("kllm.models.httpx.stream", return_value=FakeResp()):
            from kllm import models as model_ops

            try:
                model_ops.download_model("https://example.com/model.gguf")
            except model_ops.DownloadCancelledError:
                pass

    t = threading.Thread(target=run)
    t.start()
    try:
        assert started.wait(timeout=5)
        r = c.get("/v1/models/download-progress")
        body = r.json()
        assert body["state"] == "running"
        assert body["filename"] == "model.gguf"

        r = c.post("/v1/models/download/cancel")
        assert r.status_code == 200
        release.set()
        t.join(timeout=5)

        # Terminal cancelled snapshot stays visible after the loop unwinds.
        r = c.get("/v1/models/download-progress")
        body = r.json()
        assert body["state"] == "cancelled"
        assert body["phase"] == "cancelled"
    finally:
        release.set()
        t.join(timeout=5)


def test_download_error_snapshot(client, monkeypatch):
    c, agent, _ = client
    with patch("kllm.models.httpx.stream", side_effect=RuntimeError("boom")):
        from kllm import models as model_ops

        with pytest.raises(RuntimeError):
            model_ops.download_model("https://example.com/model.gguf")
        # Runner path (same failing transport): publish the terminal error snapshot.
        agent._download_job_runner("https://example.com/model.gguf", None)
    r = c.get("/v1/models/download-progress")
    body = r.json()
    assert body["state"] == "error"
    assert body["phase"] == "error"
    assert "boom" in (body.get("error") or "")


# ------------------------------------------------------------- load progress

def test_load_progress_idle(client):
    c, _, _ = client
    r = c.get("/v1/load-progress")
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] is None
    assert body["model"] is None


def test_load_progress_active(client, monkeypatch):
    c, agent, _ = client
    import os

    from kllm import load_progress

    pid = os.getpid()  # a live pid for the liveness probe
    load_progress.begin("toy.gguf", pid, 1024)
    try:
        with patch.object(load_progress, "_health_ok", return_value=False), \
             patch.object(load_progress, "_rss_bytes", return_value=512):
            r = c.get("/v1/load-progress")
        body = r.json()
        assert body["phase"] == "loading"
        assert body["model"] == "toy.gguf"
        assert body["bytes_loaded"] == 512
        assert body["fraction"] == pytest.approx(0.5)

        with patch.object(load_progress, "_health_ok", return_value=True):
            r = c.get("/v1/load-progress")
        assert r.json()["phase"] == "ready"
        assert r.json()["fraction"] == 1.0
    finally:
        load_progress.end()


def test_load_progress_dead_process(client, monkeypatch):
    c, agent, _ = client
    from kllm import load_progress

    load_progress.begin("toy.gguf", 999_999, 1024)
    try:
        with patch.object(load_progress, "_alive", return_value=False), \
             patch.object(load_progress, "_health_ok", return_value=False):
            r = c.get("/v1/load-progress")
        body = r.json()
        assert body["phase"] == "error"
        assert "exited" in body["error"]
    finally:
        load_progress.end()


def test_load_progress_rate_eta(client, monkeypatch):
    c, _, _ = client
    import os
    import time

    from kllm import load_progress

    pid = os.getpid()
    load_progress.begin("toy.gguf", pid, 10_000)
    try:
        with patch.object(load_progress, "_health_ok", return_value=False), \
             patch.object(load_progress, "_rss_bytes", side_effect=[1000, 3000]):
            c.get("/v1/load-progress")
            time.sleep(0.05)
            body = c.get("/v1/load-progress").json()
        assert body["phase"] == "loading"
        assert body["rate_bps"] is not None and body["rate_bps"] > 0
        assert body["eta_seconds"] is not None and body["eta_seconds"] > 0
    finally:
        load_progress.end()


# ----------------------------------------------------------- model settings

def test_model_settings_put_and_get(client, tmp_path):
    c, agent, _ = client
    params = {
        "context": 8192,
        "context_auto": False,
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
        "speculative": "auto",
        "temperature": 0.7,
    }
    r = c.put("/v1/model-settings/toy.gguf", json={"parameter_set": params})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "toy.gguf"
    assert body["parameter_set"]["context"] == 8192
    assert body["updated_at"]

    r = c.get("/v1/model-settings/toy.gguf")
    assert r.status_code == 200
    assert r.json()["parameter_set"]["temperature"] == 0.7


def test_model_settings_put_invalid(client):
    c, _, _ = client
    r = c.put(
        "/v1/model-settings/toy.gguf",
        json={"parameter_set": {"context": 4096, "temperature": 5.0}},
    )
    assert r.status_code == 422


def test_model_settings_get_defaults(client):
    c, _, _ = client
    r = c.get("/v1/model-settings/unknown.gguf")
    assert r.status_code == 200
    body = r.json()
    assert body["updated_at"] is None
    assert body["parameter_set"]["context"] == 4096
