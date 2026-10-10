"""Stop this node, fast-forward the checkout, and rebuild the full install."""

from __future__ import annotations

import typer

from kllm.modules.cluster.interactors.agent_runtime import _stop_local
from kllm.modules.runtimes.router_cli import run_full_install
from kllm.modules.connect.view_cli import console
from kllm.modules.updates.interactors.update_worker import _git_ensure_clean, _git_pull_ff_only
from kllm.shared.state import DEFAULT_CONTROL_PORT, repo_root


def update_cmd(
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Stop the local node, git pull --ff-only, then run the full rebuild."""
    root = repo_root()
    if root is None:
        console.print("[red]Cannot find repo root; set KLLM_REPO_ROOT to your checkout.[/red]")
        raise typer.Exit(1)
    if not (root / ".git").exists():
        console.print(f"[red]Repo root is not a git checkout: {root}[/red]")
        raise typer.Exit(1)
    try:
        _git_ensure_clean(root)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    if _stop_local(port):
        console.print("[green]Stopped[/green]")
    else:
        console.print("[dim]Nothing to stop[/dim]")

    console.print(f"[dim]Pulling {root}…[/dim]")
    try:
        _git_pull_ff_only(root)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    console.print("[dim]Rebuilding (scripts/install.py --sync)…[/dim]")
    code = run_full_install(root)
    if code != 0:
        console.print("[red]Rebuild failed[/red]")
        raise typer.Exit(code)
    console.print(
        "Start again with [bold]kllm start[/bold] (master) or "
        "[bold]kllm join --key <key>[/bold] (worker)."
    )
