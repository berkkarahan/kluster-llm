"""Dashboard launcher command."""

from __future__ import annotations

import webbrowser

import typer

from kllm.modules.cluster.interactors.agent_runtime import _agent_base, _ensure_agent
from kllm.modules.connect.view_cli import console
from kllm.shared.state import DEFAULT_CONTROL_PORT


def ui_cmd(
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Open the local Kluster dashboard (served by kllm-agent)."""
    _ensure_agent(port)
    url = f"{_agent_base(port)}/"
    console.print(f"Dashboard: [bold]{url}[/bold]")
    if open_browser:
        webbrowser.open(url)
