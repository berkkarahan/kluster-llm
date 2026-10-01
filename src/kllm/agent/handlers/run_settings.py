"""HTTP handlers for per-model run settings and inference capabilities."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException

from kllm.agent.schemas import ModelSettingsBody
from kllm.run_settings import PlanBody, RunBody, RunSettingsFields


class ModelSettingsGateway(Protocol):
    def get_model_settings(self, model: str) -> dict[str, Any] | None: ...

    def upsert_model_settings(self, model: str, value: dict[str, Any]) -> dict[str, Any]: ...


class RunSettingsHandlers:
    """Coordinate settings persistence and model metadata for run controls."""

    def __init__(
        self,
        store: ModelSettingsGateway,
        resolve_model: Callable[[str], Path],
        available_threads: Callable[[], int],
    ) -> None:
        self._store = store
        self._resolve_model = resolve_model
        self._available_threads = available_threads

    def remember(self, body: PlanBody | RunBody) -> None:
        if body.remember:
            self._store.upsert_model_settings(body.model, body.snapshot_for_remember())

    def get(self, model: str) -> dict[str, Any]:
        saved = self._store.get_model_settings(model)
        if saved:
            return saved
        return {
            "model": model,
            "parameter_set": RunSettingsFields.default_snapshot(model),
            "updated_at": None,
        }

    def capabilities(self, model: str | None = None) -> dict[str, Any]:
        from kllm.chat_templates import PRESETS
        from kllm.gguf_meta import read_inference_metadata

        metadata = (
            read_inference_metadata(self._resolve_model(model), include_template=True)
            if model
            else None
        )
        return {
            "threads_max": self._available_threads(),
            "template_embedded": bool(metadata and metadata.get("has_chat_template")),
            "template_text": (metadata or {}).get("chat_template_text"),
            "templates": [{"id": key, "label": label} for key, (label, _) in PRESETS.items()],
        }

    def save(self, model: str, body: ModelSettingsBody) -> dict[str, Any]:
        params = dict(body.parameter_set)
        params["model"] = model
        try:
            validated = RunSettingsFields.model_validate(params)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        stored = validated.model_dump(exclude={"model", "tensor_split"})
        return self._store.upsert_model_settings(model, stored)
