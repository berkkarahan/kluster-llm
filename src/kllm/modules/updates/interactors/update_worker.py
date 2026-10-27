"""Detached worker update process and local agent restart workflow."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from kllm.shared import process_registry as runtime
from kllm.modules.updates.interactors.cluster_update import (
    UpdateError,
    choose_rebuild_script,
    clear_reconnect,
    load_reconnect,
    read_llama_release,
    software_identity,
)
from kllm.shared.state import (
    DEFAULT_CONTROL_PORT,
    DEFAULT_RPC_PORT,
    ensure_home,
    logs_dir,
    repo_root,
)


def _post_progress(params: dict[str, Any], phase: str, **extra: Any) -> None:
    join_key = params.get("join_key")
    if not join_key:
        return
    bases: list[str] = []
    master_base = params.get("master_base")
    if isinstance(master_base, str) and master_base:
        bases.append(master_base.rstrip("/"))
    host = params.get("master_host")
    port = int(params.get("master_port") or DEFAULT_CONTROL_PORT)
    if host:
        direct = f"http://{host}:{port}"
        if direct not in bases:
            bases.append(direct)
    body = {
        "node_id": params.get("node_id"),
        "phase": phase,
        **{k: v for k, v in extra.items() if v is not None},
    }
    headers = {"X-Kllm-Key": str(join_key)}
    for base in bases:
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{base}/v2/cluster/updates/progress", json=body, headers=headers
                )
                if resp.status_code < 400:
                    return
        except Exception:
            continue


def _git_ensure_clean(root: Path) -> None:
    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise UpdateError(f"git status failed: {(proc.stderr or proc.stdout or '').strip()}")
    if (proc.stdout or "").strip():
        raise UpdateError("working tree is dirty; commit or stash locally before cluster update")


def _git_pull_ff_only(root: Path) -> None:
    proc = subprocess.run(
        ["git", "-C", str(root), "pull", "--ff-only"],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise UpdateError(f"git pull --ff-only failed: {err}")


def _run_rebuild(root: Path, kind: str) -> None:
    script = root / "scripts" / ("install.py" if kind == "install" else "build.py")
    if not script.is_file():
        raise UpdateError(f"missing rebuild script: {script}")
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise UpdateError(f"{script.name} failed (exit {proc.returncode}): {tail}")


def _agent_base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _agent_healthy(port: int) -> bool:
    try:
        with httpx.Client(timeout=1.0) as client:
            return client.get(f"{_agent_base(port)}/health").status_code == 200
    except Exception:
        return False


def _leave_local(port: int) -> None:
    if not _agent_healthy(port):
        return
    try:
        with httpx.Client(base_url=_agent_base(port), timeout=30.0) as client:
            client.delete("/v2/cluster/membership")
    except Exception:
        pass


def _stop_local_agent(port: int) -> None:
    if _agent_healthy(port):
        try:
            with httpx.Client(base_url=_agent_base(port), timeout=30.0) as client:
                client.delete("/v2/cluster")
        except Exception:
            pass
    runtime.kill_tracked()
    runtime.clear()


def _start_agent(port: int) -> None:
    ensure_home()
    log = logs_dir() / "agent.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "kllm.agent", "--host", "0.0.0.0", "--port", str(port)],
            stdout=lf,
            stderr=lf,
            start_new_session=True,
        )
    runtime.register_agent(proc.pid, port)
    for _ in range(80):
        if _agent_healthy(port):
            return
        time.sleep(0.25)
    raise UpdateError(f"agent failed to become healthy on port {port}")


def _join_local(params: dict[str, Any]) -> None:
    port = int(params.get("control_port") or DEFAULT_CONTROL_PORT)
    payload: dict[str, Any] = {
        "key": params["join_key"],
        "master_host": params["master_host"],
        "master_port": int(params.get("master_port") or DEFAULT_CONTROL_PORT),
        "rpc_port": int(params.get("rpc_port") or DEFAULT_RPC_PORT),
        "rpc_bind": params.get("rpc_bind"),
    }
    if params.get("gpu_ids") is not None:
        payload["gpu_ids"] = params["gpu_ids"]
    with httpx.Client(base_url=_agent_base(port), timeout=120.0) as client:
        resp = client.post("/v2/cluster/membership", json=payload)
        if resp.status_code >= 400:
            raise UpdateError(f"rejoin failed: {resp.status_code} {resp.text}")


def run_updater() -> int:
    """Detached entry: leave → pull → rebuild → restart agent → join."""
    try:
        params = load_reconnect()
    except UpdateError as exc:
        print(f"cluster update aborted: {exc}", file=sys.stderr)
        return 1

    port = int(params.get("control_port") or DEFAULT_CONTROL_PORT)
    root_s = params.get("repo_root")
    root = Path(root_s) if root_s else repo_root()
    if root is None:
        _post_progress(params, "error", error="repo root not found")
        return 1

    try:
        _post_progress(params, "leaving")
        _leave_local(port)
        _stop_local_agent(port)

        _post_progress(params, "pulling")
        _git_ensure_clean(root)
        pre_llama = read_llama_release(root)
        _git_pull_ff_only(root)
        post_llama = read_llama_release(root)
        kind = choose_rebuild_script(pre_llama, post_llama)
        identity = software_identity(root)

        _post_progress(
            params,
            "rebuilding",
            rebuild=kind,
            git_head=identity.get("git_head"),
            llama_release=identity.get("llama_release"),
        )
        _run_rebuild(root, kind)

        _post_progress(params, "rejoining", rebuild=kind)
        _start_agent(port)
        _join_local(params)
        identity = software_identity(root)
        _post_progress(
            params,
            "done",
            rebuild=kind,
            git_head=identity.get("git_head"),
            llama_release=identity.get("llama_release"),
        )
        clear_reconnect()
        return 0
    except Exception as exc:
        _post_progress(params, "error", error=str(exc))
        print(f"cluster update failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    """Parse the detached updater command and report command-line errors."""
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print("usage: python -m kllm.cluster_update run", file=sys.stderr)
        return 2
    if args[0] == "run":
        return run_updater()
    print(f"unknown command: {args[0]}", file=sys.stderr)
    return 2
