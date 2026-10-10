"""Cluster logging policy and acknowledgements from independent output sinks."""

from __future__ import annotations

import json
import threading
import time
import uuid

import psutil

from kllm.modules.logs.interactors import cluster_log
from kllm.shared import process_registry as runtime
from kllm.shared.state import ClusterState, home_dir

_policy_lock = threading.RLock()


def local_status(state: ClusterState) -> dict:
    info = runtime.load()
    sources = [(child.log_sink_pid, child.pid) for child in info.children]
    if info.agent_pid:
        sources.append((info.agent_log_sink_pid, info.agent_pid))
    status = "applied"
    for sink_pid, producer_pid in sources:
        if not psutil.pid_exists(producer_pid):
            continue
        if not sink_pid:
            status = "unsupported"
            break
        try:
            ack = json.loads((home_dir() / ".log-sinks" / f"{sink_pid}.json").read_text())
            if not psutil.pid_exists(sink_pid) or ack.get("error"):
                status = "error"
                break
            if (
                ack.get("generation") != state.logging_generation
                or ack.get("enabled") != state.logging_enabled
            ):
                status = "pending"
        except (OSError, ValueError, AttributeError, TypeError):
            status = "pending" if psutil.pid_exists(sink_pid) else "error"
    return dict(
        enabled=state.logging_enabled,
        generation=state.logging_generation,
        status=status,
        supported=True,
    )


def policy(state: ClusterState) -> dict:
    return dict(enabled=state.logging_enabled, generation=state.logging_generation)


def apply(state: ClusterState, payload: dict, hub: cluster_log.LogHub = cluster_log.HUB) -> None:
    enabled, generation = payload.get("enabled"), payload.get("generation")
    if type(enabled) is not bool or not isinstance(generation, str) or len(generation) > 64:
        raise ValueError("invalid logging policy")
    # Ordered generations prevent a delayed heartbeat from undoing a newer PUT.
    with _policy_lock:
        if state.logging_generation and generation < state.logging_generation:
            return
        if state.logging_generation == generation and state.logging_enabled == enabled:
            hub.set_enabled(enabled)
            return
        state.logging_enabled, state.logging_generation = enabled, generation
        state.save()
        hub.set_enabled(enabled)


def change(state: ClusterState, enabled: bool) -> dict:
    with _policy_lock:
        previous = state.logging_generation.split("-", 1)[0]
        revision = max(time.time_ns(), int(previous) + 1 if previous.isdecimal() else 0)
        apply(state, dict(enabled=enabled, generation=f"{revision:020d}-{uuid.uuid4().hex}"))
        return policy(state)


def overview(state: ClusterState) -> dict:
    desired = policy(state)
    nodes = {state.node_id: local_status(state)}
    if state.role == "master":
        for node_id, member in state.members.items():
            if node_id == state.node_id:
                continue
            ack = member.get("logging_ack") or {}
            status = "unsupported" if not ack.get("supported") else "pending"
            if time.time() - float(member.get("last_seen") or 0) > 20:
                status = "unreachable"
            elif (
                ack.get("generation") == desired["generation"]
                and ack.get("enabled") == desired["enabled"]
            ):
                status = ack.get("status", "pending")
            nodes[node_id] = dict(status=status)
    return {
        **desired,
        "nodes": nodes,
        "status": "applied" if all(n["status"] == "applied" for n in nodes.values()) else "partial",
    }
