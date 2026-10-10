from __future__ import annotations

import time

import typer

from kllm.modules.cluster.interactors.agent_runtime import _client
from kllm.modules.connect.view_cli import console
from kllm.shared.state import DEFAULT_CONTROL_PORT


def cluster_update_cmd(
    port: int = typer.Option(DEFAULT_CONTROL_PORT, "--port"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Poll until workers finish or error"),
) -> None:
    """Tell joined workers to git pull, smart-rebuild, and rejoin the master."""
    with _client(port) as c:
        st = c.get("/v2/node").json()
        if st.get("role") != "master":
            console.print("[red]cluster update requires master role[/red]")
            raise typer.Exit(1)
        r = c.post("/v2/cluster/updates", timeout=60.0)
        if r.status_code >= 400:
            console.print(f"[red]{r.text}[/red]")
            raise typer.Exit(1)
        data = r.json()

    workers = data.get("workers") or []
    if not workers:
        console.print("[dim]No workers to update[/dim]")
        return
    for row in workers:
        host = row.get("host") or row.get("node_id") or "?"
        if row.get("ok"):
            console.print(f"  [green]accepted[/green] {host}")
        else:
            console.print(f"  [red]fail[/red] {host}: {row.get('error')}")

    if not wait:
        return

    last_line = ""
    deadline = time.time() + 7200
    while time.time() < deadline:
        with _client(port) as c:
            try:
                st = c.get("/v2/node", timeout=2.0).json()
            except Exception:
                time.sleep(0.5)
                continue
        upd = st.get("update") or {}
        phase = upd.get("phase") or ""
        rows = upd.get("workers") or []
        parts = []
        for w in rows:
            host = w.get("host") or w.get("node_id") or "?"
            wphase = w.get("phase") or ""
            err = w.get("error")
            parts.append(f"{host}:{wphase}" + (f" ({err})" if err else ""))
        line = f"[{phase}] " + (" | ".join(parts) if parts else "")
        if line != last_line:
            console.print(f"[dim]{line}[/dim]")
            last_line = line
        if (
            phase in ("done", "error")
            and rows
            and all(w.get("phase") in ("done", "error") for w in rows)
        ):
            break
        if not rows:
            break
        time.sleep(1.0)

    with _client(port) as c:
        st = c.get("/v2/node").json()
    upd = st.get("update") or {}
    failed = [w for w in (upd.get("workers") or []) if w.get("phase") == "error"]
    if failed or upd.get("phase") == "error":
        console.print("[red]Worker update finished with errors[/red]")
        raise typer.Exit(1)
    console.print("[green]Worker update complete[/green]")
