from __future__ import annotations

import pytest

from kllm.agent.lifecycle_bus import (
    LifecycleBus,
    LifecycleEvent,
    LifecycleNotice,
    lifecycle_bus,
    publish_lifecycle,
)


def test_listeners_run_in_subscription_order_until_one_fails():
    bus = LifecycleBus()
    calls: list[str] = []

    bus.subscribe(LifecycleEvent.WORKER_JOINED, lambda _notice: calls.append("first"))
    bus.subscribe(LifecycleEvent.WORKER_JOINED, lambda _notice: calls.append("second"))

    def fail(_notice: LifecycleNotice) -> None:
        calls.append("fail")
        raise RuntimeError("listener failed")

    bus.subscribe(LifecycleEvent.WORKER_JOINED, fail)
    bus.subscribe(LifecycleEvent.WORKER_JOINED, lambda _notice: calls.append("after"))

    with pytest.raises(RuntimeError, match="listener failed"):
        bus.publish(LifecycleNotice(LifecycleEvent.WORKER_JOINED, "worker-a"))

    assert calls == ["first", "second", "fail"]


def test_unsubscribe_and_isolated_failures_do_not_stop_later_listeners():
    bus = LifecycleBus()
    calls: list[str] = []
    def fail(_notice: LifecycleNotice) -> None:
        raise RuntimeError("observer failed")

    remove_first = bus.subscribe(LifecycleEvent.REQUEST_RECEIVED, lambda _notice: calls.append("first"))
    bus.subscribe(LifecycleEvent.REQUEST_RECEIVED, fail)
    bus.subscribe(LifecycleEvent.REQUEST_RECEIVED, lambda _notice: calls.append("third"))
    remove_first()

    bus.publish(LifecycleNotice(LifecycleEvent.REQUEST_RECEIVED, "/v1/chat/completions"), isolate=True)

    assert calls == ["third"]


def test_publish_rejects_async_listeners_unless_isolated():
    bus = LifecycleBus()

    async def listener(_notice: LifecycleNotice) -> None:
        return None

    bus.subscribe(LifecycleEvent.CLUSTER_STARTED, listener)
    with pytest.raises(TypeError, match="emit"):
        bus.publish(LifecycleNotice(LifecycleEvent.CLUSTER_STARTED))

    isolated = LifecycleBus()
    calls: list[str] = []
    isolated.subscribe(LifecycleEvent.RESPONSE_SENT, listener)
    isolated.subscribe(LifecycleEvent.RESPONSE_SENT, lambda _notice: calls.append("sync"))
    isolated.publish(LifecycleNotice(LifecycleEvent.RESPONSE_SENT, "/v1/responses"), isolate=True)
    assert calls == ["sync"]


@pytest.mark.asyncio
async def test_emit_awaits_listeners_in_order():
    bus = LifecycleBus()
    calls: list[str] = []

    async def first(_notice: LifecycleNotice) -> None:
        calls.append("first")

    def second(_notice: LifecycleNotice) -> None:
        calls.append("second")

    async def third(_notice: LifecycleNotice) -> None:
        calls.append("third")

    bus.subscribe(LifecycleEvent.APP_STOPPED, first)
    bus.subscribe(LifecycleEvent.APP_STOPPED, second)
    bus.subscribe(LifecycleEvent.APP_STOPPED, third)

    await bus.emit(LifecycleNotice(LifecycleEvent.APP_STOPPED), isolate=True)

    assert calls == ["first", "second", "third"]


def test_process_bus_publish_reaches_a_subscriber():
    seen: list[str | None] = []
    unsubscribe = lifecycle_bus.subscribe(
        LifecycleEvent.DEPLOY_PLANNED,
        lambda notice: seen.append(notice.subject),
    )
    try:
        publish_lifecycle(LifecycleEvent.DEPLOY_PLANNED, "model.gguf")
    finally:
        unsubscribe()
    assert seen == ["model.gguf"]
