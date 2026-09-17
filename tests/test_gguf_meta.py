from __future__ import annotations

import struct
from pathlib import Path

from kllm.gguf_meta import read_context_length


def _write_string(buf: bytearray, s: str) -> None:
    b = s.encode("utf-8")
    buf += struct.pack("<Q", len(b))
    buf += b


def _write_kv_string(buf: bytearray, key: str, value: str) -> None:
    _write_string(buf, key)
    buf += struct.pack("<I", 8)  # STRING
    _write_string(buf, value)


def _write_kv_u32(buf: bytearray, key: str, value: int) -> None:
    _write_string(buf, key)
    buf += struct.pack("<I", 4)  # UINT32
    buf += struct.pack("<I", value)


def _minimal_gguf(*, arch: str, context_length: int) -> bytes:
    meta = bytearray()
    _write_kv_string(meta, "general.architecture", arch)
    _write_kv_u32(meta, f"{arch}.context_length", context_length)
    header = struct.pack("<4sIQQ", b"GGUF", 3, 0, 2)
    return bytes(header + meta)


def test_read_context_length_from_minimal_gguf(tmp_path: Path):
    path = tmp_path / "toy.gguf"
    path.write_bytes(_minimal_gguf(arch="llama", context_length=8192))
    assert read_context_length(path) == 8192


def test_read_context_length_invalid_returns_none(tmp_path: Path):
    path = tmp_path / "bad.gguf"
    path.write_bytes(b"not-a-gguf")
    assert read_context_length(path) is None
