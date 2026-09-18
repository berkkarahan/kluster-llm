"""Minimal GGUF metadata reader (context length, sampling, chat-template sniff)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

# GGUF value types (subset)
_UINT8, _INT8, _UINT16, _INT16 = 0, 1, 2, 3
_UINT32, _INT32, _FLOAT32, _BOOL = 4, 5, 6, 7
_STRING, _ARRAY = 8, 9
_UINT64, _INT64, _FLOAT64 = 10, 11, 12

_SCALAR_SIZE = {
    _UINT8: 1,
    _INT8: 1,
    _UINT16: 2,
    _INT16: 2,
    _UINT32: 4,
    _INT32: 4,
    _FLOAT32: 4,
    _BOOL: 1,
    _UINT64: 8,
    _INT64: 8,
    _FLOAT64: 8,
}

_SCALAR_FMT = {
    _UINT8: "B",
    _INT8: "b",
    _UINT16: "H",
    _INT16: "h",
    _UINT32: "I",
    _INT32: "i",
    _FLOAT32: "f",
    _BOOL: "?",
    _UINT64: "Q",
    _INT64: "q",
    _FLOAT64: "d",
}

# Map GGUF general.sampling.* suffixes → OpenAI / llama-server field names.
_SAMPLING_KEY_MAP = {
    "temp": "temperature",
    "temperature": "temperature",
    "top_k": "top_k",
    "top_p": "top_p",
    "min_p": "min_p",
    "penalty_repeat": "repeat_penalty",
    "penalty_present": "presence_penalty",
    "penalty_freq": "frequency_penalty",
}


def _read_string(f) -> str:
    (n,) = struct.unpack("<Q", f.read(8))
    if n > 10_000_000:
        raise ValueError("GGUF string length too large")
    return f.read(n).decode("utf-8", errors="replace")


def _skip_value(f, vtype: int) -> None:
    if vtype == _STRING:
        _read_string(f)
        return
    if vtype == _ARRAY:
        (etype,) = struct.unpack("<I", f.read(4))
        (n,) = struct.unpack("<Q", f.read(8))
        if etype == _STRING:
            for _ in range(n):
                _read_string(f)
            return
        size = _SCALAR_SIZE.get(etype)
        if size is None:
            raise ValueError(f"unsupported GGUF array element type {etype}")
        f.read(size * n)
        return
    size = _SCALAR_SIZE.get(vtype)
    if size is None:
        raise ValueError(f"unsupported GGUF value type {vtype}")
    f.read(size)


def _read_scalar(f, vtype: int) -> int | float | bool | str:
    if vtype == _STRING:
        return _read_string(f)
    fmt = _SCALAR_FMT.get(vtype)
    if fmt is None:
        raise ValueError(f"unsupported GGUF scalar type {vtype}")
    return struct.unpack("<" + fmt, f.read(_SCALAR_SIZE[vtype]))[0]


def sniff_reasoning_flags(chat_template: str) -> dict[str, Any]:
    """Detect hybrid-reasoning Jinja knobs (Unsloth Studio-style substring sniff)."""
    tmpl = chat_template or ""
    supports_enable = "enable_thinking" in tmpl
    supports_effort = "reasoning_effort" in tmpl
    supports_preserve = "preserve_thinking" in tmpl or "preserve_reasoning" in tmpl
    # Always-on think markers without enable_thinking toggle.
    always_on = (not supports_enable) and (
        "<think>" in tmpl or "reasoning_content" in tmpl or "</think>" in tmpl
    )

    default_enable: bool | None = None
    if supports_enable:
        # Qwen3.5 large: `if enable_thinking is defined and enable_thinking is false` → else opens think
        # Small Unsloth: `if enable_thinking is defined and enable_thinking is true` → else closes think
        false_guard = "enable_thinking is defined and enable_thinking is false" in tmpl
        true_guard = "enable_thinking is defined and enable_thinking is true" in tmpl
        if false_guard and not true_guard:
            default_enable = True
        elif true_guard and not false_guard:
            default_enable = False
        else:
            default_enable = True

    return {
        "supports_enable_thinking": supports_enable,
        "supports_reasoning_effort": supports_effort,
        "supports_preserve_thinking": supports_preserve,
        "always_on_thinking": always_on,
        "default_enable_thinking": default_enable,
    }


def read_inference_metadata(path: Path | str) -> dict[str, Any] | None:
    """Read architecture, context_length, sampling, and reasoning flags from a GGUF.

    Returns None if the file is not a readable GGUF. Does not retain the full
    chat template string — only sniffed reasoning flags.
    """
    p = Path(path)
    try:
        with p.open("rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None
            (version,) = struct.unpack("<I", f.read(4))
            if version not in (2, 3):
                return None
            _n_tensors, n_kv = struct.unpack("<QQ", f.read(16))
            if n_kv > 100_000:
                return None

            arch: str | None = None
            context_length: int | None = None
            sampling: dict[str, float | int] = {}
            reasoning: dict[str, Any] = {
                "supports_enable_thinking": False,
                "supports_reasoning_effort": False,
                "supports_preserve_thinking": False,
                "always_on_thinking": False,
                "default_enable_thinking": None,
            }
            has_chat_template = False

            for _ in range(n_kv):
                key = _read_string(f)
                (vtype,) = struct.unpack("<I", f.read(4))
                if key == "general.architecture" and vtype == _STRING:
                    arch = str(_read_scalar(f, vtype))
                elif key.endswith(".context_length") and vtype in (
                    _UINT32,
                    _INT32,
                    _UINT64,
                    _INT64,
                ):
                    val = int(_read_scalar(f, vtype))
                    if arch and key == f"{arch}.context_length":
                        context_length = val if val > 0 else None
                    elif context_length is None and val > 0:
                        context_length = val
                elif key.startswith("general.sampling.") and vtype in (
                    _UINT32,
                    _INT32,
                    _UINT64,
                    _INT64,
                    _FLOAT32,
                    _FLOAT64,
                ):
                    suffix = key[len("general.sampling.") :]
                    mapped = _SAMPLING_KEY_MAP.get(suffix)
                    raw = _read_scalar(f, vtype)
                    if mapped is not None:
                        if mapped in ("top_k",):
                            sampling[mapped] = int(raw)
                        else:
                            sampling[mapped] = float(raw)
                elif key in ("tokenizer.chat_template", "tokenizer.ggml.chat_template") and vtype == _STRING:
                    tmpl = str(_read_scalar(f, vtype))
                    has_chat_template = bool(tmpl.strip())
                    reasoning = sniff_reasoning_flags(tmpl)
                else:
                    _skip_value(f, vtype)

            return {
                "architecture": arch,
                "context_length": context_length,
                "sampling": sampling,
                "reasoning": reasoning,
                "has_chat_template": has_chat_template,
            }
    except (OSError, struct.error, ValueError, UnicodeError):
        return None


def read_context_length(path: Path | str) -> int | None:
    """Return `{arch}.context_length` from a GGUF file, or None if unavailable."""
    meta = read_inference_metadata(path)
    if meta is None:
        return None
    ctx = meta.get("context_length")
    return int(ctx) if ctx else None
