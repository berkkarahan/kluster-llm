"""Incremental, restart-safe tailing of local service log files."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class LocalLogTailer:
    def __init__(
        self,
        path_provider: Callable[[str], Path],
        sources: tuple[tuple[str, str], ...],
        event_factory: Callable[..., dict[str, Any]],
    ) -> None:
        self._path_provider = path_provider
        self._sources = sources
        self._event_factory = event_factory
        self._lock = threading.Lock()
        self._offsets: dict[str, int] = {}
        self._partial: dict[str, str] = {}
        self._primed = False

    def reset(self) -> None:
        with self._lock:
            self._offsets.clear()
            self._partial.clear()
            self._primed = False

    def prime_offsets(self) -> None:
        """Skip existing content so restarts do not re-relay retained history."""
        with self._lock:
            if self._primed:
                return
            self._primed = True
            for _source, name in self._sources:
                path = self._path_provider(name)
                key = str(path)
                if path.is_file() and key not in self._offsets:
                    self._offsets[key] = path.stat().st_size

    def tail_new_events(self, node_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for source, name in self._sources:
            events.extend(self._tail_file(self._path_provider(name), source, node_id))
        return events

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
        return [self._event_factory(node_id, source, piece, ts=ts) for piece in pieces if piece]
