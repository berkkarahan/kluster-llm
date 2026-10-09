"""Master-orchestrated worker git pull / rebuild / reconnect."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from kllm.exceptions import ConflictError, ValidationError
from kllm.cluster_update_progress import UpdateProgressStore
from kllm.cluster_update_reconnect import ReconnectStateError, ReconnectStore
from kllm.state import (
    DEFAULT_CONTROL_PORT,
    ensure_home,
    home_dir,
    logs_dir,
    repo_root,
)

_UPDATE_FANOUT_LOCK = threading.Lock()

_RECONNECT_NAME = "update-reconnect.json"
_PROGRESS_STORE = UpdateProgressStore()
_RECONNECT_STORE = ReconnectStore(lambda: reconnect_path(), ensure_home)


class UpdateInProgressError(ConflictError):
    pass


class UpdateError(ValidationError):
    pass


def reconnect_path() -> Path:
    return home_dir() / _RECONNECT_NAME


def get_update_progress() -> dict[str, Any] | None:
    return _PROGRESS_STORE.snapshot()


def _snapshot_update_progress_locked() -> dict[str, Any] | None:
    """Compatibility helper returning a detached progress snapshot."""
    return _PROGRESS_STORE.snapshot()


def clear_update_progress() -> None:
    _PROGRESS_STORE.clear()


def software_identity(root: Path | None = None) -> dict[str, str | None]:
    """Return ``git_head`` and ``llama_release`` for the local checkout."""
    base = root if root is not None else repo_root()
    git_head: str | None = None
    llama_pin: str | None = None
    if base is None:
        return {"git_head": None, "llama_release": None}
    pin_path = base / "llama-release"
    if pin_path.is_file():
        try:
            llama_pin = pin_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            llama_pin = None
    if (base / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "-C", str(base), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                git_head = (proc.stdout or "").strip() or None
        except (OSError, subprocess.TimeoutExpired):
            git_head = None
    return {"git_head": git_head, "llama_release": llama_pin}


def read_llama_release(root: Path) -> str:
    path = root / "llama-release"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def choose_rebuild_script(pre_llama: str, post_llama: str) -> str:
    """Return ``install`` if llama-release changed, else ``build``."""
    if pre_llama.strip() != post_llama.strip():
        return "install"
    return "build"


def _init_update_progress(workers: list[dict[str, Any]]) -> None:
    _PROGRESS_STORE.initialize(workers, software_identity())


def _set_update_phase(phase: str, *, pct: int | None = None, error: str | None = None) -> None:
    _PROGRESS_STORE.set_phase(phase, pct=pct, error=error)


def apply_worker_progress(
    *,
    node_id: str | None,
    host: str | None = None,
    phase: str,
    error: str | None = None,
    git_head: str | None = None,
    llama_release: str | None = None,
    rebuild: str | None = None,
) -> dict[str, Any] | None:
    """Update one worker row from a progress post; return aggregate snapshot."""
    return _PROGRESS_STORE.apply_worker(
        node_id=node_id,
        host=host,
        phase=phase,
        error=error,
        git_head=git_head,
        llama_release=llama_release,
        rebuild=rebuild,
    )


def request_worker_update(
    host: str,
    port: int,
    join_key: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Tell a worker agent to accept and spawn its detached updater."""
    url = f"http://{host}:{port}/v2/cluster/updates"
    headers = {"X-Kllm-Key": join_key}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json={}, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("worker returned an invalid update response")
        return payload


def fanout_worker_updates(
    workers: list[dict[str, Any]],
    join_key: str,
    *,
    default_port: int = DEFAULT_CONTROL_PORT,
) -> dict[str, Any]:
    """Fan out update commands to joined workers (excludes master rows)."""
    if not _UPDATE_FANOUT_LOCK.acquire(blocking=False):
        raise UpdateInProgressError("a cluster update is already in progress")
    worker_list = [
        {
            "node_id": w.get("node_id"),
            "host": w.get("lan_host") or w.get("host"),
            "port": int(w.get("control_port") or default_port),
        }
        for w in workers
        if (w.get("lan_host") or w.get("host")) and (w.get("role") or "worker") != "master"
    ]
    try:
        _init_update_progress(worker_list)
        if not worker_list:
            _set_update_phase("done", pct=100)
            return {"ok": True, "workers": [], "update": get_update_progress()}

        results: list[dict[str, Any]] = []
        for w in worker_list:
            host = str(w["host"])
            port = int(w["port"] or default_port)
            node_id = w.get("node_id")
            try:
                body = request_worker_update(host, port, join_key)
                apply_worker_progress(
                    node_id=node_id if isinstance(node_id, str) else None,
                    host=host,
                    phase="accepted",
                )
                results.append({"host": host, "node_id": node_id, "ok": True, "result": body})
            except Exception as exc:
                apply_worker_progress(
                    node_id=node_id if isinstance(node_id, str) else None,
                    host=host,
                    phase="error",
                    error=str(exc),
                )
                results.append({"host": host, "node_id": node_id, "ok": False, "error": str(exc)})
        _set_update_phase("updating")
        return {"ok": True, "workers": results, "update": get_update_progress()}
    finally:
        _UPDATE_FANOUT_LOCK.release()


def save_reconnect(params: dict[str, Any]) -> Path:
    return _RECONNECT_STORE.save(params)


def load_reconnect() -> dict[str, Any]:
    try:
        return _RECONNECT_STORE.load()
    except ReconnectStateError as exc:
        raise UpdateError(str(exc)) from exc


def clear_reconnect() -> None:
    _RECONNECT_STORE.clear()


def build_reconnect_params(
    *,
    node_id: str,
    join_key: str,
    master_host: str,
    master_port: int,
    control_port: int,
    rpc_port: int,
    rpc_bind: str | None,
    overlay: str,
    gpu_ids: list[int] | None,
    master_base: str | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    root = repo if repo is not None else repo_root()
    return {
        "node_id": node_id,
        "join_key": join_key,
        "master_host": master_host,
        "master_port": int(master_port),
        "control_port": int(control_port),
        "rpc_port": int(rpc_port),
        "rpc_bind": rpc_bind,
        "overlay": overlay or "none",
        "gpu_ids": list(gpu_ids) if gpu_ids is not None else None,
        "master_base": master_base or f"http://{master_host}:{int(master_port)}",
        "repo_root": str(root) if root else None,
    }


def spawn_updater() -> int:
    """Detach ``python -m kllm.cluster_update run``; return child pid."""
    ensure_home()
    log = logs_dir() / "update.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    root = repo_root()
    if root and not env.get("KLLM_REPO_ROOT"):
        env["KLLM_REPO_ROOT"] = str(root)
    with log.open("a", encoding="utf-8") as lf:
        lf.write(f"\n--- update spawn {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        lf.flush()
        proc = subprocess.Popen(
            [sys.executable, "-m", "kllm.cluster_update", "run"],
            stdout=lf,
            stderr=lf,
            start_new_session=True,
            env=env,
            cwd=str(root) if root else None,
        )
    return int(proc.pid)


def accept_local_update(
    *,
    role: str,
    node_id: str,
    join_key: str | None,
    master_host: str | None,
    master_port: int,
    control_port: int,
    rpc_port: int,
    rpc_bind: str | None,
    overlay: str,
    gpu_ids: list[int] | None,
    master_base: str | None = None,
) -> dict[str, Any]:
    """Worker-side accept: persist reconnect params and spawn the updater."""
    if role != "worker":
        raise UpdateError("cluster update requires worker role")
    if not join_key or not master_host:
        raise UpdateError("worker is missing join key or master host")
    root = repo_root()
    if root is None:
        raise UpdateError("kllm repo root not found; set KLLM_REPO_ROOT")
    if not (root / ".git").exists():
        raise UpdateError(f"repo root is not a git checkout: {root}")
    params = build_reconnect_params(
        node_id=node_id,
        join_key=join_key,
        master_host=master_host,
        master_port=master_port,
        control_port=control_port,
        rpc_port=rpc_port,
        rpc_bind=rpc_bind,
        overlay=overlay,
        gpu_ids=gpu_ids,
        master_base=master_base,
        repo=root,
    )
    save_reconnect(params)
    pid = spawn_updater()
    return {"ok": True, "accepted": True, "updater_pid": pid, "repo_root": str(root)}


def _git_ensure_clean(root: Path) -> None:
    """Compatibility wrapper for the detached update worker implementation."""
    from kllm.commands.update_worker import _git_ensure_clean as ensure_clean

    ensure_clean(root)


def run_updater() -> int:
    """Compatibility wrapper for the detached update worker entry point."""
    from kllm.commands.update_worker import run_updater as run_worker

    return run_worker()


def main(argv: list[str] | None = None) -> int:
    """Compatibility wrapper for the update worker command parser."""
    from kllm.commands.update_worker import main as worker_main

    return worker_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
