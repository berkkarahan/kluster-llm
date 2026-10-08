"""Heartbeat contact has to survive one lost packet without a false alarm."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException

from kllm.agent.services.membership_heartbeat import MembershipHeartbeatService
from kllm.state import ClusterState


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
