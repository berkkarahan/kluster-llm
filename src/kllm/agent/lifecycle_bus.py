"""In-process lifecycle bus for application, cluster, and inference transitions.

Listeners for one event run synchronously in subscription order. A listener
exception aborts the remaining listeners so a failed startup step cannot look
successful. ``isolate=True`` is for shutdown and request observation: one
listener failure is logged and the rest still run, so a subscriber cannot skip
resource cleanup or fail an inference response.
"""

from __future__ import annotations

import inspect
import logging
import threading
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

_LOG = logging.getLogger(__name__)

Listener = Callable[["LifecycleNotice"], Any]


class LifecycleEvent(str, Enum):
    """Domain transitions a subscriber can observe."""

    APP_STARTED = "app.started"
    CLUSTER_STARTED = "cluster.started"
    WORKER_JOINED = "worker.joined"
    WORKER_LEFT = "worker.left"
    DEPLOY_PLANNED = "deploy.planned"
    RUN_STARTED = "run.started"
    INFERENCE_STARTED = "inference.started"
    REQUEST_RECEIVED = "request.received"
    RESPONSE_SENT = "response.sent"
    INFERENCE_STOPPED = "inference.stopped"
    CLUSTER_STOPPED = "cluster.stopped"
    APP_STOPPED = "app.stopped"


@dataclass(frozen=True, slots=True)
class LifecycleNotice:
    """One lifecycle transition. Subscribers read live state for anything else."""

    event: LifecycleEvent
    subject: str | None = None


class LifecycleBus:
    """Synchronous observer list keyed by lifecycle event."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: dict[LifecycleEvent, list[Listener]] = defaultdict(list)

    def subscribe(self, event: LifecycleEvent, listener: Listener) -> Callable[[], None]:
        """Register ``listener`` and return an idempotent unsubscribe callable."""
        with self._lock:
            self._listeners[event].append(listener)

        def unsubscribe() -> None:
            with self._lock:
                bucket = self._listeners.get(event, [])
                try:
                    bucket.remove(listener)
                except ValueError:
                    return

        return unsubscribe

    def publish(self, notice: LifecycleNotice, *, isolate: bool = False) -> None:
        """Run sync listeners. An async listener raises unless ``isolate`` is set."""
        for listener in self._snapshot(notice.event):
            result = self._call(listener, notice, isolate=isolate)
            if not inspect.isawaitable(result):
                continue
            close = getattr(result, "close", None)
            if close is not None:
                close()
            if isolate:
                _LOG.error(
                    "ignored async lifecycle listener for %s; publish() listeners must be synchronous",
                    notice.event.value,
                )
                continue
            raise TypeError(
                f"{notice.event.value} listener returned an awaitable; use emit() instead"
            )

    async def emit(self, notice: LifecycleNotice, *, isolate: bool = False) -> None:
        """Run listeners in order, awaiting any coroutine a listener returns."""
        for listener in self._snapshot(notice.event):
            result = self._call(listener, notice, isolate=isolate)
            if inspect.isawaitable(result):
                await self._await(result, notice, isolate=isolate)

    def _snapshot(self, event: LifecycleEvent) -> tuple[Listener, ...]:
        with self._lock:
            return tuple(self._listeners.get(event, ()))

    def _call(self, listener: Listener, notice: LifecycleNotice, *, isolate: bool) -> Any:
        try:
            return listener(notice)
        except Exception:
            if not isolate:
                raise
            _LOG.exception("lifecycle listener failed for %s", notice.event.value)
            return None

    async def _await(
        self, result: Awaitable[Any], notice: LifecycleNotice, *, isolate: bool
    ) -> None:
        try:
            await result
        except Exception:
            if not isolate:
                raise
            _LOG.exception("lifecycle listener failed for %s", notice.event.value)


lifecycle_bus = LifecycleBus()


def publish_lifecycle(
    event: LifecycleEvent,
    subject: str | None = None,
    *,
    isolate: bool = False,
) -> None:
    """Publish one domain transition on the process-wide bus."""
    lifecycle_bus.publish(LifecycleNotice(event, subject), isolate=isolate)
