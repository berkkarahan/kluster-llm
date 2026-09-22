"""Cluster membership snapshot, liveness sweep, and shrink replan."""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException

from kllm import models as model_ops
from kllm import native, runtime
from kllm.extra_args import sanitize_extra_args
from kllm.run_planning import (
    build_master_launch,
    ensure_rpc_peers_reachable,
    execute_plan,
    ordered_members,
    rpc_server_endpoints,
)
from kllm.run_settings import RunBody
from kllm.state import ClusterState, repo_root

HEARTBEAT_INTERVAL_S = 2
LIVENESS_TIMEOUT_S = 20

_SNAPSHOT_OPTIONAL = (
    "backend",
    "actual_backend", "requested_backend", "devices", "unified_memory", "binary_version", "reclaimable_ram_bytes",
    "gpu_ids",
    "total_ram_bytes",
    "available_ram_bytes",
    "vram_bytes",
    "control_port",
    "rpc_port",
    "lan_host",
    "last_seen",
    "latency_ms",
)

_UNREACHABLE_WARNING = "master heartbeat unreachable"


def cluster_snapshot(members: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Build the wire-format node_id → Node map (master authority; workers mirror)."""
    out: dict[str, dict[str, Any]] = {}
    for nid, raw in (members or {}).items():
        src = dict(raw or {})
        node_id = str(src.get("node_id") or nid)
        role = src.get("role") or "worker"
        if role not in ("master", "worker"):
            role = "worker"
        node: dict[str, Any] = {
            "node_id": node_id,
            "host": str(src.get("host") or ""),
            "role": role,
        }
        for key in _SNAPSHOT_OPTIONAL:
            if key in src:
                node[key] = src[key]
        out[node_id] = node
    return out


def last_plan_matches_members(state: ClusterState) -> bool:
    """True when last_plan node_ids exactly match current cluster members."""
    plan = state.last_plan
    if not plan:
        return False
    if state.role != "master" or not state.members:
        return False
    member_ids = set(state.members.keys())
    plan_ids = {str(n) for n in (plan.get("node_ids") or []) if n}
    return bool(member_ids) and plan_ids == member_ids


def public_last_plan(state: ClusterState) -> dict[str, Any] | None:
    """Plan snapshot safe to expose on status / worker mirror (no stale multi-node plans)."""
    if not last_plan_matches_members(state):
        return None
    return state.last_plan


def reconcile_last_plan(state: ClusterState, *, persist: bool = True) -> bool:
    """Drop persisted last_plan when role or membership no longer matches. Returns True if cleared."""
    if state.last_plan is None:
        return False
    if last_plan_matches_members(state):
        return False
    state.last_plan = None
    if persist:
        state.save()
    return True


def snapshot_payload(state: ClusterState) -> dict[str, Any]:
    """Fields shared by register/heartbeat success bodies (plus register's member count)."""
    return {
        "cluster_members": cluster_snapshot(state.members),
        "last_plan": public_last_plan(state),
        "apply_error": state.apply_error,
        "apply_warning": state.apply_warning,
    }


def sweep_liveness(
    state: ClusterState,
    *,
    now: float | None = None,
    timeout_s: float = LIVENESS_TIMEOUT_S,
) -> list[str]:
    """Drop workers whose last_seen is older than timeout. Never drops the master node."""
    now = time.time() if now is None else now
    dropped: list[str] = []
    master_id = state.node_id if state.role == "master" else None
    for nid, member in list(state.members.items()):
        if master_id and nid == master_id:
            continue
        if (member or {}).get("role") == "master":
            continue
        last_seen = float((member or {}).get("last_seen") or 0)
        if now - last_seen > timeout_s:
            dropped.append(nid)
            del state.members[nid]
    return dropped


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
    if state.process_pid:
        native.stop_process(state.process_pid, force=True)
        runtime.clear_children()
    state.process_pid = None
    state.process_role = None


def _apply_serving(state: ClusterState, body: RunBody, plan_result: dict[str, Any]) -> None:
    path = model_ops.resolve_local_model(body.model)
    rpc_servers = plan_result.get("rpc_endpoints", rpc_server_endpoints(state.members))
    ensure_rpc_peers_reachable(rpc_servers)
    root = repo_root()
    if state.process_pid:
        native.stop_process(state.process_pid, force=True)
        runtime.clear_children()
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
    state.process_pid = launched.get("pid")
    state.process_role = "master"
    state.model_name = path.name
    if launched.get("pid"):
        runtime.register_child(int(launched["pid"]), "llama-server")


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


def on_membership_shrink(state: ClusterState) -> None:
    """Replan (and maybe apply) after Leave / unregister / liveness drop.

    Does not change RPC bind, overlay, join key, or cluster role.
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
            _apply_serving(state, _settings_body(state.last_settings), plan or {})
            state.apply_error = None
            state.apply_warning = None
        except Exception as exc:
            _stop_inference(state)
            state.apply_error = _does_not_fit_message(plan, _exc_text(exc))
            state.apply_warning = None
        state.save()
        return

    if fit == "tight":
        try:
            _apply_serving(state, _settings_body(state.last_settings), plan or {})
            state.apply_error = None
            state.apply_warning = (plan or {}).get("warning") or (plan or {}).get("hint") or "tight fit"
        except Exception as exc:
            _stop_inference(state)
            state.apply_error = _does_not_fit_message(plan, _exc_text(exc))
            state.apply_warning = None
        state.save()
        return

    # overflow or planner error: do not apply; stop infer; cluster stays up
    _stop_inference(state)
    state.apply_error = _does_not_fit_message(plan, plan_error)
    state.apply_warning = None
    state.save()


def clear_membership_fields(state: ClusterState) -> None:
    """Idle after Stop/Leave: empty members and cleared apply signals."""
    state.members.clear()
    state.apply_error = None
    state.apply_warning = None
    state.last_settings = None
    state.last_plan = None


def mark_master_unreachable(state: ClusterState) -> None:
    warns = list(state.netsec_warnings or [])
    if _UNREACHABLE_WARNING not in warns:
        warns.append(_UNREACHABLE_WARNING)
        state.netsec_warnings = warns
        state.save()


def clear_master_unreachable(state: ClusterState) -> None:
    warns = [w for w in (state.netsec_warnings or []) if w != _UNREACHABLE_WARNING]
    if warns != list(state.netsec_warnings or []):
        state.netsec_warnings = warns
        state.save()
