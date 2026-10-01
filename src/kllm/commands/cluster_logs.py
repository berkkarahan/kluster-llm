from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import httpx
import typer
from rich.text import Text

from kllm.commands.agent_runtime import _agent_base, _client, _ensure_agent
from kllm.commands.ui import console
from kllm.state import DEFAULT_CONTROL_PORT


def _format_log_event(event: dict) -> Text:
    try:
        stamp = datetime.fromtimestamp(float(event.get("ts") or 0)).strftime("%H:%M:%S")
    except (OSError, ValueError, TypeError):
        stamp = "??:??:??"
    node = str(event.get("node_id") or "?")
    source = str(event.get("source") or "?")
    line = str(event.get("line") or "")
    return Text.assemble(
        (f"{stamp} ", "dim"),
        (f"{node} ", "cyan"),
        (f"{source} ", "yellow"),
        line,
    )


def _print_log_event(event: dict) -> None:
    console.print(_format_log_event(event), highlight=False)


def logs_cmd(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow new cluster log lines"),
    lines: int = typer.Option(200, "--lines", "-n", help="Lines to dump before following"),
    node: Optional[str] = typer.Option(None, "--node", help="Filter by node_id"),
    source: Optional[str] = typer.Option(
        None, "--source", help="Filter by source: agent, llama, or rpc"
    ),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Dump or follow cluster logs (master journal; workers proxy to the master)."""
    if source is not None and source not in ("agent", "llama", "rpc"):
        raise typer.BadParameter("source must be agent, llama, or rpc")
    params: dict = {"n": lines, "follow": follow}
    if node:
        params["node"] = node
    if source:
        params["source"] = source
    if not follow:
        with _client(port) as c:
            r = c.get("/v2/logs", params=params)
            if r.status_code >= 400:
                console.print(f"[red]{r.text}[/red]")
                raise typer.Exit(code=1)
            data = r.json()
        if data.get("warning"):
            console.print(f"[yellow]{data['warning']}[/yellow]")
        for event in data.get("lines") or []:
            if isinstance(event, dict):
                _print_log_event(event)
        return

    _ensure_agent(port)
    try:
        with httpx.Client(base_url=_agent_base(port), timeout=None) as c:
            with c.stream("GET", "/v2/logs", params=params) as resp:
                if resp.status_code >= 400:
                    console.print(f"[red]{resp.read().decode('utf-8', errors='replace')}[/red]")
                    raise typer.Exit(code=1)
                for raw in resp.iter_lines():
                    if not raw:
                        continue
                    if raw.startswith("data:"):
                        payload = raw[5:].strip()
                        if not payload:
                            continue
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            console.print(payload)
                            continue
                        if isinstance(event, dict):
                            _print_log_event(event)
    except KeyboardInterrupt:
        return
