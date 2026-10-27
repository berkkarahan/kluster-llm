"""Composition for the worker runtime service (no installation)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from kllm.modules.settings.interactors.gpus import ResolvedGpuSelection
from kllm.modules.runtimes.interactors.runtime_setup import RuntimeSetupService
from kllm.shared.state import ClusterState


def build_runtime_setup_service(
    *,
    state: Callable[[], ClusterState],
    repository_root: Callable[[], Path | None],
    gpu_visibility_env: Callable[[str, list[int] | None], dict[str, str]],
    stop_process: Callable[..., Any],
    clear_children: Callable[[], None],
    start_worker: Callable[..., dict[str, Any]],
    resolve_gpu_selection: Callable[[list[int] | None], ResolvedGpuSelection],
    register_child: Callable[[int, str], None],
    rpc_log_path: Callable[[], Path],
    process_alive: Callable[[int], bool] = lambda _pid: True,
    stop_stray_rpc_servers: Callable[[], int] = lambda: 0,
) -> RuntimeSetupService:
    """Wire platform adapters into the worker runtime service."""
    return RuntimeSetupService(
        state=state,
        repository_root=repository_root,
        gpu_visibility_env=gpu_visibility_env,
        stop_process=stop_process,
        clear_children=clear_children,
        start_worker=start_worker,
        resolve_gpu_selection=resolve_gpu_selection,
        register_child=register_child,
        rpc_log_path=rpc_log_path,
        process_alive=process_alive,
        stop_stray_rpc_servers=stop_stray_rpc_servers,
    )
