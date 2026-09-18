from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from kllm import models as model_ops
from kllm import runtime
from kllm.cli_payload import build_plan_run_payload
from kllm.connect import AGENTS, build_launch_plan, print_plan, run_plan
from kllm.state import DEFAULT_CONTROL_PORT, ensure_home, home_dir

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Kluster LLM router CLI")
model_app = typer.Typer(help="Model download and distribution")
app.add_typer(model_app, name="model")
console = Console()


def _agent_base(port: int = DEFAULT_CONTROL_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def _agent_healthy(port: int = DEFAULT_CONTROL_PORT) -> dict | None:
    try:
        with httpx.Client(timeout=1.0) as client:
            r = client.get(f"{_agent_base(port)}/health")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


def _stop_local(port: int = DEFAULT_CONTROL_PORT, *, graceful: bool = True) -> bool:
    """Tear down tracked kllm processes. Does not start the agent."""
    stopped = False
    health = _agent_healthy(port) if graceful else None
    if health is not None:
        try:
            with httpx.Client(base_url=_agent_base(port), timeout=30.0) as client:
                client.post("/v1/stop")
            stopped = True
        except Exception:
            pass
        # Ensure agent PID is recorded so kill_tracked can terminate it.
        agent_pid = health.get("pid")
        if isinstance(agent_pid, int) and agent_pid > 0:
            info = runtime.load()
            if info.agent_pid != agent_pid:
                runtime.register_agent(agent_pid, port)
    if runtime.kill_tracked():
        stopped = True
    runtime.clear()
    return stopped


def _replace_tracked_instance(port: int = DEFAULT_CONTROL_PORT) -> None:
    """Stop any live tracked instance before start/join (replace semantics)."""
    if not runtime.any_live() and _agent_healthy(port) is None:
        return
    console.print("[dim]Replacing existing kllm instance…[/dim]")
    _stop_local(port)


def _ensure_agent(port: int = DEFAULT_CONTROL_PORT) -> None:
    ensure_home()
    health = _agent_healthy(port)
    if health is not None:
        info = runtime.load()
        agent_pid = health.get("pid")
        if isinstance(agent_pid, int) and agent_pid > 0:
            if info.agent_pid != agent_pid or info.control_port != port:
                runtime.register_agent(agent_pid, port)
        elif info.control_port != port:
            info.control_port = port
            runtime.save(info)
        return

    # Dead or missing agent: clear stale tracked PIDs, then spawn one.
    _stop_local(port, graceful=False)

    console.print("[dim]Starting local kllm-agent…[/dim]")
    log = home_dir() / "logs" / "agent.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "kllm.agent", "--host", "0.0.0.0", "--port", str(port)],
            stdout=lf,
            stderr=lf,
            start_new_session=True,
        )
    runtime.register_agent(proc.pid, port)
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


def parse_gpu_ids(value: str | None) -> list[int] | None:
    """Parse ``--gpus 0,1`` into a list of ints. ``None`` means unspecified."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise typer.BadParameter("expected comma-separated GPU indices, e.g. 0,1")
    ids: list[int] = []
    seen: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part)
        except ValueError as exc:
            raise typer.BadParameter(f"invalid GPU index {part!r}") from exc
        if idx < 0:
            raise typer.BadParameter(f"GPU index must be >= 0, got {idx}")
        if idx not in seen:
            seen.add(idx)
            ids.append(idx)
    if not ids:
        raise typer.BadParameter("expected comma-separated GPU indices, e.g. 0,1")
    return ids


def _fmt_vram(vram_bytes: int | None) -> str:
    if vram_bytes is None:
        return "—"
    return f"{vram_bytes / 1e9:.1f} GB"


def _prompt_gpu_ids(gpus: list[dict], backend: str) -> list[int] | None:
    """Interactive multi-select when multiple GPUs and stdin is a TTY.

    Returns None to mean \"all\" (omit from payload).
    """
    if len(gpus) <= 1 or not sys.stdin.isatty():
        return None
    console.print(f"[bold]Select GPUs[/bold] (backend={backend})")
    for g in gpus:
        console.print(
            f"  [{g['index']}] {g.get('name', 'GPU')}  {_fmt_vram(g.get('vram_bytes'))}"
        )
    default = ",".join(str(g["index"]) for g in gpus)
    raw = typer.prompt(
        "GPU indices (comma-separated, Enter = all)",
        default=default,
        show_default=True,
    )
    return parse_gpu_ids(raw)


def _resolve_cli_gpus(client: httpx.Client, gpus_flag: str | None) -> list[int] | None:
    """Resolve --gpus / interactive pick; None = let agent default to all."""
    explicit = parse_gpu_ids(gpus_flag)
    if explicit is not None:
        return explicit
    try:
        r = client.get("/v1/gpus")
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    gpus = data.get("gpus") or []
    backend = data.get("backend") or "cpu"
    return _prompt_gpu_ids(gpus, backend)


def _headroom_override(
    device: str | None,
    vram_percent: int | None,
    ram_percent: int | None,
) -> float | None:
    """Map CLI percent flags to plan/run headroom for this call only."""
    if vram_percent is not None and ram_percent is not None:
        raise typer.BadParameter("pass only one of --vram-percent or --ram-percent")
    if vram_percent is not None:
        if device is not None and device.strip().lower() == "cpu":
            raise typer.BadParameter("--vram-percent requires GPU device")
        return vram_percent / 100.0
    if ram_percent is not None:
        if device is not None and device.strip().lower() == "gpu":
            raise typer.BadParameter("--ram-percent requires CPU device")
        return ram_percent / 100.0
    return None


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
    gpus: Optional[str] = typer.Option(
        None, "--gpus", help="Comma-separated GPU indices to use (default: all / interactive)"
    ),
) -> None:
    """Become cluster master and advertise on the LAN."""
    _replace_tracked_instance(port)
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
        r = c.post("/v1/start", json=payload)
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


@app.command()
def stop(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """Stop the local kllm agent and any processes it started."""
    if _stop_local(port):
        console.print("[green]Stopped[/green]")
    else:
        console.print("[dim]Nothing to stop[/dim]")


@app.command()
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
    with _client(port) as c:
        gpu_ids = _resolve_cli_gpus(c, gpus)
        if gpu_ids is not None:
            payload["gpu_ids"] = gpu_ids
        r = c.post("/v1/join", json=payload)
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
    if data.get("launch", {}).get("error"):
        console.print(f"[yellow]RPC launch warning:[/yellow] {data['launch']['error']}")


@app.command()
def gpus(port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port")) -> None:
    """List local GPUs available for --gpus / dashboard selection."""
    with _client(port) as c:
        r = c.get("/v1/gpus")
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    backend = data.get("backend") or "cpu"
    rows = data.get("gpus") or []
    console.print(f"backend={backend}")
    if not rows:
        console.print("[dim]No GPUs detected[/dim]")
        return
    table = Table(title="Local GPUs")
    table.add_column("index")
    table.add_column("name")
    table.add_column("vram")
    for g in rows:
        table.add_row(str(g.get("index")), str(g.get("name")), _fmt_vram(g.get("vram_bytes")))
    console.print(table)


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
        mt.add_column("gpus")
        mt.add_column("vram")
        for m in members.values():
            mt.add_row(
                str(m.get("node_id")),
                str(m.get("host")),
                str(m.get("role")),
                str(m.get("backend")),
                str(m.get("gpu_ids") or "—"),
                str(m.get("vram_bytes")),
            )
        console.print(mt)
    if data.get("last_plan"):
        console.print(f"Last plan: {data['last_plan']}")


def _run_settings_options(
    model: str,
    context: int,
    context_auto: bool,
    device: Optional[str],
    vram_percent: Optional[int],
    ram_percent: Optional[int],
    cache_type_k: str,
    cache_type_v: str,
    speculative: str,
    draft_model: Optional[str],
    parallel: str,
    batch_size: str,
    micro_batch: str,
    vision: bool,
    mmproj: Optional[str],
    chat_template: str,
    chat_template_file: Optional[str],
    load_mode: str,
    ctx_checkpoints: str,
    cache_ram: str,
    threads: int,
    extra_arg: list[str],
    remember: bool,
    vram_weight: float,
    latency_weight: float,
) -> dict:
    headroom = _headroom_override(device, vram_percent, ram_percent)
    return build_plan_run_payload(
        model=model,
        context=context,
        context_auto=context_auto,
        device=device,
        headroom=headroom,
        vram_percent=vram_percent,
        ram_percent=ram_percent,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        speculative=speculative,
        draft_model=draft_model,
        parallel=parallel,
        batch_size=batch_size,
        micro_batch=micro_batch,
        vision=vision,
        mmproj=mmproj,
        chat_template=chat_template,
        chat_template_file=chat_template_file,
        load_mode=load_mode,
        ctx_checkpoints=ctx_checkpoints,
        cache_ram=cache_ram,
        threads=threads,
        extra_args=extra_arg,
        remember=remember,
        vram_weight=vram_weight,
        latency_weight=latency_weight,
    )


@app.command("plan")
def plan_cmd(
    model: str = typer.Option(..., "--model"),
    context: int = typer.Option(4096, "--context"),
    context_auto: bool = typer.Option(False, "--context-auto"),
    device: Optional[str] = typer.Option(
        None, "--device", help="cpu|gpu (default: config, else gpu)"
    ),
    vram_percent: Optional[int] = typer.Option(
        None, "--vram-percent", min=10, max=100, help="Override VRAM budget %% for this call (GPU)"
    ),
    ram_percent: Optional[int] = typer.Option(
        None, "--ram-percent", min=10, max=100, help="Override RAM budget %% for this call (CPU)"
    ),
    vram_weight: float = typer.Option(
        0.6, "--vram-weight", min=0.0, max=1.0, help="Placement score weight for VRAM capacity"
    ),
    latency_weight: float = typer.Option(
        0.4, "--latency-weight", min=0.0, max=1.0, help="Placement score weight for low latency"
    ),
    cache_type_k: str = typer.Option("q8_0", "--cache-type-k"),
    cache_type_v: str = typer.Option("q8_0", "--cache-type-v"),
    speculative: str = typer.Option("auto", "--speculative"),
    draft_model: Optional[str] = typer.Option(None, "--draft-model"),
    parallel: str = typer.Option("auto", "--parallel"),
    batch_size: str = typer.Option("auto", "--batch-size"),
    micro_batch: str = typer.Option("auto", "--micro-batch"),
    vision: bool = typer.Option(False, "--vision"),
    mmproj: Optional[str] = typer.Option(None, "--mmproj"),
    chat_template: str = typer.Option("default", "--chat-template"),
    chat_template_file: Optional[str] = typer.Option(None, "--chat-template-file"),
    load_mode: str = typer.Option("auto", "--load-mode"),
    ctx_checkpoints: str = typer.Option("auto", "--ctx-checkpoints"),
    cache_ram: str = typer.Option("auto", "--cache-ram"),
    threads: int = typer.Option(8, "--threads"),
    extra_arg: list[str] = typer.Option([], "--extra-arg"),
    remember: bool = typer.Option(True, "--remember/--no-remember"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Dry-run capacity-aware layer split with RUN SETTINGS parity."""
    payload = _run_settings_options(
        model,
        context,
        context_auto,
        device,
        vram_percent,
        ram_percent,
        cache_type_k,
        cache_type_v,
        speculative,
        draft_model,
        parallel,
        batch_size,
        micro_batch,
        vision,
        mmproj,
        chat_template,
        chat_template_file,
        load_mode,
        ctx_checkpoints,
        cache_ram,
        threads,
        extra_arg,
        remember,
        vram_weight,
        latency_weight,
    )
    with _client(port) as c:
        r = c.post("/v1/plan", json=payload)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(
        f"[bold]fit[/bold]={data.get('fit_status')} "
        f"estimated={data.get('estimated_bytes')} budget={data.get('budget_bytes')}"
    )
    console.print(
        f"[bold]placement[/bold] device {data.get('device_requested')}→{data.get('device_effective')} "
        f"split={data.get('tensor_split')}"
    )
    node_ids = data.get("node_ids") or []
    for i, nid in enumerate(node_ids):
        console.print(
            f"  {nid}: alloc={data.get('node_alloc_bytes', [None] * len(node_ids))[i]} "
            f"score={data.get('node_scores', [None] * len(node_ids))[i]} "
            f"latency_ms={data.get('node_latency_ms', [None] * len(node_ids))[i]}"
        )
    if data.get("warning"):
        console.print(f"[yellow]{data['warning']}[/yellow]")
    console.print(data)


@app.command()
def run(
    model: str = typer.Option(..., "--model"),
    context: int = typer.Option(4096, "--context"),
    context_auto: bool = typer.Option(False, "--context-auto"),
    device: Optional[str] = typer.Option(
        None, "--device", help="cpu|gpu (default: config, else gpu)"
    ),
    vram_percent: Optional[int] = typer.Option(
        None, "--vram-percent", min=10, max=100, help="Override VRAM budget %% for this call (GPU)"
    ),
    ram_percent: Optional[int] = typer.Option(
        None, "--ram-percent", min=10, max=100, help="Override RAM budget %% for this call (CPU)"
    ),
    vram_weight: float = typer.Option(
        0.6, "--vram-weight", min=0.0, max=1.0, help="Placement score weight for VRAM capacity"
    ),
    latency_weight: float = typer.Option(
        0.4, "--latency-weight", min=0.0, max=1.0, help="Placement score weight for low latency"
    ),
    cache_type_k: str = typer.Option("q8_0", "--cache-type-k"),
    cache_type_v: str = typer.Option("q8_0", "--cache-type-v"),
    speculative: str = typer.Option("auto", "--speculative"),
    draft_model: Optional[str] = typer.Option(None, "--draft-model"),
    parallel: str = typer.Option("auto", "--parallel"),
    batch_size: str = typer.Option("auto", "--batch-size"),
    micro_batch: str = typer.Option("auto", "--micro-batch"),
    vision: bool = typer.Option(False, "--vision"),
    mmproj: Optional[str] = typer.Option(None, "--mmproj"),
    chat_template: str = typer.Option("default", "--chat-template"),
    chat_template_file: Optional[str] = typer.Option(None, "--chat-template-file"),
    load_mode: str = typer.Option("auto", "--load-mode"),
    ctx_checkpoints: str = typer.Option("auto", "--ctx-checkpoints"),
    cache_ram: str = typer.Option("auto", "--cache-ram"),
    threads: int = typer.Option(8, "--threads"),
    extra_arg: list[str] = typer.Option([], "--extra-arg"),
    remember: bool = typer.Option(True, "--remember/--no-remember"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Plan and launch master llama-server with RPC workers."""
    payload = _run_settings_options(
        model,
        context,
        context_auto,
        device,
        vram_percent,
        ram_percent,
        cache_type_k,
        cache_type_v,
        speculative,
        draft_model,
        parallel,
        batch_size,
        micro_batch,
        vision,
        mmproj,
        chat_template,
        chat_template_file,
        load_mode,
        ctx_checkpoints,
        cache_ram,
        threads,
        extra_arg,
        remember,
        vram_weight,
        latency_weight,
    )
    with _client(port) as c:
        r = c.post("/v1/run", json=payload)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            if r.status_code == 400 and "overflow" in r.text.lower():
                raise typer.Exit(1)
            raise typer.Exit(1)
        data = r.json()
    plan = data.get("plan") or {}
    if plan.get("fit_status") == "tight" or data.get("warning"):
        console.print(f"[yellow]warning:[/yellow] {data.get('warning') or plan.get('hint')}")
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
    source: str = typer.Argument(
        ...,
        help="HF org/repo:file.gguf, org/repo:Q4_K_M, or HTTP(S) URL",
    ),
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
    """Copy a local model to joined workers (LAN pull tree)."""
    result: dict = {"done": False, "status_code": 0, "data": None, "text": ""}

    def _post() -> None:
        with _client(port) as c:
            r = c.post("/v1/models/distribute", json={"name": name}, timeout=None)
            result["status_code"] = r.status_code
            result["text"] = r.text
            try:
                result["data"] = r.json()
            except Exception:
                result["data"] = None
            result["done"] = True

    thread = threading.Thread(target=_post, daemon=True)
    thread.start()
    last_line = ""
    with _client(port) as c:
        while thread.is_alive() or not result["done"]:
            try:
                st = c.get("/v1/status", timeout=2.0).json()
                dist = st.get("distribute")
                if dist:
                    phase = dist.get("phase") or ""
                    pct = int(dist.get("pct") or 0)
                    workers = dist.get("workers") or []
                    if phase == "hashing" and not workers:
                        line = f"hashing {pct}%"
                    else:
                        parts = []
                        for w in workers:
                            host = w.get("host") or w.get("node_id") or "?"
                            wp = int(w.get("pct") or 0)
                            wphase = w.get("phase") or ""
                            parts.append(f"{host}:{wphase} {wp}%")
                        line = f"[{pct}%] " + (" | ".join(parts) if parts else phase)
                    if line != last_line:
                        console.print(f"[dim]{line}[/dim]")
                        last_line = line
            except Exception:
                pass
            if result["done"]:
                break
            thread.join(timeout=0.4)

    if result["status_code"] >= 400:
        console.print(f"[red]{result['text']}[/red]")
        raise typer.Exit(1)
    data = result["data"] or {}
    for row in data.get("results") or []:
        if row.get("ok"):
            skipped = (row.get("result") or {}).get("skipped")
            tag = "skipped" if skipped else "ok"
            console.print(f"  [green]{tag}[/green] {row.get('host')}")
        else:
            console.print(f"  [red]fail[/red] {row.get('host')}: {row.get('error')}")
    if any(not row.get("ok") for row in (data.get("results") or [])):
        raise typer.Exit(1)


@model_app.command("delete")
def model_delete(
    name: str = typer.Argument(..., help="Local model name or path"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """Delete a local model and remove copies from joined workers."""
    with _client(port) as c:
        r = c.post("/v1/models/delete", json={"name": name}, timeout=None)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    console.print(f"[green]Deleted[/green] {data.get('name') or name}")
    for row in data.get("results") or []:
        if row.get("ok"):
            console.print(f"  [green]ok[/green] {row.get('host')}")
        else:
            console.print(f"  [red]fail[/red] {row.get('host')}: {row.get('error')}")


if __name__ == "__main__":
    app()
