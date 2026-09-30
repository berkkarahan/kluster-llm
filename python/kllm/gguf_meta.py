"""Minimal GGUF metadata reader (context length, sampling, chat-template sniff)."""

from __future__ import annotations

import copy
import json
import struct
import re
from functools import lru_cache
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


def _skip_string(f) -> None:
    (n,) = struct.unpack("<Q", f.read(8))
    if n > 10_000_000:
        raise ValueError("GGUF string length too large")
    if len(f.read(n)) != n:
        raise ValueError("truncated GGUF string")


def _skip_value(f, vtype: int) -> None:
    if vtype == _STRING:
        _skip_string(f)
        return
    if vtype == _ARRAY:
        (etype,) = struct.unpack("<I", f.read(4))
        (n,) = struct.unpack("<Q", f.read(8))
        if etype == _STRING:
            for _ in range(n):
                _skip_string(f)
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


def read_inference_metadata(path: Path | str, *, include_template: bool = False) -> dict[str, Any] | None:
    """Return bounded stat-keyed metadata without rescanning unchanged GGUF files."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    result = _read_inference_metadata(
        str(p.resolve()), st.st_size, st.st_mtime_ns, st.st_ctime_ns, include_template
    )
    # Cached metadata is shared by inventory, defaults, and planning callers.
    # Return an isolated value so a caller cannot poison subsequent results.
    return copy.deepcopy(result)


@lru_cache(maxsize=32)
def _read_inference_metadata(
    path: str, size: int, mtime: int, ctime: int, include_template: bool
) -> dict[str, Any] | None:
    """Read architecture, context_length, sampling, and reasoning flags from a GGUF.

    Returns None if the file is not a readable GGUF. Does not retain the full
    chat template string — only sniffed reasoning flags.
    """
    del size, mtime, ctime
    p = Path(path)
    try:
        # Optional native fast path: tokenizer string tables can contain
        # hundreds of thousands of entries, making the bounded Python fallback
        # expensive even though their contents are intentionally skipped.
        from kllm import _native

        native_reader = getattr(_native, "_read_gguf_metadata_json", None)
        if native_reader is not None:
            encoded = native_reader(path, include_template)
            return json.loads(encoded) if encoded is not None else None
    except ImportError:
        pass
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
            block_count: int | None = None
            sampling: dict[str, float | int] = {}
            reasoning: dict[str, Any] = {
                "supports_enable_thinking": False,
                "supports_reasoning_effort": False,
                "supports_preserve_thinking": False,
                "always_on_thinking": False,
                "default_enable_thinking": None,
            }
            has_chat_template = False
            chat_template_text = None

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
                elif key.endswith(".block_count") and vtype in (
                    _UINT32,
                    _INT32,
                    _UINT64,
                    _INT64,
                ):
                    val = int(_read_scalar(f, vtype))
                    if arch and key == f"{arch}.block_count" and val > 0:
                        block_count = val
                    elif block_count is None and val > 0:
                        block_count = val
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
                    if include_template and len(tmpl.encode("utf-8")) <= 65536:
                        chat_template_text = tmpl
                    reasoning = sniff_reasoning_flags(tmpl)
                else:
                    _skip_value(f, vtype)

            return {
                "architecture": arch,
                "context_length": context_length,
                "block_count": block_count,
                "sampling": sampling,
                "reasoning": reasoning,
                "has_chat_template": has_chat_template,
                **({"chat_template_text": chat_template_text} if include_template else {}),
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


def read_model_layout(path: Path | str) -> dict[str, Any] | None:
    p = Path(path)
    st = p.stat()
    return _read_model_layout(str(p.resolve()), st.st_size, st.st_mtime_ns, st.st_ctime_ns)


@lru_cache(maxsize=16)
def _read_model_layout(path: str, size: int, mtime: int, ctime: int) -> dict[str, Any] | None:
    """Read tensor spans without loading weights; spans include alignment padding."""
    del mtime, ctime
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            version, n_tensors, n_kv = struct.unpack("<IQQ", f.read(20))
            if version not in (2, 3) or n_tensors > 1_000_000 or n_kv > 100_000:
                return None
            meta = {}
            for _ in range(n_kv):
                key = _read_string(f)
                vtype, = struct.unpack("<I", f.read(4))
                if key.startswith("tokenizer."):
                    _skip_value(f, vtype)
                elif vtype == _ARRAY:
                    etype, count = struct.unpack("<IQ", f.read(12))
                    if count > 100_000:
                        raise ValueError("metadata array too large")
                    meta[key] = [_read_scalar(f, etype) for _ in range(count)]
                else:
                    meta[key] = _read_scalar(f, vtype)
            tensors = []
            for _ in range(n_tensors):
                name = _read_string(f)
                ndim, = struct.unpack("<I", f.read(4))
                if ndim > 4:
                    raise ValueError("invalid tensor dimensions")
                dims = struct.unpack("<" + "Q" * ndim, f.read(8 * ndim))
                dtype, offset = struct.unpack("<IQ", f.read(12))
                tensors.append(dict(name=name, dimensions=dims, dtype=dtype, offset=offset))
            alignment = int(meta.get("general.alignment", 32))
            if alignment <= 0:
                raise ValueError("invalid alignment")
            data_start = (f.tell() + alignment - 1) // alignment * alignment
            tensors.sort(key=lambda t: t["offset"])
            arch = str(meta.get("general.architecture", "unknown"))
            n_layers = int(meta.get(f"{arch}.block_count", 0))
            if not 0 < n_layers <= 10000:
                return None
            layers = [0] * n_layers
            output = inputs = token_embd = 0
            has_output = False
            for i, tensor in enumerate(tensors):
                end = tensors[i + 1]["offset"] if i + 1 < len(tensors) else size - data_start
                nbytes = end - tensor["offset"]
                if nbytes < 0 or data_start + end > size:
                    raise ValueError("invalid tensor span")
                tensor["size_bytes"] = nbytes
                match = re.match(r"blk\.(\d+)\.", tensor["name"])
                if match and int(match[1]) < n_layers:
                    layers[int(match[1])] += nbytes
                elif tensor["name"].startswith("output"):
                    output += nbytes
                    has_output |= tensor["name"] == "output.weight"
                else:
                    inputs += nbytes
                    if tensor["name"] == "token_embd.weight":
                        token_embd = nbytes
            if not has_output:
                output += token_embd  # tied embeddings are duplicated for offloaded output
            return dict(architecture=arch, metadata=meta, layers=layers,
                        output_bytes=output, input_bytes=inputs, tensors=tensors,
                        confidence="metadata" if tensors and all(layers) else "approximate")
    except (OSError, ValueError, struct.error):
        return None
