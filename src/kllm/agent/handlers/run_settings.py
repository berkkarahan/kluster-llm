"""HTTP handlers for per-model run settings and inference capabilities."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException

from kllm.agent.schemas import ModelSettingsBody
from kllm.run_settings import PlanBody, RunBody, RunSettingsFields
from kllm.parameter_catalog import (
    CONTROL_FIELDS,
    PARAMETERS,
    canonicalize_parameters,
    parameter_descriptors,
)


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
            params = self._saved_parameters(saved["parameter_set"])
            return {**saved, "parameter_set": params}
        return {
            "model": model,
            "parameter_set": RunSettingsFields.default_snapshot(model),
            "updated_at": None,
        }

    @staticmethod
    def _saved_parameters(params: dict[str, Any]) -> dict[str, Any]:
        # Existing profiles may contain old aliases and request-only UI fields.
        reusable = {
            key: value
            for key, value in params.items()
            if key in RunSettingsFields.model_fields and key not in CONTROL_FIELDS
        }
        return canonicalize_parameters(reusable, legacy=True)

    def resolve(self, body: PlanBody | RunBody) -> PlanBody | RunBody:
        explicit = body.model_dump(include=body.model_fields_set)
        saved = (
            self._store.get_model_settings(body.model) if body.settings_source == "saved" else None
        )
        base = self._saved_parameters(saved["parameter_set"]) if saved else {}
        try:
            resolved = type(body).model_validate(
                {**base, **explicit, "model": body.model, "settings_source": body.settings_source}
            )
        except ValueError as exc:
            raise HTTPException(
                422, f"Saved configuration is invalid: {exc}; use settings_source=defaults to reset"
            ) from exc
        resolved._sources = {
            name: "override" if name in explicit else "saved" if name in base else "default"
            for name in PARAMETERS
        }
        return resolved

    def capabilities(self, model: str | None = None) -> dict[str, Any]:
        from kllm.chat_templates import PRESETS, selected_template_text
        from kllm.gguf_meta import read_inference_metadata

        metadata = (
            read_inference_metadata(self._resolve_model(model), include_template=True)
            if model
            else None
        )
        from kllm.engine_parameters import engine_catalog, public_expert_flags
        from kllm.extra_args import ExtraArgsError

        try:
            catalog = engine_catalog()
            engine_error = None
        except (ExtraArgsError, RuntimeError) as exc:
            catalog = {}
            engine_error = str(exc)
        template = str((metadata or {}).get("chat_template_text") or "")
        reasoning = (metadata or {}).get("reasoning") or {}
        descriptors = parameter_descriptors(
            RunSettingsFields.model_json_schema(), self._available_threads()
        )
        effort_choices = [
            level
            for level in ("minimal", "low", "medium", "high", "xhigh", "max")
            if f"'{level}'" in template or f'"{level}"' in template
        ]
        for descriptor in descriptors:
            name = descriptor["id"]
            if name in {"cache_type_k", "cache_type_v"}:
                from kllm.placement import WIDTHS

                descriptor["choices"] = list(WIDTHS)
            reason = None
            flags = descriptor["engine_flags"]
            if flags and catalog and not any(flag in catalog for flag in flags):
                reason = "Not supported by the installed llama-server"
            if name == "reasoning_effort":
                descriptor["choices"] = ["default", *effort_choices]
                if not reasoning.get("supports_reasoning_effort"):
                    reason = "The embedded template does not declare reasoning effort"
            if name == "reasoning_budget" and not (
                "<think>" in template and "</think>" in template
            ):
                reason = "Thinking budget requires recognizable thinking delimiters"
            if (
                name == "enable_thinking"
                and metadata
                and not reasoning.get("supports_enable_thinking")
            ):
                reason = "The embedded template does not expose a thinking toggle"
            if name == "moe_offload" and metadata:
                from kllm.gguf_meta import is_moe_architecture

                if not is_moe_architecture(metadata.get("architecture")):
                    reason = "This model does not declare a Mixture of Experts architecture"
            descriptor.update({"available": reason is None, "unavailable_reason": reason})
        return {
            "threads_max": self._available_threads(),
            "template_embedded": bool(metadata and metadata.get("has_chat_template")),
            "template_text": (metadata or {}).get("chat_template_text"),
            "templates": [
                {"id": key, "label": label, "text": selected_template_text(key, None, "")}
                for key, (label, _) in PRESETS.items()
            ],
            "schema_version": 1,
            "parameters": descriptors,
            "expert_flags": public_expert_flags(catalog),
            "engine_available": bool(catalog),
            "engine_error": engine_error,
            "reasoning": reasoning,
            "projectors": (
                [
                    str(path)
                    for path in sorted(self._resolve_model(model).parent.glob("*mmproj*.gguf"))
                    if path.is_file()
                ]
                if model
                else []
            ),
        }

    def preflight(self, body: RunSettingsFields, path: Path) -> None:
        from kllm.engine_parameters import (
            engine_catalog,
            validate_engine_args,
            validate_engine_parser,
        )
        from kllm.extra_args import ExtraArgsError
        from kllm.gguf_meta import read_inference_metadata, sniff_reasoning_flags
        from kllm.run_settings_resolution import resolve_launch_settings
        from kllm.run_planning import validate_vision_for_run

        resolve_launch_settings(body)
        if body.chat_template_text:
            from kllm.agent.services.template_validation import TemplateValidationService
            from kllm import native
            from kllm.exceptions import KllmError

            try:
                TemplateValidationService(native.resolve_llama_server).validate(
                    body.chat_template_text
                )
            except KllmError as exc:
                raise HTTPException(422, detail={"chat_template_text": str(exc)}) from exc
        if not body.context_auto and body.context < 512:
            raise HTTPException(422, detail={"context": "Context must be at least 512"})
        validate_vision_for_run(body)
        if body.draft_enabled() and not Path(body.draft_model).is_file():
            raise HTTPException(
                422, detail={"draft_model": "Choose an existing local draft model file"}
            )
        metadata = read_inference_metadata(path, include_template=True) or {}
        from kllm.chat_templates import selected_template_text

        template = selected_template_text(
            body.chat_template, body.chat_template_text, metadata.get("chat_template_text") or ""
        )
        reasoning = sniff_reasoning_flags(template)
        if (
            body.reasoning_effort is not None
            and body.reasoning_effort != "default"
            and not reasoning.get("supports_reasoning_effort")
        ):
            raise HTTPException(
                422,
                detail={"reasoning_effort": "Selected template does not support reasoning effort"},
            )
        effort_choices = [
            level
            for level in ("minimal", "low", "medium", "high", "xhigh", "max")
            if f"'{level}'" in template or f'"{level}"' in template
        ]
        if effort_choices and body.reasoning_effort not in (None, "default", *effort_choices):
            raise HTTPException(
                422,
                detail={"reasoning_effort": f"Choose one of: default, {', '.join(effort_choices)}"},
            )
        if body.reasoning_budget is not None and not (
            "<think>" in template and "</think>" in template
        ):
            raise HTTPException(
                422,
                detail={
                    "reasoning_budget": "Selected template has no recognizable thinking delimiters"
                },
            )
        context_max = metadata.get("context_length")
        if context_max and not body.context_auto and body.context > context_max:
            raise HTTPException(422, detail={"context": f"Model context limit is {context_max}"})
        # Typed additions must work with an overridden binary as well as the pin.
        requested = [
            PARAMETERS[name][1][0]
            for name in (
                "threads_batch",
                "seed",
                "frequency_penalty",
                "max_output_tokens",
                "draft_max",
                "reasoning_effort",
                "reasoning_budget",
                "reasoning_format",
            )
            if getattr(body, name) is not None
        ]
        parser_tokens = list(body.extra_args)
        if body.speculative not in {"auto", "off", "draft", "none", "draft-simple"}:
            parser_tokens += ["--spec-type", body.speculative]
        if parser_tokens or requested:
            try:
                catalog = engine_catalog()
                validate_engine_args(body.extra_args, catalog)
                for flag in requested:
                    if flag not in catalog:
                        raise ExtraArgsError(
                            f"{flag} is not supported by the installed llama-server"
                        )
                validate_engine_parser(parser_tokens)
            except (ExtraArgsError, RuntimeError) as exc:
                raise HTTPException(422, detail={"extra_args": str(exc)}) from exc

    def save(self, model: str, body: ModelSettingsBody) -> dict[str, Any]:
        params = dict(body.parameter_set)
        params["model"] = model
        try:
            validated = RunSettingsFields.model_validate(params)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            path = self._resolve_model(model)
        except FileNotFoundError:
            # Existing clients can prepare a profile before downloading its model.
            path = Path(model)
        self.preflight(validated, path)
        stored = validated.snapshot_for_remember()
        return self._store.upsert_model_settings(model, stored)
