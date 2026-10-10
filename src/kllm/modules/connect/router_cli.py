"""Coding-harness connection command."""

from __future__ import annotations

from typing import Optional
import os
import shutil

import typer

from kllm.modules.cluster.interactors.agent_runtime import _ensure_agent
from kllm.modules.connect.view_cli import console
from kllm.modules.connect.interactors.connect import AGENTS, build_launch_plan, print_plan, run_plan
from kllm.shared.state import DEFAULT_CONTROL_PORT
from kllm.modules.connect.interactors.agent_skills import SkillError, validate_selection


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
    url: Optional[str] = typer.Option(None, "--url", envvar="KLLM_URL"),
    timeout: float = typer.Option(600, "--timeout", min=1),
    kllm_skills: str = typer.Option(
        "all", "--kllm-skills", help="Kluster skill pack: all|operations|development|none"
    ),
) -> None:
    """Launch a coding harness with the kllm API baked in (Unsloth-style)."""
    if isinstance(kllm_skills, str):
        try:
            validate_selection(kllm_skills, session=True)
        except SkillError as error:
            raise typer.BadParameter(str(error), param_hint="--kllm-skills") from error
    remote = isinstance(url, str) and bool(url) or bool(os.environ.get("KLLM_URL"))
    if not remote:
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
        **({"kllm_skills": kllm_skills} if isinstance(kllm_skills, str) else {}),
        **(
            {
                "url": url if isinstance(url, str) else None,
                "timeout": timeout if isinstance(timeout, (int, float)) else 600,
            }
            if isinstance(url, str) or isinstance(timeout, (int, float))
            else {}
        ),
    )
    if not launch:
        try:
            print_plan(plan)
        finally:
            for directory in plan.cleanup_dirs:
                shutil.rmtree(directory, ignore_errors=True)
        return
    console.print(f"[dim]Starting {plan.agent}…[/dim]")
    code = run_plan(plan)
    raise typer.Exit(code)
