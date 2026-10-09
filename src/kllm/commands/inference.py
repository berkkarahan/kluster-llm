"""Planning, inference, and persistent configuration commands."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError as PydanticValidationError

from kllm.cli_payload import build_plan_run_payload
from kllm.commands.agent_runtime import _client
from kllm.commands.ui import console
from kllm.run_settings import PlanBody, available_threads
from kllm.state import DEFAULT_CONTROL_PORT
from kllm.extra_args import tokenize_extra_args, ExtraArgsError


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


def _parse_node_shares(node_share: list[str] | None) -> dict[str, float] | None:
    """Parse repeated ``NODE=FRACTION`` CLI values for manual placement."""
    if not node_share:
        return None
    try:
        return {key: float(value) for key, value in (part.split("=", 1) for part in node_share)}
    except (ValueError, TypeError) as exc:
        raise typer.BadParameter("use --node-share NODE=FRACTION") from exc


def _validate_run_payload(payload: dict[str, object]) -> dict[str, object]:
    """Validate CLI settings against the shared control API request schema."""
    try:
        body = PlanBody.model_validate(payload)
    except PydanticValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return body.model_dump(exclude_unset=True)


def _read_chat_template_file(path: str | None) -> str | None:
    """Read a custom template at the CLI boundary and report input errors clearly."""
    if path is None:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"could not read chat template file {path!r}: {exc}") from exc


def _run_settings_options(
    *,
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
    moe_offload: str,
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
    temperature: Optional[float],
    top_p: Optional[float],
    top_k: Optional[int],
    min_p: Optional[float],
    presence_penalty: Optional[float],
    repeat_penalty: Optional[float],
    enable_thinking: Optional[bool],
    split_mode: Optional[str] = None,
    flash_attention: str = "auto",
    node_share: list[str] | None = None,
    require_node: list[str] | None = None,
    ctx: typer.Context | None = None,
    settings: str = "saved",
    threads_batch: int | None = None,
    draft_max: int | None = None,
    seed: int | None = None,
    max_output_tokens: int | None = None,
    frequency_penalty: float | None = None,
    stop: list[str] | None = None,
    reasoning_effort: str | None = None,
    reasoning_budget: int | None = None,
    reasoning_format: str | None = None,
    gpu_id: list[int] | None = None,
    node_priority: list[str] | None = None,
    just_enough_master: bool | None = None,
    network_id: str | None = None,
    chat_template_text: str | None = None,
) -> dict[str, object]:
    headroom = None
    if chat_template_file and chat_template_text is not None:
        raise typer.BadParameter("Choose --chat-template-file or --chat-template-text")
    shares = _parse_node_shares(node_share)
    try:
        extra_tokens = tokenize_extra_args(extra_arg)
    except (ValueError, ExtraArgsError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    payload = build_plan_run_payload(
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
        moe_offload=moe_offload,
        flash_attention=flash_attention,
        node_shares=shares,
        required_nodes=require_node,
        vision=vision,
        mmproj=mmproj,
        chat_template=chat_template,
        chat_template_text=chat_template_text
        if chat_template_text is not None
        else _read_chat_template_file(chat_template_file),
        load_mode=load_mode,
        ctx_checkpoints=ctx_checkpoints,
        cache_ram=cache_ram,
        threads=threads,
        extra_args=extra_tokens,
        remember=remember,
        vram_weight=vram_weight,
        latency_weight=latency_weight,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        min_p=min_p,
        presence_penalty=presence_penalty,
        repeat_penalty=repeat_penalty,
        enable_thinking=enable_thinking,
        split_mode=split_mode,
        settings_source=settings,
        threads_batch=threads_batch,
        draft_max=draft_max,
        seed=seed,
        max_output_tokens=max_output_tokens,
        frequency_penalty=frequency_penalty,
        stop=stop,
        reasoning_effort=reasoning_effort,
        reasoning_budget=reasoning_budget,
        reasoning_format=reasoning_format,
        gpu_ids=gpu_id,
        node_priority=node_priority,
        just_enough_master=just_enough_master,
        network_id=network_id,
    )
    if ctx is not None:
        aliases = {
            "parallel": "parallel_slots",
            "load_mode": "mmap_mlock",
            "ctx_checkpoints": "checkpoints",
            "extra_arg": "extra_args",
            "node_share": "node_shares",
            "require_node": "required_nodes",
            "gpu_id": "gpu_ids",
        }
        explicit = {
            aliases.get(name, name)
            for name in ctx.params
            if getattr(ctx.get_parameter_source(name), "name", "") in {"COMMANDLINE", "ENVIRONMENT"}
        }
        if "chat_template_file" in explicit or "chat_template_text" in explicit:
            explicit.update({"chat_template", "chat_template_text"})
            payload["chat_template"] = "custom"
        payload = {
            key: value
            for key, value in payload.items()
            if key in explicit or key in {"model", "settings_source"}
        }
    return _validate_run_payload(payload)


def _print_plan_result(data: dict) -> None:
    """Print a dry-run plan the way ``kllm plan`` always has."""
    console.print(
        f"[bold]fit[/bold]={data.get('fit_status')} "
        f"estimated={data.get('estimated_bytes')} budget={data.get('budget_bytes')}"
    )
    console.print(
        f"[bold]placement[/bold] device {data.get('device_requested')}→{data.get('device_effective')} "
        f"split={data.get('tensor_split')}"
    )
    node_ids = data.get("node_ids") or []
    gpu_names = data.get("node_gpu_names") or {}
    for i, nid in enumerate(node_ids):
        labels = [str(name) for name in (gpu_names.get(nid) or []) if name]
        suffix = f" [{', '.join(labels)}]" if labels else ""
        console.print(
            f"  {nid}{suffix}: alloc={data.get('node_alloc_bytes', [None] * len(node_ids))[i]} "
            f"score={data.get('node_scores', [None] * len(node_ids))[i]} "
            f"latency_ms={data.get('node_latency_ms', [None] * len(node_ids))[i]}"
        )
    if data.get("warning"):
        console.print(f"[yellow]{data['warning']}[/yellow]")
    console.print(data)


def _print_run_result(data: dict) -> None:
    """Print a successful ``kllm run`` response."""
    plan = data.get("plan") or {}
    if plan.get("fit_status") == "tight" or data.get("warning"):
        console.print(f"[yellow]warning:[/yellow] {data.get('warning') or plan.get('hint')}")
    console.print("[green]Inference started[/green]")
    console.print(data)


def plan_cmd(
    model: str = typer.Option(..., "--model"),
    context: int = typer.Option(4096, "--context"),
    context_auto: bool = typer.Option(False, "--context-auto/--no-context-auto"),
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
    moe_offload: str = typer.Option(
        "auto", "--moe-offload", help="Offload MoE expert weights: auto or a non-negative count"
    ),
    vision: bool = typer.Option(False, "--vision/--no-vision"),
    mmproj: Optional[str] = typer.Option(None, "--mmproj"),
    chat_template: str = typer.Option("default", "--chat-template"),
    chat_template_file: Optional[str] = typer.Option(None, "--chat-template-file"),
    load_mode: str = typer.Option("auto", "--load-mode"),
    ctx_checkpoints: str = typer.Option("auto", "--ctx-checkpoints"),
    cache_ram: str = typer.Option("auto", "--cache-ram"),
    threads: int = typer.Option(min(8, available_threads()), "--threads"),
    temperature: Optional[float] = typer.Option(
        None, "--temperature", min=0.0, max=2.0, help="Sampling temperature (omit = model default)"
    ),
    top_p: Optional[float] = typer.Option(
        None, "--top-p", min=0.0, max=1.0, help="Nucleus sampling (omit = model default)"
    ),
    top_k: Optional[int] = typer.Option(
        None, "--top-k", min=0, help="Top-k sampling (omit = model default)"
    ),
    min_p: Optional[float] = typer.Option(
        None, "--min-p", min=0.0, max=1.0, help="Min-p sampling (omit = model default)"
    ),
    presence_penalty: Optional[float] = typer.Option(
        None,
        "--presence-penalty",
        min=-2.0,
        max=2.0,
        help="Presence penalty (omit = model default)",
    ),
    repeat_penalty: Optional[float] = typer.Option(
        None, "--repeat-penalty", min=0.0, max=2.0, help="Repeat penalty (omit = model default)"
    ),
    enable_thinking: Optional[bool] = typer.Option(
        None,
        "--enable-thinking/--no-enable-thinking",
        help="Force thinking on/off (omit = template default)",
    ),
    split_mode: Optional[str] = typer.Option(
        None, "--split-mode", help="Override system split mode: layer|row|tensor"
    ),
    flash_attention: str = typer.Option("auto", "--flash-attention", help="auto|on|off"),
    node_share: list[str] = typer.Option(
        [], "--node-share", help="NODE=FRACTION; repeat for every member"
    ),
    require_node: list[str] = typer.Option(
        [], "--require-node", help="Require a model layer on NODE; repeat for multiple nodes"
    ),
    extra_arg: list[str] = typer.Option([], "--extra-arg"),
    remember: bool = typer.Option(False, "--remember/--no-remember"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    settings: str = typer.Option(
        "saved", "--settings", help="saved|defaults; explicit flags override this source"
    ),
    threads_batch: Optional[int] = typer.Option(None, "--threads-batch", min=1),
    draft_max: Optional[int] = typer.Option(None, "--spec-draft-n-max", min=0),
    seed: Optional[int] = typer.Option(None, "--seed", min=-1, max=4294967295),
    max_output_tokens: Optional[int] = typer.Option(None, "--max-output-tokens", min=-1),
    frequency_penalty: Optional[float] = typer.Option(None, "--frequency-penalty", min=-2, max=2),
    stop: list[str] = typer.Option(
        [], "--stop", help="Literal stop string; repeat to supply several"
    ),
    reasoning_effort: Optional[str] = typer.Option(None, "--reasoning-effort"),
    reasoning_budget: Optional[int] = typer.Option(None, "--reasoning-budget", min=-1),
    reasoning_format: Optional[str] = typer.Option(None, "--reasoning-format"),
    gpu_id: list[int] = typer.Option([], "--gpu-id", min=0),
    node_priority: list[str] = typer.Option(
        [], "--node-priority", help="NODE in priority order; repeat"
    ),
    just_enough_master: bool = typer.Option(
        False,
        "--just-enough-master/--no-just-enough-master",
        help="Automatic placement: fill workers first; give the master only the trailing layers",
    ),
    network_id: Optional[str] = typer.Option(None, "--network-id"),
    chat_template_text: Optional[str] = typer.Option(
        None, "--chat-template-text", help="Inline custom Jinja template"
    ),
    ctx: typer.Context = None,
) -> None:
    """Dry-run capacity-aware layer split with RUN SETTINGS parity."""
    payload = _run_settings_options(
        model=model,
        context=context,
        context_auto=context_auto,
        device=device,
        vram_percent=vram_percent,
        ram_percent=ram_percent,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        speculative=speculative,
        draft_model=draft_model,
        parallel=parallel,
        batch_size=batch_size,
        micro_batch=micro_batch,
        moe_offload=moe_offload,
        vision=vision,
        mmproj=mmproj,
        chat_template=chat_template,
        chat_template_file=chat_template_file,
        load_mode=load_mode,
        ctx_checkpoints=ctx_checkpoints,
        cache_ram=cache_ram,
        threads=threads,
        extra_arg=extra_arg,
        remember=remember,
        vram_weight=vram_weight,
        latency_weight=latency_weight,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        min_p=min_p,
        presence_penalty=presence_penalty,
        repeat_penalty=repeat_penalty,
        enable_thinking=enable_thinking,
        split_mode=split_mode,
        flash_attention=flash_attention,
        node_share=node_share,
        require_node=require_node,
        ctx=ctx,
        settings=settings,
        threads_batch=threads_batch,
        draft_max=draft_max,
        seed=seed,
        max_output_tokens=max_output_tokens,
        frequency_penalty=frequency_penalty,
        stop=stop,
        reasoning_effort=reasoning_effort,
        reasoning_budget=reasoning_budget,
        reasoning_format=reasoning_format,
        gpu_id=gpu_id,
        node_priority=node_priority,
        just_enough_master=just_enough_master,
        network_id=network_id,
        chat_template_text=chat_template_text,
    )
    with _client(port) as c:
        r = c.post("/v2/inference/plans", json=payload)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()
    _print_plan_result(data)


def run(
    model: str = typer.Option(..., "--model"),
    context: int = typer.Option(4096, "--context"),
    context_auto: bool = typer.Option(False, "--context-auto/--no-context-auto"),
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
    moe_offload: str = typer.Option(
        "auto", "--moe-offload", help="Offload MoE expert weights: auto or a non-negative count"
    ),
    vision: bool = typer.Option(False, "--vision/--no-vision"),
    mmproj: Optional[str] = typer.Option(None, "--mmproj"),
    chat_template: str = typer.Option("default", "--chat-template"),
    chat_template_file: Optional[str] = typer.Option(None, "--chat-template-file"),
    load_mode: str = typer.Option("auto", "--load-mode"),
    ctx_checkpoints: str = typer.Option("auto", "--ctx-checkpoints"),
    cache_ram: str = typer.Option("auto", "--cache-ram"),
    threads: int = typer.Option(min(8, available_threads()), "--threads"),
    temperature: Optional[float] = typer.Option(
        None, "--temperature", min=0.0, max=2.0, help="Sampling temperature (omit = model default)"
    ),
    top_p: Optional[float] = typer.Option(
        None, "--top-p", min=0.0, max=1.0, help="Nucleus sampling (omit = model default)"
    ),
    top_k: Optional[int] = typer.Option(
        None, "--top-k", min=0, help="Top-k sampling (omit = model default)"
    ),
    min_p: Optional[float] = typer.Option(
        None, "--min-p", min=0.0, max=1.0, help="Min-p sampling (omit = model default)"
    ),
    presence_penalty: Optional[float] = typer.Option(
        None,
        "--presence-penalty",
        min=-2.0,
        max=2.0,
        help="Presence penalty (omit = model default)",
    ),
    repeat_penalty: Optional[float] = typer.Option(
        None, "--repeat-penalty", min=0.0, max=2.0, help="Repeat penalty (omit = model default)"
    ),
    enable_thinking: Optional[bool] = typer.Option(
        None,
        "--enable-thinking/--no-enable-thinking",
        help="Force thinking on/off (omit = template default)",
    ),
    split_mode: Optional[str] = typer.Option(
        None, "--split-mode", help="Override system split mode: layer|row|tensor"
    ),
    flash_attention: str = typer.Option("auto", "--flash-attention", help="auto|on|off"),
    node_share: list[str] = typer.Option(
        [], "--node-share", help="NODE=FRACTION; repeat for every member"
    ),
    require_node: list[str] = typer.Option(
        [], "--require-node", help="Require a model layer on NODE; repeat for multiple nodes"
    ),
    extra_arg: list[str] = typer.Option([], "--extra-arg"),
    remember: bool = typer.Option(False, "--remember/--no-remember"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    settings: str = typer.Option(
        "saved", "--settings", help="saved|defaults; explicit flags override this source"
    ),
    threads_batch: Optional[int] = typer.Option(None, "--threads-batch", min=1),
    draft_max: Optional[int] = typer.Option(None, "--spec-draft-n-max", min=0),
    seed: Optional[int] = typer.Option(None, "--seed", min=-1, max=4294967295),
    max_output_tokens: Optional[int] = typer.Option(None, "--max-output-tokens", min=-1),
    frequency_penalty: Optional[float] = typer.Option(None, "--frequency-penalty", min=-2, max=2),
    stop: list[str] = typer.Option(
        [], "--stop", help="Literal stop string; repeat to supply several"
    ),
    reasoning_effort: Optional[str] = typer.Option(None, "--reasoning-effort"),
    reasoning_budget: Optional[int] = typer.Option(None, "--reasoning-budget", min=-1),
    reasoning_format: Optional[str] = typer.Option(None, "--reasoning-format"),
    gpu_id: list[int] = typer.Option([], "--gpu-id", min=0),
    node_priority: list[str] = typer.Option(
        [], "--node-priority", help="NODE in priority order; repeat"
    ),
    just_enough_master: bool = typer.Option(
        False,
        "--just-enough-master/--no-just-enough-master",
        help="Automatic placement: fill workers first; give the master only the trailing layers",
    ),
    network_id: Optional[str] = typer.Option(None, "--network-id"),
    chat_template_text: Optional[str] = typer.Option(
        None, "--chat-template-text", help="Inline custom Jinja template"
    ),
    ctx: typer.Context = None,
) -> None:
    """Plan and launch master llama-server with RPC workers."""
    payload = _run_settings_options(
        model=model,
        context=context,
        context_auto=context_auto,
        device=device,
        vram_percent=vram_percent,
        ram_percent=ram_percent,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        speculative=speculative,
        draft_model=draft_model,
        parallel=parallel,
        batch_size=batch_size,
        micro_batch=micro_batch,
        moe_offload=moe_offload,
        vision=vision,
        mmproj=mmproj,
        chat_template=chat_template,
        chat_template_file=chat_template_file,
        load_mode=load_mode,
        ctx_checkpoints=ctx_checkpoints,
        cache_ram=cache_ram,
        threads=threads,
        extra_arg=extra_arg,
        remember=remember,
        vram_weight=vram_weight,
        latency_weight=latency_weight,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        min_p=min_p,
        presence_penalty=presence_penalty,
        repeat_penalty=repeat_penalty,
        enable_thinking=enable_thinking,
        split_mode=split_mode,
        flash_attention=flash_attention,
        node_share=node_share,
        require_node=require_node,
        ctx=ctx,
        settings=settings,
        threads_batch=threads_batch,
        draft_max=draft_max,
        seed=seed,
        max_output_tokens=max_output_tokens,
        frequency_penalty=frequency_penalty,
        stop=stop,
        reasoning_effort=reasoning_effort,
        reasoning_budget=reasoning_budget,
        reasoning_format=reasoning_format,
        gpu_id=gpu_id,
        node_priority=node_priority,
        just_enough_master=just_enough_master,
        network_id=network_id,
        chat_template_text=chat_template_text,
    )
    with _client(port) as c:
        r = c.post("/v2/inference/runs", json=payload)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            if r.status_code == 400 and "overflow" in r.text.lower():
                raise typer.Exit(1)
            raise typer.Exit(1)
        data = r.json()
    _print_run_result(data)


def register(app: typer.Typer) -> None:
    """Register plan and inference commands."""
    app.command("plan")(plan_cmd)
    app.command()(run)
