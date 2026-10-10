"""Build dashboard log snapshots from local and proxied cluster sources."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from kllm.modules.logs.interactors import cluster_log
from kllm.modules.live.interactors.live import SourceDenied
from kllm.modules.live.interactors.live_ws import apply_upstream_log_batch, new_worker_log_state, worker_log_snapshot
from kllm.shared.state import ClusterState

UpstreamLogs = dict[str, Any] | StreamingResponse | None
LogProxy = Callable[[int, bool, str | None, str | None, str | None], UpstreamLogs]
LocalSnapshot = Callable[..., dict[str, Any]]


class LiveLogsHandler:
    """Maintain and read the dashboard log mirror exposed by worker nodes."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        app_state: Any,
        snapshot: LocalSnapshot,
        proxy: LogProxy,
    ) -> None:
        self._state = state
        self._app_state = app_state
        self._snapshot = snapshot
        self._proxy = proxy

    def read(self, after: str | None) -> dict[str, Any]:
        """Return a detached log snapshot, refreshing the worker mirror if due."""
        if after is not None:
            cluster_log.parse_cursor(after)
        state = self._state()
        if state.role != "worker":
            snapshot = self._snapshot(n=200, node=None, source=None, after=after)
            continuity_id = getattr(self._app_state, "live_log_continuity", None)
            if continuity_id is None:
                continuity_id = str(uuid.uuid4())
                self._app_state.live_log_continuity = continuity_id
            snapshot.update(source="cluster", continuity_id=continuity_id, discontinuity=None)
            return snapshot

        mirror_state = getattr(self._app_state, "live_log_state", None)
        if mirror_state is None:
            mirror_state = new_worker_log_state()
            self._app_state.live_log_state = mirror_state

        with mirror_state["lock"]:
            flight = mirror_state["flight"]
            retry_delay = 30.0 if mirror_state.get("error") == "denied" else 0.5
            owner = (
                flight is None and time.monotonic() - mirror_state["last_acquired"] >= retry_delay
            )
            if owner:
                flight = threading.Event()
                mirror_state["flight"] = flight

        if owner:
            self._refresh_worker_mirror(mirror_state, flight)
        elif flight is not None:
            flight.wait(2)

        return worker_log_snapshot(mirror_state, after, self._state().node_id)

    def _refresh_worker_mirror(self, mirror_state: dict[str, Any], flight: threading.Event) -> None:
        failure: Exception | None = None
        try:
            try:
                upstream = self._proxy(500, False, None, None, mirror_state["upstream_cursor"])
                if isinstance(upstream, StreamingResponse):
                    raise RuntimeError("Master log source returned an invalid response")
            except HTTPException as exc:
                if exc.status_code in (401, 403):
                    raise SourceDenied from None
                raise RuntimeError("Master log source returned an error") from None
            apply_upstream_log_batch(mirror_state, upstream)
        except Exception as exc:
            failure = exc
            with mirror_state["lock"]:
                mirror_state["error"] = "denied" if isinstance(exc, SourceDenied) else "unavailable"
                mirror_state["last_acquired"] = time.monotonic()
        finally:
            with mirror_state["lock"]:
                mirror_state["flight"] = None
                flight.set()
        if failure is not None:
            raise failure
