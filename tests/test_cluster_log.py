"""Cluster log journal, ingest API, and worker fallback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kllm.cluster_log import (
    HUB,
    LogHub,
    cluster_journal_path,
    make_event,
    reset_for_tests,
)
from kllm.run_planning import build_master_launch
from kllm.run_settings import RunBody


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.agent as agent_mod
    import kllm.cluster_log as log_mod
    import kllm.state as state_mod

    importlib.reload(state_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    log_mod.reset_for_tests()
    return TestClient(agent_mod.app), agent_mod, log_mod


def test_ring_cap_and_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    hub = LogHub(ring_max=3)
    hub.ingest(
        [
            make_event("a", "agent", "one"),
            make_event("b", "rpc", "two"),
            make_event("a", "llama", "three"),
            make_event("a", "rpc", "four"),
        ],
        persist=False,
    )
    assert [e["line"] for e in hub.recent(10)] == ["two", "three", "four"]
    assert [e["line"] for e in hub.recent(10, node="a")] == ["three", "four"]
    assert [e["line"] for e in hub.recent(10, source="rpc")] == ["two", "four"]


def test_journal_rotate(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    hub = LogHub(journal_max_bytes=80)
    hub.ingest([make_event("n", "agent", "x" * 120)], persist=True)
    hub.ingest([make_event("n", "agent", "second")], persist=True)
    journal = cluster_journal_path()
    bak = journal.with_name(journal.name + ".1")
    assert bak.is_file()
    assert journal.is_file()
    assert "second" in journal.read_text(encoding="utf-8")


def test_tail_new_and_partial_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    from kllm.cluster_log import agent_log_path

    log = agent_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("hello\n", encoding="utf-8")
    events = HUB.tail_new_events("n1")
    assert [e["line"] for e in events] == ["hello"]
    assert events[0]["source"] == "agent"
    assert HUB.tail_new_events("n1") == []
    with log.open("a", encoding="utf-8") as fh:
        fh.write("part")
    assert HUB.tail_new_events("n1") == []
    with log.open("a", encoding="utf-8") as fh:
        fh.write("ial\n")
    events = HUB.tail_new_events("n1")
    assert [e["line"] for e in events] == ["partial"]


def test_prime_offsets_skips_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    from kllm.cluster_log import rpc_server_log_path

    log = rpc_server_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("old\n", encoding="utf-8")
    HUB.prime_offsets()
    assert HUB.tail_new_events("n1") == []
    with log.open("a", encoding="utf-8") as fh:
        fh.write("new\n")
    events = HUB.tail_new_events("n1")
    assert [e["line"] for e in events] == ["new"]
    assert events[0]["source"] == "rpc"


def test_truncate_resets_offset(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    from kllm.cluster_log import llama_server_log_path

    log = llama_server_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("aaaa\nbbbb\n", encoding="utf-8")
    assert len(HUB.tail_new_events("n1")) == 2
    log.write_text("zzzz\n", encoding="utf-8")
    events = HUB.tail_new_events("n1")
    assert [e["line"] for e in events] == ["zzzz"]
    assert events[0]["source"] == "llama"


def test_journal_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    HUB.ingest([make_event("n", "agent", "persisted")], persist=True)
    HUB.reset()
    HUB.ensure_journal_loaded()
    assert [e["line"] for e in HUB.recent(10)] == ["persisted"]


def test_subscribe_receives_ingest():
    hub = LogHub()
    q = hub.subscribe()
    hub.ingest([make_event("n", "agent", "live")], persist=False)
    event = q.get_nowait()
    assert event["line"] == "live"
    hub.unsubscribe(q)


def test_iter_sse_snapshot():
    hub = LogHub()
    hub.ingest([make_event("n", "rpc", "hello")], persist=False)
    chunks = b"".join(hub.iter_sse(10, False, None, "rpc"))
    assert b"data: " in chunks
    payload = json.loads(chunks.decode("utf-8").split("data: ", 1)[1].strip())
    assert payload["line"] == "hello"


def test_worker_relay_posts_to_master(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    from kllm.cluster_log import agent_log_path

    log = agent_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("relay-me\n", encoding="utf-8")
    posted: list[dict] = []

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            posted.append({"url": url, "json": json, "headers": headers})
            return _Resp()

    with patch("kllm.cluster_log.httpx.Client", _Client):
        HUB.relay_tick(
            node_id="w1",
            role="worker",
            master_base="http://10.0.0.1:18765",
            join_key="secret",
        )
    assert posted
    assert posted[0]["url"] == "http://10.0.0.1:18765/v1/logs"
    assert posted[0]["headers"]["X-Kllm-Key"] == "secret"
    assert posted[0]["json"]["node_id"] == "w1"
    assert posted[0]["json"]["lines"][0]["line"] == "relay-me"


def test_ingest_requires_join_key(client):
    c, agent, _log = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    r = c.post(
        "/v1/logs",
        json={"node_id": "w1", "lines": [{"source": "rpc", "line": "x"}]},
        headers={"X-Kllm-Key": "wrong"},
    )
    assert r.status_code == 401


def test_ingest_rejected_when_not_master(client):
    c, agent, _log = client
    agent.STATE.role = "worker"
    agent.STATE.join_key = "secret-key"
    r = c.post(
        "/v1/logs",
        json={"node_id": "w1", "lines": [{"source": "rpc", "line": "x"}]},
        headers={"X-Kllm-Key": "secret-key"},
    )
    assert r.status_code == 403


def test_master_ingest_and_get_snapshot(client):
    c, agent, log_mod = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    r = c.post(
        "/v1/logs",
        json={
            "node_id": "w1",
            "lines": [
                {"source": "rpc", "line": "hello rpc"},
                {"source": "agent", "line": "hello agent"},
            ],
        },
        headers={"X-Kllm-Key": "secret-key"},
    )
    assert r.status_code == 200
    assert r.json()["ingested"] == 2
    snap = c.get("/v1/logs", params={"n": 10})
    assert snap.status_code == 200
    body = snap.json()
    assert body["source"] == "cluster"
    lines = [ev["line"] for ev in body["lines"]]
    assert "hello rpc" in lines
    filtered = c.get("/v1/logs", params={"source": "rpc"})
    assert [ev["line"] for ev in filtered.json()["lines"]] == ["hello rpc"]
    bad = c.get("/v1/logs", params={"source": "nope"})
    assert bad.status_code == 400
    journal = log_mod.cluster_journal_path()
    assert journal.is_file()
    assert "hello rpc" in journal.read_text(encoding="utf-8")


def test_worker_get_falls_back_to_local(client):
    c, agent, log_mod = client
    agent.STATE.role = "worker"
    agent.STATE.master_host = "127.0.0.1"
    agent.STATE.master_port = 1
    agent.STATE.join_key = "secret-key"
    log_mod.HUB.ingest([make_event("w1", "rpc", "local-only")], persist=False)
    r = c.get("/v1/logs")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "local"
    assert "unreachable" in (body.get("warning") or "")
    assert body["lines"][0]["line"] == "local-only"


def test_build_master_launch_includes_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    body = RunBody(model="toy.gguf")
    launch = build_master_launch(
        body,
        {"tensor_split_csv": "1.0", "ngl": 32, "context_effective": 4096},
        model_path=Path("/tmp/toy.gguf"),
        rpc_servers=[],
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    assert launch["log_path"].endswith("llama-server.log")
    assert str(tmp_path) in launch["log_path"]
