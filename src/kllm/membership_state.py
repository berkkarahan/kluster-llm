"""Membership snapshots, liveness, and worker reachability state transitions."""

from __future__ import annotations

import time
from typing import Any

from kllm.state import ClusterState


LIVENESS_TIMEOUT_S = 20

_SNAPSHOT_OPTIONAL = (
    "backend",
    "actual_backend",
    "requested_backend",
    "devices",
    "rpc_devices",
    "rpc_capabilities",
    "rpc_control_version",
    "rpc_status",
    "runtime_management_version",
    "rpc_error",
    "unified_memory",
    "binary_version",
    "reclaimable_ram_bytes",
    "gpu_ids",
    "total_ram_bytes",
    "available_ram_bytes",
    "vram_bytes",
    "control_port",
    "rpc_port",
    "hostname",
    "lan_host",
    "last_seen",
    "latency_ms",
    "process_pid",
    "process_role",
    "process_started_at",
    "process_start_token",
    "logging_ack",
)

_UNREACHABLE_WARNING = "master heartbeat unreachable"


def cluster_snapshot(members: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Build the wire-format node map used by masters and worker mirrors."""
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
    """Return whether the saved master plan matches its current membership."""
    plan = state.last_plan
    if not plan or state.role != "master" or not state.members:
        return False
    member_ids = set(state.members.keys())
    plan_ids = {str(node_id) for node_id in (plan.get("node_ids") or []) if node_id}
    return bool(member_ids) and plan_ids == member_ids


def public_last_plan(state: ClusterState) -> dict[str, Any] | None:
    """Return a plan only while the master role and membership still match it."""
    if not last_plan_matches_members(state):
        return None
    return state.last_plan


def reconcile_last_plan(state: ClusterState, *, persist: bool = True) -> bool:
    """Clear a persisted plan after role or membership changes; return if cleared."""
    if state.last_plan is None or last_plan_matches_members(state):
        return False
    state.last_plan = None
    if persist:
        state.save()
    return True


def snapshot_payload(
    state: ClusterState,
    inference_activity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return common fields included in register and heartbeat responses."""
    return {
        "cluster_members": cluster_snapshot(state.members),
        "last_plan": public_last_plan(state),
        "apply_error": state.apply_error,
        "apply_warning": state.apply_warning,
        "inference_activity": inference_activity or state.inference_activity,
        "logging": {"enabled": state.logging_enabled, "generation": state.logging_generation},
    }


def sweep_liveness(
    state: ClusterState,
    *,
    now: float | None = None,
    timeout_s: float = LIVENESS_TIMEOUT_S,
) -> list[str]:
    """Drop workers past their liveness timeout without dropping the master."""
    current_time = time.time() if now is None else now
    dropped: list[str] = []
    master_id = state.node_id if state.role == "master" else None
    for node_id, member in list(state.members.items()):
        if (master_id and node_id == master_id) or (member or {}).get("role") == "master":
            continue
        last_seen = float((member or {}).get("last_seen") or 0)
        if current_time - last_seen > timeout_s:
            dropped.append(node_id)
            del state.members[node_id]
    return dropped


def clear_membership_fields(state: ClusterState) -> None:
    """Clear cluster members and apply signals when returning to idle."""
    state.members.clear()
    state.apply_error = None
    state.apply_warning = None
    state.last_settings = None
    state.last_plan = None


def mark_master_unreachable(state: ClusterState) -> None:
    warnings = list(state.netsec_warnings or [])
    if _UNREACHABLE_WARNING in warnings:
        return
    warnings.append(_UNREACHABLE_WARNING)
    state.netsec_warnings = warnings
    state.save()


def clear_master_unreachable(state: ClusterState) -> None:
    warnings = [warning for warning in (state.netsec_warnings or []) if warning != _UNREACHABLE_WARNING]
    if warnings == list(state.netsec_warnings or []):
        return
    state.netsec_warnings = warnings
    state.save()
