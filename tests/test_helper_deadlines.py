"""Portable queue/deadline behavior independent of installed helper services."""

import threading
import time

import pytest

from kllm.modules.managed_network.interactors import helper
from kllm.shared.platform.base import PlatformRefusal


def client():
    value = object.__new__(helper.HelperClient)
    value.node_id, value.account, value.lock = "fixture", "fixture", threading.Lock()
    return value


def test_queued_query_expires_without_sending_or_releasing_another_owner(monkeypatch):
    value = client()
    monkeypatch.setattr(helper, "HELPER_QUERY_TIMEOUT", 0.02)
    value._request = lambda *_args, **_kwargs: pytest.fail("Expired queued work cannot reach IPC")
    value.lock.acquire()
    try:
        with pytest.raises(PlatformRefusal) as error:
            value.request("status")
        assert error.value.code == "TIMEOUT" and value.lock.locked()
    finally:
        value.lock.release()


@pytest.mark.parametrize("action,limit", [("status", 8), ("prima_ports_apply", 45),
    ("prima_ports_renew", 45), ("prima_ports_release", 45), ("prima_ports_resolve", 45),
    ("delete", 45), ("review", 45), ("revoke", 45)])
def test_one_deadline_spans_queue_and_transport_without_changing_wire_values(action, limit):
    value = client()
    start = time.monotonic()

    def request(name, *, _deadline, **values):
        assert value.lock.locked()
        assert name == action and values == {"wire": "complete-original-signed-envelope"}
        assert start + limit <= _deadline <= time.monotonic() + limit
        return {"phase": "fixture"}

    value._request = request
    assert value.request(action, wire="complete-original-signed-envelope") == {"phase": "fixture"}
    assert not value.lock.locked()


def test_transport_failure_releases_only_its_own_queue_lock():
    value = client()

    def fail(*_args, **_kwargs):
        raise PlatformRefusal("TIMEOUT", "Fixture deadline expired")

    value._request = fail
    with pytest.raises(PlatformRefusal):
        value.request("status")
    assert not value.lock.locked()


def test_unresponsive_helper_status_keeps_timeout_distinct_from_authorization_failure():
    from types import SimpleNamespace
    from kllm.modules.managed_network.interactors.guided_setup import GuidedSetup

    value = client()

    def fail(*_args, **_kwargs):
        raise PlatformRefusal("TIMEOUT", "Local helper did not respond")

    value._request = fail
    status = value.status()
    assert status == {"ready": False, "reason": "Local helper did not respond", "error_code": "TIMEOUT"}
    guided = object.__new__(GuidedSetup)
    guided.grant = lambda: {"node_id": "fixture"}
    guided.helper = SimpleNamespace(status=lambda _: status)
    local = guided.local_status()
    assert local["error_code"] == "TIMEOUT" and local["administrator_enabled"] is False
