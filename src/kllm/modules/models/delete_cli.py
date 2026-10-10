"""Local model deletion command."""

from __future__ import annotations

import typer

from kllm.modules.cluster.interactors.agent_runtime import _client
from kllm.modules.connect.view_cli import console
from kllm.shared.state import DEFAULT_CONTROL_PORT


def model_delete(
    name: str = typer.Argument(..., help="Local model name or path"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Delete a local model from the master ``~/.kllm/models`` store."""
    with _client(port) as c:
        r = c.post("/v2/models/deletions", json={"name": name}, timeout=None)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(f"[green]Deleted[/green] {data.get('name') or name}")
