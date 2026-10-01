from __future__ import annotations

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


def test_runtime_setup_restarts_worker_with_selected_devices_and_records_process():
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
                                   "relaunch": {"pid": 99}}
    assert calls == [
        ("stop", 42, True),
        ("clear",),
        (
            "start",
            {
                "host": "10.0.0.2",
                "port": 50052,
                "repo_root": "/repo",
                "env": {"VISIBLE": "cuda:[2]"},
                "devices": ["GPU-2"],
                "log_path": "/logs/rpc.log",
            },
        ),
        ("register", 99, "rpc-server"),
    ]
    assert (state.process_pid, state.process_role, saved) == (99, "worker", [True])


def test_runtime_setup_returns_relaunch_failure_without_losing_install_result():
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
        "relaunch": {"error": "spawn failed"},
    }
