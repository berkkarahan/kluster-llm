"""Dashboard launcher command."""

from __future__ import annotations

import webbrowser

import typer

from kllm.commands.agent_runtime import _agent_base, _ensure_agent
from kllm.commands.ui import console
from kllm.state import DEFAULT_CONTROL_PORT



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
