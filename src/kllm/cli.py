"""Typer composition root. Feature commands live in their module routers."""

from __future__ import annotations

from collections.abc import Sequence

import typer

from kllm.modules.cluster.router_cli import register as register_cluster_commands
from kllm.modules.connect.cli_misc import register as register_misc_commands
from kllm.modules.inference.router_cli import register as register_inference_commands
from kllm.modules.models.router_cli import register as register_model_commands
from kllm.modules.settings.flags_cli import register as register_flags_commands
from kllm.modules.settings.router_cli import register as register_configuration_commands

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Kluster LLM router CLI")
model_app = typer.Typer(help="Model download and distribution")
cluster_app = typer.Typer(help="Cluster operations")
app.add_typer(model_app, name="model")
app.add_typer(cluster_app, name="cluster")
register_cluster_commands(app, cluster_app)
register_configuration_commands(app)
register_flags_commands(app)
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
