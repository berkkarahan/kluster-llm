"""Cluster log journal, ingest API, and worker fallback."""

from __future__ import annotations

import json
import uuid
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
    agent.STATE.node_id = "w1"
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


def test_identical_messages_get_distinct_ids_and_preserve_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    hub = LogHub()
    hub.ingest([make_event("n", "agent", "same"), make_event("n", "agent", "same")], persist=False)
    ids = [ev["event_id"] for ev in hub.recent(10)]
    assert len(set(ids)) == 2, "distinct events must not share an identity"
    supplied = "11111111-2222-3333-4444-555555555555"
    hub.ingest([make_event("n", "agent", "same", event_id=supplied)], persist=False)
    assert hub.recent(10)[-1]["event_id"] == supplied


def test_snapshot_incremental_empty_checkpoint_and_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    hub = LogHub(ring_max=10)
    from kllm.cluster_log import CursorError

    hub.ingest([make_event("n", "agent", f"line-{i}") for i in range(5)], persist=False)
    first = hub.snapshot(2)
    assert [ev["line"] for ev in first["lines"]] == ["line-3", "line-4"]
    assert first["reset"] is None and first["gap"] is True and first["gap_reason"] == "batch_limit"
    epoch, seq = first["cursor"].split(":")
    empty = hub.snapshot(10, after=f"{epoch}:{seq}")
    assert empty["lines"] == [] and empty["gap"] is False and empty["cursor"] == first["cursor"]
    hub.ingest([make_event("n", "agent", "line-5")], persist=False)
    nxt = hub.snapshot(10, after=first["cursor"])
    assert [ev["line"] for ev in nxt["lines"]] == ["line-5"]
    with pytest.raises(CursorError):
        hub.snapshot(10, after="not-a-cursor")
    with pytest.raises(CursorError):
        hub.snapshot(10, after=f"{epoch}:{int(seq) + 99}")
    assert str(uuid.UUID(epoch)) == epoch, "emitted feed epoch must be canonical UUID text"
    with pytest.raises(CursorError, match="canonical UUID"):
        hub.snapshot(10, after=f"{uuid.uuid4().hex}:0")
    foreign = hub.snapshot(10, after=f"{uuid.uuid4()}:3")
    assert foreign["reset"] == "origin_changed" and foreign["gap_reason"] == "feed_reset"


def test_snapshot_retention_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    hub = LogHub(ring_max=2)
    hub.ingest([make_event("n", "agent", f"e{i}") for i in range(5)], persist=False)
    epoch, _ = hub.snapshot(1)["cursor"].split(":")
    snap = hub.snapshot(10, after=f"{epoch}:1")
    assert snap["gap"] is True and snap["gap_reason"] == "retention"


def test_journal_merge_keeps_history_when_ring_prefilled(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    HUB.ingest([make_event("n", "agent", "persisted")], persist=True)
    HUB.reset()
    # Simulate a startup-local record arriving before the journal is loaded.
    HUB.ingest([make_event("n", "agent", "startup")], persist=False)
    HUB.ensure_journal_loaded()
    hub = HUB
    assert [ev["line"] for ev in hub.recent(10)] == ["persisted", "startup"]
    assert "persisted" in cluster_journal_path().read_text(encoding="utf-8")


def test_journal_read_failure_remains_retryable(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    reset_for_tests()
    HUB.ingest([make_event("n", "agent", "persisted")], persist=True)
    HUB.reset()
    journal = cluster_journal_path()
    original = Path.read_text
    attempts = 0

    def fail_once(path, *args, **kwargs):
        nonlocal attempts
        if path == journal and attempts == 0:
            attempts += 1
            raise OSError("temporary read failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_once)
    with pytest.raises(OSError, match="temporary read failure"):
        HUB.ensure_journal_loaded()
    assert HUB._journal_loaded is False
    HUB.ensure_journal_loaded()
    assert HUB._journal_loaded is True
    assert [event["line"] for event in HUB.recent(10)] == ["persisted"]


def test_master_log_read_surfaces_journal_failure(client, monkeypatch):
    c, agent, log_mod = client
    agent.STATE.role = "master"
    monkeypatch.setattr(
        log_mod.HUB,
        "ensure_journal_loaded",
        lambda: (_ for _ in ()).throw(OSError("temporary journal failure")),
    )
    response = c.get("/v1/logs")
    assert response.status_code == 503
    assert "journal" in response.json()["detail"]


def test_relay_carries_event_id(tmp_path, monkeypatch):
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
            posted.append(json)
            return _Resp()

    with patch("kllm.cluster_log.httpx.Client", _Client):
        HUB.relay_tick(node_id="w1", role="worker", master_base="http://x:1", join_key="k")
    line = posted[0]["lines"][0]
    assert line["event_id"]
    assert HUB.recent(1)[0]["event_id"] == line["event_id"]


def test_get_logs_incremental_and_cursor_validation(client):
    c, agent, _log = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    c.post(
        "/v1/logs",
        json={"node_id": "w1", "lines": [{"source": "rpc", "line": "one"}]},
        headers={"X-Kllm-Key": "secret-key"},
    )
    first = c.get("/v1/logs", params={"n": 10}).json()
    assert first["cursor"] and first["reset"] is None
    assert first["lines"][0]["event_id"]
    c.post(
        "/v1/logs",
        json={"node_id": "w1", "lines": [{"source": "rpc", "line": "two"}]},
        headers={"X-Kllm-Key": "secret-key"},
    )
    nxt = c.get("/v1/logs", params={"n": 10, "after": first["cursor"]}).json()
    assert [ev["line"] for ev in nxt["lines"]] == ["two"]
    assert c.get("/v1/logs", params={"after": "bad"}).status_code == 400
    assert c.get("/v1/logs", params={"after": first["cursor"], "follow": "true"}).status_code == 400
    # Legacy unversioned read still works.
    legacy = c.get("/v1/logs", params={"n": 1}).json()
    assert legacy["lines"] and "source" in legacy


def test_worker_local_fallback_reports_cursor(client):
    c, agent, log_mod = client
    agent.STATE.role = "worker"
    agent.STATE.node_id = "w1"
    agent.STATE.master_host = "127.0.0.1"
    agent.STATE.master_port = 1
    agent.STATE.join_key = "secret-key"
    log_mod.HUB.ingest([make_event("w1", "rpc", "local-only")], persist=False)
    body = c.get("/v1/logs").json()
    assert body["source"] == "local"
    assert body["cursor"]
    assert body["lines"][0]["event_id"]


def test_worker_local_snapshot_defaults_to_own_node(client):
    c, agent, log_mod = client
    agent.STATE.role = "worker"
    agent.STATE.node_id = "w1"
    agent.STATE.master_host = None
    log_mod.HUB.ingest(
        [make_event("former-remote", "rpc", "remote"), make_event("w1", "rpc", "local")],
        persist=False,
    )
    body = c.get("/v1/logs").json()
    assert body["source"] == "local"
    assert [row["line"] for row in body["lines"]] == ["local"]
    explicit = c.get("/v1/logs", params={"node": "w1"}).json()
    assert [row["line"] for row in explicit["lines"]] == ["local"]


def test_worker_proxy_reports_reachable_http_error_not_local_fallback(client, monkeypatch):
    c, agent, _log_mod = client
    agent.STATE.role = "worker"
    agent.STATE.master_host = "127.0.0.1"
    agent.STATE.master_port = 18765
    agent.STATE.join_key = "secret-key"

    class Response:
        status_code = 401

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(agent, "_master_control_base", lambda: "http://127.0.0.1:18765")
    with patch("httpx.Client", Client):
        response = c.get("/v1/logs")
    assert response.status_code == 401
    assert "master log request" in response.json()["detail"]


def test_worker_detects_old_master_missing_initial_cursor(client, monkeypatch):
    c, agent, _log_mod = client
    agent.STATE.role = "worker"
    agent.STATE.master_host = "master"
    agent.STATE.join_key = "secret-key"
    monkeypatch.setattr(agent, "_try_proxy_master_logs", lambda *args: {
        "lines": [], "source": "cluster"
    })
    response = c.get("/v1/logs")
    assert response.status_code == 502
    assert "incremental log" in response.json()["detail"]


def test_ingest_rejects_invalid_event_id_and_nonfinite_timestamp(client):
    c, agent, _log = client
    agent.STATE.role = "master"
    agent.STATE.join_key = "secret-key"
    headers = {"X-Kllm-Key": "secret-key"}
    invalid_id = c.post(
        "/v1/logs",
        json={"node_id": "w1", "lines": [{"event_id": "not-a-uuid", "line": "bad id"}]},
        headers=headers,
    )
    assert invalid_id.status_code == 422
    invalid_ts = c.post(
        "/v1/logs",
        content=b'{"node_id":"w1","lines":[{"ts":Infinity,"line":"bad time"}]}',
        headers={**headers, "Content-Type": "application/json"},
    )
    assert invalid_ts.status_code == 422


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
