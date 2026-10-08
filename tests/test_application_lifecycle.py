from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from kllm import http_clients
from kllm.agent.application_lifecycle import AgentLifecycleDependencies, AgentLifespan
from kllm.agent.lifecycle_bus import LifecycleEvent, LifecycleNotice, lifecycle_bus
from kllm.state import ClusterState


@pytest.fixture(autouse=True)
def _hardware_diagnostics_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KLLM_DEBUG_HARDWARE", raising=False)


class _InferenceClient:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def __aenter__(self) -> str:
        self._calls.append("client-open")
        return "inference-client"

    async def __aexit__(self, *_args: object) -> None:
        self._calls.append("client-close")


class _UsageThread:
    def __init__(self, calls: list[str], **kwargs: object) -> None:
        self.calls = calls
        self.kwargs = kwargs

    def start(self) -> None:
        self.calls.append("usage-start")

    def join(self, timeout: float | None = None) -> None:
        self.calls.append(f"usage-join:{timeout}")


def _quiet_state(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> ClusterState:
    state = ClusterState(**kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr(state, "save", lambda: None)
    return state


def _lifespan(
    initial_state: ClusterState,
    calls: list[str],
    **overrides: object,
) -> tuple[AgentLifespan, SimpleNamespace, threading.Event]:
    feed = SimpleNamespace()
    stop = overrides.pop("background_stop", None)
    if not isinstance(stop, threading.Event):
        stop = threading.Event()

    def ensure_master_member(**kwargs: object) -> None:
        calls.append(f"ensure-master:{kwargs.get('persist')}")

    def create_live_feed() -> SimpleNamespace:
        calls.append("live-feed")
        return feed

    async def idle_diagnostics() -> None:
        calls.append("diagnostics-enter")
        await asyncio.Event().wait()

    values: dict[str, object] = {
        "state": lambda: initial_state,
        "replace_state": lambda _state: calls.append("replace-state"),
        "upgrade_pending": lambda: False,
        "stop_children": lambda: calls.append("stop-children"),
        "load_state": lambda: initial_state,
        "reconcile_plan": lambda _state: calls.append("reconcile"),
        "reset_usage_state": lambda: calls.append("reset-usage"),
        "clear_resources": lambda: calls.append("clear-resources"),
        "clear_remote_resources": lambda: calls.append("clear-remote"),
        "control_client": lambda: calls.append("control-client"),
        "open_usage_store": lambda: calls.append("open-store"),
        "background_stop": stop,
        "adopt_child_identity": lambda *_args: calls.append("adopt"),
        "ensure_master_member": ensure_master_member,
        "create_live_feed": create_live_feed,
        "background_enabled": lambda: False,
        "start_membership": lambda: calls.append("start-membership"),
        "start_log_relay": lambda: calls.append("start-log-relay"),
        "usage_loop": lambda: calls.append("usage-loop"),
        "thread_factory": lambda **kwargs: _UsageThread(calls, **kwargs),
        "close_resource_probes": lambda: calls.append("close-probes"),
        "sample_diagnostics": idle_diagnostics,
        "close_clients": lambda: calls.append("close-clients"),
        "close_usage_store": lambda _app: calls.append("close-store"),
        "stop_live_feed": lambda _feed: calls.append("stop-feed"),
        "platform": "linux",
        "async_client_factory": lambda **_kwargs: _InferenceClient(calls),
    }
    values.update(overrides)
    dependencies = AgentLifecycleDependencies(**values)  # type: ignore[arg-type]
    return AgentLifespan(dependencies), feed, stop


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["idle", "worker"])
async def test_agent_lifespan_closes_app_resources_when_application_fails(role, monkeypatch):
    state = ClusterState(node_id="node-1", role=role, members={"old-master": {"role": "master"}})
    monkeypatch.setattr(state, "save", lambda: None)
    calls: list[str] = []
    seen: dict = {}
    feed = SimpleNamespace()

    class AsyncClientContext:
        async def __aenter__(self):
            calls.append("client-open")
            return "inference-client"

        async def __aexit__(self, *_args):
            calls.append("client-close")

    async def diagnostics() -> None:
        await asyncio.Event().wait()

    dependencies = AgentLifecycleDependencies(
        state=lambda: state,
        replace_state=lambda _state: calls.append("replace-state"),
        upgrade_pending=lambda: False,
        stop_children=lambda: calls.append("stop-children"),
        load_state=lambda: state,
        reconcile_plan=lambda _state: None,
        reset_usage_state=lambda: calls.append("reset-usage"),
        clear_resources=lambda: calls.append("clear-resources"),
        clear_remote_resources=lambda: calls.append("clear-remote"),
        control_client=lambda: calls.append("control-client"),
        open_usage_store=lambda: calls.append("open-store"),
        background_stop=threading.Event(),
        adopt_child_identity=lambda *_args: False,
        ensure_master_member=lambda **_kwargs: calls.append("ensure-master"),
        create_live_feed=lambda: feed,
        background_enabled=lambda: False,
        start_membership=lambda: pytest.fail("background workers are disabled"),
        start_log_relay=lambda: pytest.fail("background workers are disabled"),
        usage_loop=lambda: pytest.fail("background workers are disabled"),
        thread_factory=lambda **_kwargs: pytest.fail("background workers are disabled"),
        close_resource_probes=lambda: calls.append("close-probes"),
        sample_diagnostics=diagnostics,
        close_clients=lambda: calls.append("close-clients"),
        close_usage_store=lambda _app: calls.append("close-store"),
        stop_live_feed=lambda _feed: calls.append("stop-feed"),
        platform="linux",
        async_client_factory=lambda **kwargs: seen.update(kwargs) or AsyncClientContext(),
    )
    app = SimpleNamespace(state=SimpleNamespace())
    lifespan = AgentLifespan(dependencies)

    with pytest.raises(RuntimeError, match="request failed"):
        async with lifespan.run(app):
            assert ("master heartbeat unreachable" in state.netsec_warnings) == (role == "worker")
            assert "old-master" in state.members
            assert app.state.inference_client == "inference-client"
            assert seen["limits"] is http_clients.INFERENCE_LIMITS
            assert seen["limits"].keepalive_expiry == 120.0
            assert seen["trust_env"] is False
            assert seen["timeout"].connect == 5.0
            assert seen["timeout"].read is None
            assert app.state.live_feed is feed
            raise RuntimeError("request failed")

    assert calls == [
        "reset-usage",
        "clear-resources",
        "clear-remote",
        "control-client",
        "open-store",
        "ensure-master",
        "client-open",
        "close-clients",
        "close-store",
        "stop-feed",
        "client-close",
    ]
    assert dependencies.background_stop.is_set()
    assert not hasattr(app.state, "live_feed")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["worker", "idle"])
async def test_pending_upgrade_installs_loaded_state_before_startup(role, monkeypatch):
    current = _quiet_state(monkeypatch, node_id="stale", role="master")
    loaded = _quiet_state(
        monkeypatch,
        node_id="node-1",
        role=role,
        members={"old-master": {"role": "master"}},
    )
    holder = {"state": current}
    reconciled: list[ClusterState] = []
    calls: list[str] = []

    def replace_state(new: ClusterState) -> None:
        calls.append("replace-state")
        holder["state"] = new

    lifespan, _feed, _stop = _lifespan(
        current,
        calls,
        state=lambda: holder["state"],
        replace_state=replace_state,
        upgrade_pending=lambda: True,
        load_state=lambda: loaded,
        reconcile_plan=reconciled.append,
        stop_children=lambda: calls.append("stop-children"),
    )
    app = SimpleNamespace(state=SimpleNamespace())

    async with lifespan.run(app):
        assert holder["state"] is loaded
        assert app.state.inference_client == "inference-client"

    assert calls[:3] == ["stop-children", "replace-state", "reset-usage"]
    assert reconciled == [loaded]
    assert "old-master" in loaded.members
    assert ("master heartbeat unreachable" in loaded.netsec_warnings) is (role == "worker")
    assert current.netsec_warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("disk"), sqlite3.OperationalError("locked")])
async def test_usage_store_open_failure_still_serves_inference(error, monkeypatch):
    state = _quiet_state(monkeypatch, node_id="node-1")
    calls: list[str] = []

    def open_usage_store() -> None:
        calls.append("open-store")
        raise error

    lifespan, feed, _stop = _lifespan(state, calls, open_usage_store=open_usage_store)
    app = SimpleNamespace(state=SimpleNamespace())

    async with lifespan.run(app):
        assert app.state.inference_client == "inference-client"
        assert app.state.live_feed is feed

    assert "close-store" in calls
    assert calls[-1] == "client-close"
    assert not hasattr(app.state, "live_feed")


@pytest.mark.asyncio
async def test_unexpected_usage_store_error_aborts_before_serving(monkeypatch):
    state = _quiet_state(monkeypatch, node_id="node-1")
    calls: list[str] = []

    def open_usage_store() -> None:
        raise RuntimeError("corrupt")

    lifespan, _feed, stop = _lifespan(state, calls, open_usage_store=open_usage_store)
    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="corrupt"):
        async with lifespan.run(app):
            pytest.fail("inference became reachable")

    assert calls == ["reset-usage", "clear-resources", "clear-remote", "control-client"]
    assert not hasattr(app.state, "inference_client")
    assert not hasattr(app.state, "live_feed")
    assert not stop.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "members", "expected"),
    [
        ("win32", {"node-1": {"process_started_at": "2026-01-01T00:00:00Z"}}, (77, "2026-01-01T00:00:00Z")),
        ("darwin", {"node-1": {"process_started_at": "2026-01-01T00:00:00Z"}}, (77, "2026-01-01T00:00:00Z")),
        ("win32", {}, (77, None)),
        ("linux", {"node-1": {"process_started_at": "2026-01-01T00:00:00Z"}}, None),
    ],
)
async def test_only_desktop_startup_adopts_persisted_child_identity(
    platform, members, expected, monkeypatch
):
    state = _quiet_state(monkeypatch, node_id="node-1", process_pid=77, members=members)
    adopted: list[tuple[int | None, str | None]] = []
    calls: list[str] = []

    def adopt(pid: int | None, started: str | None) -> bool:
        adopted.append((pid, started))
        return True

    lifespan, _feed, _stop = _lifespan(
        state,
        calls,
        platform=platform,
        adopt_child_identity=adopt,
    )
    app = SimpleNamespace(state=SimpleNamespace())

    async with lifespan.run(app):
        assert app.state.inference_client == "inference-client"

    assert adopted == ([] if expected is None else [expected])


@pytest.mark.asyncio
async def test_managed_network_start_failure_does_not_serve_or_stop(monkeypatch):
    state = _quiet_state(monkeypatch, node_id="node-1")
    calls: list[str] = []
    stop = threading.Event()
    stop.set()

    async def start_managed_network(_app: object) -> None:
        calls.append("network-start")
        raise RuntimeError("adapter missing")

    async def stop_managed_network(_app: object) -> None:
        calls.append("network-stop")

    lifespan, _feed, stop = _lifespan(
        state,
        calls,
        background_stop=stop,
        background_enabled=lambda: True,
        start_managed_network=start_managed_network,
        stop_managed_network=stop_managed_network,
    )
    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="adapter missing"):
        async with lifespan.run(app):
            pytest.fail("inference became reachable")

    assert "network-start" in calls
    assert "network-stop" not in calls
    assert "start-membership" not in calls
    assert "client-open" not in calls
    assert not hasattr(app.state, "live_feed")
    assert not stop.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "enabled"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("YES", True),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
async def test_hardware_diagnostics_follow_debug_flag(raw, enabled, monkeypatch):
    monkeypatch.setenv("KLLM_DEBUG_HARDWARE", raw)
    state = _quiet_state(monkeypatch, node_id="node-1")
    calls: list[str] = []
    started = asyncio.Event()

    async def sample_diagnostics() -> None:
        calls.append("diagnostics-start")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            calls.append("diagnostics-cancel")
            raise

    lifespan, _feed, _stop = _lifespan(state, calls, sample_diagnostics=sample_diagnostics)
    app = SimpleNamespace(state=SimpleNamespace())

    async with lifespan.run(app):
        if enabled:
            await asyncio.wait_for(started.wait(), timeout=1)
        else:
            assert "diagnostics-start" not in calls

    if enabled:
        assert "diagnostics-cancel" in calls
    else:
        assert calls.count("diagnostics-start") == 0


@pytest.mark.asyncio
async def test_background_and_network_release_after_the_app_fails(monkeypatch):
    monkeypatch.setenv("KLLM_DEBUG_HARDWARE", "1")
    state = _quiet_state(monkeypatch, node_id="node-1")
    calls: list[str] = []
    started = asyncio.Event()
    threads: list[_UsageThread] = []
    stop = threading.Event()
    stop.set()

    async def start_managed_network(app: object) -> None:
        assert not hasattr(app.state, "live_feed")
        calls.append("network-start")

    async def stop_managed_network(_app: object) -> None:
        assert stop.is_set()
        calls.append("network-stop")

    async def sample_diagnostics() -> None:
        calls.append("diagnostics-start")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            calls.append("diagnostics-cancel")
            raise

    def usage_loop() -> None:
        calls.append("usage-body")

    def thread_factory(**kwargs: object) -> _UsageThread:
        thread = _UsageThread(calls, **kwargs)
        threads.append(thread)
        return thread

    lifespan, feed, stop = _lifespan(
        state,
        calls,
        background_stop=stop,
        background_enabled=lambda: True,
        start_managed_network=start_managed_network,
        stop_managed_network=stop_managed_network,
        sample_diagnostics=sample_diagnostics,
        thread_factory=thread_factory,
        usage_loop=usage_loop,
    )
    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="request failed"):
        async with lifespan.run(app):
            await asyncio.wait_for(started.wait(), timeout=1)
            assert not stop.is_set()
            assert app.state.live_feed is feed
            assert app.state.inference_client == "inference-client"
            raise RuntimeError("request failed")

    assert len(threads) == 1
    assert threads[0].kwargs == {"target": usage_loop, "name": "kllm-usage", "daemon": True}
    assert "usage-body" not in calls
    assert calls == [
        "reset-usage",
        "clear-resources",
        "clear-remote",
        "control-client",
        "open-store",
        "ensure-master:True",
        "network-start",
        "live-feed",
        "start-membership",
        "start-log-relay",
        "usage-start",
        "client-open",
        "diagnostics-start",
        "network-stop",
        "close-probes",
        "usage-join:4",
        "diagnostics-cancel",
        "close-clients",
        "close-store",
        "stop-feed",
        "client-close",
    ]
    assert stop.is_set()
    assert not hasattr(app.state, "live_feed")


def _record_app_events() -> tuple[list[LifecycleEvent], Callable[[], None]]:
    seen: list[LifecycleEvent] = []

    def record(notice: LifecycleNotice) -> None:
        seen.append(notice.event)

    unsubscribe_started = lifecycle_bus.subscribe(LifecycleEvent.APP_STARTED, record)
    unsubscribe_stopped = lifecycle_bus.subscribe(LifecycleEvent.APP_STOPPED, record)

    def unsubscribe() -> None:
        unsubscribe_started()
        unsubscribe_stopped()

    return seen, unsubscribe


@pytest.mark.asyncio
async def test_app_events_bracket_the_serving_window(monkeypatch):
    state = _quiet_state(monkeypatch, node_id="node-1")
    seen, unsubscribe = _record_app_events()
    lifespan, _feed, _stop = _lifespan(state, [])
    app = SimpleNamespace(state=SimpleNamespace())
    try:
        async with lifespan.run(app):
            assert seen == [LifecycleEvent.APP_STARTED]
        assert seen == [LifecycleEvent.APP_STARTED, LifecycleEvent.APP_STOPPED]
    finally:
        unsubscribe()


@pytest.mark.asyncio
async def test_failed_startup_does_not_publish_app_events(monkeypatch):
    state = _quiet_state(monkeypatch, node_id="node-1")
    seen, unsubscribe = _record_app_events()

    async def start_managed_network(_app: object) -> None:
        raise RuntimeError("adapter missing")

    lifespan, _feed, _stop = _lifespan(
        state,
        [],
        start_managed_network=start_managed_network,
    )
    app = SimpleNamespace(state=SimpleNamespace())
    try:
        with pytest.raises(RuntimeError, match="adapter missing"):
            async with lifespan.run(app):
                pytest.fail("inference became reachable")
        assert seen == []
    finally:
        unsubscribe()


@pytest.mark.asyncio
async def test_shutdown_continues_after_a_cleanup_listener_fails(monkeypatch):
    state = _quiet_state(monkeypatch, node_id="node-1")
    calls: list[str] = []

    def close_clients() -> None:
        calls.append("close-clients")
        raise RuntimeError("close failed")

    lifespan, _feed, _stop = _lifespan(state, calls, close_clients=close_clients)
    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="request failed"):
        async with lifespan.run(app):
            raise RuntimeError("request failed")

    assert calls.index("close-clients") < calls.index("close-store")
    assert "stop-feed" in calls
    assert calls[-1] == "client-close"
