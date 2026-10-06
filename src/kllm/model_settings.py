"""Per-model RUN SETTINGS persistence (~/.kllm/model-settings.json)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kllm.state import ensure_home

_store_lock = threading.RLock()


def _store_path() -> Path:
    ensure_home()
    return Path(ensure_home()) / "model-settings.json"


def _load_raw() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"models": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"models": {}}
    if not isinstance(data, dict):
        return {"models": {}}
    models = data.get("models")
    if not isinstance(models, dict):
        data["models"] = {}
    return data


def _save_raw(data: dict[str, Any]) -> None:
    path = _store_path()
    fd, name = tempfile.mkstemp(prefix=".model-settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def get_model_settings(model_id: str) -> dict[str, Any] | None:
    data = _load_raw()
    entry = (data.get("models") or {}).get(model_id)
    if not isinstance(entry, dict):
        return None
    params = entry.get("parameter_set")
    if not isinstance(params, dict):
        return None
    return {
        "model": model_id,
        "parameter_set": params,
        "updated_at": entry.get("updated_at"),
    }


def upsert_model_settings(model_id: str, parameter_set: dict[str, Any]) -> dict[str, Any]:
    with _store_lock:
        data = _load_raw()
        models = data.setdefault("models", {})
        stamp = datetime.now(timezone.utc).isoformat()
        models[model_id] = {"parameter_set": parameter_set, "updated_at": stamp}
        _save_raw(data)
    return {"model": model_id, "parameter_set": parameter_set, "updated_at": stamp}
