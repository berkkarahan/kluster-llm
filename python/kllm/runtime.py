"""Track processes started by kllm so stop only kills what we launched."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from kllm import native
from kllm.state import DEFAULT_CONTROL_PORT, ensure_home, runtime_path


@dataclass
class ChildProc:
    pid: int
    kind: str


@dataclass
class RuntimeInfo:
    agent_pid: int | None = None
    control_port: int = DEFAULT_CONTROL_PORT
    children: list[ChildProc] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_pid": self.agent_pid,
            "control_port": self.control_port,
            "children": [asdict(c) for c in self.children],
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
                children.append(ChildProc(pid=pid, kind=str(kind)))
        agent_pid = data.get("agent_pid")
        if not isinstance(agent_pid, int) or agent_pid <= 0:
            agent_pid = None
        port = data.get("control_port", DEFAULT_CONTROL_PORT)
        if not isinstance(port, int):
            port = DEFAULT_CONTROL_PORT
        return cls(agent_pid=agent_pid, control_port=port, children=children)


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


def register_agent(pid: int, control_port: int = DEFAULT_CONTROL_PORT) -> None:
    info = load()
    info.agent_pid = pid
    info.control_port = control_port
    save(info)


def register_child(pid: int, kind: str) -> None:
    info = load()
    # Local node runs at most one llama/rpc child at a time.
    info.children = [ChildProc(pid=pid, kind=kind)]
    save(info)


def clear_children() -> None:
    info = load()
    if not info.children:
        return
    info.children = []
    save(info)


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


def _alive(pid: int) -> bool:
    try:
        return bool(native.process_alive(pid))
    except Exception:
        return False
