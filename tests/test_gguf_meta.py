from __future__ import annotations

import struct
from pathlib import Path

from kllm import gguf_meta
import pytest

from kllm.gguf_meta import (
    is_moe_architecture,
    read_context_length,
    read_inference_metadata,
    sniff_reasoning_flags,
)


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


def _write_kv_f32(buf: bytearray, key: str, value: float) -> None:
    _write_string(buf, key)
    buf += struct.pack("<I", 6)  # FLOAT32
    buf += struct.pack("<f", value)


def _gguf_from_writers(writers) -> bytes:
    parts: list[bytes] = []
    for write in writers:
        buf = bytearray()
        write(buf)
        parts.append(bytes(buf))
    return struct.pack("<4sIQQ", b"GGUF", 3, 0, len(parts)) + b"".join(parts)


def _minimal_gguf(*, arch: str, context_length: int) -> bytes:
    return _gguf_from_writers(
        [
            lambda b: _write_kv_string(b, "general.architecture", arch),
            lambda b: _write_kv_u32(b, f"{arch}.context_length", context_length),
        ]
    )


def test_read_context_length_from_minimal_gguf(tmp_path: Path):
    path = tmp_path / "toy.gguf"
    path.write_bytes(_minimal_gguf(arch="llama", context_length=8192))
    assert read_context_length(path) == 8192


def test_metadata_cache_is_stat_keyed_and_returns_isolated_values(tmp_path: Path):
    gguf_meta._read_inference_metadata.cache_clear()
    path = tmp_path / "cached.gguf"
    path.write_bytes(_minimal_gguf(arch="llama", context_length=8192))
    first = read_inference_metadata(path)
    assert first is not None
    first["sampling"]["temperature"] = 99
    before = gguf_meta._read_inference_metadata.cache_info()
    second = read_inference_metadata(path)
    after = gguf_meta._read_inference_metadata.cache_info()
    assert second is not None and second["sampling"] == {}
    assert after.hits == before.hits + 1

    path.write_bytes(_minimal_gguf(arch="llama", context_length=4096))
    assert read_context_length(path) == 4096

    path.write_bytes(b"GGUF\x03\x00")
    before_truncated_read = gguf_meta._read_inference_metadata.cache_info()
    assert read_inference_metadata(path) is None
    after_truncated_read = gguf_meta._read_inference_metadata.cache_info()
    assert after_truncated_read.misses == before_truncated_read.misses + 1


def test_read_context_length_invalid_returns_none(tmp_path: Path):
    path = tmp_path / "bad.gguf"
    path.write_bytes(b"not-a-gguf")
    assert read_context_length(path) is None


def test_read_inference_metadata_sampling_and_template(tmp_path: Path):
    tmpl = (
        "{%- if enable_thinking is defined and enable_thinking is false -%}"
        "<think></think>"
        "{%- else -%}"
        "<think>\n"
        "{%- endif -%}"
    )
    path = tmp_path / "qwen.gguf"
    path.write_bytes(
        _gguf_from_writers(
            [
                lambda b: _write_kv_string(b, "general.architecture", "qwen35moe"),
                lambda b: _write_kv_u32(b, "qwen35moe.context_length", 262144),
                lambda b: _write_kv_u32(b, "qwen35moe.block_count", 40),
                lambda b: _write_kv_f32(b, "general.sampling.temp", 0.6),
                lambda b: _write_kv_u32(b, "general.sampling.top_k", 20),
                lambda b: _write_kv_f32(b, "general.sampling.top_p", 0.95),
                lambda b: _write_kv_string(b, "tokenizer.chat_template", tmpl),
            ]
        )
    )

    meta = read_inference_metadata(path)
    assert meta is not None
    assert meta["architecture"] == "qwen35moe"
    assert meta["context_length"] == 262144
    assert meta["block_count"] == 40
    assert abs(float(meta["sampling"]["temperature"]) - 0.6) < 1e-5
    assert meta["sampling"]["top_k"] == 20
    assert abs(float(meta["sampling"]["top_p"]) - 0.95) < 1e-5
    assert meta["has_chat_template"] is True
    assert meta["reasoning"]["supports_enable_thinking"] is True
    assert meta["reasoning"]["default_enable_thinking"] is True


def test_sniff_reasoning_default_off_when_true_guard():
    tmpl = (
        "{%- if enable_thinking is defined and enable_thinking is true -%}"
        "<think>\n"
        "{%- else -%}"
        "<think></think>"
        "{%- endif -%}"
    )
    flags = sniff_reasoning_flags(tmpl)
    assert flags["supports_enable_thinking"] is True
    assert flags["default_enable_thinking"] is False


def test_sniff_reasoning_effort_and_preserve():
    tmpl = "foo reasoning_effort bar preserve_thinking baz enable_thinking"
    flags = sniff_reasoning_flags(tmpl)
    assert flags["supports_reasoning_effort"] is True
    assert flags["supports_preserve_thinking"] is True
    assert flags["supports_enable_thinking"] is True


@pytest.mark.parametrize(
    "arch",
    ["gpt-oss", "gpt_oss", "GPT-OSS", "glm4moe", "qwen3next"],
)
def test_is_moe_architecture_accepts_known_moe_names(arch: str):
    assert is_moe_architecture(arch) is True


@pytest.mark.parametrize("arch", ["llama", "qwen35", None, ""])
def test_is_moe_architecture_rejects_dense_and_empty_names(arch: str | None):
    assert is_moe_architecture(arch) is False
