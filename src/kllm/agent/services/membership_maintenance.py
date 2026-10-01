"""Asynchronous master-side cleanup after worker liveness loss."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable

from kllm import membership, usage
from kllm.state import ClusterState

ClusterIdentity = tuple[str, str | None]
PendingShrink = tuple[bool, ClusterIdentity, ClusterState]
PendingKey = tuple[int, str]


class MembershipMaintenance:
    """Sweep stale members and serialize durable cleanup and shrink replanning."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        store: Callable[[], usage.UsageStore],
        *,
        shrink_cluster: Callable[[ClusterState], None],
    ) -> None:
        self._state = state
        self._store = store
        self._shrink_cluster = shrink_cluster
        self._sweep_lock = threading.Lock()
        self._maintenance_lock = threading.Lock()
        self._pending: dict[PendingKey, PendingShrink] = {}
        self._inflight = False

    def synchronize(self, *, shrink: bool = True) -> list[str]:
        """Sweep stale workers and queue identity-scoped maintenance work."""
        state = self._state()
        if state.role != "master":
            return []
        with self._sweep_lock:
            dropped = membership.sweep_liveness(state)
        if not dropped:
            return dropped

        with self._maintenance_lock:
            identity = (state.node_id, state.join_key)
            for node_id in dropped:
                key = (id(state), node_id)
                previous = self._pending.get(key)
                self._pending[key] = (
                    shrink or bool(previous and previous[0] and previous[1] == identity),
                    identity,
                    state,
                )
            if self._inflight:
                return dropped
            self._inflight = True
        threading.Thread(
            target=self._maintain,
            name="kllm-membership-maintenance",
            daemon=True,
        ).start()
        return dropped

    def _maintain(self) -> None:
        try:
            while True:
                with self._maintenance_lock:
                    pending = dict(self._pending)
                    self._pending.clear()
                    if not pending:
                        return
                try:
                    state = self._state()
                    identity = (state.node_id, state.join_key)
                    if state.role != "master":
                        continue
                    removed = [
                        node_id
                        for (state_id, node_id), (_, expected, expected_state) in pending.items()
                        if state_id == id(state)
                        and expected_state is state
                        and expected == identity
                        and node_id not in state.members
                    ]
                    if not removed:
                        continue
                    try:
                        store = self._store()
                        for node_id in removed:
                            current = self._state()
                            if (
                                current is not state
                                or current.role != "master"
                                or (current.node_id, current.join_key) != identity
                            ):
                                break
                            if node_id not in current.members:
                                store.end_node(node_id, state="unobserved")
                    except (OSError, sqlite3.Error) as exc:
                        self._mark_store_failed(str(exc))
                    current = self._state()
                    if (
                        current is not state
                        or current.role != "master"
                        or (current.node_id, current.join_key) != identity
                    ):
                        continue
                    current.save()
                    if any(
                        pending[(id(state), node_id)][0]
                        and node_id not in current.members
                        for node_id in removed
                    ):
                        self._shrink_cluster(current)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "membership maintenance failed (%s)", type(exc).__name__
                    )
        finally:
            with self._maintenance_lock:
                restart = bool(self._pending)
                self._inflight = restart
            if restart:
                threading.Thread(
                    target=self._maintain,
                    name="kllm-membership-maintenance",
                    daemon=True,
                ).start()

    def _mark_store_failed(self, error: str) -> None:
        try:
            store = self._store()
            store.recording = "failed"
            reason = "disk full" if "disk full" in error.lower() else "storage unavailable"
            store.error = f"usage lifetime close failed: {reason}"
        except (OSError, sqlite3.Error):
            pass
