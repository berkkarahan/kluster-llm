"""Shared terminal rendering primitives for CLI commands."""

from rich.console import Console

console = Console()


def format_vram(vram_bytes: int | None) -> str:
    """Format a byte count as a human-readable VRAM amount."""
    if type(vram_bytes) is not int or vram_bytes < 0:
        return "—"
    return f"{vram_bytes / 1024**3:.1f} GiB"
