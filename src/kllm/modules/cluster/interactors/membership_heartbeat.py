"""Worker heartbeat delivery and master-to-worker latency probes."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from kllm.shared.state import DEFAULT_CONTROL_PORT, ClusterState

_LATENCY_EMA_ALPHA = 0.3
# Heartbeats are every 2s. One lost packet must not flip the dashboard warning
# or rewrite cluster state; the second consecutive miss still lands well inside
# the 20s membership timeout.
_UNREACHABLE_AFTER = 2


class MembershipHeartbeatService:
    """Deliver worker heartbeats and refresh master-side control-plane RTTs."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        ensure_control_route: Callable[[str, int], list[str]],
        control_base: Callable[[], str],
        heartbeat_payload: Callable[[], dict[str, Any]],
        control_client: Callable[[], Any],
        mark_unreachable: Callable[[ClusterState], None],
        clear_unreachable: Callable[[ClusterState], None],
        worker_local_leave: Callable[[], None],
        apply_snapshot: Callable[[dict[str, Any]], None],
        member_rpc_host: Callable[[dict[str, Any]], str],
    ) -> None:
        self._state = state
        self._ensure_control_route = ensure_control_route
        self._control_base = control_base
        self._heartbeat_payload = heartbeat_payload
        self._control_client = control_client
        self._mark_unreachable = mark_unreachable
        self._clear_unreachable = clear_unreachable
        self._worker_local_leave = worker_local_leave
        self._apply_snapshot = apply_snapshot
        self._member_rpc_host = member_rpc_host
        self._contact_failures = 0
        self._observed_state: ClusterState | None = None

    def _bind_state(self, state: ClusterState) -> None:
        """A replaced cluster state starts its own failure streak."""
        if state is not self._observed_state:
            self._observed_state = state
            self._contact_failures = 0

    def _note_contact_failure(self, state: ClusterState) -> None:
        self._contact_failures += 1
        if self._contact_failures >= _UNREACHABLE_AFTER:
            self._mark_unreachable(state)

    def _note_contact_success(self, state: ClusterState) -> None:
        self._contact_failures = 0
        self._clear_unreachable(state)

    def post_heartbeat(self) -> None:
        state = self._state()
        if state.role != "worker" or not state.master_host or not state.join_key:
            self._contact_failures = 0
            return
        self._bind_state(state)
        try:
            self._ensure_control_route(state.master_host, state.master_port)
        except HTTPException:
            self._note_contact_failure(state)
            return
        try:
            response = self._control_client().post(
                f"{self._control_base()}/v2/cluster/nodes/heartbeat",
                json=self._heartbeat_payload(),
                headers={"X-Kllm-Key": state.join_key},
                timeout=5.0,
            )
            if response.status_code != 200:
                self._note_contact_failure(state)
                return
            data = response.json()
        except Exception:
            self._note_contact_failure(state)
            return

        self._note_contact_success(state)
        snapshot = data.get("cluster_members")
        accepted = data.get("accepted")
        omitted = isinstance(snapshot, dict) and state.node_id not in snapshot
        if accepted is False or omitted:
            self._worker_local_leave()
            return
        self._apply_snapshot(data)
        state.save()

    def probe_latencies(self) -> None:
        """Measure worker health endpoint RTTs and update their EMA in place."""
        state = self._state()
        if state.role != "master":
            return
        master_id = state.node_id
        if master_id in state.members:
            state.members[master_id]["latency_ms"] = 0.0
        for node_id, member in list(state.members.items()):
            if node_id == master_id or (member or {}).get("role") == "master":
                continue
            raw = member or {}
            if not raw.get("host") and not raw.get("lan_host"):
                continue
            host = raw.get("lan_host") or self._member_rpc_host(raw)
            port = int(raw.get("control_port") or DEFAULT_CONTROL_PORT)
            try:
                started = time.perf_counter()
                response = self._control_client().get(f"http://{host}:{port}/health", timeout=1.0)
                if response.status_code != 200:
                    continue
                sample_ms = (time.perf_counter() - started) * 1000.0
            except Exception:
                continue
            previous = raw.get("latency_ms")
            smoothed = (
                sample_ms
                if previous is None
                else _LATENCY_EMA_ALPHA * sample_ms + (1.0 - _LATENCY_EMA_ALPHA) * float(previous)
            )
            state.members[node_id]["latency_ms"] = smoothed
