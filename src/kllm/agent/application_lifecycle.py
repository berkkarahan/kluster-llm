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


class AgentLifespan:
    """Own startup and shutdown sequencing for application-scoped resources."""

    def __init__(self, dependencies: AgentLifecycleDependencies) -> None:
        self._deps = dependencies

    @asynccontextmanager
    async def run(self, app: Any) -> AsyncIterator[None]:
        """Prepare shared state, expose the app, then close resources in order."""
        deps = self._deps
        if deps.upgrade_pending():
            deps.stop_children()
            state = deps.load_state()
            deps.replace_state(state)
            deps.reconcile_plan(state)
        deps.reset_usage_state()
        deps.clear_resources()
        deps.clear_remote_resources()
        deps.control_client()
        try:
            deps.open_usage_store()
        except (OSError, sqlite3.Error):
            pass  # The private history API reports 503 while inference can still start.
        deps.background_stop.clear()
        if deps.platform in ("win32", "darwin"):
            state = deps.state()
            member = state.members.get(state.node_id) or {}
            deps.adopt_child_identity(state.process_pid, member.get("process_started_at"))
        deps.ensure_master_member(persist=True)
        if deps.start_managed_network is not None:
            await deps.start_managed_network(app)
        app.state.live_feed = deps.create_live_feed()

        usage_thread: threading.Thread | None = None
        if deps.background_enabled():
            deps.start_membership()
            deps.start_log_relay()
            usage_thread = deps.thread_factory(
                target=deps.usage_loop,
                name="kllm-usage",
                daemon=True,
            )
            usage_thread.start()

        async with deps.async_client_factory(
            timeout=httpx.Timeout(None, connect=5), trust_env=False
        ) as client:
            app.state.inference_client = client
            diagnostics_task = (
                asyncio.create_task(deps.sample_diagnostics())
                if os.getenv("KLLM_DEBUG_HARDWARE", "").lower() in ("1", "true", "yes")
                else None
            )
            try:
                yield
            finally:
                deps.background_stop.set()
                if deps.stop_managed_network is not None:
                    await deps.stop_managed_network(app)
                if usage_thread is not None:
                    await asyncio.to_thread(deps.close_resource_probes)
                    await asyncio.to_thread(usage_thread.join, 4)
                if diagnostics_task is not None:
                    diagnostics_task.cancel()
                    await asyncio.gather(diagnostics_task, return_exceptions=True)
                deps.close_clients()
                deps.close_usage_store(app)
                if hasattr(app.state, "live_feed"):
                    await asyncio.to_thread(deps.stop_live_feed, app.state.live_feed)
                    del app.state.live_feed
