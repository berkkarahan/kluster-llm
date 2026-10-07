from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from kllm.agent.schemas import LlamaSetupBody
from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.agent.services.runtime_setup import RuntimeSetupService
from kllm.core.gpu_selection import GpuDeviceInput, GpuSelection
from kllm.exceptions import NotFoundError
from kllm.state import ClusterState


def test_runtime_setup_maps_install_errors_to_domain_exceptions():
    state = ClusterState(node_id="node-1", role="master")
    service = RuntimeSetupService(
        lambda: state,
        install_pack=lambda *_args: (_ for _ in ()).throw(FileNotFoundError("pack missing")),
        repository_root=lambda: None,
        gpu_visibility_env=lambda *_args: {},
        stop_process=lambda *_args, **_kwargs: None,
        clear_children=lambda: None,
        start_worker=lambda **_kwargs: {},
        resolve_gpu_selection=lambda _ids: ResolvedGpuSelection(
            GpuSelection((), "cpu", None), ()
        ),
        register_child=lambda *_args: None,
        rpc_log_path=lambda: Path("rpc.log"),
    )

    with pytest.raises(NotFoundError, match="pack missing"):
        service.setup(LlamaSetupBody())


def test_installation_defers_start_until_a_master_command():
    state = ClusterState(
        node_id="worker-1",
        role="worker",
        process_pid=42,
        rpc_bind="10.0.0.2",
        rpc_port=50052,
        gpu_ids=[2],
    )
    state.members[state.node_id] = {"backend": "cuda"}
    saved: list[bool] = []
    state.save = lambda: saved.append(True)  # type: ignore[method-assign]
    calls: list[object] = []
    service = RuntimeSetupService(
        lambda: state,
        install_pack=lambda tag, backend, force: {
            "ok": True,
            "tag": tag,
            "backend": backend,
            "force": force,
        },
        repository_root=lambda: Path("/repo"),
        gpu_visibility_env=lambda backend, ids: {"VISIBLE": f"{backend}:{ids}"},
        stop_process=lambda pid, *, force: calls.append(("stop", pid, force)),
        clear_children=lambda: calls.append(("clear",)),
        start_worker=lambda **kwargs: calls.append(("start", kwargs)) or {"pid": 99},
        resolve_gpu_selection=lambda ids: ResolvedGpuSelection(
            GpuSelection((2,), "cuda", 1024) if ids == [2] else GpuSelection((), "cpu", None),
            (GpuDeviceInput(index=2, device="GPU-2"),) if ids == [2] else (),
        ),
        register_child=lambda pid, role: calls.append(("register", pid, role)),
        rpc_log_path=lambda: Path("/logs/rpc.log"),
    )

    result = service.setup(LlamaSetupBody(tag="v1", backend="cuda", force=True))

    assert result.as_payload() == {"ok": True, "tag": "v1", "backend": "cuda", "force": True,
                                   "relaunch": None}
    assert calls == []
    assert state.process_pid == 42
    assert service.start_commanded_worker(state) == {"pid": 99}
    assert calls == [
        ("stop", 42, True),
        ("clear",),
        (
            "start",
            {
                "host": "10.0.0.2",
                "port": 50052,
                "repo_root": "/repo",
                "env": None,
                "devices": ["GPU-2", "CPU"],
                "log_path": "/logs/rpc.log",
            },
        ),
        ("register", 99, "rpc-server"),
    ]
    assert (state.process_pid, state.process_role, saved) == (99, "worker", [True])
    assert state.members[state.node_id]["rpc_devices"] == [
        {"index": 0, "device": "GPU-2", "backend": "cuda", "memory_kind": "gpu"},
        {"index": 1, "device": "CPU", "backend": "cpu", "memory_kind": "ram"},
    ]


def test_runtime_setup_does_not_try_to_spawn_when_install_succeeds():
    state = ClusterState(node_id="worker-1", role="worker")
    service = RuntimeSetupService(
        lambda: state,
        install_pack=lambda *_args: {"ok": True},
        repository_root=lambda: None,
        gpu_visibility_env=lambda *_args: {},
        stop_process=lambda *_args, **_kwargs: None,
        clear_children=lambda: None,
        start_worker=lambda **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
        resolve_gpu_selection=lambda _ids: ResolvedGpuSelection(
            GpuSelection((), "cpu", None), ()
        ),
        register_child=lambda *_args: None,
        rpc_log_path=lambda: Path("rpc.log"),
    )

    assert service.setup(LlamaSetupBody()).as_payload() == {
        "ok": True,
        "relaunch": None,
    }


def _worker_state() -> ClusterState:
    state = ClusterState(
        node_id="worker-1",
        role="worker",
        master_host="10.0.0.1",
        join_key="secret",
        rpc_bind="10.0.0.2",
        rpc_port=50052,
        gpu_ids=[2],
    )
    state.members[state.node_id] = {"backend": "cuda"}
    state.save = lambda: None  # type: ignore[method-assign]
    return state


def _service(state: ClusterState, calls: list[object], *, alive: bool | Callable[[int], bool] = True) -> RuntimeSetupService:
    def process_alive(pid: int) -> bool:
        return alive(pid) if callable(alive) else bool(alive)

    return RuntimeSetupService(
        lambda: state,
        install_pack=lambda *_args: {"ok": True},
        repository_root=lambda: Path("/repo"),
        gpu_visibility_env=lambda *_args: {},
        stop_process=lambda pid, *, force: calls.append(("stop", pid, force)),
        clear_children=lambda: calls.append(("clear",)),
        start_worker=lambda **kwargs: calls.append(("start", kwargs)) or {"pid": 99},
        resolve_gpu_selection=lambda ids: ResolvedGpuSelection(
            GpuSelection((2,), "cuda", 1024) if ids == [2] else GpuSelection((), "cpu", None),
            (GpuDeviceInput(index=2, device="GPU-2"),) if ids == [2] else (),
        ),
        register_child=lambda pid, role: calls.append(("register", pid, role)),
        rpc_log_path=lambda: Path("/logs/rpc.log"),
        process_alive=process_alive,
        stop_stray_rpc_servers=lambda: calls.append(("stray",)) or 0,
    )


def test_ensure_worker_leaves_idle_members_joined_without_spawning():
    state = _worker_state()
    calls: list[object] = []

    result = _service(state, calls).ensure_worker(state)

    assert result is None
    assert calls == []
    assert (state.process_pid, state.process_role) == (None, None)


def test_ensure_worker_skips_healthy_worker():
    state = _worker_state()
    state.process_pid = 42
    state.process_role = "worker"
    calls: list[object] = []

    result = _service(state, calls, alive=True).ensure_worker(state)

    assert result is None
    assert calls == []


def test_commanded_start_replaces_a_dead_tracked_process():
    state = _worker_state()
    state.process_pid = 42
    state.process_role = "worker"
    calls: list[object] = []

    result = _service(state, calls, alive=lambda pid: pid == 99).start_commanded_worker(state)

    assert result is not None and result.get("pid") == 99
    assert ("stop", 42, True) in calls


def test_commanded_start_clears_state_when_replacement_exits_immediately():
    state = _worker_state()
    calls: list[object] = []

    result = _service(state, calls, alive=False).start_commanded_worker(state)

    assert result is not None and result.get("error")
    assert (state.process_pid, state.process_role) == (None, None)
    assert state.members[state.node_id]["rpc_devices"] == []
    assert not any(call[0] == "register" for call in calls if isinstance(call, tuple))


def test_ensure_worker_ignores_non_worker_role():
    state = ClusterState(node_id="master", role="master")
    calls: list[object] = []

    assert _service(state, calls).ensure_worker(state) is None
    assert calls == []
