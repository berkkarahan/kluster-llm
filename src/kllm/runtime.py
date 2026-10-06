"""Track processes started by kllm so stop only kills what we launched."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from kllm import native
from kllm.state import DEFAULT_CONTROL_PORT, ensure_home, runtime_path


@dataclass
class ChildProc:
    pid: int
    kind: str
    start_token: str | None = None
    started_at: str | None = None
    log_sink_pid: int | None = None


@dataclass
class RuntimeInfo:
    agent_pid: int | None = None
    control_port: int = DEFAULT_CONTROL_PORT
    agent_log_sink_pid: int | None = None
    children: list[ChildProc] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_pid": self.agent_pid,
            "control_port": self.control_port,
            "agent_log_sink_pid": self.agent_log_sink_pid,
            "children": [{k: v for k, v in asdict(c).items() if v is not None} for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeInfo:
        children_raw = data.get("children") or []
        children: list[ChildProc] = []
        for row in children_raw:
            if not isinstance(row, dict):
                continue
            pid = row.get("pid")
            kind = row.get("kind") or "child"
            if isinstance(pid, int) and pid > 0:
                children.append(ChildProc(pid=pid, kind=str(kind),
                                          start_token=row.get("start_token"), started_at=row.get("started_at"), log_sink_pid=row.get("log_sink_pid")))
        agent_pid = data.get("agent_pid")
        if not isinstance(agent_pid, int) or agent_pid <= 0:
            agent_pid = None
        port = data.get("control_port", DEFAULT_CONTROL_PORT)
        if not isinstance(port, int):
            port = DEFAULT_CONTROL_PORT
        return cls(agent_pid=agent_pid, control_port=port, children=children, agent_log_sink_pid=data.get("agent_log_sink_pid"))


def load() -> RuntimeInfo:
    ensure_home()
    path = runtime_path()
    if not path.exists():
        return RuntimeInfo()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RuntimeInfo()
    if not isinstance(data, dict):
        return RuntimeInfo()
    return RuntimeInfo.from_dict(data)


def save(info: RuntimeInfo) -> None:
    ensure_home()
    runtime_path().write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")


def clear() -> None:
    path = runtime_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def clear_agent_if_matches(pid: int) -> bool:
    """Clear a failed agent registration without clobbering a replacement."""
    info = load()
    if info.agent_pid != pid:
        return False
    info.agent_pid = None
    info.agent_log_sink_pid = None
    if info.children:
        save(info)
    else:
        clear()
    return True


def register_agent(pid: int, control_port: int = DEFAULT_CONTROL_PORT, log_sink_pid: int | None = None) -> None:
    info = load()
    if info.agent_pid != pid or log_sink_pid is not None:
        info.agent_log_sink_pid = log_sink_pid
    info.agent_pid = pid
    info.control_port = control_port
    save(info)


def register_child(pid: int, kind: str) -> None:
    info = load()
    # Local node runs at most one llama/rpc child at a time.
    identity = native.registered_process_identity(pid) or {}
    info.children = [ChildProc(pid=pid, kind=kind, start_token=identity.get("start_token"),
                               started_at=identity.get("started_at"), log_sink_pid=identity.get("log_sink_pid"))]
    save(info)


def adopt_child_identity(pid: int | None, expected_started_at: str | None) -> bool:
    """Upgrade legacy metadata only against a previously saved matching start time."""
    if not pid or not expected_started_at or not native.resource_probe_available():
        return False
    info = load()
    child = next((c for c in info.children if c.pid == pid), None)
    if child is None or (child.start_token and child.started_at):
        return False
    try:
        token, started = native.require_native()._process_identity(pid)
    except (RuntimeError, ValueError, OSError):
        return False
    if started != expected_started_at or (child.start_token is not None and child.start_token != token):
        return False
    child.start_token, child.started_at = token, started
    save(info)
    return True


def clear_children() -> None:
    info = load()
    if not info.children:
        return
    info.children = []
    save(info)


def stop_children() -> bool:
    """Stop recorded model/runtime children while leaving the agent process alive."""
    info = load()
    stopped = False
    for child in info.children:
        if _alive(child.pid):
            native.stop_process(child.pid, force=True)
            stopped = True
    info.children = []
    save(info)
    return stopped


def any_live() -> bool:
    info = load()
    if info.agent_pid and _alive(info.agent_pid):
        return True
    return any(_alive(c.pid) for c in info.children)


def kill_tracked() -> bool:
    """Force-kill agent and children recorded in the runtime file.

    Returns True if at least one process was targeted for kill.
    """
    info = load()
    killed = False
    for child in info.children:
        if _alive(child.pid):
            native.stop_process(child.pid, force=True)
            killed = True
    if info.agent_pid and _alive(info.agent_pid):
        native.stop_process(info.agent_pid, force=True)
        killed = True
    return killed


# A node runs at most one rpc-server. A rejoin or agent restart can orphan a
# previously launched instance; it keeps holding the RPC port, so the next
# launch fails to bind and the worker silently stops contributing.
_RPC_SERVER_NAMES = frozenset(
    {"rpc-server", "rpc-server.exe", "ggml-rpc-server", "ggml-rpc-server.exe"}
)


def stop_stray_rpc_servers() -> int:
    """Terminate leftover rpc-server processes that would block the RPC port.

    Returns the number of processes targeted. Best-effort: permission or
    platform errors are ignored so a join/relaunch can still proceed.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a runtime dependency
        return 0
    me = os.getpid()
    killed = 0
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = proc.info.get("pid")
            name = (proc.info.get("name") or "").lower()
        except (psutil.Error, OSError):
            continue
        if pid in (None, me) or name not in _RPC_SERVER_NAMES:
            continue
        try:
            proc.kill()
            killed += 1
        except (psutil.Error, OSError):
            continue
    return killed


def _alive(pid: int) -> bool:
    try:
        return bool(native.process_alive(pid))
    except Exception:
        return False
