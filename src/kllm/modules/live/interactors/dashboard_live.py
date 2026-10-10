"""Compose and cache the dashboard's shared live-feed runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kllm.modules.live.interactors.live import LiveFeed
from kllm.shared.state import ClusterState


class DashboardLiveService:
    """Build one shared live feed from injected application source callbacks."""

    def __init__(
        self,
        *,
        app_state: Any,
        state: Callable[[], ClusterState],
        status: Callable[[], Any],
        coverage: Callable[[], str],
        usage: Callable[[], Any],
        load: Callable[[], Any],
        download: Callable[[], Any],
        logs: Callable[[str | None], Any],
        upstream_success: Callable[[str], str | None],
    ) -> None:
        self._app_state = app_state
        self._state = state
        self._status = status
        self._coverage = coverage
        self._usage = usage
        self._load = load
        self._download = download
        self._logs = logs
        self._upstream_success = upstream_success

    def create(self) -> LiveFeed:
        """Create a new feed for an application lifespan or isolated consumer."""
        return LiveFeed(
            {
                "status": (self._status, self._coverage),
                "usage": (self._usage, self._coverage),
                "load": (self._load, "local"),
                "download": (self._download, "local"),
            },
            self._logs,
            upstream_success=self._upstream_success,
            scope=self._scope,
        )

    def get(self) -> LiveFeed:
        """Return the lifespan-owned feed, starting its refresh scheduler once."""
        feed = getattr(self._app_state, "live_feed", None)
        if feed is None:
            feed = self.create()
            self._app_state.live_feed = feed
        feed.start()
        return feed

    def _scope(self) -> tuple[Any, ...]:
        state = self._state()
        return (
            state.role,
            state.node_id,
            state.master_host,
            state.join_key,
            state.process_pid,
        )
