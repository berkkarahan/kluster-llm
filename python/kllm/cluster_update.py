"""Master-orchestrated worker git pull / rebuild / reconnect."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from kllm import runtime
from kllm.state import (
    DEFAULT_CONTROL_PORT,
    DEFAULT_RPC_PORT,
    ensure_home,
    home_dir,
    logs_dir,
    repo_root,
)

_UPDATE_PROGRESS: dict[str, Any] | None = None
_UPDATE_PROGRESS_LOCK = threading.Lock()
_UPDATE_FANOUT_LOCK = threading.Lock()

_RECONNECT_NAME = "update-reconnect.json"


class UpdateInProgressError(RuntimeError):
    pass


class UpdateError(RuntimeError):
    pass


def reconnect_path() -> Path:
    return home_dir() / _RECONNECT_NAME


def get_update_progress() -> dict[str, Any] | None:
    with _UPDATE_PROGRESS_LOCK:
        return _snapshot_update_progress_locked()


def _snapshot_update_progress_locked() -> dict[str, Any] | None:
    snap = _UPDATE_PROGRESS
    if snap is None:
        return None
    out = dict(snap)
    workers = snap.get("workers")
    if isinstance(workers, list):
        out["workers"] = [dict(w) for w in workers]
    return out


def clear_update_progress() -> None:
    global _UPDATE_PROGRESS
    with _UPDATE_PROGRESS_LOCK:
        _UPDATE_PROGRESS = None


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
    global _UPDATE_PROGRESS
    identity = software_identity()
    rows = [
        {
            "node_id": w.get("node_id"),
            "host": w.get("host"),
            "phase": "pending",
            "error": None,
            "git_head": None,
            "llama_release": None,
            "rebuild": None,
        }
        for w in workers
    ]
    with _UPDATE_PROGRESS_LOCK:
        _UPDATE_PROGRESS = {
            "phase": "fanout",
            "pct": 0,
            "target_git_head": identity.get("git_head"),
            "target_llama_release": identity.get("llama_release"),
            "workers": rows,
            "error": None,
        }


def _set_update_phase(phase: str, *, pct: int | None = None, error: str | None = None) -> None:
    with _UPDATE_PROGRESS_LOCK:
        if _UPDATE_PROGRESS is None:
            return
        _UPDATE_PROGRESS["phase"] = phase
        if pct is not None:
            _UPDATE_PROGRESS["pct"] = pct
        if error is not None:
            _UPDATE_PROGRESS["error"] = error


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
    with _UPDATE_PROGRESS_LOCK:
        if _UPDATE_PROGRESS is None:
            # Late progress after clear — ignore.
            return None
        workers = _UPDATE_PROGRESS.get("workers") or []
        idx = None
        for i, row in enumerate(workers):
            if node_id and row.get("node_id") == node_id:
                idx = i
                break
        if idx is None and host:
            for i, row in enumerate(workers):
                if row.get("host") == host:
                    idx = i
                    break
        if idx is None:
            workers.append(
                {
                    "node_id": node_id,
                    "host": host,
                    "phase": phase,
                    "error": error,
                    "git_head": git_head,
                    "llama_release": llama_release,
                    "rebuild": rebuild,
                }
            )
            _UPDATE_PROGRESS["workers"] = workers
        else:
            row = dict(workers[idx])
            row["phase"] = phase
            if error is not None:
                row["error"] = error
            if git_head is not None:
                row["git_head"] = git_head
            if llama_release is not None:
                row["llama_release"] = llama_release
            if rebuild is not None:
                row["rebuild"] = rebuild
            workers[idx] = row
            _UPDATE_PROGRESS["workers"] = workers

        done = sum(1 for w in workers if w.get("phase") in ("done", "error"))
        total = max(len(workers), 1)
        pct = int(100 * done / total)
        _UPDATE_PROGRESS["pct"] = pct
        if workers and all(w.get("phase") == "done" for w in workers):
            _UPDATE_PROGRESS["phase"] = "done"
        elif workers and all(w.get("phase") in ("done", "error") for w in workers):
            _UPDATE_PROGRESS["phase"] = "error"
            errs = [w.get("error") for w in workers if w.get("phase") == "error"]
            _UPDATE_PROGRESS["error"] = "; ".join(e for e in errs if e) or "worker update failed"
        elif any(w.get("phase") not in ("pending", "done", "error") for w in workers):
            _UPDATE_PROGRESS["phase"] = "updating"
        return _snapshot_update_progress_locked()

def request_worker_update(
    host: str,
    port: int,
    join_key: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Tell a worker agent to accept and spawn its detached updater."""
    url = f"http://{host}:{port}/v1/cluster/update"
    headers = {"X-Kllm-Key": join_key}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json={}, headers=headers)
        resp.raise_for_status()
        return resp.json()


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
            port = int(w["port"])
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
                results.append(
                    {"host": host, "node_id": node_id, "ok": False, "error": str(exc)}
                )
        _set_update_phase("updating")
        return {"ok": True, "workers": results, "update": get_update_progress()}
    finally:
        _UPDATE_FANOUT_LOCK.release()


def save_reconnect(params: dict[str, Any]) -> Path:
    ensure_home()
    path = reconnect_path()
    path.write_text(json.dumps(params, indent=2), encoding="utf-8")
    return path


def load_reconnect() -> dict[str, Any]:
    path = reconnect_path()
    if not path.is_file():
        raise UpdateError(f"missing reconnect file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise UpdateError("invalid reconnect file")
    return data


def clear_reconnect() -> None:
    path = reconnect_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


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
                resp = client.post(f"{base}/v1/cluster/update-progress", json=body, headers=headers)
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
            client.post("/v1/leave")
    except Exception:
        pass


def _stop_local_agent(port: int) -> None:
    if _agent_healthy(port):
        try:
            with httpx.Client(base_url=_agent_base(port), timeout=30.0) as client:
                client.post("/v1/stop")
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
        "overlay": params.get("overlay") or "none",
    }
    if params.get("gpu_ids") is not None:
        payload["gpu_ids"] = params["gpu_ids"]
    with httpx.Client(base_url=_agent_base(port), timeout=120.0) as client:
        resp = client.post("/v1/join", json=payload)
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
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print("usage: python -m kllm.cluster_update run", file=sys.stderr)
        return 2
    if args[0] == "run":
        return run_updater()
    print(f"unknown command: {args[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
