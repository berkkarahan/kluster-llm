"""Minimal GGUF metadata reader (context length only)."""

from __future__ import annotations

import struct
from pathlib import Path

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


def read_context_length(path: Path | str) -> int | None:
    """Return `{arch}.context_length` from a GGUF file, or None if unavailable."""
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
                        return val if val > 0 else None
                    if context_length is None and val > 0:
                        context_length = val
                else:
                    _skip_value(f, vtype)
            return context_length
    except (OSError, struct.error, ValueError, UnicodeError):
        return None
