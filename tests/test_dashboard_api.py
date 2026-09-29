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
    with TestClient(agent_mod.app) as client:
        yield client, agent_mod, token


def test_list_models_empty(client):
    c, _, _ = client
    r = c.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_usage_ingest_private_identity_and_durable_ack(client):
    from datetime import datetime, timedelta, timezone
    from kllm import usage

    c, agent, _ = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = usage.utc(now - timedelta(minutes=1))
    agent.STATE.members["w1"] = {
        "node_id": "w1", "role": "worker", "host": "worker", "last_seen": now.timestamp(),
        "process_pid": 12, "process_role": "worker", "process_started_at": started,
    }
    point = {
        "lifetime_id": f"w1:12:{started}", "pid": 12, "process_started_at": started,
        "process_role": "worker", "observed_at": usage.utc(now),
        "process": {"cpu_percent_machine": 12.5, "rss_bytes": 2 * 1024**3},
        "host": {"cpu_percent": 20, "used_bytes": 4 * 1024**3, "total_bytes": 8 * 1024**3},
        "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"},
    }
    body = {"node_id": "w1", "readings": [point]}
    assert c.post("/v1/usage/ingest", json=body).status_code == 401
    headers = {"X-Kllm-Key": "secret-key"}
    assert c.post("/v1/usage/ingest", json=body, headers=headers).json()["accepted"] == 1
    assert c.post("/v1/usage/ingest", json=body, headers=headers).json()["duplicates"] == 1
    assert agent.STATE.members["w1"]["last_seen"] == now.timestamp()
    bad = {**body, "readings": [{**point, "pid": 13}]}
    assert c.post("/v1/usage/ingest", json=bad, headers=headers).status_code == 400
    assert c.post("/v1/usage/ingest", json={**body, "extra": "secret"}, headers=headers).status_code in (400, 422)
    assert c.post("/v1/usage/ingest", json={**body, "readings": [point] * 9}, headers=headers).status_code == 400
    assert c.post("/v1/usage/ingest", content=iter((b" " * 32768, b" " * 32769)),
                  headers={**headers, "Content-Type": "application/json"}).status_code == 400
    assert c.post("/v1/usage/ingest", json={"node_id": "unknown", "readings": [point]}, headers=headers).status_code == 400
    assert agent.app.state.usage_store.count() == 1

    second = {**point, "observed_at": usage.utc(now + timedelta(seconds=2))}
    third = {**point, "observed_at": usage.utc(now + timedelta(seconds=4))}
    store = agent.app.state.usage_store
    previous_save = store.last_successful_save
    write = store._write

    def fail_second(*args, **kwargs):
        if args[2]["observed_at"] == third["observed_at"]:
            raise sqlite3.OperationalError("disk full")
        return write(*args, **kwargs)

    import sqlite3
    from unittest.mock import patch as mock_patch

    with mock_patch.object(store, "_write", fail_second):
        failed = c.post("/v1/usage/ingest", json={**body, "readings": [second, third]}, headers=headers)
    assert failed.status_code == 503
    assert store.count() == 1
    assert store.last_successful_save == previous_save
    saved = c.post("/v1/usage/ingest", json={**body, "readings": [point, second]}, headers=headers).json()
    assert (saved["accepted"], saved["duplicates"], store.count()) == (1, 1, 2)


def test_usage_live_stale_departed_and_worker_proxy(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from kllm import usage

    c, agent, _ = client
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = usage.utc(now - timedelta(minutes=1))
    agent.STATE.role = "master"
    agent.STATE.node_id = "master"
    agent.STATE.members = {
        "master": {"node_id": "master", "role": "master", "host": "same"},
        "w1": {"node_id": "w1", "role": "worker", "host": "same", "backend": "cpu",
               "process_pid": 12, "process_started_at": started, "last_seen": now.timestamp(),
               "total_ram_bytes": 8 * 1024**3, "devices": [{"index": 0, "name": "GPU", "vram_bytes": 4 * 1024**3}]},
    }
    point = {
        "lifetime_id": f"w1:12:{started}", "pid": 12, "process_started_at": started,
        "process_role": "worker", "observed_at": usage.utc(now - timedelta(seconds=11)),
        "process": {"cpu_percent_machine": 12.5, "rss_bytes": 2 * 1024**3},
        "host": {"cpu_percent": 20, "used_bytes": 4 * 1024**3, "total_bytes": 8 * 1024**3},
        "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"},
    }
    agent.app.state.usage_store.record("w1", "same", point, now=now,
                                       context={"backend": "cpu", "total_ram_bytes": 8 * 1024**3,
                                                "devices": [{"index": 0, "name": "GPU", "vram_bytes": 4 * 1024**3}],
                                                "assignment": "model.gguf"})
    body = c.get("/v1/usage/live").json()
    assert set(body["nodes"]) == {"master", "w1"}
    assert body["nodes"]["w1"]["freshness"] == "stale"
    assert body["nodes"]["w1"]["reading"]["process"]["rss_bytes"] == 2 * 1024**3
    assert body["relationships"] == [{"from_node_id": "master", "to_node_id": "w1", "kind": "master-worker"}]
    agent.STATE.members.pop("w1")
    departed = c.get("/v1/usage/live").json()["nodes"]["w1"]
    assert departed["connected"] is False
    assert departed["backend"] == "cpu" and departed["total_ram_bytes"] == 8 * 1024**3
    assert departed["devices"][0]["name"] == "GPU"
    assert departed["assignment"] == "model.gguf"
    agent.STATE.members["w1"] = {"node_id": "w1", "role": "worker", "host": "same", "last_seen": now.timestamp()}
    assert list(c.get("/v1/usage/live").json()["nodes"]).count("w1") == 1
    agent.STATE.members.pop("w1")
    agent.STATE.role = "worker"
    agent.STATE.master_host = "10.0.0.1"
    agent.STATE.join_key = "secret-key"
    requests = []

    class FakeControl:
        def get(self, path, **kwargs):
            requests.append((path, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: body)

    from types import SimpleNamespace
    monkeypatch.setattr(agent.http_clients, "control_client", lambda: FakeControl())
    assert c.get("/v1/usage/live").json() == body
    assert requests[0][1]["headers"] == {"X-Kllm-Key": "secret-key"}


def test_usage_live_never_attributes_old_reading_to_stopped_or_new_child(client):
    from datetime import datetime, timedelta, timezone
    from kllm import usage

    c, agent, _ = client
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = usage.utc(now - timedelta(minutes=1))
    agent.STATE.role = "master"
    agent.STATE.node_id = "master"
    agent.STATE.members = {
        "master": {"node_id": "master", "role": "master", "host": "same"},
        "w1": {"node_id": "w1", "role": "worker", "host": "same", "process_pid": 12,
               "process_started_at": started, "last_seen": now.timestamp()},
    }
    point = {"lifetime_id": f"w1:12:{started}", "pid": 12, "process_started_at": started,
             "process_role": "worker", "observed_at": usage.utc(now),
             "process": {"cpu_percent_machine": 10, "rss_bytes": 1024},
             "host": {"cpu_percent": 5, "used_bytes": 1024, "total_bytes": 4096},
             "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"}}
    agent.app.state.usage_store.record("w1", "same", point, now=now)
    assert c.get("/v1/usage/live").json()["nodes"]["w1"]["reading"]["pid"] == 12
    for pid in (None, 13):
        agent.STATE.members["w1"]["process_pid"] = pid
        result = c.get("/v1/usage/live").json()["nodes"]["w1"]
        assert result["reading"] is None
        assert result["freshness"] == "unavailable"
        assert c.get("/v1/usage/lifetimes", params={"node_id": "w1"}).json()["lifetimes"]


def test_stopped_master_still_exposes_retained_history(client):
    from datetime import datetime, timedelta, timezone
    from kllm import usage

    c, agent, _ = client
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = usage.utc(now - timedelta(minutes=1))
    node = agent.STATE.node_id
    point = {"lifetime_id": f"{node}:12:{started}", "pid": 12, "process_started_at": started,
             "process_role": "master", "observed_at": usage.utc(now),
             "process": {"cpu_percent_machine": 0, "rss_bytes": 1024},
             "host": {"cpu_percent": 0, "used_bytes": 1024, "total_bytes": 4096},
             "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"}}
    agent.app.state.usage_store.record(node, "local", point, now=now)
    agent.STATE.members.clear()
    agent.STATE.role = "idle"
    live = c.get("/v1/usage/live").json()
    assert live["relationships"] == []
    assert live["nodes"][node]["connected"] is False
    assert live["nodes"][node]["freshness"] == "stale"
    assert live["recording"]["recording"] == "interrupted"
    assert c.get("/v1/usage/lifetimes", params={"node_id": node}).json()["lifetimes"][0]["pid"] == 12


def test_live_usage_sweeps_expired_members_without_status_poll(client, monkeypatch):
    import time

    c, agent, _ = client
    agent.STATE.role = "master"
    agent.STATE.members["late"] = {"node_id": "late", "role": "worker", "host": "worker",
                                   "last_seen": time.time() - 30}
    monkeypatch.setattr(agent.membership_mod, "on_membership_shrink", lambda *_: None)
    result = c.get("/v1/usage/live").json()
    assert "late" not in agent.STATE.members
    assert "late" not in result["nodes"]  # No retained observations for this node.
    assert all(link["to_node_id"] != "late" for link in result["relationships"])


def test_usage_collector_runs_without_dashboard(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from kllm import usage

    _, agent, _ = client
    started = (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()
    process = SimpleNamespace(create_time=lambda: started, cpu_percent=lambda interval=None: 80,
                              memory_info=lambda: SimpleNamespace(rss=2048))
    monkeypatch.setattr(usage.psutil, "Process", lambda pid: process)
    monkeypatch.setattr(usage.psutil, "cpu_count", lambda logical: 8)
    monkeypatch.setattr(usage.psutil, "cpu_percent", lambda interval=None: 10)
    monkeypatch.setattr(usage.psutil, "virtual_memory", lambda: SimpleNamespace(used=2048, total=4096))
    agent.STATE.role = "master"
    agent.STATE.process_pid = 42
    agent.STATE.process_role = "master"
    agent.STATE.members[agent.STATE.node_id] = {"node_id": agent.STATE.node_id, "host": "master", "role": "master"}
    ticks = iter((False, False, True))
    monkeypatch.setattr(agent._background_stop, "wait", lambda interval: next(ticks))
    agent._usage_loop()
    store = agent.app.state.usage_store
    assert store.count() == 2
    assert store.latest(agent.STATE.node_id)["process"]["cpu_percent_machine"] == 10


def test_worker_ingest_failure_is_visible_without_stopping_inference(client, monkeypatch):
    from types import SimpleNamespace
    from kllm import usage

    c, agent, _ = client
    agent.STATE.role = "worker"
    agent.STATE.master_host = "127.0.0.1"
    agent.STATE.join_key = "private-key"
    agent.STATE.process_pid = 99
    agent.STATE.process_role = "worker"
    monkeypatch.setattr(usage.Sampler, "sample", lambda *_: {"pid": 99})
    monkeypatch.setattr(agent.http_clients, "control_client", lambda: SimpleNamespace(
        post=lambda *a, **k: SimpleNamespace(status_code=503)))
    ticks = iter((False, True))
    monkeypatch.setattr(agent._background_stop, "wait", lambda _: next(ticks))
    agent._usage_loop()
    monkeypatch.setattr(agent, "_proxy_master_usage", lambda *a, **k: {
        "nodes": {agent.STATE.node_id: {"node_id": agent.STATE.node_id, "reading": None, "connected": True}},
        "recording": {"recording": "recording", "last_successful_save": "2026-09-28T12:00:00Z"}})
    response = c.get("/v1/usage/live").json()
    assert response["recording"]["recording"] == "interrupted"
    assert response["recording"]["last_successful_save"] == "2026-09-28T12:00:00Z"
    assert "ingest" in response["nodes"][agent.STATE.node_id]["error"]
    assert "private-key" not in str(response)
    assert agent.STATE.process_pid == 99
    from fastapi import HTTPException

    monkeypatch.setattr(agent, "_proxy_master_usage", lambda *a, **k: (_ for _ in ()).throw(
        HTTPException(503, "master usage history unavailable")))
    missing = c.get("/v1/usage/live")
    assert missing.status_code == 503
    assert missing.json()["detail"]["recording"] == "interrupted"
    assert missing.json()["detail"]["last_successful_save"] == "2026-09-28T12:00:00Z"
    assert "ingest" in missing.json()["detail"]["error"]


def test_worker_to_master_store_to_worker_history_boundary(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from kllm import usage
    from kllm.state import ClusterState

    c, agent, _ = client
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = usage.utc(now - timedelta(minutes=1))
    master = ClusterState(node_id="master", role="master", join_key="secret")
    master.members["w1"] = {"node_id": "w1", "host": "worker", "role": "worker",
                            "process_pid": 99, "process_started_at": started, "process_role": "worker"}
    worker = ClusterState(node_id="w1", role="worker", master_host="master", join_key="secret",
                          process_pid=99, process_role="worker")
    point = {"lifetime_id": f"w1:99:{started}", "pid": 99, "process_started_at": started,
             "process_role": "worker", "observed_at": usage.utc(now),
             "process": {"cpu_percent_machine": None, "rss_bytes": 2048},
             "host": {"cpu_percent": 0, "used_bytes": 2048, "total_bytes": 4096}, "devices": [],
             "availability": {"process_cpu": "initializing", "process_rss": "measured"}}

    class PrivateControl:
        def post(self, url, **kwargs):
            assert kwargs["headers"] == {"X-Kllm-Key": "secret"}
            agent.STATE = master
            try:
                return c.post("/v1/usage/ingest", json=kwargs["json"], headers=kwargs["headers"])
            finally:
                agent.STATE = worker

        def get(self, url, **kwargs):
            assert kwargs["headers"] == {"X-Kllm-Key": "secret"}
            agent.STATE = master
            try:
                return c.get(url[url.index("/v1/"):], params=kwargs.get("params"))
            finally:
                agent.STATE = worker

    monkeypatch.setattr(agent.http_clients, "control_client", lambda: PrivateControl())
    monkeypatch.setattr(agent, "_master_control_base", lambda: "http://master:18765")
    monkeypatch.setattr(usage.Sampler, "sample", lambda *_: point)
    ticks = iter((False, True))
    monkeypatch.setattr(agent._background_stop, "wait", lambda _: next(ticks))
    agent.STATE = worker
    agent._usage_loop()
    assert agent.app.state.usage_store.count() == 1
    response = c.get("/v1/usage/readings", params={"node_id": "w1", "lifetime_id": point["lifetime_id"]})
    assert response.status_code == 200
    assert response.json()["readings"][0]["process"]["rss_bytes"] == 2048
    assert "secret" not in str(response.json())
    assert c.get("/v1/usage/live").json()["nodes"]["w1"]["reading"]["pid"] == 99


def test_failed_liveness_history_save_keeps_last_successful_save(client, monkeypatch):
    import sqlite3
    import time

    c, agent, _ = client
    agent.STATE.role = "master"
    agent.STATE.members["w1"] = {"node_id": "w1", "role": "worker", "last_seen": time.time() - 30}
    store = agent.app.state.usage_store
    store.last_successful_save = "2026-09-28T12:00:00Z"
    monkeypatch.setattr(store, "end_node", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
    agent._sync_master_liveness(shrink=False)
    status = c.get("/v1/usage/live").json()["recording"]
    assert status["recording"] == "failed"
    assert status["last_successful_save"] == "2026-09-28T12:00:00Z"
    assert "disk full" in status["error"]


def test_corrupt_saved_usage_is_server_failure_not_bad_browser_input(client):
    from datetime import datetime, timedelta, timezone
    from kllm import usage

    c, agent, _ = client
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = usage.utc(now - timedelta(minutes=1))
    agent.STATE.role = "master"
    point = {"lifetime_id": f"worker:12:{started}", "pid": 12, "process_started_at": started,
             "process_role": "worker", "observed_at": usage.utc(now),
             "process": {"cpu_percent_machine": 0, "rss_bytes": 1024},
             "host": {"cpu_percent": 0, "used_bytes": 1024, "total_bytes": 4096},
             "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"}}
    agent.app.state.usage_store.record("worker", "host", point, now=now)
    with agent.app.state.usage_store.db:
        agent.app.state.usage_store.db.execute("UPDATE readings SET payload='{' WHERE node_id='worker'")
    response = c.get("/v1/usage/readings", params={"node_id": "worker", "lifetime_id": point["lifetime_id"]})
    assert response.status_code == 503
    assert "history unavailable" in response.json()["detail"]


def test_malformed_saved_usage_times_report_503_not_client_error(client):
    from datetime import datetime, timedelta, timezone
    from kllm import usage

    c, agent, _ = client
    now = datetime.now(timezone.utc).replace(microsecond=0)
    began = usage.utc(now - timedelta(minutes=1))
    agent.STATE.role = "master"
    node = agent.STATE.node_id
    point = {"lifetime_id": f"{node}:12:{began}", "pid": 12, "process_started_at": began,
             "process_role": "master", "observed_at": usage.utc(now),
             "process": {"cpu_percent_machine": 0, "rss_bytes": 1024},
             "host": {"cpu_percent": 0, "used_bytes": 1024, "total_bytes": 4096},
             "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"}}
    store = agent.app.state.usage_store
    store.record(node, "master", point, now=now)
    with store.db:
        store.db.execute("UPDATE readings SET payload=? WHERE node_id=?",
                         (json.dumps({**point, "observed_at": "invalid"}), node))
    safe = TestClient(agent.app, raise_server_exceptions=False)
    assert safe.get("/v1/usage/live").status_code == 503
    with store.db:
        store.db.execute("INSERT OR REPLACE INTO meta VALUES('storage_truncated','1')")
        store.db.execute("INSERT OR REPLACE INTO meta VALUES('storage_truncated_at','invalid')")
    assert safe.get("/v1/usage/lifetimes", params={"node_id": node}).status_code == 503


def test_usage_history_filters_errors_and_departed_node(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from kllm import usage

    c, agent, _ = client
    agent.STATE.role = "master"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = usage.utc(now - timedelta(minutes=1))
    point = {"lifetime_id": f"w1:12:{started}", "pid": 12, "process_started_at": started,
             "process_role": "worker", "observed_at": usage.utc(now - timedelta(seconds=2)),
             "process": {"cpu_percent_machine": 0, "rss_bytes": 0},
             "host": {"cpu_percent": 0, "used_bytes": 0, "total_bytes": 1024},
             "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"}}
    agent.app.state.usage_store.record("w1", "departed", point, now=now)
    agent.app.state.usage_store.record("w1", "departed", {**point, "observed_at": usage.utc(now - timedelta(seconds=1))}, now=now)
    assert c.get("/v1/usage/lifetimes", params={"node_id": "unknown"}).status_code == 404
    lifetimes = c.get("/v1/usage/lifetimes", params={"node_id": "w1"}).json()
    assert lifetimes["connected"] is False
    assert lifetimes["lifetimes"][0]["pid"] == 12
    filters = {"node_id": "w1", "lifetime_id": point["lifetime_id"], "window": "15m"}
    series = c.get("/v1/usage/series", params=filters).json()
    assert len(series["buckets"]) <= 720
    page = c.get("/v1/usage/readings", params={**filters, "limit": 1}).json()
    assert page["readings"][0]["process"]["cpu_percent_machine"] == 0
    import base64

    forged = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    forged["start"] = usage.utc(now - timedelta(hours=1))
    invalid = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode()
    assert c.get("/v1/usage/readings", params={**filters, "cursor": invalid}).status_code == 400
    assert c.get("/v1/usage/series", params={**filters, "window": "wrong"}).status_code in (400, 422)
    assert c.get("/v1/usage/readings", params={**filters, "limit": 1001}).status_code in (400, 422)
    assert c.get("/v1/usage/readings", params={**filters, "cursor": "broken"}).status_code == 400
    agent.STATE.role = "worker"
    agent.STATE.master_host = "10.0.0.1"
    agent.STATE.join_key = "secret"

    class FakeControl:
        def get(self, url, **kwargs):
            assert kwargs["headers"] == {"X-Kllm-Key": "secret"}
            return SimpleNamespace(status_code=200, json=lambda: lifetimes)

    from types import SimpleNamespace
    monkeypatch.setattr(agent.http_clients, "control_client", lambda: FakeControl())
    assert c.get("/v1/usage/lifetimes", params={"node_id": "w1"}).json() == lifetimes


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


def test_inference_activity_distinguishes_prompt_and_generation(monkeypatch):
    from kllm import inference_activity

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        def __init__(self, slots):
            self.slots = slots

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def get(self, url):
            return Response(200, self.slots) if url.endswith("/slots") else Response(200)

    monkeypatch.setattr(
        inference_activity.httpx,
        "Client",
        lambda **_: Client(
            [{"is_processing": True, "n_prompt_tokens": 10, "n_prompt_tokens_processed": 4, "next_token": {"n_decoded": 0}}]
        ),
    )
    prompt = inference_activity.sample(8080, running=True)
    assert prompt["phase"] == "processing_input"
    assert prompt["prompt_tokens_processed"] == 4

    monkeypatch.setattr(
        inference_activity.httpx,
        "Client",
        lambda **_: Client(
            [{"is_processing": True, "n_prompt_tokens": 10, "n_prompt_tokens_processed": 10, "next_token": {"n_decoded": 3}}]
        ),
    )
    generating = inference_activity.sample(8080, running=True)
    assert generating["phase"] == "generating"
    assert generating["output_tokens"] == 3


def test_worker_status_uses_master_activity_snapshot(client):
    c, agent, _ = client
    agent.STATE.role = "worker"
    agent._apply_cluster_snapshot(
        {
            "inference_activity": {
                "phase": "processing_input",
                "active_requests": 1,
                "prompt_tokens": 10,
                "prompt_tokens_processed": 4,
                "output_tokens": 0,
                "slots": 1,
                "server_ready": True,
            }
        }
    )
    status = c.get("/v1/status").json()
    assert status["inference_activity"]["phase"] == "processing_input"


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


def test_delete_model_master_local_only(client):
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

    r = c.post("/v1/models/delete", json={"name": "fanout.gguf"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert not gguf.exists()
    assert body["results"] == []


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
    if os.name != "nt":
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
