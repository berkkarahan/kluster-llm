"""Local agent lifecycle and HTTP client management for CLI commands."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any

import httpx
import typer

from kllm import config as kllm_config, log_sink, runtime
from kllm.commands.ui import console
from kllm.state import (
    DEFAULT_CONTROL_PORT,
    ClusterState,
    ClusterStateLoadError,
    ensure_home,
    home_dir,
)

__all__ = [
    "_agent_base",
    "_agent_healthy",
    "_client",
    "_ensure_agent",
    "_replace_tracked_instance",
    "_stop_local",
]


def _agent_base(port: int = DEFAULT_CONTROL_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def _agent_healthy(port: int = DEFAULT_CONTROL_PORT) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=1.0) as client:
            r = client.get(f"{_agent_base(port)}/health")
            if r.status_code == 200:
                payload: object = r.json()
                if isinstance(payload, dict):
                    return {str(key): value for key, value in payload.items()}
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
                client.delete("/v2/cluster")
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


def _validate_saved_state() -> None:
    try:
        ClusterState.load(migrate=False)
    except ClusterStateLoadError as exc:
        typer.echo(f"Cannot start local kllm-agent: {exc}", err=True)
        typer.echo(
            "Fix access to that file, or move it to a backup name and retry to create "
            "fresh local state.",
            err=True,
        )
        typer.echo(
            "Resetting cluster state changes this node's identity and saved cluster settings; "
            "downloaded models are kept.",
            err=True,
        )
        typer.echo(f"Agent log: {home_dir() / 'logs' / 'agent.log'}", err=True)
        raise typer.Exit(code=1) from None


def _terminate_spawned_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    except OSError:
        pass


def _cleanup_failed_start(proc: subprocess.Popen[Any], sink: subprocess.Popen[Any]) -> None:
    _terminate_spawned_process(proc)
    if sink.poll() is None:
        try:
            sink.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _terminate_spawned_process(sink)
    runtime.clear_agent_if_matches(proc.pid)


def _ensure_agent(port: int = DEFAULT_CONTROL_PORT) -> None:
    ensure_home()
    health = _agent_healthy(port)
    if health is not None:
        if health.get("control_protocol") != kllm_config.CONTROL_PROTOCOL_VERSION:
            typer.echo(
                "The running kllm-agent uses an incompatible control protocol. "
                "Upgrade the agent and all cluster nodes together, then restart it.",
                err=True,
            )
            raise typer.Exit(code=1)
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
    _validate_saved_state()

    console.print("[dim]Starting local kllm-agent…[/dim]")
    log = home_dir() / "logs" / "agent.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    sink = subprocess.Popen(
        log_sink.command(log),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "kllm.agent", "--host", "0.0.0.0", "--port", str(port)],
            stdout=sink.stdin,
            stderr=sink.stdin,
            start_new_session=True,
        )
    except BaseException:
        sink.terminate()
        sink.wait(timeout=5)
        raise
    finally:
        if sink.stdin:
            sink.stdin.close()
    try:
        runtime.register_agent(proc.pid, port, sink.pid)
        for _ in range(40):
            return_code = proc.poll()
            if return_code is not None:
                typer.echo(
                    f"kllm-agent exited during startup with code {return_code}. See {log}.",
                    err=True,
                )
                raise typer.Exit(code=1)
            try:
                with httpx.Client(timeout=0.5) as client:
                    if client.get(f"{_agent_base(port)}/health").status_code == 200:
                        return
            except Exception:
                pass
            time.sleep(0.25)
        typer.echo(
            f"kllm-agent did not become healthy within 10 seconds. See {log}.",
            err=True,
        )
        raise typer.Exit(code=1)
    except BaseException:
        _cleanup_failed_start(proc, sink)
        raise


def _client(port: int = DEFAULT_CONTROL_PORT) -> httpx.Client:
    _ensure_agent(port)
    return httpx.Client(base_url=_agent_base(port), timeout=120.0)
