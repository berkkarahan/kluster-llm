"""Compatibility exports and registration for model commands."""

from __future__ import annotations

import typer

from kllm.modules.models.catalog_cli import model_templates as model_templates
from kllm.modules.models.delete_cli import model_delete as model_delete
from kllm.modules.models.download_cli import model_download as model_download

__all__ = ["model_delete", "model_download", "model_templates", "register"]


def register(model_app: typer.Typer) -> None:
    """Register model catalog and storage commands."""
    model_app.command("templates")(model_templates)
    model_app.command("download")(model_download)
    model_app.command("delete")(model_delete)
