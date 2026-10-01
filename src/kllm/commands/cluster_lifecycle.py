from __future__ import annotations

from typing import Optional

import typer

from kllm import config as kllm_config
from kllm.commands.agent_runtime import (
    _client,
    _replace_tracked_instance,
    _stop_local,
)
from kllm.commands.gpu_options import resolve_cli_gpus as _resolve_cli_gpus
from kllm.commands.ui import console
from kllm.state import DEFAULT_CONTROL_PORT


def start(
    key: Optional[str] = typer.Option(
        None, "--key", help="Cluster join key (auto-generated if omitted)"
    ),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    rpc_bind: Optional[str] = typer.Option(
        None, "--rpc-bind", help="RPC/advertise bind host (default: private LAN IP, not 0.0.0.0)"
    ),
    overlay: str = typer.Option(
        "none", "--overlay", help="RPC overlay: none | wireguard (join-key gated WireGuard mesh)"
    ),
    gpus: Optional[str] = typer.Option(
        None, "--gpus", help="Comma-separated GPU indices to use (default: all / interactive)"
    ),
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="Preferred llama.cpp engine on this node: metal|cuda|rocm|vulkan (omit = all devices)",
    ),
) -> None:
    """Become cluster master and advertise on the LAN."""
    _replace_tracked_instance(port)
    if backend is not None:
        kllm_config.save_config({"accel_backend": backend.strip().lower()})
    with _client(port) as c:
        gpu_ids = _resolve_cli_gpus(c, gpus)
        payload: dict = {
            "key": key,
            "control_port": port,
            "rpc_bind": rpc_bind,
            "overlay": overlay,
        }
        if gpu_ids is not None:
            payload["gpu_ids"] = gpu_ids
        r = c.post("/v2/cluster", json=payload)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(f"[green]Master started[/green] node={data['node_id']}")
    console.print(f"Join key: [bold]{data['join_key']}[/bold]")
    console.print(f"RPC bind: {data.get('rpc_bind')}  overlay={data.get('overlay')}")
    if data.get("gpu_ids") is not None:
        console.print(f"GPUs: {data.get('gpu_ids')}")
    if data.get("wg_address"):
        console.print(f"WireGuard: {data.get('wg_address')} ({data.get('overlay_status')})")
    for w in data.get("netsec_warnings") or []:
        console.print(f"[yellow]netsec:[/yellow] {w}")
    console.print("Workers: kllm join --key <key> [--overlay wireguard]")


def stop(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """Stop the local kllm agent and any processes it started."""
    if _stop_local(port):
        console.print("[green]Stopped[/green]")
    else:
        console.print("[dim]Nothing to stop[/dim]")


def join(
    key: Optional[str] = typer.Option(None, "--key", help="Cluster join key"),
    master: Optional[str] = typer.Option(
        None, "--master", help="Master host (skip key-based mDNS discovery)"
    ),
    master_port: int = typer.Option(
        DEFAULT_CONTROL_PORT,
        "--master-port",
        help="Master control-plane port (not the local agent port)",
    ),
    port: int = typer.Option(
        DEFAULT_CONTROL_PORT,
        "--port",
        help="Local kllm-agent port",
    ),
    rpc_port: int = typer.Option(50052, "--rpc-port"),
    rpc_bind: Optional[str] = typer.Option(
        None, "--rpc-bind", help="RPC bind host (default: private LAN IP, not 0.0.0.0)"
    ),
    overlay: str = typer.Option(
        "none", "--overlay", help="RPC overlay: none | wireguard (must match master when using WG)"
    ),
    gpus: Optional[str] = typer.Option(
        None, "--gpus", help="Comma-separated GPU indices to use (default: all / interactive)"
    ),
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="Preferred llama.cpp engine on this node: metal|cuda|rocm|vulkan (omit = all devices)",
    ),
) -> None:
    """Join as worker using the join key (mDNS discovery) or an explicit --master."""
    if not key:
        console.print("[red]--key is required[/red]")
        raise typer.Exit(2)

    payload: dict = {
        "key": key,
        "master_port": master_port,
        "rpc_port": rpc_port,
        "rpc_bind": rpc_bind,
        "overlay": overlay,
    }
    if master:
        payload["master_host"] = master
    else:
        console.print("[dim]Discovering master via join key…[/dim]")

    _replace_tracked_instance(port)
    if backend is not None:
        kllm_config.save_config({"accel_backend": backend.strip().lower()})
    with _client(port) as c:
        gpu_ids = _resolve_cli_gpus(c, gpus)
        if gpu_ids is not None:
            payload["gpu_ids"] = gpu_ids
        r = c.post("/v2/cluster/membership", json=payload)
        if r.status_code == 401:
            console.print("[red]Invalid join key[/red]")
            raise typer.Exit(1)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(f"[green]Joined[/green] master={data['master']}")
    console.print(f"RPC bind: {data.get('rpc_bind')}  overlay={data.get('overlay')}")
    if data.get("gpu_ids") is not None:
        console.print(f"GPUs: {data.get('gpu_ids')}")
    if data.get("wg_address"):
        console.print(f"WireGuard: {data.get('wg_address')} ({data.get('overlay_status')})")
    for w in data.get("netsec_warnings") or []:
        console.print(f"[yellow]netsec:[/yellow] {w}")
    for w in data.get("llama_warnings") or []:
        console.print(f"[yellow]llama:[/yellow] {w}")
    if data.get("launch", {}).get("error"):
        console.print(f"[yellow]RPC launch warning:[/yellow] {data['launch']['error']}")


def leave(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """Leave the cluster as a worker."""
    with _client(port) as c:
        r = c.delete("/v2/cluster/membership")
        r.raise_for_status()
    console.print("[green]Left cluster[/green]")
