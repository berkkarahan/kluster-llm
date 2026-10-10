from __future__ import annotations

from unittest.mock import patch

import pytest
from kllm.shared.state import ClusterState

from kllm.modules.cluster.interactors import background_tasks
from kllm.modules.cluster.interactors.membership_loop import MembershipLoop


def test_capacity_scheduler_releases_slot_when_thread_cannot_start():
    scheduler = background_tasks.CapacityRefreshScheduler(lambda: None)

    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        def start(self) -> None:
            raise RuntimeError("thread creation failed")

    with patch.object(background_tasks.threading, "Thread", BrokenThread):
        with pytest.raises(RuntimeError, match="thread creation failed"):
            scheduler.schedule()

    assert scheduler.inflight is False


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("master", ["synchronize", "probe", "refresh"]),
        ("worker", ["heartbeat", "refresh"]),
        ("idle", ["refresh"]),
    ],
)
def test_membership_tick_orders_role_work_before_capacity(role, expected):
    events: list[str] = []
    loop = MembershipLoop(
        stop=background_tasks.threading.Event(),
        state=lambda: ClusterState(role=role),
        interval_seconds=2.0,
        synchronize_master=lambda: events.append("synchronize"),
        post_worker_heartbeat=lambda: events.append("heartbeat"),
        schedule_worker_probe=lambda: events.append("probe"),
        schedule_capacity_refresh=lambda: events.append("refresh"),
    )

    loop.tick()

    assert events == expected


def test_membership_tick_never_starts_worker_runtime():
    events: list[str] = []
    loop = MembershipLoop(
        stop=background_tasks.threading.Event(),
        state=lambda: ClusterState(role="worker"),
        interval_seconds=2.0,
        synchronize_master=lambda: events.append("synchronize"),
        post_worker_heartbeat=lambda: events.append("heartbeat"),
        ensure_worker_runtime=lambda: events.append("runtime"),
        schedule_worker_probe=lambda: events.append("probe"),
        schedule_capacity_refresh=lambda: events.append("refresh"),
    )

    loop.tick()

    assert events == ["heartbeat", "refresh"]


def test_membership_loop_logs_tick_failure_and_continues(caplog):
    state = ClusterState(role="master")
    ticks = iter((False, False, True))
    events: list[str] = []
    attempts = 0

    class StopEvent:
        def wait(self, _interval: float) -> bool:
            return next(ticks)

    def synchronize() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("internal details")
        events.append("synchronize")

    loop = MembershipLoop(
        stop=StopEvent(),  # type: ignore[arg-type]
        state=lambda: state,
        interval_seconds=2.0,
        synchronize_master=synchronize,
        post_worker_heartbeat=lambda: None,
        schedule_worker_probe=lambda: events.append("probe"),
        schedule_capacity_refresh=lambda: events.append("refresh"),
    )

    loop.run()

    assert events == ["synchronize", "probe", "refresh"]
    assert "membership tick failed (RuntimeError)" in caplog.text
    assert "internal details" not in caplog.text


def test_membership_loop_releases_start_guard_when_thread_fails_to_start():
    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        def start(self) -> None:
            raise RuntimeError("thread creation failed")

    loop = MembershipLoop(
        stop=background_tasks.threading.Event(),
        state=lambda: ClusterState(),
        interval_seconds=2.0,
        synchronize_master=lambda: None,
        post_worker_heartbeat=lambda: None,
        schedule_worker_probe=lambda: None,
        schedule_capacity_refresh=lambda: None,
        thread_factory=BrokenThread,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="thread creation failed"):
        loop.start()
    with pytest.raises(RuntimeError, match="thread creation failed"):
        loop.start()
