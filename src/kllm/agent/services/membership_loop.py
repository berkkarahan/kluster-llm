"""Membership tick policy and its resilient background thread lifecycle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from kllm.state import ClusterState

LOGGER = logging.getLogger(__name__)


class MembershipLoop:
    """Run role-specific membership work without delaying the control-plane cadence."""

    def __init__(
        self,
        *,
        stop: threading.Event,
        state: Callable[[], ClusterState],
        interval_seconds: float,
        synchronize_master: Callable[[], object],
        post_worker_heartbeat: Callable[[], None],
        schedule_worker_probe: Callable[[], None],
        schedule_capacity_refresh: Callable[[], None],
        ensure_worker_runtime: Callable[[], None] | None = None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self._stop = stop
        self._state = state
        self._interval = interval_seconds
        self._synchronize_master = synchronize_master
        self._post_worker_heartbeat = post_worker_heartbeat
        self._schedule_worker_probe = schedule_worker_probe
        self._schedule_capacity_refresh = schedule_capacity_refresh
        self._ensure_worker_runtime = ensure_worker_runtime
        self._thread_factory = thread_factory
        self._start_lock = threading.Lock()
        self._started = False

    def tick(self) -> None:
        """Run the role-specific fast path, then enqueue slow capacity work."""
        role = self._state().role
        if role == "master":
            self._synchronize_master()
            self._schedule_worker_probe()
        elif role == "worker":
            self._post_worker_heartbeat()
            if self._ensure_worker_runtime is not None:
                self._ensure_worker_runtime()
        self._schedule_capacity_refresh()

    def run(self) -> None:
        """Keep ticking until stopped; log failures and continue on the next interval."""
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception as exc:
                LOGGER.warning("membership tick failed (%s)", type(exc).__name__)

    def start(self) -> bool:
        """Start the daemon once; return false if it was already started."""
        with self._start_lock:
            if self._started:
                return False
            self._started = True
        try:
            self._thread_factory(
                target=self.run,
                name="kllm-membership",
                daemon=True,
            ).start()
        except BaseException:
            with self._start_lock:
                self._started = False
            raise
        return True
