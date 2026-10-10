"""Cluster log journal, local file tailing, and worker→master relay."""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Iterator

from kllm.shared import http_clients
from kllm.modules.logs.gateways.cluster_log_forwarder import (
    BATCH_MAX_BYTES as BATCH_MAX_BYTES,
    BATCH_MAX_LINES as BATCH_MAX_LINES,
    PENDING_MAX as PENDING_MAX,
    WorkerLogForwarder,
)
from kllm.modules.logs.gateways.cluster_log_store import LogJournal
from kllm.modules.logs.gateways.cluster_log_tail import LocalLogTailer
from kllm.shared.exceptions import ValidationError
from kllm.shared.state import home_dir

RING_MAX = 8000
FLUSH_INTERVAL_S = 0.5
JOURNAL_MAX_BYTES = 16 * 1024 * 1024
VALID_SOURCES = frozenset({"agent", "llama", "rpc"})
STORED_SOURCES = VALID_SOURCES | {"unknown"}
_TAG_BY_SOURCE = {
    "agent": "ui",
    "llama": "inference",
    "rpc": "inference",
    "unknown": "unclassified",
}
LOCAL_SOURCES: tuple[tuple[str, str], ...] = (
    ("agent", "agent.log"),
    ("llama", "llama-server.log"),
    ("rpc", "rpc-server.log"),
)


def _log_file(name: str) -> Path:
    return home_dir() / "logs" / name


def agent_log_path() -> Path:
    return _log_file("agent.log")


def llama_server_log_path() -> Path:
    return _log_file("llama-server.log")


def rpc_server_log_path() -> Path:
    return _log_file("rpc-server.log")


def cluster_journal_path() -> Path:
    return _log_file("cluster.jsonl")


class CursorError(ValidationError):
    """Raised for malformed, future, or otherwise unusable snapshot cursors."""


def normalize_event_id(value: Any) -> str | None:
    """Return a canonical UUID string for a supplied identity, else None."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError):
            return None
    return None


def parse_cursor(value: Any) -> tuple[str, int]:
    """Split an opaque ``<epoch-uuid>:<sequence>`` cursor, raising CursorError."""
    if not isinstance(value, str) or not value or len(value) > 128:
        raise CursorError("malformed cursor")
    epoch, sep, seq = value.partition(":")
    if not sep or not seq or not seq.isdigit():
        raise CursorError("malformed cursor")
    try:
        parsed_epoch = uuid.UUID(epoch)
    except (ValueError, AttributeError) as exc:
        raise CursorError("malformed cursor") from exc
    if str(parsed_epoch) != epoch:
        raise CursorError("epoch must be a canonical UUID")
    return epoch, int(seq)


def classify_source(source: Any) -> tuple[str, str]:
    """Return the stored source and its single producer tag.

    ``agent`` is the control application. ``llama`` and ``rpc`` are inference.
    Any other value, including a missing source, is ``unknown`` / ``unclassified``.
    """
    if source in ("agent", "llama", "rpc"):
        src = str(source)
        return src, _TAG_BY_SOURCE[src]
    return "unknown", _TAG_BY_SOURCE["unknown"]


def _source_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def make_event(
    node_id: str,
    source: str,
    line: str,
    ts: float | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    try:
        stamp = float(time.time() if ts is None else ts)
    except (TypeError, ValueError):
        stamp = time.time()
    if not math.isfinite(stamp):
        stamp = time.time()
    src, tag = classify_source(source)
    return {
        "event_id": normalize_event_id(event_id) or str(uuid.uuid4()),
        "ts": stamp,
        "node_id": str(node_id),
        "source": src,
        "tag": tag,
        "line": str(line).rstrip("\r\n"),
    }


def match_event(
    event: dict[str, Any],
    node: str | None = None,
    source: str | None = None,
) -> bool:
    if node and str(event.get("node_id") or "") != node:
        return False
    if source and str(event.get("source") or "") != source:
        return False
    return True


def encode_sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


class LogHub:
    def __init__(
        self,
        ring_max: int = RING_MAX,
        journal_max_bytes: int = JOURNAL_MAX_BYTES,
        journal: LogJournal | None = None,
        tailer: LocalLogTailer | None = None,
        forwarder: WorkerLogForwarder | None = None,
    ) -> None:
        self.ring_max = ring_max
        self.journal_max_bytes = journal_max_bytes
        self.journal = journal or LogJournal(cluster_journal_path, journal_max_bytes)
        self._lock = threading.RLock()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_max)
        self._seqs: deque[int] = deque(maxlen=ring_max)
        self._seq_counter = 0
        self._epoch = str(uuid.uuid4())
        self._subs: list[queue.Queue[dict[str, Any] | None]] = []
        self.forwarder = forwarder or WorkerLogForwarder(lambda: http_clients.control_client())
        self._journal_loaded = False
        self.enabled = True
        self.tailer = tailer or LocalLogTailer(_log_file, LOCAL_SOURCES, make_event)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self.enabled == enabled:
                return
            self.enabled = enabled
            self.forwarder.clear()
            self.tailer.reset()
            self.tailer.prime_offsets()
            if not enabled:
                for subscriber in self._subs:
                    try:
                        subscriber.put_nowait(None)
                    except queue.Full:
                        subscriber.get_nowait()
                        subscriber.put_nowait(None)
                self._subs.clear()

    def reset(self) -> None:
        with self._lock:
            self.enabled = True
            self._ring = deque(maxlen=self.ring_max)
            self._seqs = deque(maxlen=self.ring_max)
            self._seq_counter = 0
            self._epoch = str(uuid.uuid4())
            self._subs.clear()
            self.forwarder.clear()
            self._journal_loaded = False
            self.tailer.reset()

    def _append_locked(self, event: dict[str, Any], *, notify: bool = True) -> None:
        self._seq_counter += 1
        self._ring.append(event)
        self._seqs.append(self._seq_counter)
        if notify:
            self._notify_locked(event)

    def prime_offsets(self) -> None:
        self.tailer.prime_offsets()

    def ensure_journal_loaded(self) -> None:
        """Load retained master history once, before newer startup events.

        Always merges the active journal instead of skipping when the ring is
        already non-empty, so a startup-local record cannot hide retained history.
        Loaded events keep their persisted identity; legacy rows get a runtime one.
        """
        with self._lock:
            if self._journal_loaded:
                return
            loaded = self.journal.load()
            if not loaded:
                self._journal_loaded = True
                return
            existing_ids = {ev.get("event_id") for ev in self._ring}
            loaded = [ev for ev in loaded if ev.get("event_id") not in existing_ids]
            if not loaded:
                self._journal_loaded = True
                return
            existing = list(self._ring)
            self._ring.clear()
            self._seqs.clear()
            self._seq_counter = 0
            for event in loaded + existing:
                self._append_locked(
                    make_event(
                        str(event.get("node_id") or ""),
                        _source_text(event.get("source")),
                        str(event.get("line") or ""),
                        ts=event.get("ts"),
                        event_id=event.get("event_id"),
                    ),
                    notify=False,
                )
            self._journal_loaded = True

    def ingest(self, events: list[dict[str, Any]], *, persist: bool) -> int:
        cleaned: list[dict[str, Any]] = []
        for raw in events:
            if not isinstance(raw, dict):
                continue
            line = raw.get("line")
            if line is None:
                continue
            cleaned.append(
                make_event(
                    str(raw.get("node_id") or ""),
                    _source_text(raw.get("source")),
                    str(line),
                    ts=raw.get("ts"),
                    event_id=raw.get("event_id"),
                )
            )
        if not cleaned:
            return 0
        with self._lock:
            if not self.enabled:
                return 0
            for event in cleaned:
                self._append_locked(event)
            if persist:
                self._append_journal_locked(cleaned)
        return len(cleaned)

    def snapshot(
        self,
        n: int = 200,
        node: str | None = None,
        source: str | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        """Atomic bounded tail read plus a resumable receiver-local checkpoint."""
        n = max(1, min(int(n), 5000))
        parsed = parse_cursor(after) if after is not None else None
        reset: str | None = None
        gap = False
        gap_reason: str | None = None
        with self._lock:
            epoch = self._epoch
            high = self._seq_counter
            unfiltered = node is None and source is None
            from_seq: int | None = None
            if parsed is not None:
                p_epoch, p_seq = parsed
                if p_epoch != epoch:
                    reset = "origin_changed"
                    gap = True
                    gap_reason = "feed_reset"
                elif p_seq > high:
                    raise CursorError("cursor is beyond the current log position")
                else:
                    from_seq = p_seq
                    if unfiltered:
                        youngest = self._seqs[0] if self._seqs else high
                        if p_seq < youngest - 1:
                            gap = True
                            gap_reason = "retention"
            ring = list(self._ring)
            seqs = list(self._seqs)
        matched = [
            ev
            for seq, ev in zip(seqs, ring)
            if match_event(ev, node, source) and (from_seq is None or seq > from_seq)
        ]
        if len(matched) > n:
            matched = matched[-n:]
            if gap_reason != "retention":
                gap = True
                gap_reason = "batch_limit"
        return {
            "lines": matched,
            "cursor": f"{epoch}:{high}",
            "reset": reset,
            "gap": gap,
            "gap_reason": gap_reason,
        }

    def recent(
        self,
        n: int = 200,
        node: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        n = max(1, min(int(n), 5000))
        with self._lock:
            matched = [ev for ev in self._ring if match_event(ev, node, source)]
        if len(matched) > n:
            return matched[-n:]
        return matched

    def subscribe(self) -> queue.Queue[dict[str, Any] | None]:
        q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1000)
        with self._lock:
            if self.enabled:
                self._subs.append(q)
            else:
                q.put_nowait(None)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any] | None]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def tail_new_events(self, node_id: str) -> list[dict[str, Any]]:
        return self.tailer.tail_new_events(node_id)

    def relay_tick(
        self,
        *,
        node_id: str,
        role: str,
        master_base: str | None,
        join_key: str | None,
    ) -> None:
        # Serialize policy transitions with collection and forwarding, including
        # clearing pending batches. A completed stop has no in-flight local relay.
        with self._lock:
            if not self.enabled:
                return
            events = self.tail_new_events(node_id)
            persist = role == "master"
            if events:
                self.ingest(events, persist=persist)
                if role == "worker":
                    self.forwarder.enqueue(events)
            if role == "worker" and master_base and join_key:
                self.forwarder.flush(master_base, join_key)

    def iter_sse(
        self,
        n: int,
        follow: bool,
        node: str | None,
        source: str | None,
    ) -> Iterator[bytes]:
        for event in self.recent(n, node, source):
            yield encode_sse(event)
        if not follow or not self.enabled:
            return
        q = self.subscribe()
        try:
            while True:
                queued_event = q.get()
                if queued_event is None:
                    break
                if match_event(queued_event, node, source):
                    yield encode_sse(queued_event)
        finally:
            self.unsubscribe(q)

    def _notify_locked(self, event: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def _append_journal_locked(self, events: list[dict[str, Any]]) -> None:
        self.journal.append(events)


HUB = LogHub()


def reset_for_tests() -> None:
    HUB.reset()
