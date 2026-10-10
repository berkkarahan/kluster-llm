"""Sanitize user-supplied llama-server argv tokens (research R8)."""

from __future__ import annotations

import re
import shlex

from kllm.shared.exceptions import ValidationError

FLAG_RE = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9-_]*$")
BLOCKED_FLAGS = frozenset(
    {
        "--host",
        "--port",
        "--rpc",
        "--device",
        "-dev",
        "--tensor-split",
        "-ts",
        "--split-mode",
        "-sm",
        "--gpu-layers",
        "--n-gpu-layers",
        "-ngl",
        "--main-gpu",
        "-mg",
        "--override-tensor",
        "-ot",
        "--cpu-moe",
        "-cmoe",
        "--n-cpu-moe",
        "-ncmoe",
        "--n-cpu-ffn",
        "-ncffn",
        "--fit",
        "-fit",
        "--override-kv",
        "--model",
        "-m",
        "--model-url",
        "-mu",
        "--hf-repo",
        "-hf",
        "-hfr",
        "--chat-template",
        "--chat-template-file",
        "--jinja",
        "--no-jinja",
        "--no-kv-offload",
        "-nkvo",
        "--no-op-offload",
        "--kv-offload",
        "-kvo",
        "--lazy-mode",
        "-lzm",
        "--kv-unified-per-slot",
        "--model-draft",
        "-md",
        "--mmproj",
        "-mm",
        "-h",
        "-p",
        "--help",
        "--usage",
        "--version",
        "--list-devices",
        "--cache-list",
        "-cl",
        "--completion-bash",
        "--api-key",
        "--api-key-file",
        "--ssl-key-file",
        "--ssl-cert-file",
        "--webui",
        "--no-webui",
        "--path",
        "--media-path",
        "--models-dir",
        "--models-preset",
        "--models-max",
        "--models-autoload",
        "--props",
        "--tools",
        "--tools-file",
        "--mcp",
        "--mcp-config",
        "--hf-token",
        "-hft",
        "--hf-file",
        "-hff",
        "--log-file",
        "--log-disable",
        "--log-prompts-dir",
        "--log-verbosity",
        "--verbosity",
        "-lv",
    }
)


class ExtraArgsError(ValidationError):
    pass


def tokenize_extra_args(raw: list[str]) -> list[str]:
    """Parse repeated CLI strings once. API argv arrays must not use this helper."""
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
    """Validate an argv vector, preserving literal paths, JSON and negative values."""
    cleaned: list[str] = []
    for token in tokens:
        if not isinstance(token, str) or "\0" in token:
            raise ExtraArgsError("extra_args: arguments must be strings without NUL")
        if token.startswith("--") and "=" in token:
            cleaned.extend(token.split("=", 1))
        else:
            cleaned.append(token)
    if cleaned and not FLAG_RE.fullmatch(cleaned[0]):
        raise ExtraArgsError(f"extra_args: expected a flag, got {cleaned[0]!r}")
    for tok in cleaned:
        if FLAG_RE.fullmatch(tok) and (
            tok in BLOCKED_FLAGS
            or tok.startswith(
                (
                    "--models-",
                    "--no-models-",
                    "--tool",
                    "--mcp",
                    "--ssl-",
                    "--api-key",
                    "--log-",
                    "--no-log-",
                    "--hf-",
                    "--docker-",
                )
            )
            or tok.endswith("-default")
        ):
            raise ExtraArgsError(f"extra_args: {tok!r} is managed by kllm and cannot be overridden")
    return cleaned


def merge_extra_args(first_class: list[str], extra: list[str]) -> list[str]:
    return list(first_class) + sanitize_extra_args(extra)
