"""Feature flag command."""

from __future__ import annotations

from typing import Optional

import typer

from kllm.shared import config as kllm_config
from kllm.modules.connect.view_cli import console


def _print_flag(name: str, enabled: bool) -> None:
    console.print(f"{name} = {'enabled' if enabled else 'disabled'}")


def flags_cmd(
    name: Optional[str] = typer.Argument(
        None, help="Feature flag to show or change (omit to list all flags)"
    ),
    enable: Optional[bool] = typer.Option(
        None, "--enable/--disable", help="Enable or disable the named flag"
    ),
) -> None:
    """Show or change local dashboard feature flags."""
    try:
        if name is None:
            for flag, enabled in kllm_config.feature_flags().items():
                _print_flag(flag, enabled)
            return
        if enable is not None:
            kllm_config.set_feature_flag(name, enable)
        _print_flag(name, kllm_config.get_feature_flag(name))
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="name") from exc


def register(app: typer.Typer) -> None:
    """Register feature flag commands."""
    app.command("flags")(flags_cmd)
