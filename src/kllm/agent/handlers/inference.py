"""HTTP-bound inference planning and launch orchestration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import HTTPException

from kllm.agent.run_route import run_response
from kllm.run_settings import PlanBody, RunBody, RunSettingsFields
from kllm.state import ClusterState


class MasterLaunchBuilder(Protocol):
    def __call__(
        self,
        body: RunBody,
        plan_result: dict[str, Any],
        *,
        model_path: Path,
        rpc_servers: list[str],
        infer_port: int,
        repo_root: str | None,
        gpu_env: dict[str, str],
        extra_args: list[str],
    ) -> dict[str, Any]: ...


class MasterLaunchFinisher(Protocol):
    def __call__(
        self,
        model_path: Path,
        plan_result: dict[str, Any],
        launched: dict[str, Any],
        persist_settings: Callable[[], None],
        remember: Callable[[], None],
    ) -> None: ...


@dataclass(frozen=True)
class InferenceDependencies:
    """Application collaborators explicitly supplied by the agent composition root."""

    state: ClusterState
    ensure_master_member: Callable[[], bool]
    merge_extra: Callable[[RunSettingsFields], list[str]]
    execute_plan: Callable[[RunSettingsFields, dict[str, Any]], dict[str, Any]]
    resolve_model: Callable[[str], Path]
    persist_last_settings: Callable[[PlanBody | RunBody], None]
    remember: Callable[[PlanBody | RunBody], None]
    validate_vision: Callable[[RunBody], None]
    ensure_rpc_peers_reachable: Callable[[list[str]], None]
    repository_root: Callable[[], Path | None]
    stop_previous_master: Callable[[], None]
    gpu_visibility_env: Callable[[str, list[int] | None], dict[str, str]]
    sanitize_extra_args: Callable[[list[str]], list[str]]
    build_master_launch: MasterLaunchBuilder
    spawn_master: Callable[[dict[str, Any]], dict[str, Any]]
    finish_master_launch: MasterLaunchFinisher
    stop_managed_inference: Callable[[], None]
    inference_idle: Callable[[], dict[str, Any]]


class InferenceHandlers:
    """Validate inference requests, plan capacity, and launch the master process."""

    def __init__(self, dependencies: InferenceDependencies) -> None:
        self._deps = dependencies

    @staticmethod
    def _guard_context(body: RunSettingsFields) -> None:
        if not body.context_auto and body.context < 512:
            raise HTTPException(
                400, detail={"context": "context must be >= 512 when context_auto is false"}
            )

    def plan(self, body: PlanBody, preview: bool = False) -> dict[str, Any]:
        state = self._deps.state
        if state.role != "master":
            raise HTTPException(400, "plan requires master role")
        self._deps.ensure_master_member()
        self._guard_context(body)
        self._deps.merge_extra(body)
        result = self._deps.execute_plan(body, state.members)
        if preview:
            return result
        if state.process_pid:
            self._deps.remember(body)
            return result
        path = self._deps.resolve_model(body.model)
        state.last_plan = result
        state.model_name = path.name
        if result.get("fit_status") == "overflow":
            state.apply_error = (
                result.get("hint") or "model and context exceed cluster budget (overflow)"
            )
            state.apply_warning = None
        elif result.get("fit_status") == "tight":
            state.apply_error = None
            state.apply_warning = result.get("warning") or result.get("hint")
        else:
            state.apply_error = None
            state.apply_warning = None
        self._deps.persist_last_settings(body)
        state.save()
        self._deps.remember(body)
        return result

    def run(self, body: RunBody) -> dict[str, Any]:
        state = self._deps.state
        if state.role != "master":
            raise HTTPException(400, "run requires master role")
        self._deps.ensure_master_member()
        self._guard_context(body)
        self._deps.merge_extra(body)
        self._deps.validate_vision(body)
        plan_result = self._deps.execute_plan(body, state.members)
        if plan_result.get("fit_status") == "overflow":
            raise HTTPException(
                400,
                plan_result.get("hint") or "model and context exceed cluster budget (overflow)",
            )
        path = self._deps.resolve_model(body.model)
        rpc_servers = list(plan_result.get("rpc_endpoints") or [])
        self._deps.ensure_rpc_peers_reachable(rpc_servers)
        root = self._deps.repository_root()
        self._deps.stop_previous_master()
        backend = (state.members.get(state.node_id) or {}).get("backend") or "cpu"
        gpu_env = self._deps.gpu_visibility_env(backend, state.gpu_ids)
        extra = self._deps.sanitize_extra_args(body.extra_args) if body.extra_args else []
        self._debug_launch_inputs(backend, state.gpu_ids, plan_result, rpc_servers, gpu_env)
        launch_config = self._deps.build_master_launch(
            body,
            plan_result,
            model_path=path,
            rpc_servers=rpc_servers,
            infer_port=state.infer_port,
            repo_root=str(root) if root else None,
            gpu_env=gpu_env,
            extra_args=extra,
        )
        launched = self._deps.spawn_master(launch_config)
        self._deps.finish_master_launch(
            path,
            plan_result,
            launched,
            lambda: self._deps.persist_last_settings(body),
            lambda: self._deps.remember(body),
        )
        return run_response(plan_result, launched)

    def stop(self) -> dict[str, Any]:
        state = self._deps.state
        if state.role != "master":
            raise HTTPException(400, "stop inference requires master role")
        self._deps.stop_managed_inference()
        state.model_name = None
        state.inference_activity = self._deps.inference_idle()
        state.save()
        return {"ok": True, "role": state.role, "model_name": None}

    @staticmethod
    def _debug_launch_inputs(
        backend: str,
        gpu_ids: list[int] | None,
        plan_result: dict[str, Any],
        rpc_servers: list[str],
        gpu_env: dict[str, str],
    ) -> None:
        if os.getenv("KLLM_DEBUG_HARDWARE", "").lower() not in ("1", "true", "yes"):
            return
        try:
            from kllm._dbg_a1ff77 import write

            write(
                "H1,H2,H4",
                "agent/handlers/inference.py:run",
                "run launch inputs",
                {
                    "backend": backend,
                    "gpu_ids": gpu_ids,
                    "gpu_env": gpu_env,
                    "rpc_servers": rpc_servers,
                    "tensor_split": plan_result.get("tensor_split_csv"),
                    "node_ids": plan_result.get("node_ids"),
                    "ngl": plan_result.get("ngl"),
                    "node_devices": plan_result.get("node_devices"),
                    "node_alloc_bytes": plan_result.get("node_alloc_bytes"),
                    "device_effective": plan_result.get("device_effective"),
                },
            )
        except Exception:
            pass
