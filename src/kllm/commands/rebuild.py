"""Run the full repo installer from the CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from kllm.state import repo_root


def run_full_install(root: Path) -> int:
    """Run scripts/install.py --sync under *root*; return the process exit code."""
    script = root / "scripts" / "install.py"
    if not script.is_file():
        typer.secho(f"Missing installer: {script}", err=True)
        raise typer.Exit(1)
    proc = subprocess.run([sys.executable, str(script), "--sync"], cwd=str(root))
    return proc.returncode or 0


def rebuild_cmd() -> None:
    """Run scripts/install.py --sync from the detected repo root (full install)."""
    root = repo_root()
    if root is None:
        typer.secho(
            "Cannot find repo root; set KLLM_REPO_ROOT to your checkout.",
            err=True,
        )
        raise typer.Exit(1)
    raise typer.Exit(run_full_install(root))
