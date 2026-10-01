"""HTTP boundary and composition for local llama.cpp runtime setup."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm.agent.schemas import LlamaSetupBody
from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.agent.services.runtime_setup import RuntimeSetupService
from kllm.exceptions import NotFoundError, ValidationError
from kllm.state import ClusterState


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
) -> RuntimeSetupService:
    """Wire platform adapters into the domain runtime installer."""

    def install_pack(tag: str | None, backend: str, force: bool) -> dict[str, Any]:
        from kllm.llama_pack import install_cached_llama_pack

        return install_cached_llama_pack(tag=tag, backend=backend, force=force)

    return RuntimeSetupService(
        state=state,
        install_pack=install_pack,
        repository_root=repository_root,
        gpu_visibility_env=gpu_visibility_env,
        stop_process=stop_process,
        clear_children=clear_children,
        start_worker=start_worker,
        resolve_gpu_selection=resolve_gpu_selection,
        register_child=register_child,
        rpc_log_path=rpc_log_path,
    )


def setup_runtime(
    body: LlamaSetupBody | None,
    api_key: str | None,
    *,
    state: ClusterState,
    service: Callable[[], RuntimeSetupService],
) -> dict[str, Any]:
    """Authorize and execute the runtime setup operation at the HTTP boundary."""
    if state.join_key and api_key != state.join_key:
        raise HTTPException(401, "invalid join key")
    try:
        result = service().setup(body or LlamaSetupBody())
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result.as_payload()
