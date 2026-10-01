"""CLI parsing and interactive selection for GPU options."""

from __future__ import annotations

import sys

import httpx
import typer

from kllm.core.gpu_selection import parse_gpu_ids as _parse_gpu_ids
from kllm.exceptions import ValidationError
from kllm.commands.ui import console, format_vram


def parse_gpu_ids(value: str | None) -> list[int] | None:
    """Parse ``--gpus 0,1`` into a list of ints. ``None`` means unspecified."""
    try:
        return _parse_gpu_ids(value)
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _prompt_gpu_ids(gpus: list[dict[str, object]], backend: str) -> list[int] | None:
    """Prompt for selected GPUs when multiple devices and stdin is a TTY."""
    if len(gpus) <= 1 or not sys.stdin.isatty():
        return None
    console.print(f"[bold]Select GPUs[/bold] (backend={backend})")
    for gpu in gpus:
        index = gpu.get("index")
        name = gpu.get("name", "GPU")
        vram_bytes = gpu.get("vram_bytes")
        vram = vram_bytes if type(vram_bytes) is int else None
        console.print(f"  [{index}] {name}  {format_vram(vram)}")
    default = ",".join(str(gpu["index"]) for gpu in gpus)
    raw = typer.prompt(
        "GPU indices (comma-separated, Enter = all)",
        default=default,
        show_default=True,
    )
    return parse_gpu_ids(raw)


def resolve_cli_gpus(client: httpx.Client, gpus_flag: str | None) -> list[int] | None:
    """Resolve ``--gpus`` or prompt interactively; None means agent default."""
    explicit = parse_gpu_ids(gpus_flag)
    if explicit is not None:
        return explicit
    try:
        response = client.get("/v2/node/devices")
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    rows = data.get("gpus") or []
    if not isinstance(rows, list):
        return None
    gpus = [row for row in rows if isinstance(row, dict)]
    return _prompt_gpu_ids(gpus, str(data.get("backend") or "cpu"))
