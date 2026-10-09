"""HTTP handlers for usage-history reads and worker proxying."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException, Request

from kllm import usage
from kllm.exceptions import ExternalServiceError
from kllm.agent.services.usage_overview import UsageOverviewService
from kllm.state import ClusterState
from kllm.exceptions import PersistenceError
from kllm.persistence.operations import initialize


UsageWindow = Literal["15m", "1h", "24h"]
UsageParams = dict[str, str | int]


class UsageLiveHandlers:
    """Serve the live usage view locally or proxy the master view from a worker."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        proxy: Callable[[str, UsageParams | None], dict[str, Any]],
        overview: UsageOverviewService,
        store: Callable[[], usage.UsageStore],
        sync_liveness: Callable[..., list[str]],
        resource_schema_supported: Callable[[], bool],
        worker_error: Callable[[], str | None],
        last_save: Callable[[], str | None],
        set_last_save: Callable[[str | None], None],
        set_last_success_at: Callable[[str], None],
        utc_now: Callable[[], str],
    ) -> None:
        self._state = state
        self._proxy = proxy
        self._overview = overview
        self._store = store
        self._sync_liveness = sync_liveness
        self._resource_schema_supported = resource_schema_supported
        self._worker_error = worker_error
        self._last_save = last_save
        self._set_last_save = set_last_save
        self._set_last_success_at = set_last_success_at
        self._utc_now = utc_now

    def live(self) -> dict[str, Any]:
        state = self._state()
        if state.role == "worker":
            try:
                data = self._proxy("/v2/telemetry", None)
            except HTTPException as exc:
                if exc.status_code >= 500:
                    raise HTTPException(
                        503,
                        {
                            "message": "master usage history unavailable; check private control connection",
                            "recording": "interrupted",
                            "error": self._worker_error() or "master unavailable",
                            "last_successful_save": self._last_save(),
                        },
                    ) from None
                raise
            recording = data.get("recording", {})
            self._set_last_save(recording.get("last_successful_save") or self._last_save())
            self._set_last_success_at(self._utc_now())
            error = self._worker_error()
            if error:
                if state.node_id in data.get("nodes", {}):
                    data["nodes"][state.node_id]["error"] = error
                data["recording"] = {
                    **data["recording"],
                    "recording": "interrupted",
                    "error": error,
                }
            return data

        self._sync_liveness(shrink=True)
        try:
            return self._overview.build(
                state,
                self._store,
                resource_schema_supported=self._resource_schema_supported(),
            )
        except ExternalServiceError as exc:
            raise HTTPException(503, str(exc)) from exc


class UsageHistoryProxy:
    """Fetch authenticated history from the master and sanitize upstream errors."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        master_control_base: Callable[[], str],
        control_client: Callable[[], Any],
    ) -> None:
        self._state = state
        self._master_control_base = master_control_base
        self._control_client = control_client

    def get(self, path: str, params: UsageParams | None = None) -> dict[str, Any]:
        state = self._state()
        if not state.master_host or not state.join_key:
            raise HTTPException(503, "master usage history unavailable")
        try:
            response = self._control_client().get(
                f"{self._master_control_base()}{path}",
                params=params,
                headers={"X-Kllm-Key": state.join_key},
                timeout=5.0,
            )
            if response.status_code != 200:
                status = (
                    response.status_code
                    if response.status_code in (400, 401, 403, 404, 422)
                    else 503
                )
                raise HTTPException(
                    status,
                    "master usage access denied"
                    if status in (401, 403)
                    else "master usage history unavailable",
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise HTTPException(503, "master usage history unavailable")
            return payload
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "master usage history unavailable") from None


class UsageHistoryHandlers:
    """Serve retained history from the master store or proxy it from a worker."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        store: Callable[[], usage.UsageStore],
        proxy: Callable[[str, UsageParams | None], dict[str, Any]],
    ) -> None:
        self._state = state
        self._store = store
        self._proxy = proxy

    def lifetimes(self, node_id: str) -> dict[str, Any]:
        state = self._state()
        if state.role == "worker":
            return self._proxy("/v2/telemetry/lifetimes", {"node_id": node_id})

        def query() -> dict[str, Any]:
            store = self._store()
            with store.snapshot():
                known = store.known_nodes()
                member = state.members.get(node_id)
                if node_id not in known and not member:
                    raise LookupError("unknown node")
                meta = member or known[node_id]
                return {
                    "node_id": node_id,
                    "host": meta.get("host") or node_id,
                    "connected": member is not None,
                    "lifetimes": store.lifetimes(node_id),
                    "retained_node_ids": sorted(known),
                    **store.coverage(node_id),
                }

        return self._read(query)

    def series(self, node_id: str, lifetime_id: str, window: UsageWindow) -> dict[str, Any]:
        if self._state().role == "worker":
            return self._proxy(
                "/v2/telemetry/series",
                {"node_id": node_id, "lifetime_id": lifetime_id, "window": window},
            )
        return self._read(lambda: self._store().series(node_id, lifetime_id, window))

    def readings(
        self,
        node_id: str,
        lifetime_id: str,
        window: UsageWindow,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        params: UsageParams = {
            "node_id": node_id,
            "lifetime_id": lifetime_id,
            "window": window,
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor
        if self._state().role == "worker":
            return self._proxy("/v2/telemetry/readings", params)
        return self._read(
            lambda: self._store().readings(node_id, lifetime_id, window, limit=limit, cursor=cursor)
        )

    @staticmethod
    def _read(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return action()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (OSError, sqlite3.Error, PersistenceError):
            raise HTTPException(
                503, "usage history unavailable; previously saved records preserved"
            ) from None


class UsageIngestHandlers:
    """Validate and persist worker usage batches at the HTTP boundary."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        resources: usage.ResourceState,
        store: Callable[[], usage.UsageStore],
        assignment: Callable[[str], str | None],
        node_context: Callable[[dict[str, Any], str | None], dict[str, Any]],
        resource_validation_available: Callable[[], bool],
    ) -> None:
        self._state = state
        self._resources = resources
        self._store = store
        self._assignment = assignment
        self._node_context = node_context
        self._resource_validation_available = resource_validation_available

    async def ingest(self, request: Request) -> dict[str, Any]:
        state = self._state()
        if state.role != "master":
            raise HTTPException(400, "usage ingest requires master role")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                raise HTTPException(400, "usage batch exceeds 64 KiB")
        try:
            body = usage.usage_probe.decode(bytes(raw), framed=False)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HTTPException(400, "invalid usage JSON") from None
        if not isinstance(body, dict) or set(body) not in (
            {"node_id", "readings"},
            {"node_id", "readings", "resources"},
        ):
            raise HTTPException(
                400, "usage batch requires node_id, readings and optional resources"
            )
        node_id = body["node_id"]
        member = state.members.get(node_id) if isinstance(node_id, str) else None
        if not member or node_id == state.node_id or member.get("role") != "worker":
            raise HTTPException(400, "unregistered worker node")
        points = body["readings"]
        snapshot = body.get("resources")
        if (
            not isinstance(points, list)
            or not (0 if snapshot is not None else 1) <= len(points) <= 8
        ):
            raise HTTPException(400, "usage batch must contain 1–8 readings")
        now = datetime.now(timezone.utc)
        try:
            if snapshot is not None:
                if not self._resource_validation_available():
                    raise ValueError("native resource validation unavailable")
                usage.validate_resources(node_id, snapshot, member, now)
            if points and (
                not member.get("process_pid")
                or not member.get("process_started_at")
                or member.get("process_role") != "worker"
            ):
                raise ValueError("worker has no registered managed process")
            for point in points:
                usage.validate(
                    node_id,
                    point,
                    now=now,
                    expected_pid=member["process_pid"],
                    expected_start=member["process_started_at"],
                    expected_role="worker",
                )
            model = self._assignment(node_id)
            if points:
                store = await initialize(self._store)

                def persist():
                    self._resources.prune(state.members)
                    # State locks never span checkout or SQL.
                    with store._store.writer():
                        with self._resources.lock:
                            if snapshot is not None:
                                self._resources.validate_session(node_id, snapshot)
                        accepted = store.record_many(
                            node_id,
                            member.get("host") or node_id,
                            points,
                            now=now,
                            expected_pid=member["process_pid"],
                            expected_start=member["process_started_at"],
                            expected_role="worker",
                            model=model,
                            context=self._node_context(member, model),
                        )
                        with self._resources.lock:
                            resources_accepted = (
                                self._resources.install(node_id, snapshot)
                                if snapshot is not None
                                else False
                            )
                        return accepted, resources_accepted, store.last_successful_save

                accepted, resources_accepted, last_save = await store._store.arun(persist)
            else:
                self._resources.prune(state.members)
                with self._resources.lock:
                    resources_accepted = (
                        self._resources.install(node_id, snapshot)
                        if snapshot is not None
                        else False
                    )
                accepted, last_save = 0, None
        except usage.ResourceConflict as exc:
            raise HTTPException(
                409, {"message": str(exc), "resource_generation": exc.generation}
            ) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (OSError, sqlite3.Error, PersistenceError):
            raise HTTPException(
                503, "usage recording unavailable; previous history retained"
            ) from None
        result = {
            "accepted": accepted,
            "duplicates": len(points) - accepted,
            "last_successful_save": last_save,
        }
        if snapshot is not None:
            result["resources_accepted"] = resources_accepted
        return result
