"""Master usage-overview assembly using persisted history and live resources."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from kllm import usage
from kllm.exceptions import ExternalServiceError
from kllm.state import ClusterState


class UsageOverviewService:
    """Combine usage history, cluster membership, and recent resource snapshots."""

    def __init__(
        self,
        resources: usage.ResourceState,
        assignment_for_node: Callable[[str], str | None],
    ) -> None:
        self._resources = resources
        self._assignment_for_node = assignment_for_node

    def build(
        self,
        state: ClusterState,
        store_provider: Callable[[], usage.UsageStore],
        *,
        resource_schema_supported: bool,
    ) -> dict[str, Any]:
        """Return the master overview, retaining live resources during DB outages."""
        try:
            store = store_provider()
            members = dict(state.members) if state.role == "master" else {}
            self._resources.prune(members)
            known = store.known_nodes()
            now = datetime.now(timezone.utc)
            nodes: dict[str, dict[str, Any]] = {}
            for node_id in members.keys() | known.keys():
                member = members.get(node_id)
                meta = member or known[node_id]
                point = store.latest(node_id)
                age = (
                    max(0, (now - usage.parse_utc(point["observed_at"])).total_seconds())
                    if point
                    else None
                )
                connected = member is not None
                current = bool(
                    member
                    and member.get("process_pid")
                    and member.get("process_started_at")
                    and point
                    and point["pid"] == member["process_pid"]
                    and usage.parse_utc(point["process_started_at"])
                    == usage.parse_utc(member["process_started_at"])
                )
                node: dict[str, Any] = {
                    "node_id": node_id,
                    "host": meta.get("host") or node_id,
                    "role": meta.get("role", "worker"),
                    "backend": meta.get("actual_backend") or meta.get("backend"),
                    "devices": meta.get("devices") or [],
                    "unified_memory": bool(meta.get("unified_memory")),
                    "total_ram_bytes": meta.get("total_ram_bytes"),
                    "vram_bytes": meta.get("vram_bytes"),
                    "assignment": self._assignment_for_node(node_id)
                    if member
                    else meta.get("assignment"),
                    "connected": connected,
                    "process_state": "running"
                    if current
                    else "no managed process"
                    if connected
                    else "unobserved",
                    "reading": point if current or not connected else None,
                    "age_seconds": round(age, 1)
                    if age is not None and point and (current or not connected)
                    else None,
                    "freshness": "fresh"
                    if connected and current and age is not None and age < 10
                    else "stale"
                    if point and (current or not connected)
                    else "unavailable",
                }
                self._attach_resources(node_id, node, connected, now)
                if node["resources"] and member and member.get("process_pid"):
                    verified = any(
                        process["pid"] == member["process_pid"]
                        and process["identity_verified"]
                        and process["state"] == "running"
                        for process in node["resources"]["processes"]
                    )
                    node["process_state"] = "running" if verified else "unavailable"
                nodes[node_id] = node
            relationships = [
                {"from_node_id": state.node_id, "to_node_id": node_id, "kind": "master-worker"}
                for node_id, member in members.items()
                if node_id != state.node_id and member.get("role") == "worker"
            ]
            recording = store.coverage()
            if state.role != "master" and recording["recording"] != "failed":
                recording["recording"] = "interrupted"
            return {
                "nodes": nodes,
                "relationships": relationships,
                "recording": recording,
                "resource_schema_versions": [1] if resource_schema_supported else [],
            }
        except (OSError, sqlite3.Error) as exc:
            with self._resources.lock:
                retained = any(node_id in state.members for node_id in self._resources.latest)
            if not retained:
                raise ExternalServiceError(
                    "usage history unavailable; previously saved records preserved"
                ) from exc
            now = datetime.now(timezone.utc)
            nodes = {
                node_id: self._attach_resources(
                    node_id,
                    {
                        "node_id": node_id,
                        "host": member.get("host") or node_id,
                        "role": member.get("role", "worker"),
                        "connected": True,
                        "reading": None,
                        "freshness": "unavailable",
                        "process_state": "unavailable"
                        if member.get("process_pid")
                        else "no managed process",
                    },
                    True,
                    now,
                )
                for node_id, member in state.members.items()
            }
            return {
                "nodes": nodes,
                "relationships": [],
                "resource_schema_versions": [1],
                "recording": {
                    "recording": "failed",
                    "error": "usage history unavailable; live resources retained",
                    "last_successful_save": None,
                },
            }

    def _attach_resources(
        self,
        node_id: str,
        node: dict[str, Any],
        connected: bool,
        now: datetime,
    ) -> dict[str, Any]:
        with self._resources.lock:
            snapshot = self._resources.latest.get(node_id)
            received = self._resources.received.get(node_id, time.monotonic())
        residence = max(0.0, time.monotonic() - received)
        age, freshness = usage.resource_freshness(snapshot, connected, now, residence)
        node.update(resources=snapshot, resource_age_seconds=age, resource_freshness=freshness)
        return node
