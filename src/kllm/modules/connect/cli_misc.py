"""Compatibility exports and registration for dashboard and connect commands."""

from __future__ import annotations

import typer

from kllm.modules.connect.router_cli import connect_cmd as connect_cmd
from kllm.modules.connect.dashboard_cli import ui_cmd as ui_cmd
from kllm.modules.runtimes.router_cli import rebuild_cmd as rebuild_cmd
from kllm.modules.updates.router_cli import update_cmd as update_cmd

__all__ = ["connect_cmd", "register", "rebuild_cmd", "update_cmd", "ui_cmd"]


def register(app: typer.Typer) -> None:
    """Register dashboard and harness commands."""
    app.command("rebuild")(rebuild_cmd)
    app.command("update")(update_cmd)
    app.command("ui")(ui_cmd)
    app.command(
        "connect", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
    )(connect_cmd)
