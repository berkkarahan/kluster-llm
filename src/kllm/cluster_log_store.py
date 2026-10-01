"""Bounded JSONL persistence for the cluster log journal."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


class LogJournal:
    """Read and append retained events in a rotating JSONL journal."""

    def __init__(
        self,
        path_provider: Callable[[], Path],
        max_bytes: int,
    ) -> None:
        self._path_provider = path_provider
        self._max_bytes = max_bytes

    def load(self) -> list[dict[str, Any]]:
        path = self._path_provider()
        if not path.is_file():
            return []

        text = path.read_text(encoding="utf-8", errors="replace")
        events: list[dict[str, Any]] = []
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and "line" in event:
                events.append(event)
        return events

    def append(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        path = self._path_provider()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as journal:
            for event in events:
                journal.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _rotate_if_needed(self, path: Path) -> None:
        if not path.is_file() or path.stat().st_size < self._max_bytes:
            return
        backup = path.with_name(path.name + ".1")
        if backup.exists():
            backup.unlink()
        path.replace(backup)
