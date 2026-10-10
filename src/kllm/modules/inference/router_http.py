"""HTTP-bound inference planning and launch orchestration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Protocol
from contextlib import nullcontext
from typing import ContextManager
from uuid import uuid4

from fastapi import HTTPException

from kllm.shared.lifecycle_bus import LifecycleEvent
from kllm.modules.inference.interactors.run_route import run_response
from kllm.modules.inference.entities import PlanBody, RunBody, RunSettingsFields
from kllm.shared.state import ClusterState
from kllm.modules.inference.interactors.effective_config import effective_configuration
from kllm.modules.inference.interactors.rpc_runs import remap_rpc_plan, selected_workers
from kllm.modules.runtimes.interactors.selection import snapshot


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
    network_members: Callable[[str], dict[str, Any]] | None = None
    network_run: Callable[[str | None], ContextManager[dict[str, Any]]] | None = None
    network_release: Callable[[], None] | None = None
    network_diagnostics: Callable[[str], dict[str, Any]] | None = None
    network_stop: Callable[[], ContextManager[None]] | None = None
    resolve_settings: Callable[[PlanBody | RunBody], PlanBody | RunBody] | None = None
    preflight: Callable[[RunSettingsFields, Path], None] | None = None
    rpc_context: (
        Callable[[RunBody, dict[str, Any], dict[str, Any]], ContextManager[None]] | None
    ) = None
    network_run_selected: (
        Callable[[str | None, list[str]], ContextManager[dict[str, Any]]] | None
    ) = None
    runtime_context: Callable[[], ContextManager[dict[str, Any]]] | None = None
    lifecycle_publisher: Callable[[LifecycleEvent, str | None], None] | None = None


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
        with self._deps.runtime_context() if self._deps.runtime_context else nullcontext():
            return self._plan(body, preview)

    def _plan(self, body: PlanBody, preview: bool = False) -> dict[str, Any]:
        state = self._deps.state
        if state.role != "master":
            raise HTTPException(400, "plan requires master role")
        if self._deps.resolve_settings:
            body = self._deps.resolve_settings(body)
        path = self._deps.resolve_model(body.model)
        if self._deps.preflight:
            self._deps.preflight(body, path)
        self._deps.ensure_master_member()
        self._guard_context(body)
        self._deps.merge_extra(body)
        members = state.members
        if body.network_id:
            if self._deps.network_members is None:
                raise HTTPException(409, detail={"code": "UNSUPPORTED_CAPABILITY"})
            members = self._deps.network_members(body.network_id)
        result = self._deps.execute_plan(body, members)
        runtime_snapshot = snapshot()
        if runtime_snapshot:
            result["runtime_id"] = runtime_snapshot["runtime_id"]
            result["runtime_required_nodes"] = [state.node_id, *selected_workers(result, members)]
        result["effective_config"] = effective_configuration(body, result, path)
        if body.network_id and self._deps.network_diagnostics:
            result.update(self._deps.network_diagnostics(body.network_id))
        if preview or body.model_config.get("extra") != "ignore":
            if not preview and body.remember:
                self._deps.remember(body)
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
        if self._deps.lifecycle_publisher is not None:
            self._deps.lifecycle_publisher(LifecycleEvent.DEPLOY_PLANNED, body.model)
        return result

    def run(self, body: RunBody) -> dict[str, Any]:
        with self._deps.runtime_context() if self._deps.runtime_context else nullcontext():
            return self._run(body)

    def _run(self, body: RunBody) -> dict[str, Any]:
        state = self._deps.state
        if state.role != "master":
            raise HTTPException(400, "run requires master role")
        if self._deps.resolve_settings:
            body = self._deps.resolve_settings(body)
        path = self._deps.resolve_model(body.model)
        if self._deps.preflight:
            self._deps.preflight(body, path)
        self._deps.ensure_master_member()
        self._guard_context(body)
        self._deps.merge_extra(body)
        self._deps.validate_vision(body)
        if body.network_id and self._deps.network_run is None:
            raise HTTPException(409, detail={"code": "UNSUPPORTED_CAPABILITY"})
        members = (
            self._deps.network_members(body.network_id)
            if body.network_id and self._deps.network_members
            else state.members
        )
        plan_result = self._deps.execute_plan(body, members)
        if plan_result.get("fit_status") == "overflow":
            raise HTTPException(
                400, plan_result.get("hint") or "model and context exceed cluster budget (overflow)"
            )
        rpc_context = (
            self._deps.rpc_context(body, plan_result, members)
            if self._deps.rpc_context
            else nullcontext()
        )
        with rpc_context:
            return self._launch(body, path, plan_result, members)

    def _launch(
        self, body: RunBody, path: Path, plan_result: dict[str, Any], members: dict[str, Any]
    ) -> dict[str, Any]:
        state = self._deps.state
        if self._deps.network_run_selected:
            context = self._deps.network_run_selected(
                body.network_id, selected_workers(plan_result, members)
            )
        else:
            context = (
                self._deps.network_run(body.network_id)
                if self._deps.network_run
                else nullcontext(members)
            )
        with context as launch_members:
            plan_result = remap_rpc_plan(plan_result, members, launch_members)
            if body.network_id and self._deps.network_diagnostics:
                plan_result.update(self._deps.network_diagnostics(body.network_id))
            if plan_result.get("fit_status") == "overflow":
                raise HTTPException(
                    400,
                    plan_result.get("hint") or "model and context exceed cluster budget (overflow)",
                )
            path = self._deps.resolve_model(body.model)
            rpc_servers = list(plan_result.get("rpc_endpoints") or [])
            if not body.network_id and self._deps.rpc_context is None:
                self._deps.ensure_rpc_peers_reachable(rpc_servers)
            root = self._deps.repository_root()
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
            effective = effective_configuration(body, plan_result, path)
            selected_runtime = snapshot()
            if selected_runtime:
                effective["runtime"] = {
                    key: selected_runtime[key]
                    for key in ("runtime_id", "build_id", "repository", "commit", "ref")
                }
            self._deps.stop_previous_master()
            state.active_config = None
            state.process_pid = None
            state.process_role = None
            state.save()
            launched = self._deps.spawn_master(launch_config)
            effective.update({"process_pid": launched.get("pid"), "run_id": str(uuid4())})
            state.active_config = effective
            self._deps.finish_master_launch(
                path,
                plan_result,
                launched,
                lambda: self._deps.persist_last_settings(body),
                lambda: self._deps.remember(body),
            )
            return {**run_response(plan_result, launched), "effective_config": effective}

    def stop(self) -> dict[str, Any]:
        state = self._deps.state
        if state.role != "master":
            raise HTTPException(400, "stop inference requires master role")
        context = self._deps.network_stop() if self._deps.network_stop else nullcontext()
        with context:
            self._deps.stop_managed_inference()
            if self._deps.network_release:
                self._deps.network_release()
            state.model_name = None
            state.active_config = None
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

LEGACY_V1_LOAD: tuple[tuple[str, str, str], ...] = (
    ('/v1/load-progress', 'GET', 'load_progress_endpoint'),
)

LEGACY_V1_PROXY: tuple[tuple[str, str, str], ...] = (
    ('/v1/chat/completions', 'POST', 'chat_completions'),
    ('/v1/messages', 'POST', 'anthropic_messages'),
    ('/v1/responses', 'POST', 'responses'),
    ('/v1/responses/input_tokens', 'POST', 'response_tokens'),
)

LEGACY_V1_RUN: tuple[tuple[str, str, str], ...] = (
    ('/v1/model-settings/{model}', 'GET', 'get_model_settings'),
    ('/v1/run-capabilities', 'GET', 'run_capabilities'),
    ('/v1/chat-template/validate', 'POST', 'validate_chat_template'),
    ('/v1/model-settings/{model}', 'PUT', 'put_model_settings'),
    ('/v1/plan', 'POST', 'plan'),
    ('/v1/run', 'POST', 'run'),
    ('/v1/inference/stop', 'POST', 'stop_inference'),
)

CONTROL_V2: tuple[tuple[str, str, str], ...] = (
    ('/models/chat-template/validation', 'POST', 'validate_chat_template'),
    ('/inference/capabilities', 'GET', 'run_capabilities'),
    ('/inference/models', 'GET', 'inference_models'),
    ('/inference/connection', 'GET', 'connection_snapshot'),
    ('/inference/plans', 'POST', 'plan'),
    ('/inference/runs', 'POST', 'run'),
    ('/inference/runs/stop', 'POST', 'stop_inference'),
    ('/inference/load', 'GET', 'load_progress_endpoint'),
    ('/inference/model-settings/{model}', 'GET', 'get_model_settings'),
    ('/inference/model-settings/{model}', 'PUT', 'put_model_settings'),
)
