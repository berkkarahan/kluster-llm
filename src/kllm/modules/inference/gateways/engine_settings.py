"""Atomic, revisioned engine settings sidecar, independent of legacy profiles."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kllm.shared.atomic_io import replace_file
from kllm.shared.persistence.sqlite import file_lock


class ModelSettingsChanged(ValueError):
    """The caller's observed revision no longer owns this write."""


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    engine: str
    model: str
    revision: int = Field(ge=1)
    updated_at: str
    parameter_set: dict[str, Any]
    requested_parameter_set: dict[str, Any]


class _SettingsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    settings: dict[str, dict[str, _Document]]


class EngineSettingsStore:
    def __init__(self, path: Path, *, lock_timeout: float = 5.0):
        self.path = Path(path)
        self.lock_timeout = lock_timeout

    def _read(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"schema_version": 1, "settings": {}}
        try:
            raw = json.loads(text)
            if type(raw.get("schema_version")) is not int:
                raise ValueError("invalid schema version")
            value = _SettingsFile.model_validate(raw).model_dump()
            for engine, models in value["settings"].items():
                for model, document in models.items():
                    if document["engine"] != engine or document["model"] != model:
                        raise ValueError("document identity differs from key")
            # Reject non-finite values even in untyped parameter dictionaries.
            json.dumps(value, allow_nan=False)
            return value
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError(f"Engine settings file is invalid; repair it before saving: {error}") from error

    def get(self, engine: str, model: str) -> dict | None:
        return deepcopy(self._read()["settings"].get(engine, {}).get(model))

    def put(self, engine: str, model: str, expected_revision: int,
            parameter_set: dict, requested_parameter_set: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path.with_name(self.path.name + ".lock"), self.lock_timeout):
            data = self._read()
            models = data["settings"].setdefault(engine, {})
            revision = models.get(model, {}).get("revision", 0)
            if revision != expected_revision:
                raise ModelSettingsChanged("Model settings changed; reload and review before saving")
            document = _Document(engine=engine, model=model, revision=revision + 1,
                updated_at=datetime.now(timezone.utc).isoformat(),
                parameter_set=parameter_set, requested_parameter_set=requested_parameter_set).model_dump()
            models[model] = document
            content = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
            fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                replace_file(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return deepcopy(document)
