from __future__ import annotations

from collections.abc import Sequence

import httpx as httpx
import typer

from kllm.commands.common import (
    _agent_base as _agent_base,
    _agent_healthy as _agent_healthy,
    _client as _client,
    _ensure_agent as _ensure_agent,
    _fmt_vram as _fmt_vram,
    _prompt_gpu_ids as _prompt_gpu_ids,
    _replace_tracked_instance as _replace_tracked_instance,
    _resolve_cli_gpus as _resolve_cli_gpus,
    _stop_local as _stop_local,
    console as console,
    parse_gpu_ids as parse_gpu_ids,
)
from kllm.commands.cluster import (
    _format_log_event as _format_log_event,
    _print_log_event as _print_log_event,
    cluster_update_cmd as cluster_update_cmd,
    gpus as gpus,
    join as join,
    leave as leave,
    logs_cmd as logs_cmd,
    register as register_cluster_commands,
    start as start,
    status as status,
    stop as stop,
)
from kllm.commands.inference import (
    _headroom_override as _headroom_override,
    _print_plan_result as _print_plan_result,
    _print_run_result as _print_run_result,
    _run_settings_options as _run_settings_options,
    plan_cmd as plan_cmd,
    register as register_inference_commands,
    run as run,
)
from kllm.commands.configuration import (
    config_cmd as config_cmd,
    register as register_configuration_commands,
)
from kllm.commands.misc import (
    connect_cmd as connect_cmd,
    register as register_misc_commands,
    ui_cmd as ui_cmd,
)
from kllm.commands.models import (
    model_delete as model_delete,
    model_download as model_download,
    model_templates as model_templates,
    register as register_model_commands,
)

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Kluster LLM router CLI")
model_app = typer.Typer(help="Model download and distribution")
cluster_app = typer.Typer(help="Cluster operations")
app.add_typer(model_app, name="model")
app.add_typer(cluster_app, name="cluster")
register_cluster_commands(app, cluster_app)
register_configuration_commands(app)
register_inference_commands(app)
register_misc_commands(app)
register_model_commands(model_app)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process status code."""
    command = typer.main.get_command(app)
    try:
        command.main(args=list(argv) if argv is not None else None, prog_name="kllm")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    except KeyboardInterrupt:
        typer.echo("Interrupted.", err=True)
        return 130
    return 0


if __name__ == "__main__":
    app()
