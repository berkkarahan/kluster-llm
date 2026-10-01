"""Model template discovery command."""

from __future__ import annotations

from typing import Optional

import typer

from kllm.commands.agent_runtime import _client
from kllm.commands.ui import console
from kllm.state import DEFAULT_CONTROL_PORT
def model_templates(
    model: Optional[str] = typer.Option(None, "--model"),
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
) -> None:
    """List pinned chat templates and inspect a model's embedded template."""
    with _client(port) as client:
        response = client.get(
            "/v2/inference/capabilities", params={"model": model} if model else {}
        )
        response.raise_for_status()
        data = response.json()
    console.print(
        f"Auto (embedded GGUF): {'present' if data['template_embedded'] else 'unavailable'}"
    )
    for preset in data["templates"]:
        console.print(f"{preset['id']}: {preset['label']}")
    console.print("custom: --chat-template-file PATH")
