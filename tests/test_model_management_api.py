"""Tests for the async download job, load-progress, and per-model settings routes."""

from __future__ import annotations

import threading
import uuid
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
    from kllm import models as model_ops
    from kllm import load_progress

    previous_download_state = (model_ops._DOWNLOAD_PROGRESS, model_ops._DOWNLOAD_LAST_TERMINAL, model_ops._DOWNLOAD_CANCEL)
    previous_load_state = (load_progress._active, load_progress._latest_terminal)
    model_ops._DOWNLOAD_PROGRESS = model_ops._DOWNLOAD_LAST_TERMINAL = model_ops._DOWNLOAD_CANCEL = None
    load_progress._active = load_progress._latest_terminal = None
    load_progress._samples.clear()

    importlib.reload(state_mod)
    importlib.reload(auth_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    token = auth_mod.ensure_api_token()
    try:
        yield TestClient(agent_mod.app), agent_mod, token
    finally:
        model_ops._DOWNLOAD_PROGRESS, model_ops._DOWNLOAD_LAST_TERMINAL, model_ops._DOWNLOAD_CANCEL = previous_download_state
        load_progress._active, load_progress._latest_terminal = previous_load_state
        load_progress._samples.clear()


# ---------------------------------------------------------------- downloads

def test_download_progress_idle(client):
    c, _, _ = client
    r = c.get("/v1/models/download-progress")
    assert r.status_code == 200
    assert r.json() == {"state": "idle"}


def test_download_start_and_complete(client, monkeypatch):
    c, agent, _ = client
    finished = threading.Event()

    def fake_runner(source, name):
        from kllm import models as model_ops

        model_ops._DOWNLOAD_LOCK.release()
        model_ops._DOWNLOAD_CANCEL = None
        finished.set()

    monkeypatch.setattr(
        agent,
        "_download_job_runner",
        fake_runner,
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
    assert finished.wait(timeout=5)
    accepted = r.json()
    assert accepted["ok"] is True
    assert accepted["accepted"] is True
    assert accepted["source"] == "https://example.com/toy.gguf"
    uuid.UUID(accepted["operation_id"])
    assert accepted["started_at"] == accepted["updated_at"]
    assert accepted["finished_at"] is None

    # Runner (mocked) finished: terminal snapshot visible to pollers.
    model_ops.set_download_terminal("toy.gguf", 8, "complete")
    r = c.get("/v1/models/download-progress")
    body = r.json()
    assert body["state"] == "complete"
    assert body["phase"] == "complete"
    assert body["pct"] == 100
    assert body["operation_id"] == accepted["operation_id"]
    assert body["finished_at"] is not None

    # Cancel with no active job → 409.
    r = c.post("/v1/models/download/cancel")
    assert r.status_code == 409

    # A retained terminal is observable but does not occupy the admission slot.
    finished.clear()
    r = c.post("/v1/models/download", json={"source": "https://example.com/next.gguf"})
    assert r.status_code == 200
    assert finished.wait(timeout=5)


def test_operation_records_keep_terminal_separate_from_current():
    from kllm import models as model_ops

    previous = model_ops._DOWNLOAD_PROGRESS
    try:
        model_ops._DOWNLOAD_PROGRESS = {
            "operation_id": "00000000-0000-4000-8000-000000000001",
            "phase": "complete",
        }
        records = model_ops.download_progress_records()
        assert records == {
            "current": None,
            "last_terminal": {
                "operation_id": "00000000-0000-4000-8000-000000000001",
                "phase": "complete",
                "state": "complete",
            },
        }
        model_ops._DOWNLOAD_PROGRESS = {
            "operation_id": "00000000-0000-4000-8000-000000000002",
            "phase": "downloading",
        }
        records = model_ops.download_progress_records()
        assert records["current"]["operation_id"].endswith("0002")
        assert records["last_terminal"]["operation_id"].endswith("0001")
    finally:
        model_ops._DOWNLOAD_PROGRESS = previous


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

    def fake_stream(_method, _url, **kwargs):
        assert kwargs["timeout"] == 5.0
        return FakeResp()

    def run():
        with patch("kllm.models.httpx.stream", side_effect=fake_stream):
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
    assert body["operation_id"] is None
    assert body["started_at"] is None
    assert body["updated_at"] is None
    assert body["finished_at"] is None


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
        uuid.UUID(body["operation_id"])
        assert body["started_at"]
        assert body["updated_at"]
        assert body["finished_at"] is None

        with patch.object(load_progress, "_health_ok", return_value=True):
            r = c.get("/v1/load-progress")
        ready = r.json()
        assert ready["phase"] == "ready"
        assert ready["fraction"] == 1.0
        assert ready["finished_at"] is not None
    finally:
        load_progress.end()

    retained = c.get("/v1/load-progress").json()
    assert retained["phase"] == "ready"
    assert retained["operation_id"] == ready["operation_id"]


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


def test_load_records_keep_previous_terminal_during_next_load(monkeypatch):
    from kllm import load_progress

    old_active, old_terminal = load_progress._active, load_progress._latest_terminal
    try:
        load_progress._active = load_progress._latest_terminal = None
        load_progress.begin("first.gguf", 123, 100)
        first_id = load_progress._active["operation_id"]
        with patch.object(load_progress, "_alive", return_value=True), \
             patch.object(load_progress, "_health_ok", return_value=True):
            assert load_progress.records(8080)["last_terminal"]["phase"] == "ready"
        load_progress.end()

        load_progress.begin("second.gguf", 124, 100)
        second_id = load_progress._active["operation_id"]
        with patch.object(load_progress, "_health_ok", return_value=False), \
             patch.object(load_progress, "_alive", return_value=True), \
             patch.object(load_progress, "_rss_bytes", return_value=20):
            records = load_progress.records(8080)
        assert records["current"]["operation_id"] == second_id
        assert records["last_terminal"]["operation_id"] == first_id
        load_progress.end()
        terminal = load_progress.records(8080)["last_terminal"]
        assert terminal["operation_id"] == second_id
        assert terminal["phase"] == "cancelled"
    finally:
        load_progress._active, load_progress._latest_terminal = old_active, old_terminal


def test_late_load_sample_cannot_terminalize_replacement_operation(monkeypatch):
    from kllm import load_progress

    old_active, old_terminal = load_progress._active, load_progress._latest_terminal
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def health(_port):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(3)
            return True
        return False

    try:
        load_progress._active = load_progress._latest_terminal = None
        load_progress.begin("first.gguf", 123, 100)
        result = []
        with patch.object(load_progress, "_alive", return_value=True), \
             patch.object(load_progress, "_health_ok", side_effect=health), \
             patch.object(load_progress, "_rss_bytes", return_value=10):
            worker = threading.Thread(target=lambda: result.append(load_progress.sample(8080)))
            worker.start()
            assert entered.wait(1)
            load_progress.begin("second.gguf", 124, 100)
            second_id = load_progress._active["operation_id"]
            release.set()
            worker.join(timeout=3)
        assert not worker.is_alive()
        assert result[0]["operation_id"] == second_id
        assert result[0]["phase"] != "ready"
        assert load_progress._latest_terminal is None
    finally:
        release.set()
        load_progress.end()
        load_progress._active, load_progress._latest_terminal = old_active, old_terminal


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
