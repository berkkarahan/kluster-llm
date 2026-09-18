"""Per-model inference defaults from GGUF metadata + Unsloth/LM Studio heuristics."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kllm.gguf_meta import read_inference_metadata

# Unsloth Qwen3.5 thinking / general (when GGUF omits these keys).
_HEURISTIC_THINKING_ON = {
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
}
_HEURISTIC_THINKING_OFF = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
}

# OpenAI / llama-server body keys we may fill when the client omits them.
_REQUEST_SAMPLING_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "frequency_penalty",
    "repeat_penalty",
)

# Launch argv flag ↔ body field.
_LAUNCH_FLAG_MAP = (
    ("temperature", "--temp"),
    ("top_k", "--top-k"),
    ("top_p", "--top-p"),
    ("min_p", "--min-p"),
    ("presence_penalty", "--presence-penalty"),
    ("repeat_penalty", "--repeat-penalty"),
)

_cache_lock = threading.Lock()
_cache: dict[tuple[str, int, int], ModelDefaults] = {}


@dataclass(frozen=True)
class ModelDefaults:
    """Resolved sampling + chat-template kwargs for one GGUF."""

    sampling: dict[str, float | int] = field(default_factory=dict)
    chat_template_kwargs: dict[str, Any] = field(default_factory=dict)
    reasoning: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # field → gguf|heuristic|template
    architecture: str | None = None
    context_length: int | None = None
    jinja: bool = False
    reasoning_format: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        """Compact shape for /v1/models entries."""
        out: dict[str, Any] = {
            "sampling": dict(self.sampling),
            "chat_template_kwargs": dict(self.chat_template_kwargs),
            "reasoning": dict(self.reasoning),
            "sources": dict(self.sources),
        }
        if self.architecture:
            out["architecture"] = self.architecture
        if self.context_length is not None:
            out["context_length"] = self.context_length
        if self.reasoning_format:
            out["reasoning_format"] = self.reasoning_format
        return out

    def to_launch_dict(self) -> dict[str, Any]:
        """First-class keys for native master launch / argv builder."""
        out: dict[str, Any] = {}
        for key in (
            "temperature",
            "top_k",
            "top_p",
            "min_p",
            "presence_penalty",
            "repeat_penalty",
        ):
            if key in self.sampling:
                out[key] = self.sampling[key]
        if self.jinja:
            out["jinja"] = True
        if self.chat_template_kwargs:
            out["chat_template_kwargs"] = json.dumps(
                self.chat_template_kwargs, separators=(",", ":")
            )
        if self.reasoning_format:
            out["reasoning_format"] = self.reasoning_format
        return out


def _is_hybrid_family(architecture: str | None, reasoning: dict[str, Any]) -> bool:
    if reasoning.get("supports_enable_thinking") or reasoning.get("always_on_thinking"):
        return True
    if not architecture:
        return False
    arch = architecture.lower()
    return arch.startswith("qwen3") or arch.startswith("qwen35") or "qwen3" in arch


def resolve_model_defaults(model_path: Path | str | None) -> ModelDefaults:
    """GGUF-first sampling; Unsloth fill-ins for hybrid-reasoning templates."""
    if model_path is None:
        return ModelDefaults()
    path = Path(model_path)
    try:
        st = path.stat()
    except OSError:
        return ModelDefaults()
    cache_key = (str(path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit is not None:
            return hit

    meta = read_inference_metadata(path)
    if meta is None:
        result = ModelDefaults()
        with _cache_lock:
            _cache[cache_key] = result
        return result

    sampling: dict[str, float | int] = dict(meta.get("sampling") or {})
    sources: dict[str, str] = {k: "gguf" for k in sampling}
    reasoning = dict(meta.get("reasoning") or {})
    architecture = meta.get("architecture")
    chat_kwargs: dict[str, Any] = {}
    jinja = bool(meta.get("has_chat_template"))
    reasoning_format: str | None = None

    if _is_hybrid_family(architecture, reasoning):
        default_think = reasoning.get("default_enable_thinking")
        if default_think is None and reasoning.get("always_on_thinking"):
            default_think = True
        if reasoning.get("supports_enable_thinking") and default_think is not None:
            chat_kwargs["enable_thinking"] = bool(default_think)
            sources["enable_thinking"] = "template"
        if default_think is False:
            for key, val in _HEURISTIC_THINKING_OFF.items():
                if key not in sampling:
                    sampling[key] = val
                    sources[key] = "heuristic"
        else:
            # Thinking on (or always-on / unknown): fill Unsloth gaps only.
            for key, val in _HEURISTIC_THINKING_ON.items():
                if key not in sampling:
                    sampling[key] = val
                    sources[key] = "heuristic"
        if (
            reasoning.get("supports_enable_thinking")
            or reasoning.get("always_on_thinking")
            or chat_kwargs.get("enable_thinking")
        ):
            reasoning_format = "deepseek"

    result = ModelDefaults(
        sampling=sampling,
        chat_template_kwargs=chat_kwargs,
        reasoning=reasoning,
        sources=sources,
        architecture=architecture,
        context_length=meta.get("context_length"),
        jinja=jinja,
        reasoning_format=reasoning_format,
    )
    with _cache_lock:
        _cache[cache_key] = result
    return result


def clear_defaults_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _flag_present(argv: list[str], flag: str) -> bool:
    return flag in argv


def merge_defaults_into_extra_args(
    extra_args: list[str], defaults: ModelDefaults
) -> list[str]:
    """Prepend default sampling / jinja / kwargs flags not already in extra_args."""
    prefix: list[str] = []
    for field_name, flag in _LAUNCH_FLAG_MAP:
        if field_name not in defaults.sampling:
            continue
        if _flag_present(extra_args, flag) or _flag_present(prefix, flag):
            continue
        prefix.extend([flag, str(defaults.sampling[field_name])])
    if defaults.jinja and not (
        _flag_present(extra_args, "--jinja") or _flag_present(extra_args, "--no-jinja")
    ):
        prefix.append("--jinja")
    if defaults.chat_template_kwargs and not _flag_present(
        extra_args, "--chat-template-kwargs"
    ):
        prefix.extend(
            [
                "--chat-template-kwargs",
                json.dumps(defaults.chat_template_kwargs, separators=(",", ":")),
            ]
        )
    if defaults.reasoning_format and not _flag_present(
        extra_args, "--reasoning-format"
    ):
        prefix.extend(["--reasoning-format", defaults.reasoning_format])
    return prefix + list(extra_args)


def apply_defaults_to_chat_body(
    body: dict[str, Any], defaults: ModelDefaults
) -> dict[str, Any]:
    """Fill missing sampling / chat_template_kwargs on an OpenAI-style chat body."""
    # Promote top-level enable_thinking into chat_template_kwargs before merge.
    out = dict(body)
    if "enable_thinking" in out and out["enable_thinking"] is not None:
        kwargs = dict(out.get("chat_template_kwargs") or {})
        if "enable_thinking" not in kwargs:
            kwargs["enable_thinking"] = bool(out["enable_thinking"])
            out["chat_template_kwargs"] = kwargs
        out.pop("enable_thinking", None)
    for key in _REQUEST_SAMPLING_KEYS:
        if key in out:
            continue
        if key in defaults.sampling:
            out[key] = defaults.sampling[key]
    # Deep-merge chat_template_kwargs: defaults first, request wins per key.
    if defaults.chat_template_kwargs or isinstance(out.get("chat_template_kwargs"), dict):
        merged = dict(defaults.chat_template_kwargs)
        existing = out.get("chat_template_kwargs")
        if isinstance(existing, dict):
            merged.update(existing)
        if merged:
            out["chat_template_kwargs"] = merged
    return out


def overlay_user_inference(
    defaults: ModelDefaults,
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    min_p: float | None = None,
    presence_penalty: float | None = None,
    repeat_penalty: float | None = None,
    enable_thinking: bool | None = None,
) -> ModelDefaults:
    """Apply explicit user overrides on top of GGUF/heuristic defaults.

    ``None`` leaves the underlying default unchanged.
    """
    sampling = dict(defaults.sampling)
    sources = dict(defaults.sources)
    chat_kwargs = dict(defaults.chat_template_kwargs)

    overrides: dict[str, float | int] = {}
    if temperature is not None:
        overrides["temperature"] = float(temperature)
    if top_p is not None:
        overrides["top_p"] = float(top_p)
    if top_k is not None:
        overrides["top_k"] = int(top_k)
    if min_p is not None:
        overrides["min_p"] = float(min_p)
    if presence_penalty is not None:
        overrides["presence_penalty"] = float(presence_penalty)
    if repeat_penalty is not None:
        overrides["repeat_penalty"] = float(repeat_penalty)
    for key, val in overrides.items():
        sampling[key] = val
        sources[key] = "user"

    if enable_thinking is not None:
        chat_kwargs["enable_thinking"] = bool(enable_thinking)
        sources["enable_thinking"] = "user"

    return ModelDefaults(
        sampling=sampling,
        chat_template_kwargs=chat_kwargs,
        reasoning=dict(defaults.reasoning),
        sources=sources,
        architecture=defaults.architecture,
        context_length=defaults.context_length,
        jinja=defaults.jinja,
        reasoning_format=defaults.reasoning_format,
    )


def defaults_as_dict(defaults: ModelDefaults) -> dict[str, Any]:
    """Full dataclass dump (tests / debugging)."""
    return asdict(defaults)
