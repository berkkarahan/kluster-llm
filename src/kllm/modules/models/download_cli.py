"""Model download command and progress display."""

from __future__ import annotations

import time
from typing import Optional

import typer

from kllm.modules import models as model_ops
from kllm.modules.cluster.interactors.agent_runtime import _client
from kllm.modules.connect.view_cli import console
from kllm.shared.state import DEFAULT_CONTROL_PORT


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
        r = c.post("/v2/models/downloads", json={"source": source, "name": name}, timeout=None)
        if r.status_code >= 400:
            console.print(
                f"[yellow]API download failed ({r.status_code}); falling back local[/yellow]"
            )
            path = model_ops.download_model(source, dest_name=name)
            console.print(f"[green]Downloaded[/green] {path}")
            return
        data = r.json()
    if not data.get("accepted"):
        console.print(f"[red]Download rejected: {data}[/red]")
        raise typer.Exit(1)
    last_line = ""
    while True:
        with _client(port) as c:
            try:
                st = c.get("/v2/models/downloads/current", timeout=2.0).json()
            except Exception:
                time.sleep(0.5)
                continue
        state = st.get("state") or "idle"
        if state == "complete":
            console.print(f"[green]Downloaded[/green] {st.get('filename', source)}")
            return
        if state in ("error", "cancelled"):
            console.print(f"[red]Download {state}[/red] {st.get('error', '')}")
            raise typer.Exit(1)
        pct = int(st.get("pct") or 0)
        total = st.get("total")
        line = f"downloading {pct}%" + (
            f" · {int(st.get('bytes') or 0) / 1e9:.2f}/{total / 1e9:.2f} GB" if total else ""
        )
        rate = st.get("rate_bps")
        eta = st.get("eta_seconds")
        if rate:
            line += f" · {int(rate) / 1e6:.1f} MB/s"
        if eta:
            line += f" · ~{int(eta)}s left"
        if line != last_line:
            console.print(f"[dim]{line}[/dim]")
            last_line = line
        time.sleep(0.4)
