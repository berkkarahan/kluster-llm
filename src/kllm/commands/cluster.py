"""Compatibility exports and registration for cluster commands."""

from __future__ import annotations

import typer

from kllm.commands.cluster_inspection import gpus as gpus, status as status
from kllm.commands.cluster_lifecycle import (
    join as join,
    leave as leave,
    start as start,
    stop as stop,
)
from kllm.commands.cluster_logs import (
    _format_log_event as _format_log_event,
    _print_log_event as _print_log_event,
    logs_cmd as logs_cmd,
)
from kllm.commands.cluster_update import cluster_update_cmd as cluster_update_cmd

__all__ = [
    "_format_log_event",
    "_print_log_event",
    "cluster_update_cmd",
    "gpus",
    "join",
    "leave",
    "logs_cmd",
    "register",
    "start",
    "status",
    "stop",
]


def register(app: typer.Typer, cluster_app: typer.Typer) -> None:
    """Register lifecycle, inspection, log, and update commands."""
    app.command()(start)
    app.command()(stop)
    app.command()(join)
    app.command()(gpus)
    app.command()(leave)
    app.command()(status)
    app.command("logs")(logs_cmd)
    cluster_app.command("update")(cluster_update_cmd)
