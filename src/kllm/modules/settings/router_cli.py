"""Persistent local configuration command."""

from __future__ import annotations

from typing import Optional

import typer

from kllm.shared import config as kllm_config
from kllm.modules.connect.view_cli import console


def config_cmd(
    split_mode: Optional[str] = typer.Option(
        None,
        "--split-mode",
        help="Persistent system default: layer|row|tensor (omit to show config)",
    ),
) -> None:
    """Show or update local system settings."""
    try:
        if split_mode is not None:
            config = kllm_config.save_config({"split_mode": split_mode})
            console.print(f"split_mode={config['split_mode']}")
            return
        console.print(kllm_config.load_config())
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--split-mode") from exc


def register(app: typer.Typer) -> None:
    """Register local configuration commands."""
    app.command("config")(config_cmd)
