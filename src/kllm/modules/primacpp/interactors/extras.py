"""Translate declared Prima launch extras into owned, immutable inputs."""

import hashlib
import json
import math
import struct
from pathlib import Path

from kllm.modules.inference.interactors.extra_args import sanitize_extra_args

MAX_KWARGS_BYTES = 64 * 1024
HEAD_OPTIONS = {"--embedding": 0, "--embeddings": 0, "--reranking": 0, "--rerank": 0, "--pooling": 1}


def _head_option(tokens: list[str], index: int) -> tuple[list[str], int]:
    flag = tokens[index]
    end = index + HEAD_OPTIONS[flag] + 1
    if end > len(tokens):
        raise ValueError(f"Prima {flag} requires a value")
    if flag == "--pooling" and tokens[index + 1] not in ("none", "mean", "cls", "last", "rank"):
        raise ValueError("Prima pooling requires none, mean, cls, last or rank")
    return tokens[index:end], end


def head_only_arguments(settings: dict) -> list[str]:
    """Server modes affect the head; workers receive evaluated output selection."""
    declared_extras(settings)
    tokens = sanitize_extra_args(settings.get("extra_args") or [])
    arguments = []
    index = 0
    while index < len(tokens):
        flag = tokens[index]
        if flag in HEAD_OPTIONS:
            values, index = _head_option(tokens, index)
            arguments.extend(values)
        elif flag in ("--chat-template-kwargs", "--lora", "--lora-scaled"):
            index += 3 if flag == "--lora-scaled" else 2
        else:
            raise ValueError(f"Prima does not implement launch extra {flag}")
    reranking = any(flag in ("--reranking", "--rerank") for flag in arguments)
    embedding = any(flag in ("--embedding", "--embeddings") for flag in arguments)
    if reranking and embedding:
        raise ValueError("Prima reranking and embedding modes are mutually exclusive")
    if reranking and "--pooling" in arguments and arguments[arguments.index("--pooling") + 1] != "rank":
        raise ValueError("Prima reranking requires rank pooling")
    return arguments


def declared_extras(settings: dict) -> tuple[str | None, list[dict]]:
    tokens = sanitize_extra_args(settings.get("extra_args") or [])
    source = None
    adapters = []
    index = 0
    while index < len(tokens):
        flag = tokens[index]
        if flag == "--chat-template-kwargs":
            if source is not None or index + 1 >= len(tokens):
                raise ValueError("Prima template defaults require one --chat-template-kwargs JSON value")
            source = tokens[index + 1]
            index += 2
        elif flag in ("--lora", "--lora-scaled"):
            count = 3 if flag == "--lora-scaled" else 2
            if index + count > len(tokens) or not tokens[index + 1]:
                raise ValueError("Prima LoRA requires an adapter path and optional finite scale")
            try:
                scale = float(tokens[index + 2]) if count == 3 else 1.0
            except ValueError as error:
                raise ValueError("Prima LoRA scale must be finite") from error
            if not math.isfinite(scale) or abs(scale) > 3.4028234663852886e38:
                raise ValueError("Prima LoRA scale must fit a finite native float")
            native_scale = struct.unpack("f", struct.pack("f", scale))[0]
            if scale and native_scale == 0:
                raise ValueError("Prima LoRA scale underflows a native float")
            adapters.append({"path": tokens[index + 1], "scale": native_scale})
            index += count
        elif flag in HEAD_OPTIONS:
            _, index = _head_option(tokens, index)
        else:
            raise ValueError(f"Prima does not implement launch extra {flag}")
    return source, adapters


def adapter_requests(settings: dict) -> list[dict]:
    return declared_extras(settings)[1]


def template_kwargs(settings: dict) -> str | None:
    source = declared_extras(settings)[0]
    if source is None:
        return None
    try:
        if len(source.encode("utf-8")) > MAX_KWARGS_BYTES:
            raise ValueError("maximum 64 KiB")
        values = json.loads(source)
        if not isinstance(values, dict):
            raise ValueError("expected an object")
        # Preserve insertion order for templates that iterate user dictionaries.
        encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        encoded_bytes = encoded.encode("utf-8")
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Prima template keyword defaults must be a finite JSON object") from error
    if len(encoded_bytes) > MAX_KWARGS_BYTES:
        raise ValueError("Prima template keyword defaults must be at most 64 KiB")
    return encoded


def materialize_template_kwargs(settings: dict, directory: Path) -> Path | None:
    source = template_kwargs(settings)
    if source is None:
        return None
    data = source.encode("utf-8")
    path = directory / ("template-kwargs-" + hashlib.sha256(data).hexdigest() + ".json")
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != len(data) or path.read_bytes() != data:
            raise ValueError("Owned Prima template keyword file differs from its signed source")
    return path


def extra_arguments(flags: set[str], settings: dict, *, rank: int, kwargs_file: Path | None = None) -> list[str]:
    source = template_kwargs(settings)
    head = head_only_arguments(settings) if rank == 0 else []
    if any(flag in HEAD_OPTIONS and flag not in flags for flag in head):
        raise ValueError("Upgrade Prima for the requested head embedding/pooling mode")
    if adapter_requests(settings) and "--lora-scaled" not in flags:
        raise ValueError("Upgrade Prima for scaled LoRA launch arguments")
    if source is None or rank != 0:
        return head
    if not {"--chat-template-kwargs", "--chat-template-kwargs-file", "--jinja"} <= flags:
        raise ValueError("Upgrade Prima for deployment template keyword defaults")
    return head + (["--chat-template-kwargs-file", str(kwargs_file)] if kwargs_file else
            ["--chat-template-kwargs", source])
