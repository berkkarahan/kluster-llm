"""Engine-scoped settings validation and explicit persistence (no migration)."""
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm.modules.inference.entities import EngineModelSettingsBody, RunSettingsFields
from kllm.modules.inference.gateways.engine_settings import EngineSettingsStore, ModelSettingsChanged
from kllm.modules.inference.interactors.parameter_catalog import canonicalize_parameters
from kllm.modules.inference.interactors.run_settings_http import ModelSettingsGateway
from kllm.modules.runtimes.interactors.selection import use_runtime

REQUEST_ONLY = frozenset({"remember", "advanced_open", "settings_source", "network_id", "review"})
PRIMA_DEFAULTS = {"cache_type_k": "f16", "cache_type_v": "f16", "flash_attention": "off"}


class EngineSettingsHandlers:
    def __init__(self, store: EngineSettingsStore, legacy: ModelSettingsGateway,
                 resolve_model: Callable[[str], Path], capture_runtime: Callable[[str], dict],
                 preflight: Callable[[RunSettingsFields, Path], None]):
        self._store, self._legacy = store, legacy
        self._resolve_model, self._capture_runtime, self._preflight = resolve_model, capture_runtime, preflight

    @staticmethod
    def _engine(engine: str) -> None:
        if engine != "primacpp":
            raise HTTPException(422, "Engine model settings currently support primacpp only")

    def _public(self, document: dict) -> dict:
        legacy = self._legacy.get_model_settings(document["model"])
        return {**document, "legacy_parameter_set": deepcopy(legacy["parameter_set"]) if legacy else None}

    def get(self, engine: str, model: str) -> dict[str, Any]:
        self._engine(engine)
        try:
            document = self._store.get(engine, model)
        except ValueError as error:
            raise HTTPException(409, detail={"code": "MODEL_SETTINGS_INVALID", "message": str(error)}) from error
        if document is None:
            defaults = RunSettingsFields(model=model, **PRIMA_DEFAULTS).snapshot_for_remember()
            document = {"engine": engine, "model": model, "revision": 0, "updated_at": None,
                        "parameter_set": defaults, "requested_parameter_set": deepcopy(defaults)}
        return self._public(document)

    def save(self, engine: str, model: str, body: EngineModelSettingsBody) -> dict[str, Any]:
        self._engine(engine)
        requested = {key: deepcopy(value) for key, value in body.parameter_set.items() if key not in REQUEST_ONLY}
        if "model" in requested and requested["model"] != model:
            raise HTTPException(422, "Parameter model must match the route model")
        requested.pop("model", None)
        try:
            unknown = requested.keys() - RunSettingsFields.model_fields.keys()
            if unknown:
                raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
            canonical = canonicalize_parameters(requested)
            validated = RunSettingsFields.model_validate({**PRIMA_DEFAULTS, **canonical, "model": model})
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        try:
            runtime = self._capture_runtime(body.runtime_id)
        except KeyError:
            raise HTTPException(404, "Runtime does not exist") from None
        except (ValueError, OSError) as error:
            raise HTTPException(409, detail={"code": "RUNTIME_NOT_READY", "message": str(error)}) from error
        if runtime.get("engine") != engine or runtime.get("runtime_id") != body.runtime_id:
            raise HTTPException(422, "Captured runtime does not match the requested engine/runtime")
        with use_runtime(runtime):
            try:
                path = self._resolve_model(model)
            except FileNotFoundError:
                path = Path(model)
            try:
                self._preflight(validated, path)
            except HTTPException as error:
                if error.status_code == 400:
                    raise HTTPException(422, detail=error.detail) from error
                raise
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        try:
            document = self._store.put(engine, model, body.expected_revision,
                                       validated.snapshot_for_remember(), requested)
        except ModelSettingsChanged as error:
            raise HTTPException(409, detail={"code": "MODEL_SETTINGS_CHANGED", "message": str(error)}) from error
        except ValueError as error:
            raise HTTPException(409, detail={"code": "MODEL_SETTINGS_INVALID", "message": str(error)}) from error
        return self._public(document)
