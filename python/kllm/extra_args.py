"""Sanitize user-supplied llama-server argv tokens (research R8)."""

from __future__ import annotations

import re
import shlex

FLAG_RE = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9-_]*$")
SHELL_META = re.compile(r"[;&|`$<>(){}\\!\n\r]")
BLOCKED_FLAGS = frozenset(
    {
        "--host",
        "--port",
        "--rpc",
        "--device", "-dev", "--tensor-split", "-ts", "--split-mode", "-sm",
        "--gpu-layers", "--n-gpu-layers", "-ngl", "--main-gpu", "-mg",
        "--override-tensor", "-ot", "--cpu-moe", "-cmoe", "--n-cpu-moe", "-ncmoe",
        "--n-cpu-ffn", "-ncffn", "--fit", "-fit", "--override-kv",
        "--model", "-m", "--model-url", "-mu", "--hf-repo", "-hf", "-hfr",
        "--no-kv-offload", "-nkvo", "--no-op-offload",
        "--kv-unified-per-slot", "--model-draft", "-md", "--mmproj", "-mm",
        "-h",
        "-p",
    }
)


class ExtraArgsError(ValueError):
    pass


def tokenize_extra_args(raw: list[str]) -> list[str]:
    """Flatten whitespace-separated tokens from a list of strings."""
    out: list[str] = []
    for part in raw:
        if not part or not str(part).strip():
            continue
        for token in shlex.split(str(part), posix=True):
            if token.startswith("--") and "=" in token:
                out.extend(token.split("=", 1))
            else:
                out.append(token)
    return out


def sanitize_extra_args(tokens: list[str]) -> list[str]:
    """Validate tokens; reject shell metacharacters and unsafe cluster overrides."""
    cleaned = tokenize_extra_args(tokens)
    safe: list[str] = []
    i = 0
    while i < len(cleaned):
        tok = cleaned[i]
        if SHELL_META.search(tok):
            raise ExtraArgsError(f"extra_args: shell metacharacters not allowed in {tok!r}")
        if tok.startswith("-") and not FLAG_RE.match(tok):
            raise ExtraArgsError(f"extra_args: invalid flag {tok!r}")
        if tok in BLOCKED_FLAGS:
            raise ExtraArgsError(f"extra_args: {tok!r} is managed by kllm and cannot be overridden")
        safe.append(tok)
        if tok.startswith("-") and i + 1 < len(cleaned) and not cleaned[i + 1].startswith("-"):
            nxt = cleaned[i + 1]
            if SHELL_META.search(nxt):
                raise ExtraArgsError(f"extra_args: unsafe value for {tok!r}")
            if tok in ("--host", "--port", "--rpc"):
                raise ExtraArgsError(f"extra_args: {tok!r} is managed by kllm")
            safe.append(nxt)
            i += 2
            continue
        i += 1
    return safe


def merge_extra_args(first_class: list[str], extra: list[str]) -> list[str]:
    return list(first_class) + sanitize_extra_args(extra)
