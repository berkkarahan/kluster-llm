"""Bounded, retryable batches for forwarding worker logs to the master."""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable
from typing import Any


PENDING_MAX = 8000
BATCH_MAX_LINES = 200
BATCH_MAX_BYTES = 64 * 1024


class WorkerLogForwarder:
    """Queue local log events and deliver bounded batches through an injected client."""

    def __init__(
        self,
        control_client: Callable[[], Any],
        *,
        pending_max: int = PENDING_MAX,
        batch_max_lines: int = BATCH_MAX_LINES,
        batch_max_bytes: int = BATCH_MAX_BYTES,
    ) -> None:
        self._control_client = control_client
        self._pending_max = pending_max
        self._batch_max_lines = batch_max_lines
        self._batch_max_bytes = batch_max_bytes
        self._lock = threading.Lock()
        self._pending: deque[dict[str, Any]] = deque()

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()

    def enqueue(self, events: list[dict[str, Any]]) -> None:
        with self._lock:
            self._pending.extend(events)
            self._trim_locked()

    def flush(self, master_base: str, join_key: str) -> None:
        """Send queued events until empty; restore a failed batch for a later retry."""
        url = f"{master_base.rstrip('/')}/v1/logs"
        headers = {"X-Kllm-Key": join_key}
        while True:
            with self._lock:
                if not self._pending:
                    return
                batch = self._take_batch_locked()
            if not batch:
                return
            payload = {
                "node_id": batch[0].get("node_id") or "",
                "lines": [
                    {
                        "event_id": event.get("event_id"),
                        "ts": event["ts"],
                        "source": event["source"],
                        "line": event["line"],
                    }
                    for event in batch
                ],
            }
            try:
                response = self._control_client().post(
                    url, json=payload, headers=headers, timeout=5.0
                )
                if response.status_code != 200:
                    raise RuntimeError(f"ingest {response.status_code}")
            except Exception:
                with self._lock:
                    for event in reversed(batch):
                        self._pending.appendleft(event)
                    self._trim_locked()
                return

    def _take_batch_locked(self) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        size = 0
        while self._pending and len(batch) < self._batch_max_lines:
            event = self._pending[0]
            extra = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
            if batch and size + extra > self._batch_max_bytes:
                break
            self._pending.popleft()
            batch.append(event)
            size += extra
        return batch

    def _trim_locked(self) -> None:
        while len(self._pending) > self._pending_max:
            self._pending.popleft()
