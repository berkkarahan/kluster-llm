from __future__ import annotations

import typer
from rich.table import Table

from kllm.commands.agent_runtime import _client
from kllm.commands.ui import console, format_vram as _fmt_vram
from kllm.state import DEFAULT_CONTROL_PORT


def gpus(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """List local GPUs available for --gpus / dashboard selection."""
    with _client(port) as c:
        r = c.get("/v2/node/devices")
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    backend = data.get("backend") or "cpu"
    rows = data.get("gpus") or data.get("devices") or []
    console.print(f"effective_backend={backend}")
    if data.get("backend_warning"):
        console.print(f"[yellow]{data['backend_warning']}[/yellow]")
    if not rows:
        console.print("[dim]No GPUs detected[/dim]")
        return
    table = Table(title="Local GPUs")
    table.add_column("index")
    table.add_column("backend")
    table.add_column("name")
    table.add_column("vram")
    for g in rows:
        table.add_row(
            str(g.get("index")),
            str(g.get("backend") or "—"),
            str(g.get("name")),
            _fmt_vram(g.get("vram_bytes")),
        )
    console.print(table)


def status(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """Show local role, peers, capacity, and last plan."""
    with _client(port) as c:
        r = c.get("/v2/node")
        r.raise_for_status()
        data = r.json()
    table = Table(title="Kluster LLM status")
    table.add_column("Field")
    table.add_column("Value")
    for k in (
        "role",
        "node_id",
        "join_key",
        "master_host",
        "process_pid",
        "model_name",
        "inference_up",
        "rpc_bind",
        "overlay",
        "wg_address",
        "overlay_status",
        "gpu_ids",
        "apply_error",
        "apply_warning",
    ):
        table.add_row(k, str(data.get(k)))
    console.print(table)
    if data.get("apply_error"):
        console.print(f"[red]apply_error:[/red] {data['apply_error']}")
    if data.get("apply_warning"):
        console.print(f"[yellow]apply_warning:[/yellow] {data['apply_warning']}")
    for w in data.get("netsec_warnings") or []:
        console.print(f"[yellow]netsec:[/yellow] {w}")
    cap = data.get("capacity") or {}
    if cap:
        console.print(
            f"Capacity: RAM {_fmt_vram(cap.get('total_ram_bytes') or 0)}, "
            f"VRAM {_fmt_vram(cap.get('vram_bytes'))}, backend={cap.get('backend')}"
        )
    members = data.get("members") or {}
    if members:
        mt = Table(title="Members")
        mt.add_column("id")
        mt.add_column("host")
        mt.add_column("role")
        mt.add_column("backend")
        mt.add_column("gpus")
        mt.add_column("vram")
        for m in members.values():
            mt.add_row(
                str(m.get("node_id")),
                str(m.get("host")),
                str(m.get("role")),
                str(m.get("backend")),
                str(m.get("gpu_ids") or "—"),
                _fmt_vram(m.get("vram_bytes")),
            )
        console.print(mt)
    if data.get("last_plan"):
        console.print(f"Last plan: {data['last_plan']}")
