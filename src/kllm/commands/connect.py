"""Coding-harness connection command."""

from __future__ import annotations

from typing import Optional

import typer

from kllm.commands.agent_runtime import _ensure_agent
from kllm.commands.ui import console
from kllm.connect import AGENTS, build_launch_plan, print_plan, run_plan
from kllm.state import DEFAULT_CONTROL_PORT



def connect_cmd(
    ctx: typer.Context,
    agent: str = typer.Argument(..., help=f"Harness: {', '.join(AGENTS)}"),
    model: Optional[str] = typer.Option(None, "--model", "-m"),
    api_key: Optional[str] = typer.Option(None, "--api-key", envvar="KLLM_API_KEY"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    launch: bool = typer.Option(True, "--launch/--no-launch"),
    serve: bool = typer.Option(True, "--serve/--no-serve"),
    persist: bool = typer.Option(False, "--persist/--no-persist"),
    yolo: bool = typer.Option(False, "--yolo"),
    context: int = typer.Option(4096, "--context"),
) -> None:
    """Launch a coding harness with the kllm API baked in (Unsloth-style)."""
    _ensure_agent(port)
    plan = build_launch_plan(
        agent,
        port=port,
        model=model,
        api_key=api_key,
        serve=serve,
        persist=persist,
        yolo=yolo,
        context=context,
        extra=list(ctx.args),
    )
    if not launch:
        print_plan(plan)
        return
    console.print(f"[dim]Starting {plan.agent}…[/dim]")
    code = run_plan(plan)
    raise typer.Exit(code)
