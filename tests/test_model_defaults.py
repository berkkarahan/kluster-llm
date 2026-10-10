from __future__ import annotations

import struct
from pathlib import Path

import kllm.modules.models.interactors.gguf_meta as gguf_meta
import kllm.modules.inference.interactors.model_defaults as model_defaults
from kllm.modules.inference.interactors.model_defaults import (
    ModelDefaults,
    apply_defaults_to_chat_body,
    clear_defaults_cache,
    merge_defaults_into_extra_args,
    resolve_model_defaults,
)


def _write_string(buf: bytearray, s: str) -> None:
    b = s.encode("utf-8")
    buf += struct.pack("<Q", len(b))
    buf += b


def _write_kv_string(buf: bytearray, key: str, value: str) -> None:
    _write_string(buf, key)
    buf += struct.pack("<I", 8)
    _write_string(buf, value)


def _write_kv_u32(buf: bytearray, key: str, value: int) -> None:
    _write_string(buf, key)
    buf += struct.pack("<I", 4)
    buf += struct.pack("<I", value)


def _write_kv_f32(buf: bytearray, key: str, value: float) -> None:
    _write_string(buf, key)
    buf += struct.pack("<I", 6)
    buf += struct.pack("<f", value)


def _gguf(writers) -> bytes:
    parts: list[bytes] = []
    for write in writers:
        buf = bytearray()
        write(buf)
        parts.append(bytes(buf))
    return struct.pack("<4sIQQ", b"GGUF", 3, 0, len(parts)) + b"".join(parts)


def _thinking_on_gguf() -> bytes:
    tmpl = (
        "{%- if enable_thinking is defined and enable_thinking is false -%}"
        "<think></think>{%- else -%}<think>\n{%- endif -%}"
    )
    return _gguf(
        [
            lambda b: _write_kv_string(b, "general.architecture", "qwen35moe"),
            lambda b: _write_kv_u32(b, "qwen35moe.context_length", 4096),
            lambda b: _write_kv_f32(b, "general.sampling.temp", 0.6),
            lambda b: _write_kv_u32(b, "general.sampling.top_k", 20),
            lambda b: _write_kv_f32(b, "general.sampling.top_p", 0.95),
            lambda b: _write_kv_string(b, "tokenizer.chat_template", tmpl),
        ]
    )


def _plain_llama_gguf() -> bytes:
    return _gguf(
        [
            lambda b: _write_kv_string(b, "general.architecture", "llama"),
            lambda b: _write_kv_u32(b, "llama.context_length", 2048),
        ]
    )


def test_resolve_gguf_wins_and_heuristic_fills(tmp_path: Path):
    clear_defaults_cache()
    path = tmp_path / "qwen.gguf"
    path.write_bytes(_thinking_on_gguf())
    d = resolve_model_defaults(path)
    assert abs(float(d.sampling["temperature"]) - 0.6) < 1e-5
    assert d.sampling["top_k"] == 20
    assert d.sources["temperature"] == "gguf"
    assert d.sampling["min_p"] == 0.0
    assert d.sampling["presence_penalty"] == 1.5
    assert d.sources["min_p"] == "heuristic"
    assert d.chat_template_kwargs.get("enable_thinking") is True
    assert d.reasoning_format == "deepseek"
    assert d.jinja is True


def test_resolve_no_heuristic_for_plain_llama(tmp_path: Path):
    clear_defaults_cache()
    path = tmp_path / "llama.gguf"
    path.write_bytes(_plain_llama_gguf())
    d = resolve_model_defaults(path)
    assert d.sampling == {}
    assert d.chat_template_kwargs == {}
    assert d.reasoning_format is None


def test_defaults_cache_reuses_identical_file_and_invalidates_truncation(
    tmp_path: Path, monkeypatch
):
    clear_defaults_cache()
    gguf_meta._read_inference_metadata.cache_clear()
    path = tmp_path / "cached-defaults.gguf"
    path.write_bytes(_thinking_on_gguf())
    original_reader = model_defaults.read_inference_metadata
    reads = 0

    def counted_reader(file_path, *, include_template=False):
        nonlocal reads
        reads += 1
        return original_reader(file_path, include_template=include_template)

    monkeypatch.setattr(model_defaults, "read_inference_metadata", counted_reader)
    first = resolve_model_defaults(path)
    second = resolve_model_defaults(path)
    assert second == first
    assert reads == 1

    path.write_bytes(b"GGUF\x03\x00")
    truncated = resolve_model_defaults(path)
    assert truncated == ModelDefaults()
    assert reads == 2


def test_thinking_off_template_uses_instruct_heuristic(tmp_path: Path):
    clear_defaults_cache()
    tmpl = (
        "{%- if enable_thinking is defined and enable_thinking is true -%}"
        "<think>\n{%- else -%}<think></think>{%- endif -%}"
    )
    path = tmp_path / "small.gguf"
    path.write_bytes(
        _gguf(
            [
                lambda b: _write_kv_string(b, "general.architecture", "qwen3"),
                lambda b: _write_kv_u32(b, "qwen3.context_length", 4096),
                lambda b: _write_kv_string(b, "tokenizer.chat_template", tmpl),
            ]
        )
    )
    d = resolve_model_defaults(path)
    assert d.chat_template_kwargs.get("enable_thinking") is False
    assert d.sampling["temperature"] == 0.7
    assert d.sampling["top_p"] == 0.8
    assert d.sources["temperature"] == "heuristic"


def test_apply_defaults_request_wins():
    defaults = ModelDefaults(
        sampling={"temperature": 0.6, "top_k": 20, "min_p": 0.0},
        chat_template_kwargs={"enable_thinking": True},
    )
    body = apply_defaults_to_chat_body(
        {
            "messages": [],
            "temperature": 0.1,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        defaults,
    )
    assert body["temperature"] == 0.1
    assert body["top_k"] == 20
    assert body["min_p"] == 0.0
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_merge_defaults_into_extra_args_skips_existing():
    defaults = ModelDefaults(
        sampling={"temperature": 0.6, "top_k": 20},
        jinja=True,
        chat_template_kwargs={"enable_thinking": True},
        reasoning_format="deepseek",
    )
    merged = merge_defaults_into_extra_args(["--temp", "0.9", "--foo"], defaults)
    assert merged.count("--temp") == 1
    assert "--top-k" in merged
    assert "--jinja" in merged
    assert "--chat-template-kwargs" in merged
    assert "--reasoning-format" in merged
    assert "--foo" in merged
