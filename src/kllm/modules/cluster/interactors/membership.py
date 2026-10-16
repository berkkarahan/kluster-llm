"""Cluster membership snapshot, liveness sweep, and shrink replan."""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from fastapi import HTTPException

from kllm.modules import models as model_ops
from kllm.shared import native, process_registry as runtime
from kllm.modules.inference.interactors import load_progress
from kllm.modules.inference.interactors.extra_args import sanitize_extra_args
from kllm.modules.inference.interactors.run_planning import ensure_rpc_peers_reachable, execute_plan, ordered_members
from kllm.modules.inference.interactors.run_launch import build_master_launch
from kllm.modules.inference.entities import RunBody
from kllm.shared.state import ClusterState, repo_root
from kllm.modules.cluster.interactors.membership_state import (
    LIVENESS_TIMEOUT_S as LIVENESS_TIMEOUT_S,
    clear_master_unreachable as clear_master_unreachable,
    clear_membership_fields as clear_membership_fields,
    cluster_snapshot as cluster_snapshot,
    last_plan_matches_members as last_plan_matches_members,
    mark_master_unreachable as mark_master_unreachable,
    public_last_plan as public_last_plan,
    reconcile_last_plan as reconcile_last_plan,
    snapshot_payload as snapshot_payload,
    sweep_liveness as sweep_liveness,
)

HEARTBEAT_INTERVAL_S = 2


def _gpu_visibility_env(backend: str, gpu_ids: list[int] | None) -> dict[str, str]:
    if not gpu_ids:
        return {}
    csv = ",".join(str(i) for i in gpu_ids)
    if backend == "cuda":
        return {"CUDA_VISIBLE_DEVICES": csv}
    if backend == "vulkan":
        return {"GGML_VK_VISIBLE_DEVICES": csv}
    if backend == "rocm":
        return {
            "HIP_VISIBLE_DEVICES": csv,
            "ROCR_VISIBLE_DEVICES": csv,
        }
    return {}


def _exc_text(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, str):
            return detail
        return str(detail)
    return str(exc)


def _settings_body(last_settings: dict[str, Any]) -> RunBody:
    data = dict(last_settings)
    data.pop("tensor_split", None)
    return RunBody.model_validate(data)


def _does_not_fit_message(plan: dict[str, Any] | None, extra: str | None = None) -> str:
    hint = ""
    if plan:
        hint = str(plan.get("hint") or plan.get("warning") or "")
    if hint and "does not fit" in hint.lower():
        return hint
    if hint:
        return f"does not fit remaining cluster capacity: {hint}"
    if extra:
        return f"does not fit remaining cluster capacity: {extra}"
    return "does not fit remaining cluster capacity"


def _stop_inference(state: ClusterState) -> None:
    state.active_config = None
    if state.process_pid:
        native.stop_process(state.process_pid, force=True)
        runtime.clear_children()
    load_progress.end()
    state.process_pid = None
    state.process_role = None


def _apply_serving(state: ClusterState, body: RunBody, plan_result: dict[str, Any]) -> None:
    path = model_ops.resolve_local_model(body.model)
    rpc_servers = list(plan_result.get("rpc_endpoints") or [])
    ensure_rpc_peers_reachable(rpc_servers)
    root = repo_root()
    if state.process_pid:
        native.stop_process(state.process_pid, force=True)
        runtime.clear_children()
        load_progress.end()
    gpu_env = _gpu_visibility_env(
        (state.members.get(state.node_id) or {}).get("backend") or "cpu",
        state.gpu_ids,
    )
    extra = sanitize_extra_args(body.extra_args) if body.extra_args else []
    launch_cfg = build_master_launch(
        body,
        plan_result,
        model_path=path,
        rpc_servers=rpc_servers,
        infer_port=state.infer_port,
        repo_root=str(root) if root else None,
        gpu_env=gpu_env,
        extra_args=extra,
    )
    launched = native.start_master(**launch_cfg)
    from kllm.modules.inference.interactors.effective_config import effective_configuration
    from uuid import uuid4

    state.active_config = effective_configuration(body, plan_result, path)
    state.active_config.update({"process_pid": launched.get("pid"), "run_id": str(uuid4())})
    state.process_pid = launched.get("pid")
    state.process_role = "master"
    state.model_name = path.name
    if launched.get("pid"):
        runtime.register_child(int(launched["pid"]), "llama-server")
        try:
            total_bytes = path.stat().st_size
        except OSError:
            total_bytes = 0
        load_progress.begin(path.name, int(launched["pid"]), total_bytes)


def on_membership_grow(state: ClusterState) -> None:
    """Replan after a worker joins. Clears stale shrink overflow when the cluster fits again."""
    if not state.last_settings:
        if state.apply_error or state.apply_warning:
            state.apply_error = None
            state.apply_warning = None
            state.save()
        return
    try:
        body = _settings_body(state.last_settings)
        plan = execute_plan(body, state.members)
    except Exception:
        state.save()
        return
    state.last_plan = plan
    fit = plan.get("fit_status")
    if fit == "overflow":
        state.apply_error = _does_not_fit_message(plan, None)
        state.apply_warning = None
    elif fit == "tight":
        state.apply_error = None
        state.apply_warning = plan.get("warning") or plan.get("hint")
    else:
        state.apply_error = None
        state.apply_warning = None
    state.save()


def on_membership_shrink(
    state: ClusterState,
    *,
    apply_serving: Callable[[ClusterState, RunBody, dict[str, Any]], None] = _apply_serving,
    stop_serving: Callable[[ClusterState], None] = _stop_inference,
) -> None:
    """Replan (and maybe apply) after Leave / unregister / liveness drop.

    Does not change RPC bind, join key, or cluster role.
    Does not auto-shrink RUN SETTINGS.
    """
    state.save()
    if not state.last_settings:
        return

    was_running = bool(state.process_pid and state.process_role == "master")
    plan: dict[str, Any] | None = None
    plan_error: str | None = None
    try:
        body = _settings_body(state.last_settings)
        plan = execute_plan(body, state.members)
    except Exception as exc:
        plan_error = _exc_text(exc)
        plan = dict(state.last_plan or {})
        for key in (
            "device_allocations",
            "device_order",
            "rpc_endpoints",
            "node_alloc_bytes",
            "tensor_split",
            "tensor_split_csv",
        ):
            plan.pop(key, None)
        plan["fit_status"] = "overflow"
        plan["hint"] = plan_error
        plan["node_ids"] = [
            m.get("node_id") for m in ordered_members(state.members) if m.get("node_id")
        ]

    if plan is not None:
        state.last_plan = plan

    fit = (plan or {}).get("fit_status")
    if plan_error:
        fit = "overflow"

    if not was_running:
        if fit == "overflow":
            state.apply_error = _does_not_fit_message(plan, plan_error)
            state.apply_warning = None
        elif fit == "tight":
            state.apply_error = None
            state.apply_warning = (plan or {}).get("warning") or (plan or {}).get("hint")
        else:
            state.apply_error = None
            state.apply_warning = None
        state.save()
        return

    if fit == "fits":
        try:
            apply_serving(state, _settings_body(state.last_settings), plan or {})
            state.apply_error = None
            state.apply_warning = None
        except Exception as exc:
            stop_serving(state)
            state.apply_error = _does_not_fit_message(plan, _exc_text(exc))
            state.apply_warning = None
        state.save()
        return

    if fit == "tight":
        try:
            apply_serving(state, _settings_body(state.last_settings), plan or {})
            state.apply_error = None
            state.apply_warning = (
                (plan or {}).get("warning") or (plan or {}).get("hint") or "tight fit"
            )
        except Exception as exc:
            stop_serving(state)
            state.apply_error = _does_not_fit_message(plan, _exc_text(exc))
            state.apply_warning = None
        state.save()
        return

    # overflow or planner error: do not apply; stop infer; cluster stays up
    stop_serving(state)
    state.apply_error = _does_not_fit_message(plan, plan_error)
    state.apply_warning = None
    state.save()
