"""Persistent reconnect parameters for the detached cluster updater."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class ReconnectStateError(ValueError):
    """Raised when the persisted reconnect handoff is missing or malformed."""


class ReconnectStore:
    """Persist one updater handoff using an injected path and home initializer."""

    def __init__(self, path: Callable[[], Path], ensure_home: Callable[[], Path]) -> None:
        self._path = path
        self._ensure_home = ensure_home

    def save(self, params: Mapping[str, Any]) -> Path:
        self._ensure_home()
        path = self._path()
        path.write_text(json.dumps(dict(params), indent=2), encoding="utf-8")
        return path

    def load(self) -> dict[str, Any]:
        path = self._path()
        if not path.is_file():
            raise ReconnectStateError(f"missing reconnect file: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReconnectStateError("invalid reconnect file") from exc
        if not isinstance(value, dict):
            raise ReconnectStateError("invalid reconnect file")
        return value

    def clear(self) -> None:
        path = self._path()
        if not path.exists():
            return
        try:
            path.unlink()
        except OSError:
            pass
