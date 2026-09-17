from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from kllm import discovery, models as model_ops
from kllm.connect import AGENTS, build_launch_plan, print_plan, run_plan
from kllm.state import DEFAULT_CONTROL_PORT, ensure_home, home_dir

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Kluster LLM router CLI")
model_app = typer.Typer(help="Model download and distribution")
app.add_typer(model_app, name="model")
console = Console()


def _agent_base(port: int = DEFAULT_CONTROL_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def _ensure_agent(port: int = DEFAULT_CONTROL_PORT) -> None:
    ensure_home()
    try:
        with httpx.Client(timeout=1.0) as client:
            r = client.get(f"{_agent_base(port)}/health")
            if r.status_code == 200:
                return
    except Exception:
        pass

    console.print("[dim]Starting local kllm-agent…[/dim]")
    log = home_dir() / "logs" / "agent.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as lf:
        subprocess.Popen(
            [sys.executable, "-m", "kllm.agent", "--host", "0.0.0.0", "--port", str(port)],
            stdout=lf,
            stderr=lf,
            start_new_session=True,
        )
    for _ in range(40):
        try:
            with httpx.Client(timeout=0.5) as client:
                if client.get(f"{_agent_base(port)}/health").status_code == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise typer.Exit(code=1)


def _client(port: int = DEFAULT_CONTROL_PORT) -> httpx.Client:
    _ensure_agent(port)
    return httpx.Client(base_url=_agent_base(port), timeout=120.0)


@app.command()
def start(
    key: Optional[str] = typer.Option(None, "--key", help="Cluster join key (auto-generated if omitted)"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    rpc_bind: Optional[str] = typer.Option(
        None, "--rpc-bind", help="RPC/advertise bind host (default: private LAN IP, not 0.0.0.0)"
    ),
    overlay: str = typer.Option(
        "none", "--overlay", help="RPC overlay: none | wireguard (join-key gated WireGuard mesh)"
    ),
) -> None:
    """Become cluster master and advertise on the LAN."""
    with _client(port) as c:
        r = c.post(
            "/v1/start",
            json={"key": key, "control_port": port, "rpc_bind": rpc_bind, "overlay": overlay},
        )
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(f"[green]Master started[/green] node={data['node_id']}")
    console.print(f"Join key: [bold]{data['join_key']}[/bold]")
    console.print(f"RPC bind: {data.get('rpc_bind')}  overlay={data.get('overlay')}")
    if data.get("wg_address"):
        console.print(f"WireGuard: {data.get('wg_address')} ({data.get('overlay_status')})")
    for w in data.get("netsec_warnings") or []:
        console.print(f"[yellow]netsec:[/yellow] {w}")
    console.print("Workers: kllm join --key <key> [--overlay wireguard]")


@app.command()
def stop(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """Stop local role session (master tears down the cluster session)."""
    with _client(port) as c:
        r = c.post("/v1/stop")
        r.raise_for_status()
    console.print("[green]Stopped[/green]")


@app.command()
def join(
    key: Optional[str] = typer.Option(None, "--key", help="Cluster join key"),
    master: Optional[str] = typer.Option(None, "--master", help="Master host (skip discovery)"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    rpc_port: int = typer.Option(50052, "--rpc-port"),
    rpc_bind: Optional[str] = typer.Option(
        None, "--rpc-bind", help="RPC bind host (default: private LAN IP, not 0.0.0.0)"
    ),
    overlay: str = typer.Option(
        "none", "--overlay", help="RPC overlay: none | wireguard (must match master when using WG)"
    ),
) -> None:
    """Discover or target a master and join as worker."""
    if not key:
        console.print("[red]--key is required[/red]")
        raise typer.Exit(2)
    master_host = master
    master_port = port
    if not master_host:
        console.print("[dim]Browsing LAN for masters…[/dim]")
        found = discovery.browse_masters(timeout=2.5)
        if not found:
            console.print("[red]No masters discovered. Pass --master HOST[/red]")
            raise typer.Exit(1)
        if len(found) > 1 and not master:
            console.print("Multiple masters found; specify --master:")
            for m in found:
                console.print(f"  {m.host}:{m.port} ({m.name})")
            raise typer.Exit(2)
        master_host = found[0].host
        master_port = found[0].port

    with _client(port) as c:
        r = c.post(
            "/v1/join",
            json={
                "key": key,
                "master_host": master_host,
                "master_port": master_port,
                "rpc_port": rpc_port,
                "rpc_bind": rpc_bind,
                "overlay": overlay,
            },
        )
        if r.status_code == 401:
            console.print("[red]Invalid join key[/red]")
            raise typer.Exit(1)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(f"[green]Joined[/green] master={data['master']}")
    console.print(f"RPC bind: {data.get('rpc_bind')}  overlay={data.get('overlay')}")
    if data.get("wg_address"):
        console.print(f"WireGuard: {data.get('wg_address')} ({data.get('overlay_status')})")
    for w in data.get("netsec_warnings") or []:
        console.print(f"[yellow]netsec:[/yellow] {w}")
    if data.get("launch", {}).get("error"):
        console.print(f"[yellow]RPC launch warning:[/yellow] {data['launch']['error']}")


@app.command()
def leave(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """Leave the cluster as a worker."""
    with _client(port) as c:
        r = c.post("/v1/leave")
        r.raise_for_status()
    console.print("[green]Left cluster[/green]")


@app.command()
def status(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """Show local role, peers, capacity, and last plan."""
    with _client(port) as c:
        r = c.get("/v1/status")
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
    ):
        table.add_row(k, str(data.get(k)))
    console.print(table)
    for w in data.get("netsec_warnings") or []:
        console.print(f"[yellow]netsec:[/yellow] {w}")
    cap = data.get("capacity") or {}
    if cap:
        console.print(
            f"Capacity: RAM {cap.get('total_ram_bytes', 0) / 1e9:.1f} GB, "
            f"VRAM {cap.get('vram_bytes')}, backend={cap.get('backend')}"
        )
    members = data.get("members") or {}
    if members:
        mt = Table(title="Members")
        mt.add_column("id")
        mt.add_column("host")
        mt.add_column("role")
        mt.add_column("backend")
        mt.add_column("vram")
        for m in members.values():
            mt.add_row(
                str(m.get("node_id")),
                str(m.get("host")),
                str(m.get("role")),
                str(m.get("backend")),
                str(m.get("vram_bytes")),
            )
        console.print(mt)
    if data.get("last_plan"):
        console.print(f"Last plan: {data['last_plan']}")


@app.command("plan")
def plan_cmd(
    model: str = typer.Option(..., "--model"),
    context: int = typer.Option(4096, "--context"),
    device: Optional[str] = typer.Option(
        None, "--device", help="cpu|gpu (default: config, else gpu)"
    ),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Dry-run capacity-aware layer split."""
    payload: dict = {"model": model, "context": context}
    if device:
        payload["device"] = device
    with _client(port) as c:
        r = c.post("/v1/plan", json=payload)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(data)


@app.command()
def run(
    model: str = typer.Option(..., "--model"),
    context: int = typer.Option(4096, "--context"),
    device: Optional[str] = typer.Option(
        None, "--device", help="cpu|gpu (default: config, else gpu)"
    ),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Plan and launch master llama-server with RPC workers."""
    payload: dict = {"model": model, "context": context}
    if device:
        payload["device"] = device
    with _client(port) as c:
        r = c.post("/v1/run", json=payload)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print("[green]Inference started[/green]")
    console.print(data)


@app.command("ui")
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


@app.command(
    "connect",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
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


@model_app.command("download")
def model_download(
    source: str = typer.Argument(..., help="HF org/repo:file.gguf or HTTP(S) URL"),
    name: Optional[str] = typer.Option(None, "--name"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Download a model on the master into ~/.kllm/models."""
    with _client(port) as c:
        r = c.post("/v1/models/download", json={"source": source, "name": name}, timeout=None)
        if r.status_code >= 400:
            console.print(f"[yellow]API download failed ({r.status_code}); falling back local[/yellow]")
            path = model_ops.download_model(source, dest_name=name)
            console.print(f"[green]Downloaded[/green] {path}")
            return
        data = r.json()
    console.print(f"[green]Downloaded[/green] {data.get('path')}")


@model_app.command("distribute")
def model_distribute(
    name: str = typer.Argument(..., help="Local model name or path"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Push a local model file to all joined workers."""
    with _client(port) as c:
        r = c.post("/v1/models/distribute", json={"name": name}, timeout=None)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    for row in data.get("results") or []:
        if row.get("ok"):
            console.print(f"  [green]ok[/green] {row.get('host')}")
        else:
            console.print(f"  [red]fail[/red] {row.get('host')}: {row.get('error')}")


if __name__ == "__main__":
    app()
