"""Transient ordering checks independent of GPU/native hardware."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from kllm import usage


def test_generation_reset_is_idempotent_and_old_messages_cannot_restore_state():
    state = usage.ResourceState()
    request = dict(request_id=uuid.uuid4().hex, previous_generation=None)
    first = state.establish("worker", request)
    assert state.install("worker", dict(generation=first, sequence=5))
    assert state.establish("worker", request) == first
    assert state.sessions["worker"]["sequence"] == 5
    assert not state.install("worker", dict(generation=first, sequence=4))
    second = state.establish("worker", dict(request_id=uuid.uuid4().hex, previous_generation=first))
    assert second != first and "worker" not in state.latest
    with pytest.raises(usage.ResourceConflict):
        state.install("worker", dict(generation=first, sequence=6))
    with pytest.raises(usage.ResourceConflict):
        state.establish("worker", request)
    assert state.sessions["worker"]["generation"] == second
    state.prune([])
    assert not state.sessions and not state.latest


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"request_id": "a", "previous_generation": None},
        {"request_id": "f" * 32, "previous_generation": True},
        {"request_id": "f" * 32, "previous_generation": None, "extra": 1},
    ],
)
def test_invalid_reset_does_not_mutate_state(payload):
    state = usage.ResourceState()
    with pytest.raises(ValueError):
        state.establish("worker", payload)
    assert state.sessions == {}


def test_future_worker_clock_cannot_keep_cached_resources_fresh():
    now = datetime.now(timezone.utc)
    future = usage.utc(now + timedelta(seconds=29))
    snapshot = dict(
        completed_at=future,
        host=dict(memory=dict(used=dict(value=1, status="measured", observed_at=future))),
        devices=[],
        processes=[],
    )
    age, freshness = usage.resource_freshness(snapshot, True, now, residence=11)
    assert age == 11
    assert freshness == "stale"


def test_legacy_identity_adoption_requires_the_saved_start(tmp_path, monkeypatch):
    from kllm import runtime

    monkeypatch.setattr(runtime, "runtime_path", lambda: tmp_path / "runtime.json")
    monkeypatch.setattr(runtime, "ensure_home", lambda: None)
    monkeypatch.setattr(runtime.native, "resource_probe_available", lambda: True)
    monkeypatch.setattr(
        runtime.native,
        "require_native",
        lambda: SimpleNamespace(
            _process_identity=lambda pid: ("exact-token", "2026-09-30T00:00:00.000001Z")
        ),
    )
    runtime.save(runtime.RuntimeInfo(children=[runtime.ChildProc(42, "llama-server")]))
    assert not runtime.adopt_child_identity(42, "2026-09-30T00:00:00.000000Z")
    assert runtime.load().children[0].start_token is None
    assert runtime.adopt_child_identity(42, "2026-09-30T00:00:00.000001Z")
    assert runtime.load().children[0].start_token == "exact-token"


def test_new_sequence_cannot_overwrite_a_newer_resource_observation():
    state = usage.ResourceState()
    generation = state.establish(
        "worker", dict(request_id=uuid.uuid4().hex, previous_generation=None)
    )
    first = dict(
        generation=generation,
        sequence=1,
        host=dict(cpu=dict(value=10, observed_at="2026-09-30T00:00:02Z")),
    )
    assert state.install("worker", first)
    late = dict(
        generation=generation,
        sequence=2,
        host=dict(cpu=dict(value=90, observed_at="2026-09-30T00:00:01Z")),
    )
    assert not state.install("worker", late)
    assert state.sessions["worker"]["sequence"] == 1
    assert state.latest["worker"] is first
