import importlib
import json
import socket
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import httpx
import uvicorn
from websockets.sync.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed
from kllm.agent.live import LiveFeed, SourceDenied, _encoded
from kllm.agent.services import dashboard_live as dashboard_live_service
from kllm.state import ClusterState


@pytest.fixture(autouse=True)
def close_owned_live_feeds(monkeypatch):
    feeds = []
    original = LiveFeed.__init__

    def tracked_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        feeds.append(self)

    monkeypatch.setattr(LiveFeed, "__init__", tracked_init)
    yield
    for feed in feeds:
        feed.stop()


def test_dashboard_live_service_caches_feed_and_tracks_scope(monkeypatch):
    class FakeFeed:
        def __init__(self, sources, logs, *, upstream_success, scope):
            self.sources = sources
            self.logs = logs
            self.upstream_success = upstream_success
            self.scope = scope
            self.starts = 0

        def start(self):
            self.starts += 1

    monkeypatch.setattr(dashboard_live_service, "LiveFeed", FakeFeed)
    state = ClusterState(role="worker", node_id="worker-a", master_host="master")
    app_state = type("AppState", (), {})()
    service = dashboard_live_service.DashboardLiveService(
        app_state=app_state,
        state=lambda: state,
        status=lambda: {"role": state.role},
        coverage=lambda: "private",
        usage=lambda: {},
        load=lambda: {},
        download=lambda: {},
        logs=lambda after: {"after": after},
        upstream_success=lambda topic: topic,
    )

    first = service.get()
    second = service.get()

    assert first is second
    assert first.starts == 2
    assert set(first.sources) == {"status", "usage", "load", "download"}
    assert first.sources["status"][0]() == {"role": "worker"}
    assert first.logs("cursor") == {"after": "cursor"}
    assert first.upstream_success("usage") == "usage"
    assert first.scope() == ("worker", "worker-a", "master", None, None)
    state.node_id = "worker-b"
    assert first.scope()[1] == "worker-b"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import kllm.state as state_mod
    import kllm.agent as agent_mod

    importlib.reload(state_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    agent_mod.STATE.node_id = "testnode"
    agent_mod.STATE.save()
    with TestClient(agent_mod.app) as test_client:
        yield test_client, agent_mod


def test_dashboard_live_get_validates_and_hides_status_secrets(client, monkeypatch):
    test_client, agent = client
    monkeypatch.setattr(
        agent,
        "_live_status",
        lambda: {"role": "master", "join_key": "secret-key", "apply_error": "model does not fit"},
    )
    agent.app.state.live_feed = agent._new_live_feed()

    response = test_client.get("/v1/dashboard/live", params={"topics": "status"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["version"] == 1 and set(body["areas"]) == {"status"}
    assert body["areas"]["status"]["availability"] == "available"
    assert body["areas"]["status"]["data"]["join_key"] == "secret-key"
    assert body["areas"]["status"]["data"]["apply_error"] == "model does not fit"
    with test_client.websocket_connect(
        "/v1/dashboard/live", headers={"Origin": "http://testserver"}
    ) as socket:
        socket.receive_json()
        socket.send_json(
            {"type": "subscribe", "version": 1, "generation": 1, "topics": ["status"], "logs": None}
        )
        frame = socket.receive_json()
        assert frame["areas"]["status"]["data"]["join_key"] == "secret-key"
        assert frame["areas"]["status"]["data"]["apply_error"] == "model does not fit"
        socket.send_json({"type": "ack", "generation": 1, "batch": frame["batch"]})
    assert (
        test_client.get("/v1/dashboard/live", params={"topics": "status,status"}).status_code == 422
    )
    assert (
        test_client.get("/v1/dashboard/live", params={"topics": "", "logs": "0"}).status_code == 422
    )
    assert (
        test_client.get(
            "/v1/dashboard/live", params={"topics": "status", "after": "bad"}
        ).status_code
        == 422
    )
    assert (
        test_client.get(
            "/v1/dashboard/live", params={"topics": "", "logs": "1", "after": "bad"}
        ).status_code
        == 400
    )


def test_live_status_uses_completed_member_metadata_without_running_probes(client, monkeypatch):
    _, agent = client
    agent.STATE.role = "master"
    agent.STATE.members = {
        "testnode": {
            "node_id": "testnode",
            "role": "master",
            "total_ram_bytes": 4096,
            "available_ram_bytes": 2048,
            "backend": "cpu",
            "git_head": "cached",
        }
    }
    monkeypatch.setattr(
        agent.native,
        "probe_capacity",
        lambda: (_ for _ in ()).throw(AssertionError("native probe")),
    )
    monkeypatch.setattr(
        agent.cluster_update,
        "software_identity",
        lambda: (_ for _ in ()).throw(AssertionError("software probe")),
    )
    data = agent._live_status()
    assert data["capacity"]["total_ram_bytes"] == 4096
    assert data["software"]["git_head"] == "cached"
    assert data["members"]["testnode"]["role"] == "master"


def test_status_then_live_status_does_not_repeat_gpu_acquisition(client, native_inventory_counts):
    test_client, agent = client

    assert test_client.get("/v1/status").status_code == 200
    after_status = dict(native_inventory_counts)
    assert after_status == {"list_gpus": 0, "binary_capabilities": 1}

    agent._live_status()
    assert native_inventory_counts == after_status


def test_dashboard_live_get_origin_admission(client):
    test_client, _ = client
    assert (
        test_client.get(
            "/v1/dashboard/live", params={"topics": "download", "page_origin": "bad"}
        ).status_code
        == 422
    )
    assert (
        test_client.get(
            "/v1/dashboard/live",
            params={"topics": "download", "page_origin": "https://example.test"},
        ).status_code
        == 403
    )
    assert (
        test_client.get(
            "/v1/dashboard/live", params={"topics": "download", "page_origin": "http://testserver"}
        ).status_code
        == 200
    )
    assert (
        test_client.get(
            "/v1/dashboard/live",
            params={"topics": "download"},
            headers={"Origin": "https://example.test"},
        ).status_code
        == 403
    )


def test_dashboard_live_websocket_sends_hello_and_current_status(client, monkeypatch):
    test_client, agent = client
    monkeypatch.setattr(agent, "_live_status", lambda: {"role": "idle", "node_id": "testnode"})
    agent.app.state.live_feed = agent._new_live_feed()

    with test_client.websocket_connect(
        "/v1/dashboard/live", headers={"Origin": "http://testserver"}
    ) as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello" and hello["version"] == 1
        socket.send_json(
            {"type": "subscribe", "version": 1, "generation": 1, "topics": ["status"], "logs": None}
        )
        frame = socket.receive_json()
        assert frame["type"] == "data" and frame["initial"] is True
        assert frame["areas"]["status"]["data"]["node_id"] == "testnode"
        socket.send_json({"type": "ack", "generation": 1, "batch": frame["batch"]})

    with test_client.websocket_connect(
        "/v1/dashboard/live", headers={"Origin": "http://testserver"}
    ) as socket:
        socket.receive_json()
        socket.send_json(
            {"type": "subscribe", "version": 1, "generation": 1, "topics": [{}], "logs": None}
        )
        assert socket.receive_json()["code"] == "invalid_request"


def test_live_socket_closes_on_serving_role_replacement(client):
    test_client, agent = client
    agent.STATE.role = "idle"
    agent.app.state.live_feed = agent._new_live_feed()
    with test_client.websocket_connect(
        "/v1/dashboard/live", headers={"Origin": "http://testserver"}
    ) as socket:
        socket.receive_json()
        socket.send_json(
            {"type": "subscribe", "version": 1, "generation": 1, "topics": ["status"], "logs": None}
        )
        initial = socket.receive_json()
        socket.send_json({"type": "ack", "generation": 1, "batch": initial["batch"]})
        agent.STATE.role = "master"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1012


@pytest.mark.integration
def test_real_uvicorn_http_snapshot_and_websocket_change(client, monkeypatch):
    _, agent = client
    state = {"role": "idle", "node_id": "testnode"}
    monkeypatch.setattr(agent, "_live_status", lambda: dict(state))
    agent.app.state.live_feed = agent._new_live_feed()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(agent.app, host="127.0.0.1", port=port, log_level="critical", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Uvicorn did not start"

        base = f"http://127.0.0.1:{port}"
        response = httpx.get(f"{base}/v1/dashboard/live", params={"topics": "status"}, timeout=3)
        assert response.status_code == 200
        initial_http = response.json()["areas"]["status"]
        assert initial_http["data"]["node_id"] == "testnode"

        with websocket_connect(
            f"ws://127.0.0.1:{port}/v1/dashboard/live",
            additional_headers={"Origin": base},
            open_timeout=3,
        ) as ws:
            hello = ws.recv(timeout=3)
            assert '"type":"hello"' in hello
            ws.send(
                '{"type":"subscribe","version":1,"generation":1,"topics":["status"],"logs":null}'
            )
            initial = json.loads(ws.recv(timeout=3))
            assert initial["type"] == "data" and initial["initial"] is True
            assert initial["areas"]["status"]["revision"] == initial_http["revision"]
            with websocket_connect(
                f"ws://127.0.0.1:{port}/v1/dashboard/live",
                additional_headers={"Origin": base},
                open_timeout=3,
            ) as healthy:
                healthy.recv(timeout=3)
                healthy.send(
                    '{"type":"subscribe","version":1,"generation":1,"topics":["status"],"logs":null}'
                )
                healthy_frame = json.loads(healthy.recv(timeout=3))
                assert healthy_frame["type"] == "data" and healthy_frame["initial"] is True
                healthy.send(
                    json.dumps({"type": "ack", "generation": 1, "batch": healthy_frame["batch"]})
                )
            with websocket_connect(
                f"ws://127.0.0.1:{port}/v1/dashboard/live",
                additional_headers={"Origin": base},
                open_timeout=3,
            ) as invalid:
                invalid.recv(timeout=3)
                invalid.send("x" * 4097)
                with pytest.raises(ConnectionClosed) as closed:
                    invalid.recv(timeout=3)
                assert closed.value.rcvd.code == 1009
            ping = json.loads(ws.recv(timeout=3))
            assert ping["type"] == "ping"
            ws.send(json.dumps({"type": "pong", "id": ping["id"]}))
            ws.send(json.dumps({"type": "ack", "generation": 1, "batch": initial["batch"]}))

            state["node_id"] = "changed-node"
            changed = None
            end = time.monotonic() + 5
            while time.monotonic() < end:
                message = json.loads(ws.recv(timeout=max(0.1, end - time.monotonic())))
                if message.get("type") == "ping":
                    ws.send(json.dumps({"type": "pong", "id": message["id"]}))
                elif message.get("type") == "data":
                    changed = message
                    break
            assert changed is not None
            assert changed["areas"]["status"]["data"]["node_id"] == "changed-node"
            assert changed["areas"]["status"]["revision"] > initial_http["revision"]
            ws.send(json.dumps({"type": "ack", "generation": 1, "batch": changed["batch"]}))

        with websocket_connect(
            f"ws://127.0.0.1:{port}/v1/dashboard/live",
            additional_headers={"Origin": base},
            open_timeout=3,
        ) as ws:
            ws.recv(timeout=3)
            ws.send(
                '{"type":"subscribe","version":1,"generation":1,"topics":["status"],"logs":null}'
            )
            frame = json.loads(ws.recv(timeout=3))
            ws.send(json.dumps({"type": "ack", "generation": 1, "batch": frame["batch"] + 1}))
            error = json.loads(ws.recv(timeout=3))
            assert error["type"] == "error" and error["code"] == "invalid_request"

        with websocket_connect(
            f"ws://127.0.0.1:{port}/v1/dashboard/live",
            additional_headers={"Origin": base},
            open_timeout=3,
        ) as ws:
            ws.recv(timeout=3)
            ws.send("x" * 4097)
            with pytest.raises(ConnectionClosed) as closed:
                ws.recv(timeout=3)
            assert closed.value.rcvd.code == 1009
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "Uvicorn did not shut down"


@pytest.mark.integration
def test_real_uvicorn_near_limit_initial_snapshot_is_one_frame(client):
    _, agent = client

    def large() -> dict[str, str]:
        return {"payload": "x" * 900_000}

    agent.app.state.live_feed = LiveFeed(
        {topic: (large, "local") for topic in ("status", "usage", "load", "download")},
        lambda _after: {
            "lines": [
                {
                    "event_id": "00000000-0000-4000-8000-000000000001",
                    "ts": 1.0,
                    "node_id": "n1",
                    "source": "agent",
                    "line": "y" * 250_000,
                }
            ],
            "cursor": "00000000-0000-4000-8000-000000000002:1",
            "reset": None,
            "gap": False,
            "gap_reason": None,
            "source": "cluster",
        },
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(agent.app, host="127.0.0.1", port=port, log_level="critical", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        base = f"http://127.0.0.1:{port}"
        with websocket_connect(
            f"ws://127.0.0.1:{port}/v1/dashboard/live",
            additional_headers={"Origin": base},
            open_timeout=3,
            max_size=5 * 1024 * 1024,
        ) as ws:
            ws.recv(timeout=3)
            ws.send(
                '{"type":"subscribe","version":1,"generation":1,"topics":["status","usage","load","download"],"logs":{"after":null,"continuity_id":null}}'
            )
            raw = ws.recv(timeout=3)
            frame = json.loads(raw)
            assert len(raw.encode()) < 5 * 1024 * 1024
            assert frame["type"] == "data" and frame["initial"] is True
            assert set(frame["areas"]) == {"status", "usage", "load", "download"}
            assert len(frame["logs"]["snapshot"]["lines"]) == 1
            ws.send(json.dumps({"type": "ack", "generation": 1, "batch": frame["batch"]}))
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_live_feed_shares_acquisition_and_isolates_sanitized_area_failure():
    calls = {"status": 0, "usage": 0}

    def status():
        calls["status"] += 1
        return {"node_id": "n1", "join_key": "secret"}

    def usage():
        calls["usage"] += 1
        raise RuntimeError("token=secret private response")

    feed = LiveFeed({"status": (status, "local"), "usage": (usage, "cluster")}, lambda _after: {})
    first = feed.snapshot(["status", "usage"])
    second = feed.snapshot(["status", "usage"])
    assert calls == {"status": 1, "usage": 1}
    assert first["areas"]["status"]["data"]["join_key"] == "secret"
    assert second["areas"]["status"]["revision"] == first["areas"]["status"]["revision"]
    assert first["areas"]["usage"]["availability"] == "unavailable"
    assert "secret" not in json.dumps(first["areas"]["usage"]["error"])


def test_live_feed_rejects_oversized_area_without_truncating_data():
    feed = LiveFeed(
        {"status": (lambda: {"payload": "x" * (1024 * 1024 + 1)}, "local")}, lambda _after: {}
    )
    record = feed.snapshot(["status"])["areas"]["status"]
    assert record["availability"] == "unavailable"
    assert record["error"]["code"] == "payload_too_large"
    assert record["data"] is None


def test_live_feed_rejects_nonfinite_source_values():
    record = LiveFeed(
        {"usage": (lambda: {"age_seconds": float("nan")}, "local")}, lambda _after: {}
    ).snapshot(["usage"])["areas"]["usage"]
    assert record["availability"] == "unavailable"
    assert record["error"]["code"] == "invalid_source_data"
    assert record["data"] is None


def test_live_feed_preserves_area_denial_as_denied():
    def denied():
        raise SourceDenied("secret-bearing upstream detail")

    record = LiveFeed({"usage": (denied, "cluster")}, lambda _after: {}).snapshot(["usage"])[
        "areas"
    ]["usage"]
    assert record["availability"] == "denied"
    assert record["error"] == {"code": "forbidden", "message": "Source access denied"}


def test_worker_usage_denial_does_not_become_unavailable_or_echo_upstream_body(client, monkeypatch):
    test_client, agent = client
    agent.STATE.role = "worker"
    agent.STATE.master_host = "10.0.0.1"
    agent.STATE.join_key = "private-join-key"
    agent.app.state.live_feed = agent._new_live_feed()

    class DeniedResponse:
        status_code = 403
        text = "private-join-key in upstream response"

    class Upstream:
        def get(self, *_args, **_kwargs):
            return DeniedResponse()

    monkeypatch.setattr(agent.http_clients, "control_client", lambda: Upstream())
    old_route = test_client.get("/v1/usage/live")
    assert old_route.status_code == 403
    assert "private-join-key" not in old_route.text
    response = test_client.get("/v1/dashboard/live", params={"topics": "status,usage"})
    assert response.status_code == 200
    areas = response.json()["areas"]
    assert areas["status"]["availability"] == "available"
    assert areas["usage"]["availability"] == "denied"
    assert "private-join-key" not in json.dumps(areas["usage"])


def test_error_area_does_not_hide_a_good_status_in_initial_websocket_frame(client, monkeypatch):
    test_client, agent = client
    monkeypatch.setattr(agent, "_live_status", lambda: {"role": "idle", "node_id": "testnode"})
    monkeypatch.setattr(
        agent, "_live_load", lambda: (_ for _ in ()).throw(RuntimeError("load unavailable"))
    )
    agent.app.state.live_feed = agent._new_live_feed()
    with test_client.websocket_connect(
        "/v1/dashboard/live", headers={"Origin": "http://testserver"}
    ) as socket:
        socket.receive_json()
        socket.send_json(
            {
                "type": "subscribe",
                "version": 1,
                "generation": 1,
                "topics": ["status", "load"],
                "logs": None,
            }
        )
        frame = socket.receive_json()
        assert frame["initial"] is True
        assert frame["areas"]["status"]["availability"] == "available"
        assert frame["areas"]["load"]["availability"] == "unavailable"
        assert frame["areas"]["load"]["data"] is None
        socket.send_json({"type": "ack", "generation": 1, "batch": frame["batch"]})


def test_live_area_denial_retains_last_upstream_success_time():
    calls = 0

    def source():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise SourceDenied
        return {"nodes": {}}

    feed = LiveFeed(
        {"usage": (source, "local_with_cluster_view")},
        lambda _after: {},
        upstream_success=lambda _topic: "2026-09-29T12:00:00Z",
    )
    first = feed.snapshot(["usage"])["areas"]["usage"]
    feed._areas["usage"] = (0, first, b"old")
    denied = feed.snapshot(["usage"])["areas"]["usage"]
    assert denied["availability"] == "denied"
    assert denied["upstream_last_success_at"] == "2026-09-29T12:00:00Z"


def test_cached_usage_age_advances_without_claiming_a_new_observation():
    feed = LiveFeed(
        {
            "usage": (
                lambda: {
                    "nodes": {
                        "n1": {
                            "age_seconds": 9.8,
                            "freshness": "fresh",
                            "reading": {"observed_at": "2026-09-29T12:00:00Z"},
                        }
                    }
                },
                "local",
            )
        },
        lambda _after: {},
    )
    first = feed.snapshot(["usage"])["areas"]["usage"]
    feed._areas["usage"] = (time.monotonic() - 0.4, first, b"cached")
    aged = feed.snapshot(["usage"])["areas"]["usage"]
    assert aged["sampled_at"] == first["sampled_at"]
    assert aged["revision"] == first["revision"]
    assert aged["data"]["nodes"]["n1"]["age_seconds"] >= 10
    assert aged["data"]["nodes"]["n1"]["freshness"] == "stale"


@pytest.mark.unit
def test_near_limit_four_area_plus_logs_snapshot_fits_one_frame():
    topics = ("status", "usage", "load", "download")
    sources = {topic: (lambda: {"payload": "x" * 900_000}, "local") for topic in topics}
    log_result = {
        "lines": [
            {
                "event_id": "00000000-0000-4000-8000-000000000001",
                "ts": 1.0,
                "node_id": "n1",
                "source": "agent",
                "line": "y" * 250_000,
            }
        ],
        "cursor": "00000000-0000-4000-8000-000000000002:1",
        "reset": None,
        "gap": False,
        "gap_reason": None,
        "source": "cluster",
    }
    snapshot = LiveFeed(sources, lambda _after: dict(log_result)).snapshot(list(topics), logs=True)
    assert set(snapshot["areas"]) == set(topics)
    assert len(_encoded(snapshot["logs"])) <= 256 * 1024
    assert len(_encoded(snapshot)) < 5 * 1024 * 1024


@pytest.mark.unit
def test_log_count_limit_reports_omitted_prefix_without_losing_highwater():
    from kllm import cluster_log

    hub = cluster_log.LogHub(ring_max=800)
    hub.ingest(
        [cluster_log.make_event("master", "agent", f"event {i}") for i in range(501)], persist=False
    )
    feed = LiveFeed({}, lambda after: {**hub.snapshot(501, after=after), "source": "cluster"})
    try:
        result = feed.snapshot([], logs=True)["logs"]
        snap = result["snapshot"]
        assert len(snap["lines"]) == 500
        assert snap["lines"][0]["line"] == "event 1"
        assert snap["cursor"].endswith(":501")
        assert snap["gap"] is True and snap["gap_reason"] == "batch_limit"
        empty = feed.snapshot([], logs=True, after=snap["cursor"])["logs"]["snapshot"]
        assert empty["lines"] == [] and empty["cursor"] == snap["cursor"]
    finally:
        feed.stop()


@pytest.mark.unit
def test_log_suffix_fits_the_maximal_newest_complete_result_without_mutating_source():
    from kllm.agent.live import _fit_log_suffix
    from kllm.cluster_log import make_event

    rows = [make_event("n1", "agent", f"line-{i}-" + "x" * 100, ts=1.0) for i in range(20)]
    snapshot = {
        "lines": list(rows),
        "source": "cluster",
        "cursor": "epoch:20",
        "reset": None,
        "gap": False,
        "gap_reason": None,
    }
    source = list(snapshot["lines"])
    prefix = {"availability": "available", "sampled_at": "2026-09-30T00:00:00Z", "error": None}
    expected = {**snapshot, "lines": rows[-5:], "gap": True, "gap_reason": "batch_limit"}
    budget = len(_encoded({**prefix, "snapshot": expected}))

    lines, gap = _fit_log_suffix(snapshot, budget, prefix)

    assert lines == rows[-5:]
    assert gap == {"gap": True, "gap_reason": "batch_limit"}
    assert snapshot["lines"] == rows[-5:]
    assert source == rows
    assert len(_encoded({**prefix, "snapshot": snapshot})) == budget
    larger = {**snapshot, "lines": rows[-6:]}
    assert len(_encoded({**prefix, "snapshot": larger})) > budget


@pytest.mark.unit
def test_log_suffix_uses_encoded_unicode_and_retains_retention_gap():
    from kllm.agent.live import _fit_log_suffix

    prefix = {"availability": "available", "sampled_at": "2026-09-30T00:00:00Z", "error": None}
    lines = [
        {"event_id": "one", "line": 'older "quoted"\\\\ text 😀' * 20},
        {"event_id": "two", "line": "newest café 東京\\n" * 20},
    ]
    base = {
        "lines": lines,
        "source": "cluster",
        "cursor": "epoch:2",
        "reset": None,
        "gap": False,
        "gap_reason": None,
    }
    exact_budget = len(_encoded({**prefix, "snapshot": base}))
    exact = dict(base)
    assert _fit_log_suffix(exact, exact_budget, prefix)[0] == lines
    shrunk = dict(base)
    kept, gap = _fit_log_suffix(shrunk, exact_budget - 1, prefix)
    assert kept == lines[-1:]
    assert gap == {"gap": True, "gap_reason": "batch_limit"}
    assert len(_encoded({**prefix, "snapshot": shrunk})) <= exact_budget - 1

    retained = {**base, "gap": True, "gap_reason": "retention"}
    tight = len(_encoded({**prefix, "snapshot": {**retained, "lines": []}}))
    _fit_log_suffix(retained, tight + 20, prefix)
    assert retained["gap_reason"] == "retention"


@pytest.mark.unit
def test_log_suffix_validates_omitted_rows_and_rejects_unfit_metadata():
    from kllm.agent.live import _fit_log_suffix

    prefix = {"availability": "available", "sampled_at": "2026-09-30T00:00:00Z", "error": None}
    invalid = {
        "lines": [{"line": "older", "bad": float("nan")}, {"line": "newest"}],
        "source": "cluster",
        "cursor": "epoch:2",
        "reset": None,
        "gap": False,
        "gap_reason": None,
    }
    with pytest.raises(ValueError):
        _fit_log_suffix(invalid, 128, prefix)

    impossible = {
        "lines": [{"line": "x" * 1000}],
        "source": "cluster",
        "cursor": "epoch:1",
        "reset": None,
        "gap": False,
        "gap_reason": None,
    }
    with pytest.raises(OverflowError):
        _fit_log_suffix(impossible, 1, prefix)


def test_dashboard_log_get_and_websocket_keep_event_ids_and_checkpoint(client, monkeypatch):
    test_client, agent = client
    from kllm import cluster_log

    hub = cluster_log.LogHub()
    agent.STATE.role = "idle"
    event = cluster_log.make_event("testnode", "agent", "same message")
    hub.ingest([event], persist=False)
    monkeypatch.setattr(cluster_log, "HUB", hub)
    agent.app.state.live_feed = agent._new_live_feed()
    params = {"topics": "", "logs": "1"}
    http_logs = test_client.get("/v1/dashboard/live", params=params).json()["logs"]["snapshot"]
    assert [line["event_id"] for line in http_logs["lines"]] == [event["event_id"]]
    with test_client.websocket_connect(
        "/v1/dashboard/live", headers={"Origin": "http://testserver"}
    ) as socket:
        socket.receive_json()
        socket.send_json(
            {
                "type": "subscribe",
                "version": 1,
                "generation": 1,
                "topics": [],
                "logs": {"after": None, "continuity_id": None},
            }
        )
        frame = socket.receive_json()
        assert frame["initial"] is True
        ws_logs = frame["logs"]["snapshot"]
        assert ws_logs["lines"] == http_logs["lines"]
        assert ws_logs["cursor"] == http_logs["cursor"]
        socket.send_json({"type": "ack", "generation": 1, "batch": frame["batch"]})
    empty = test_client.get(
        "/v1/dashboard/live", params={**params, "after": http_logs["cursor"]}
    ).json()["logs"]["snapshot"]
    assert empty["lines"] == [] and empty["cursor"] == http_logs["cursor"]


@pytest.mark.unit
def test_live_feed_coalesces_source_work_without_blocking_other_areas():
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def slow_status():
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(3)
        return {"node_id": "n1"}

    feed = LiveFeed(
        {"status": (slow_status, "local"), "usage": (lambda: {"nodes": {}}, "cluster")},
        lambda _after: {},
        wait_budget=0.05,
    )
    results = []
    owner = threading.Thread(target=lambda: results.append(feed.snapshot(["status"])))
    owner.start()
    try:
        assert started.wait(1)
        assert feed.snapshot(["usage"])["areas"]["usage"]["availability"] == "available"
        waiting = feed.snapshot(["status"])
        assert waiting["areas"]["status"]["availability"] == "loading"
    finally:
        release.set()
        owner.join(timeout=3)
    assert not owner.is_alive()
    assert calls == 1
    assert results[0]["areas"]["status"]["availability"] in {"loading", "available"}
    feed._futures["status"].result(timeout=3)
    assert feed.snapshot(["status"])["areas"]["status"]["data"]["node_id"] == "n1"
    feed.stop()


@pytest.mark.unit
def test_live_feed_test_wait_budget_bounds_pending_snapshot():
    entered = threading.Event()
    release = threading.Event()

    def blocked():
        entered.set()
        assert release.wait(3)
        return {"node_id": "n1"}

    feed = LiveFeed({"status": (blocked, "local")}, lambda _after: {}, wait_budget=0.05)
    workers = []
    try:
        began = time.monotonic()
        result = feed.snapshot(["status"])
        assert time.monotonic() - began < 0.2
        assert entered.is_set()
        assert result["areas"]["status"]["availability"] == "loading"
    finally:
        release.set()
        feed._futures["status"].result(timeout=3)
        workers = list(feed._executor._threads)
        feed.stop()
        for worker in workers:
            worker.join(timeout=1)
        assert all(not worker.is_alive() for worker in workers)


def test_live_feed_cold_sources_are_independent_and_initial_read_is_bounded():
    entered = threading.Event()
    release = threading.Event()

    def blocked_status():
        entered.set()
        assert release.wait(4)
        return {"node_id": "n1"}

    feed = LiveFeed(
        {"status": (blocked_status, "local"), "usage": (lambda: {"nodes": {}}, "local")},
        lambda _after: {},
    )
    try:
        began = time.monotonic()
        first = feed.snapshot(["status", "usage"])
        assert time.monotonic() - began < 2.5
        assert entered.is_set()
        assert first["areas"]["status"]["availability"] == "loading"
        assert first["areas"]["usage"]["availability"] == "available"
    finally:
        release.set()
        feed.stop()


def test_live_feed_viewer_interest_drives_one_shared_cadence_and_releases_on_close():
    counted = threading.Event()
    calls = 0

    def status():
        nonlocal calls
        calls += 1
        if calls >= 2:
            counted.set()
        return {"node_id": "n1"}

    feed = LiveFeed({"status": (status, "local")}, lambda _after: {})
    feed.start()
    try:
        feed.subscribe("viewer-a", ["status"])
        feed.subscribe("viewer-b", ["status"])
        assert counted.wait(2.5), "a visible subscription did not drive shared source acquisition"
        assert calls <= 3, "viewer count multiplied source acquisitions"
        feed.unsubscribe("viewer-a")
        feed.unsubscribe("viewer-b")
        after_close = calls
        time.sleep(1.2)
        assert calls == after_close, "viewer-owned source work continued after both viewers left"
    finally:
        feed.stop()


def test_expired_http_lease_stops_source_acquisition():
    calls = 0

    def status():
        nonlocal calls
        calls += 1
        return {"node_id": "n1"}

    feed = LiveFeed({"status": (status, "local")}, lambda _after: {})
    feed.start()
    try:
        feed.snapshot(["status"])
        assert 0 < feed._leases["status"] - time.monotonic() <= 5
        with feed._lock:
            feed._leases["status"] = time.monotonic() - 1
        time.sleep(0.2)
        after_expiry = calls
        time.sleep(1.2)
        assert calls == after_expiry, "expired HTTP demand continued source acquisition"
    finally:
        feed.stop()


@pytest.mark.unit
def test_scope_change_rejects_obsolete_in_flight_source_result():
    scope = {"role": "master"}
    started = threading.Event()
    release = threading.Event()

    def status():
        role = scope["role"]
        if role == "master":
            started.set()
            assert release.wait(3)
        return {"role": role}

    feed = LiveFeed(
        {"status": (status, "local")},
        lambda _after: {},
        scope=lambda: scope["role"],
        wait_budget=0.05,
    )
    try:
        first = feed.snapshot(["status"])
        assert started.is_set() and first["areas"]["status"]["availability"] == "loading"
        old_epoch = first["epoch"]
        scope["role"] = "worker"
        assert feed.current_epoch() != old_epoch
        release.set()
        feed._futures["status"].result(timeout=3)
        recovered = feed.snapshot(["status"])
        assert recovered["areas"]["status"]["data"] == {"role": "worker"}
        assert recovered["epoch"] != old_epoch
    finally:
        release.set()
        feed.stop()


def test_slow_logs_do_not_delay_cold_status_or_initial_deadline():
    entered = threading.Event()
    release = threading.Event()

    def logs(_after):
        entered.set()
        assert release.wait(4)
        return {
            "lines": [],
            "cursor": "00000000-0000-4000-8000-000000000001:0",
            "source": "cluster",
            "reset": None,
            "gap": False,
            "gap_reason": None,
        }

    feed = LiveFeed({"status": (lambda: {"role": "master"}, "cluster")}, logs)
    try:
        start = time.monotonic()
        result = feed.snapshot(["status"], logs=True)
        assert time.monotonic() - start < 2.5
        assert entered.is_set()
        assert result["areas"]["status"]["availability"] == "available"
        assert result["logs"]["availability"] == "loading"
    finally:
        release.set()
        feed.stop()


def test_live_progress_snapshot_carries_active_and_latest_terminal(client, monkeypatch):
    test_client, agent = client
    from kllm import models as model_ops, load_progress
    from kllm.models.download_progress import DownloadProgress

    previous_download = (
        model_ops.downloads._DOWNLOAD_PROGRESS,
        model_ops.downloads._DOWNLOAD_LAST_TERMINAL,
    )
    monkeypatch.setattr(
        load_progress,
        "records",
        lambda _port: {
            "current": {"operation_id": "load-current", "phase": "loading"},
            "last_terminal": {"operation_id": "load-finished", "phase": "ready"},
        },
    )
    model_ops.downloads._DOWNLOAD_PROGRESS = DownloadProgress(
        operation_id="download-current",
        filename="next.gguf",
        phase="downloading",
        bytes=1,
        total=2,
        pct=50,
        rate_bps=None,
        eta_seconds=None,
        started_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        finished_at=None,
    )
    model_ops.downloads._DOWNLOAD_LAST_TERMINAL = DownloadProgress(
        operation_id="download-finished",
        filename="done.gguf",
        phase="complete",
        bytes=2,
        total=2,
        pct=100,
        rate_bps=None,
        eta_seconds=None,
        started_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
    )
    try:
        agent.app.state.live_feed = agent._new_live_feed()
        data = test_client.get("/v1/dashboard/live", params={"topics": "load,download"}).json()[
            "areas"
        ]
        assert data["load"]["data"]["current"]["operation_id"] == "load-current"
        assert data["load"]["data"]["last_terminal"]["operation_id"] == "load-finished"
        assert data["download"]["data"]["current"]["operation_id"] == "download-current"
        assert data["download"]["data"]["last_terminal"]["operation_id"] == "download-finished"
    finally:
        model_ops.downloads._DOWNLOAD_PROGRESS, model_ops.downloads._DOWNLOAD_LAST_TERMINAL = (
            previous_download
        )


@pytest.mark.asyncio
async def test_live_feed_lifespan_restarts_with_new_epoch_and_releases_state(client):
    _, agent = client
    async with agent.lifespan(agent.app):
        first_epoch = agent.app.state.live_feed.epoch
    assert not hasattr(agent.app.state, "live_feed")
    async with agent.lifespan(agent.app):
        second_epoch = agent.app.state.live_feed.epoch
        assert second_epoch != first_epoch
    assert not hasattr(agent.app.state, "live_feed")


def test_worker_live_log_mirror_uses_private_cursor_and_reports_coverage_changes(
    client, monkeypatch
):
    _, agent = client
    agent.STATE.role = "worker"
    agent.STATE.node_id = "worker-1"
    remote_epoch = "00000000-0000-4000-8000-000000000099"
    events = [
        {
            "event_id": "00000000-0000-4000-8000-000000000001",
            "ts": 1.0,
            "node_id": "master",
            "source": "agent",
            "line": "one",
        },
        {
            "event_id": "00000000-0000-4000-8000-000000000002",
            "ts": 2.0,
            "node_id": "worker-1",
            "source": "rpc",
            "line": "two",
        },
    ]
    remote = {
        "cursor": f"{remote_epoch}:1",
        "lines": events[:1],
        "reset": None,
        "gap": False,
        "gap_reason": None,
    }
    upstream_calls = []
    monkeypatch.setattr(
        agent,
        "_try_proxy_master_logs",
        lambda *args: (upstream_calls.append(args), dict(remote))[1],
    )
    agent.app.state.live_log_state = None
    first = agent._live_logs(None)
    assert first["source"] == "cluster" and [row["line"] for row in first["lines"]] == ["one"]
    remote["cursor"] = f"{remote_epoch}:2"
    remote["lines"] = events[1:]
    agent.app.state.live_log_state["last_acquired"] = 0
    next_snapshot = agent._live_logs(first["cursor"])
    assert [row["line"] for row in next_snapshot["lines"]] == ["two"]
    assert next_snapshot["lines"][0]["event_id"] == events[1]["event_id"]
    assert upstream_calls[1][-1] == f"{remote_epoch}:1", (
        "client cursor leaked into upstream acquisition"
    )

    continuity = next_snapshot["continuity_id"]
    monkeypatch.setattr(agent, "_try_proxy_master_logs", lambda *args: None)
    agent.app.state.live_log_state["last_acquired"] = 0
    local = agent._live_logs(None)
    assert local["source"] == "local"
    assert local["continuity_id"] != continuity
    assert local["gap"] is True
    assert local["warning"]


def test_worker_live_log_access_denial_is_not_replaced_with_local_fallback(client, monkeypatch):
    test_client, agent = client
    agent.STATE.role = "worker"
    agent.STATE.node_id = "worker-1"
    agent.app.state.live_log_state = None
    agent.app.state.live_feed = agent._new_live_feed()

    def denied(*_args):
        raise HTTPException(403, "upstream private denial")

    monkeypatch.setattr(agent, "_try_proxy_master_logs", denied)
    response = test_client.get("/v1/dashboard/live", params={"topics": "", "logs": "1"})
    assert response.status_code == 200
    result = response.json()["logs"]
    assert result["availability"] == "denied"
    assert result["error"]["code"] == "forbidden"
    assert "private" not in result["error"]["message"]
