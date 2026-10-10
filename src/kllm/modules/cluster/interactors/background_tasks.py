"""Small schedulers for background work that must never block control-plane ticks."""

from __future__ import annotations

import threading
from collections.abc import Callable


class CapacityRefreshScheduler:
    """Allow a single asynchronous capacity refresh at a time."""

    def __init__(self, refresh: Callable[[], None]) -> None:
        self._refresh = refresh
        self._lock = threading.Lock()
        self._inflight = False

    @property
    def inflight(self) -> bool:
        with self._lock:
            return self._inflight

    def schedule(self) -> bool:
        """Start one daemon refresh, returning false when one is already active."""
        with self._lock:
            if self._inflight:
                return False
            self._inflight = True

        def run() -> None:
            try:
                self._refresh()
            finally:
                with self._lock:
                    self._inflight = False

        try:
            threading.Thread(
                target=run,
                name="kllm-capacity-refresh",
                daemon=True,
            ).start()
        except BaseException:
            with self._lock:
                self._inflight = False
            raise
        return True
