"""Heartbeat contact has to survive one lost packet without a false alarm."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException

from kllm.modules.cluster.interactors.membership_heartbeat import MembershipHeartbeatService
from kllm.shared.state import ClusterState


def _worker(tmp_path, monkeypatch) -> ClusterState:
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(
        role="worker",
        node_id="worker",
        master_host="10.0.0.1",
        join_key="test",
    )
    state.save = lambda: None  # type: ignore[method-assign]
    return state


def _service(state_box: dict, client):
    marks: list[ClusterState] = []
    clears: list[ClusterState] = []
    service = MembershipHeartbeatService(
        state=lambda: state_box["state"],
        ensure_control_route=lambda *_args: [],
        control_base=lambda: "http://10.0.0.1:9",
        heartbeat_payload=lambda: {"node_id": "worker"},
        control_client=lambda: client,
        mark_unreachable=marks.append,
        clear_unreachable=clears.append,
        worker_local_leave=lambda: None,
        apply_snapshot=lambda _payload: None,
        member_rpc_host=lambda member: str(member.get("host") or ""),
    )
    return service, marks, clears


def test_one_missed_heartbeat_does_not_mark_the_master_down(tmp_path, monkeypatch):
    state = _worker(tmp_path, monkeypatch)
    client = SimpleNamespace(
        post=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("timed out"))
    )
    service, marks, clears = _service({"state": state}, client)

    service.post_heartbeat()

    assert marks == []
    assert clears == []


def test_second_miss_marks_and_a_later_success_clears(tmp_path, monkeypatch):
    state = _worker(tmp_path, monkeypatch)
    failures = {"left": 2}

    def post(*_args, **_kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise OSError("timed out")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"accepted": True, "cluster_members": {"worker": {"role": "worker"}}},
        )

    service, marks, clears = _service({"state": state}, SimpleNamespace(post=post))

    service.post_heartbeat()
    service.post_heartbeat()
    assert len(marks) == 1
    service.post_heartbeat()
    assert clears == [state]


def test_replaced_cluster_state_starts_a_new_failure_streak(tmp_path, monkeypatch):
    first = _worker(tmp_path, monkeypatch)
    second = _worker(tmp_path, monkeypatch)
    box = {"state": first}
    client = SimpleNamespace(
        post=lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(503))
    )
    service, marks, _clears = _service(box, client)

    service.post_heartbeat()
    box["state"] = second
    service.post_heartbeat()

    assert marks == []


def test_probe_latencies_and_concurrent_state_save_do_not_deadlock(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(
        role="master",
        node_id="master",
        members={
            "master": {"node_id": "master", "role": "master", "host": "10.0.0.1"},
            "w1": {"node_id": "w1", "role": "worker", "host": "10.0.0.2", "control_port": 18765},
        },
    )
    in_http = threading.Event()
    proceed_http = threading.Event()

    def slow_get(*_args, **_kwargs):
        in_http.set()
        assert proceed_http.wait(timeout=2.0)
        return SimpleNamespace(status_code=200)

    service, _marks, _clears = _service({"state": state}, SimpleNamespace(get=slow_get))
    errors: list[BaseException] = []

    def run_probe():
        try:
            service.probe_latencies()
            state.save()
        except BaseException as exc:
            errors.append(exc)

    def run_heartbeat_save():
        try:
            assert in_http.wait(timeout=2.0)
            state.save()
            proceed_http.set()
        except BaseException as exc:
            errors.append(exc)
            proceed_http.set()

    t1 = threading.Thread(target=run_probe)
    t2 = threading.Thread(target=run_heartbeat_save)
    t1.start()
    t2.start()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert not t1.is_alive(), "probe_latencies + state.save deadlocked"
    assert not t2.is_alive(), "concurrent state.save deadlocked"
    assert errors == []
    assert state.members["w1"].get("latency_ms") is not None


def test_heartbeat_refreshes_last_seen_before_liveness_sweep(tmp_path, monkeypatch):
    import time
    from kllm.modules.cluster.interactors.membership import LIVENESS_TIMEOUT_S, sweep_liveness
    from kllm.modules.cluster.router_http import MembershipHandlers
    from kllm.shared.schemas import RegisterBody

    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    now = time.time()
    state = ClusterState(
        role="master",
        node_id="master",
        members={
            "master": {"node_id": "master", "role": "master", "last_seen": now},
            "w1": {
                "node_id": "w1",
                "role": "worker",
                "host": "10.0.0.2",
                "last_seen": now - (LIVENESS_TIMEOUT_S + 30),
            },
        },
    )
    handlers = MembershipHandlers(
        lambda: state,
        sync_liveness=lambda **_: sweep_liveness(state),
        resources=SimpleNamespace(prune=lambda _: None),
        resource_probe_available=lambda: False,
        store=lambda: SimpleNamespace(end_node=lambda _: None),
        inference_activity=lambda: {},
    )
    body = RegisterBody(node_id="w1", host="10.0.0.2")
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.2"))

    out = handlers.heartbeat(body, request)

    assert out["accepted"] is True
    assert "w1" in state.members

