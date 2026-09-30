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

from kllm import http_clients
from kllm.state import home_dir

RING_MAX = 8000
PENDING_MAX = 8000
BATCH_MAX_LINES = 200
BATCH_MAX_BYTES = 64 * 1024
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


class CursorError(ValueError):
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


def _rotate_if_needed(path: Path, max_bytes: int) -> None:
    if not path.is_file() or path.stat().st_size < max_bytes:
        return
    bak = path.with_name(path.name + ".1")
    if bak.exists():
        bak.unlink()
    path.replace(bak)


class LogHub:
    def __init__(
        self,
        ring_max: int = RING_MAX,
        journal_max_bytes: int = JOURNAL_MAX_BYTES,
    ) -> None:
        self.ring_max = ring_max
        self.journal_max_bytes = journal_max_bytes
        self._lock = threading.Lock()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_max)
        self._seqs: deque[int] = deque(maxlen=ring_max)
        self._seq_counter = 0
        self._epoch = str(uuid.uuid4())
        self._subs: list[queue.Queue[dict[str, Any] | None]] = []
        self._pending: deque[dict[str, Any]] = deque()
        self._journal_loaded = False
        self._offsets: dict[str, int] = {}
        self._partial: dict[str, str] = {}
        self._primed = False

    def reset(self) -> None:
        with self._lock:
            self._ring = deque(maxlen=self.ring_max)
            self._seqs = deque(maxlen=self.ring_max)
            self._seq_counter = 0
            self._epoch = str(uuid.uuid4())
            self._subs.clear()
            self._pending.clear()
            self._journal_loaded = False
            self._offsets.clear()
            self._partial.clear()
            self._primed = False

    def _append_locked(self, event: dict[str, Any], *, notify: bool = True) -> None:
        self._seq_counter += 1
        self._ring.append(event)
        self._seqs.append(self._seq_counter)
        if notify:
            self._notify_locked(event)

    def prime_offsets(self) -> None:
        """Skip existing local file content so agent restarts do not re-relay history."""
        with self._lock:
            if self._primed:
                return
            self._primed = True
            for _source, name in LOCAL_SOURCES:
                path = _log_file(name)
                key = str(path)
                if path.is_file() and key not in self._offsets:
                    self._offsets[key] = path.stat().st_size

    def ensure_journal_loaded(self) -> None:
        """Load retained master history once, before newer startup events.

        Always merges the active journal instead of skipping when the ring is
        already non-empty, so a startup-local record cannot hide retained history.
        Loaded events keep their persisted identity; legacy rows get a runtime one.
        """
        with self._lock:
            if self._journal_loaded:
                return
            path = cluster_journal_path()
            if not path.is_file():
                self._journal_loaded = True
                return
            text = path.read_text(encoding="utf-8", errors="replace")
            loaded: list[dict[str, Any]] = []
            for raw in text.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and "line" in event:
                    loaded.append(event)
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
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any] | None]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def tail_new_events(self, node_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for source, name in LOCAL_SOURCES:
            events.extend(self._tail_file(_log_file(name), source, node_id))
        return events

    def relay_tick(
        self,
        *,
        node_id: str,
        role: str,
        master_base: str | None,
        join_key: str | None,
    ) -> None:
        events = self.tail_new_events(node_id)
        persist = role == "master"
        if events:
            self.ingest(events, persist=persist)
            if role == "worker":
                self._enqueue_pending(events)
        if role == "worker" and master_base and join_key:
            self._flush_pending(master_base, join_key)

    def iter_sse(
        self,
        n: int,
        follow: bool,
        node: str | None,
        source: str | None,
    ) -> Iterator[bytes]:
        for event in self.recent(n, node, source):
            yield encode_sse(event)
        if not follow:
            return
        q = self.subscribe()
        try:
            while True:
                event = q.get()
                if event is None:
                    break
                if match_event(event, node, source):
                    yield encode_sse(event)
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
        path = cluster_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path, self.journal_max_bytes)
        with path.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _tail_file(self, path: Path, source: str, node_id: str) -> list[dict[str, Any]]:
        key = str(path)
        if not path.is_file():
            return []
        try:
            size = path.stat().st_size
        except OSError:
            return []
        with self._lock:
            offset = self._offsets.get(key, 0)
            if size < offset:
                offset = 0
                self._partial.pop(key, None)
            if size == offset:
                self._offsets[key] = offset
                return []
        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                new_offset = fh.tell()
        except OSError:
            return []
        with self._lock:
            self._offsets[key] = new_offset
            prefix = self._partial.pop(key, "")
        text = prefix + chunk.decode("utf-8", errors="replace")
        if not text:
            return []
        pieces = text.splitlines()
        if text.endswith("\n") or text.endswith("\r"):
            remainder = ""
        elif pieces:
            remainder = pieces.pop()
        else:
            remainder = text
        with self._lock:
            if remainder:
                self._partial[key] = remainder
        ts = time.time()
        return [make_event(node_id, source, piece, ts=ts) for piece in pieces if piece]

    def _enqueue_pending(self, events: list[dict[str, Any]]) -> None:
        with self._lock:
            self._pending.extend(events)
            while len(self._pending) > PENDING_MAX:
                self._pending.popleft()

    def _take_batch_locked(self) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        size = 0
        while self._pending and len(batch) < BATCH_MAX_LINES:
            event = self._pending[0]
            extra = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
            if batch and size + extra > BATCH_MAX_BYTES:
                break
            self._pending.popleft()
            batch.append(event)
            size += extra
        return batch

    def _flush_pending(self, master_base: str, join_key: str) -> None:
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
                        "event_id": ev.get("event_id"),
                        "ts": ev["ts"],
                        "source": ev["source"],
                        "line": ev["line"],
                    }
                    for ev in batch
                ],
            }
            try:
                resp = http_clients.control_client().post(
                    url, json=payload, headers=headers, timeout=5.0
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"ingest {resp.status_code}")
            except Exception:
                with self._lock:
                    for event in reversed(batch):
                        self._pending.appendleft(event)
                    while len(self._pending) > PENDING_MAX:
                        self._pending.popleft()
                return


HUB = LogHub()


def reset_for_tests() -> None:
    HUB.reset()
