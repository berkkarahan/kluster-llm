"""Read-only installed-engine capabilities and argument preflight."""

from __future__ import annotations

import re
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from kllm.extra_args import BLOCKED_FLAGS, ExtraArgsError, sanitize_extra_args


@dataclass(frozen=True)
class EngineFlag:
    aliases: tuple[str, ...]
    arguments: tuple[str, ...]
    description: str


def parse_server_help(help_text: str) -> dict[str, EngineFlag]:
    """Parse option headers, including long headers and their continuation text."""
    blocks: list[tuple[str, list[str]]] = []
    for line in help_text.splitlines():
        if re.match(r"^--?[A-Za-z]", line):
            parts = re.split(r"\s{2,}(?=[^\s-])", line, maxsplit=1)
            blocks.append((parts[0], parts[1:]))
        elif blocks and line.strip():
            blocks[-1][1].append(line.strip())
    result: dict[str, EngineFlag] = {}
    for header, lines in blocks:
        matches = list(re.finditer(r"--?[A-Za-z][\w-]*", header))
        if not matches:
            continue
        aliases = tuple(match.group() for match in matches)
        suffix = header[matches[-1].end():].strip()
        arguments = tuple(suffix.split())
        description = " ".join(lines)
        if "removed" in description.lower() or "deprecated" in description.lower():
            continue
        spec = EngineFlag(aliases, arguments, description)
        result.update({alias: spec for alias in aliases})
    return result


@lru_cache(maxsize=8)
def _read_catalog(path: str, mtime_ns: int, size: int) -> dict[str, EngineFlag]:
    del mtime_ns, size
    try:
        result = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=15, check=False,
                                env={key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtraArgsError("Could not inspect llama-server; check the installed runtime") from exc
    if result.returncode:
        raise ExtraArgsError("llama-server --help failed; check the installed runtime")
    catalog = parse_server_help(result.stdout + "\n" + result.stderr)
    if not catalog:
        raise ExtraArgsError("llama-server returned no supported parameter information")
    return catalog


def engine_catalog() -> dict[str, EngineFlag]:
    from kllm import native
    from kllm.state import repo_root

    root = repo_root()
    binary = native.resolve_llama_server(str(root) if root else None)
    if not binary:
        raise ExtraArgsError("Install llama-server before validating engine options")
    try:
        stat = Path(binary).stat()
    except OSError as exc:
        raise ExtraArgsError("The configured llama-server binary is unavailable") from exc
    return _read_catalog(binary, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=64)
def _validate_parser(path: str, mtime_ns: int, tokens: tuple[str, ...]) -> None:
    del mtime_ns
    try:
        result = subprocess.run([path, *tokens, "--help"], capture_output=True, text=True, timeout=15, check=False,
                                env={key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtraArgsError("Could not validate the installed engine's arguments") from exc
    if result.returncode:
        details = [line.strip() for line in (result.stderr + result.stdout).splitlines()
                   if re.search(r"invalid|error|exception", line, re.IGNORECASE)]
        raise ExtraArgsError("Engine argument validation failed: " + (details[-1][:500] if details else "check the option values"))


def validate_engine_parser(tokens: list[str]) -> None:
    """Exercise the engine parser in help mode; no model or server is started."""
    if not tokens:
        return
    from kllm import native
    from kllm.state import repo_root
    root = repo_root()
    binary = native.resolve_llama_server(str(root) if root else None)
    if not binary:
        raise ExtraArgsError("Install llama-server before validating engine options")
    try:
        stat = Path(binary).stat()
    except OSError as exc:
        raise ExtraArgsError("The configured llama-server binary is unavailable") from exc
    _validate_parser(binary, stat.st_mtime_ns, tuple(tokens))


def validate_engine_args(tokens: list[str], catalog: dict[str, EngineFlag]) -> None:
    tokens = sanitize_extra_args(tokens)
    index = 0
    while index < len(tokens):
        flag = tokens[index]
        spec = catalog.get(flag)
        if spec is None:
            raise ExtraArgsError(f"extra_args: {flag!r} is not supported by the installed llama-server")
        for alias in spec.aliases:
            sanitize_extra_args([alias])
        count = len(spec.arguments)
        values = tokens[index + 1:index + 1 + count]
        if len(values) != count or any(value in catalog for value in values):
            raise ExtraArgsError(f"extra_args: {flag} requires {count} value(s)")
        for placeholder, value in zip(spec.arguments, values):
            choices = re.fullmatch(r"[\[{]([\w.,|+-]+)[\]}]", placeholder)
            if choices and value not in re.split(r"[,|]", choices.group(1)):
                raise ExtraArgsError(f"extra_args: invalid value for {flag}; choose {choices.group(1)}")
        index += 1 + count


def public_expert_flags(catalog: dict[str, EngineFlag]) -> list[dict[str, Any]]:
    from kllm.parameter_catalog import ALIASES

    out = []
    for flag, spec in catalog.items():
        if not flag.startswith("--") or flag != next((a for a in spec.aliases if a.startswith("--")), flag):
            continue
        try:
            for alias in spec.aliases:
                sanitize_extra_args([alias])
        except ExtraArgsError:
            continue
        if flag in BLOCKED_FLAGS or flag in ALIASES:
            continue
        out.append({"flag": flag, "arguments": list(spec.arguments), "description": spec.description})
    return sorted(out, key=lambda item: item["flag"])
