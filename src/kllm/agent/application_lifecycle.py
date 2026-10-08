"""FastAPI lifecycle coordination for the composed kllm agent application."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from kllm.agent.lifecycle_bus import (
    LifecycleBus,
    LifecycleEvent,
    LifecycleNotice,
    publish_lifecycle,
)
from kllm.http_clients import INFERENCE_LIMITS
from kllm.membership_state import mark_master_unreachable
from kllm.state import ClusterState


@dataclass(frozen=True)
class AgentLifecycleDependencies:
    """External state and resource operations required by agent lifespan."""

    state: Callable[[], ClusterState]
    replace_state: Callable[[ClusterState], None]
    upgrade_pending: Callable[[], bool]
    stop_children: Callable[[], Any]
    load_state: Callable[[], ClusterState]
    reconcile_plan: Callable[[ClusterState], Any]
    reset_usage_state: Callable[[], None]
    clear_resources: Callable[[], None]
    clear_remote_resources: Callable[[], None]
    control_client: Callable[[], Any]
    open_usage_store: Callable[[], Any]
    background_stop: threading.Event
    adopt_child_identity: Callable[[int | None, str | None], bool]
    ensure_master_member: Callable[..., Any]
    create_live_feed: Callable[[], Any]
    background_enabled: Callable[[], bool]
    start_membership: Callable[[], None]
    start_log_relay: Callable[[], None]
    usage_loop: Callable[[], None]
    thread_factory: Callable[..., threading.Thread]
    close_resource_probes: Callable[[], None]
    sample_diagnostics: Callable[[], Any]
    close_clients: Callable[[], None]
    close_usage_store: Callable[[Any], None]
    stop_live_feed: Callable[[Any], Any]
    start_managed_network: Callable[[Any], Awaitable[None]] | None = None
    stop_managed_network: Callable[[Any], Awaitable[None]] | None = None
    platform: str = sys.platform
    async_client_factory: Callable[..., Any] = httpx.AsyncClient


class _LifespanSequence:
    """Ordered start and stop listeners for one application lifespan."""

    def __init__(self, deps: AgentLifecycleDependencies, app: Any) -> None:
        self._deps = deps
        self._app = app
        self.usage_thread: threading.Thread | None = None
        self.diagnostics_task: asyncio.Task[Any] | None = None
        self._inference_client: Any = None

    def inference_client_open(self) -> bool:
        return self._inference_client is not None

    def subscribe(self, bus: LifecycleBus) -> None:
        started = LifecycleEvent.APP_STARTED
        stopped = LifecycleEvent.APP_STOPPED
        for listener in (
            self._upgrade,
            self._reset_resources,
            self._open_usage_store,
            self._prepare_membership,
            self._start_network,
            self._open_live_feed,
            self._start_background,
            self._open_inference_client,
            self._start_diagnostics,
        ):
            bus.subscribe(started, listener)
        for listener in (
            self._signal_stop,
            self._stop_network,
            self._join_usage,
            self._cancel_diagnostics,
            self._close_clients,
            self._close_usage_store,
            self._stop_live_feed,
            self._close_inference_client,
        ):
            bus.subscribe(stopped, listener)

    def _upgrade(self, _notice: LifecycleNotice) -> None:
        deps = self._deps
        if not deps.upgrade_pending():
            return
        deps.stop_children()
        state = deps.load_state()
        deps.replace_state(state)
        deps.reconcile_plan(state)

    def _reset_resources(self, _notice: LifecycleNotice) -> None:
        deps = self._deps
        deps.reset_usage_state()
        deps.clear_resources()
        deps.clear_remote_resources()
        # A persisted worker mirror is not evidence of contact in this process.
        # Keep it for display/recovery until a heartbeat confirms the master.
        if deps.state().role == "worker":
            mark_master_unreachable(deps.state())
        deps.control_client()

    def _open_usage_store(self, _notice: LifecycleNotice) -> None:
        try:
            self._deps.open_usage_store()
        except (OSError, sqlite3.Error):
            return  # The private history API reports 503 while inference can still start.

    def _prepare_membership(self, _notice: LifecycleNotice) -> None:
        deps = self._deps
        deps.background_stop.clear()
        if deps.platform in ("win32", "darwin"):
            state = deps.state()
            member = state.members.get(state.node_id) or {}
            deps.adopt_child_identity(state.process_pid, member.get("process_started_at"))
        deps.ensure_master_member(persist=True)

    async def _start_network(self, _notice: LifecycleNotice) -> None:
        if self._deps.start_managed_network is not None:
            await self._deps.start_managed_network(self._app)

    def _open_live_feed(self, _notice: LifecycleNotice) -> None:
        self._app.state.live_feed = self._deps.create_live_feed()

    def _start_background(self, _notice: LifecycleNotice) -> None:
        deps = self._deps
        if not deps.background_enabled():
            return
        deps.start_membership()
        deps.start_log_relay()
        self.usage_thread = deps.thread_factory(
            target=deps.usage_loop,
            name="kllm-usage",
            daemon=True,
        )
        self.usage_thread.start()

    async def _open_inference_client(self, _notice: LifecycleNotice) -> None:
        client = self._deps.async_client_factory(
            timeout=httpx.Timeout(None, connect=5.0),
            trust_env=False,
            limits=INFERENCE_LIMITS,
        )
        self._app.state.inference_client = await client.__aenter__()
        self._inference_client = client

    def _start_diagnostics(self, _notice: LifecycleNotice) -> None:
        if os.getenv("KLLM_DEBUG_HARDWARE", "").lower() not in ("1", "true", "yes"):
            return
        self.diagnostics_task = asyncio.create_task(self._deps.sample_diagnostics())

    def _signal_stop(self, _notice: LifecycleNotice) -> None:
        self._deps.background_stop.set()

    async def _stop_network(self, _notice: LifecycleNotice) -> None:
        if self._deps.stop_managed_network is not None:
            await self._deps.stop_managed_network(self._app)

    async def _join_usage(self, _notice: LifecycleNotice) -> None:
        if self.usage_thread is None:
            return
        await asyncio.to_thread(self._deps.close_resource_probes)
        await asyncio.to_thread(self.usage_thread.join, 4)

    async def _cancel_diagnostics(self, _notice: LifecycleNotice) -> None:
        task = self.diagnostics_task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _close_clients(self, _notice: LifecycleNotice) -> None:
        self._deps.close_clients()

    def _close_usage_store(self, _notice: LifecycleNotice) -> None:
        self._deps.close_usage_store(self._app)

    async def _stop_live_feed(self, _notice: LifecycleNotice) -> None:
        if not hasattr(self._app.state, "live_feed"):
            return
        await asyncio.to_thread(self._deps.stop_live_feed, self._app.state.live_feed)
        del self._app.state.live_feed

    async def _close_inference_client(self, _notice: LifecycleNotice) -> None:
        client = self._inference_client
        if client is None:
            return
        self._inference_client = None
        await client.__aexit__(None, None, None)


class AgentLifespan:
    """Own startup and shutdown sequencing for application-scoped resources."""

    def __init__(self, dependencies: AgentLifecycleDependencies) -> None:
        self._deps = dependencies

    @asynccontextmanager
    async def run(self, app: Any) -> AsyncIterator[None]:
        """Prepare shared state, expose the app, then close resources in order."""
        sequence = _LifespanSequence(self._deps, app)
        bus = LifecycleBus()
        sequence.subscribe(bus)
        ready = False
        try:
            await bus.emit(LifecycleNotice(LifecycleEvent.APP_STARTED))
            ready = True
            publish_lifecycle(LifecycleEvent.APP_STARTED, _node_id(self._deps))
            yield
        finally:
            if ready:
                await bus.emit(LifecycleNotice(LifecycleEvent.APP_STOPPED), isolate=True)
                publish_lifecycle(LifecycleEvent.APP_STOPPED, _node_id(self._deps), isolate=True)
            elif sequence.inference_client_open():
                # Startup failed after the inference client opened. Close that
                # client only; the earlier steps have no matching rollback.
                await sequence._close_inference_client(LifecycleNotice(LifecycleEvent.APP_STOPPED))


def _node_id(deps: AgentLifecycleDependencies) -> str | None:
    node_id = deps.state().node_id
    return node_id or None
