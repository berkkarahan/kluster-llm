"""Background relay of worker log events to the cluster master."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from kllm.modules.logs.interactors import cluster_log
from kllm.shared.state import ClusterState


class LogRelayService:
    """Run the log hub's periodic cluster relay outside the HTTP boundary."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        stop: threading.Event,
        master_control_base: Callable[[], str],
        *,
        hub: cluster_log.LogHub = cluster_log.HUB,
    ) -> None:
        self._state = state
        self._stop = stop
        self._master_control_base = master_control_base
        self._hub = hub
        self._start_lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        """Start the relay thread once for the lifetime of the service."""
        with self._start_lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self.run, name="kllm-logs", daemon=True).start()

    def run(self) -> None:
        """Prime offsets and periodically relay logs until application shutdown."""
        self._hub.prime_offsets()
        while not self._stop.wait(cluster_log.FLUSH_INTERVAL_S):
            try:
                self.tick()
            except Exception as exc:
                logging.getLogger(__name__).debug("log relay tick failed (%s)", type(exc).__name__)

    def tick(self) -> None:
        """Perform one role-aware log relay iteration."""
        state = self._state()
        self._hub.set_enabled(state.logging_enabled)
        if state.role == "master":
            self._hub.ensure_journal_loaded()
        master_base = (
            self._master_control_base() if state.role == "worker" and state.master_host else None
        )
        self._hub.relay_tick(
            node_id=state.node_id or "unknown",
            role=state.role,
            master_base=master_base,
            join_key=state.join_key,
        )
