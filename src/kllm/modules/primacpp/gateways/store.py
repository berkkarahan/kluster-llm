"""SQLite-backed gateway for Prima Prepare & Profile runs, stages, and measurements."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kllm.modules.primacpp.gateways import schema as tables
from kllm.shared.persistence import SQLiteOptions, SQLiteStore

_open_stores: dict[Path, "ProfilingStore"] = {}
_open_stores_lock = threading.RLock()


def _upsert(table, values: dict[str, Any], conflict_keys: list[str], update_fields: list[str]):
    statement = sqlite_insert(table).values(values)
    return statement.on_conflict_do_update(
        index_elements=conflict_keys,
        set_={name: statement.excluded[name] for name in update_fields},
    )


class ProfilingStore:
    """Authoritative SQLite store for profiling runs, stages, node tasks, and measured profiles."""

    def __init__(self, home_or_path: Path, *, options: SQLiteOptions | None = None):
        target = Path(home_or_path).absolute()
        if target.is_dir() or not target.name.endswith(".sqlite"):
            self.path = (target / "prima-profiles.sqlite").resolve()
        else:
            self.path = target.resolve()

        self.lock = threading.RLock()
        self._store = SQLiteStore(self.path, tables.schema, profile="network", options=options)
        with _open_stores_lock:
            _open_stores[self._store.path] = self

    @classmethod
    def active_for(cls, home_or_path: Path) -> "ProfilingStore | None":
        target = Path(home_or_path).absolute()
        if target.is_dir() or not target.name.endswith(".sqlite"):
            target = (target / "prima-profiles.sqlite").resolve()
        else:
            target = target.resolve()
        with _open_stores_lock:
            store = _open_stores.get(target)
            if store is not None and store._store.state == "OPEN" and store._store.pid == os.getpid():
                return store
            return None

    def read(self):
        return self._store.connection(write=False)

    def write(self):
        return self._store.connection(write=True)

    def close(self) -> None:
        try:
            self._store.close()
        finally:
            with _open_stores_lock:
                if _open_stores.get(self._store.path) is self:
                    _open_stores.pop(self._store.path, None)

    async def arun(self, callback, *args, cancellable=True, **kwargs):
        return await self._store.arun(callback, *args, cancellable=cancellable, **kwargs)

    async def aclose(self, *, deadline: float | None = None):
        try:
            await self._store.aclose(deadline=deadline)
        finally:
            with _open_stores_lock:
                if _open_stores.get(self._store.path) is self:
                    _open_stores.pop(self._store.path, None)

    # -------------------------------------------------------------------------
    # Run Lifecycle
    # -------------------------------------------------------------------------

    def create_run(
        self,
        run_data: dict[str, Any],
        stages_data: list[dict[str, Any]],
        nodes_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically initialize a profiling run with its sequential stages and participating nodes."""
        with self.write() as conn:
            # Insert run
            run_row = {
                "run_id": run_data["run_id"],
                "request_id": run_data["request_id"],
                "request_digest": run_data["request_digest"],
                "runtime_id": run_data["runtime_id"],
                "model_path": run_data.get("model_path", ""),
                "model_sha256": run_data.get("model_sha256", ""),
                "network_id": run_data.get("network_id"),
                "status": run_data.get("status", "queued"),
                "current_stage": run_data.get("current_stage", "materialize"),
                "include_profile": bool(run_data.get("include_profile", False)),
                "plan_digest": run_data["plan_digest"],
                "manifest_digest": run_data.get("manifest_digest"),
                "command_owner": json.dumps(run_data.get("command_owner") or {}),
                "settings_json": json.dumps(run_data.get("settings") or {}),
                "manifest_json": json.dumps(run_data.get("manifest") or {}) if run_data.get("manifest") else None,
                "created_at": run_data.get("created_at", time.time()),
                "started_at": run_data.get("started_at"),
                "updated_at": run_data.get("updated_at", time.time()),
                "finished_at": run_data.get("finished_at"),
                "expires_at": run_data.get("expires_at"),
                "error_message": run_data.get("error_message"),
                "summary_json": json.dumps(run_data.get("summary") or {}),
            }
            conn.execute(tables.profiling_runs.insert().values(run_row))

            # Insert stages
            for s in stages_data:
                stage_row = {
                    "id": f"{run_data['run_id']}:{s['stage_name']}",
                    "run_id": run_data["run_id"],
                    "stage_name": s["stage_name"],
                    "stage_order": int(s["stage_order"]),
                    "status": s.get("status", "pending"),
                    "attempt": int(s.get("attempt", 1)),
                    "max_attempts": int(s.get("max_attempts", 3)),
                    "progress_fraction": float(s.get("progress_fraction", 0.0)),
                    "progress_label": str(s.get("progress_label", "")),
                    "bytes_done": int(s.get("bytes_done", 0)),
                    "bytes_total": int(s.get("bytes_total", 0)),
                    "started_at": s.get("started_at"),
                    "updated_at": s.get("updated_at", time.time()),
                    "finished_at": s.get("finished_at"),
                    "error_message": s.get("error_message"),
                }
                conn.execute(tables.profiling_stages.insert().values(stage_row))

            # Insert nodes
            for n in nodes_data:
                node_row = {
                    "id": f"{run_data['run_id']}:{n['node_id']}",
                    "run_id": run_data["run_id"],
                    "node_id": n["node_id"],
                    "role": n.get("role", "worker"),
                    "rank_index": int(n.get("rank_index", 0)),
                    "assigned_address": str(n.get("assigned_address", "127.0.0.1")),
                    "backend": str(n.get("backend", "cpu")),
                    "devices_json": json.dumps(n.get("devices") or []),
                    "status": n.get("status", "pending"),
                    "remote_job_id": n.get("remote_job_id"),
                    "updated_at": n.get("updated_at", time.time()),
                }
                conn.execute(tables.profiling_nodes.insert().values(node_row))

        return self.get_run(run_data["run_id"])

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Fetch full run record with parsed JSON fields."""
        with self.read() as conn:
            stmt = select(tables.profiling_runs).where(tables.profiling_runs.c.run_id == run_id)
            row = conn.execute(stmt).mappings().first()
            if not row:
                return None
            return self._format_run_row(dict(row))

    def get_run_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        """Fetch run record by client request idempotency token."""
        with self.read() as conn:
            stmt = select(tables.profiling_runs).where(tables.profiling_runs.c.request_id == request_id)
            row = conn.execute(stmt).mappings().first()
            if not row:
                return None
            return self._format_run_row(dict(row))

    def get_active_run_for_model(self, model_sha256: str, runtime_id: str) -> dict[str, Any] | None:
        """Fetch any running or queued run targeting this model and runtime."""
        with self.read() as conn:
            stmt = (
                select(tables.profiling_runs)
                .where(
                    tables.profiling_runs.c.model_sha256 == model_sha256,
                    tables.profiling_runs.c.runtime_id == runtime_id,
                    tables.profiling_runs.c.status.in_(["queued", "running"]),
                )
                .order_by(tables.profiling_runs.c.created_at.desc())
            )
            row = conn.execute(stmt).mappings().first()
            if not row:
                return None
            return self._format_run_row(dict(row))

    def list_runs(self, limit: int = 20, offset: int = 0, status: str | None = None) -> list[dict[str, Any]]:
        """List runs paginated, newest first."""
        with self.read() as conn:
            stmt = select(tables.profiling_runs)
            if status:
                stmt = stmt.where(tables.profiling_runs.c.status == status)
            stmt = stmt.order_by(tables.profiling_runs.c.created_at.desc()).limit(limit).offset(offset)
            rows = conn.execute(stmt).mappings().all()
            return [self._format_run_row(dict(r)) for r in rows]

    def update_run(self, run_id: str, **fields: Any) -> None:
        """Update fields on a profiling run."""
        with self.write() as conn:
            values = dict(fields)
            values["updated_at"] = values.get("updated_at", time.time())
            if "settings" in values:
                values["settings_json"] = json.dumps(values.pop("settings"))
            if "manifest" in values:
                values["manifest_json"] = json.dumps(values.pop("manifest"))
            if "command_owner" in values and not isinstance(values["command_owner"], str):
                values["command_owner"] = json.dumps(values["command_owner"])
            if "summary" in values:
                values["summary_json"] = json.dumps(values.pop("summary"))

            stmt = (
                update(tables.profiling_runs)
                .where(tables.profiling_runs.c.run_id == run_id)
                .values(**values)
            )
            conn.execute(stmt)

    def _format_run_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        try:
            result["settings"] = json.loads(result.get("settings_json") or "{}")
        except Exception:
            result["settings"] = {}
        try:
            result["manifest"] = json.loads(result.get("manifest_json") or "null")
        except Exception:
            result["manifest"] = None
        try:
            result["command_owner"] = json.loads(result.get("command_owner") or "{}")
        except Exception:
            result["command_owner"] = {}
        try:
            result["summary"] = json.loads(result.get("summary_json") or "{}")
        except Exception:
            result["summary"] = {}
        return result

    # -------------------------------------------------------------------------
    # Stage Management & Resumption
    # -------------------------------------------------------------------------

    def get_stages(self, run_id: str) -> list[dict[str, Any]]:
        """Fetch all stages for a run ordered by stage_order ascending."""
        with self.read() as conn:
            stmt = (
                select(tables.profiling_stages)
                .where(tables.profiling_stages.c.run_id == run_id)
                .order_by(tables.profiling_stages.c.stage_order.asc())
            )
            rows = conn.execute(stmt).mappings().all()
            return [dict(r) for r in rows]

    def get_stage(self, run_id: str, stage_name: str) -> dict[str, Any] | None:
        """Fetch specific stage record."""
        with self.read() as conn:
            stmt = select(tables.profiling_stages).where(
                tables.profiling_stages.c.run_id == run_id,
                tables.profiling_stages.c.stage_name == stage_name,
            )
            row = conn.execute(stmt).mappings().first()
            return dict(row) if row else None

    def transition_stage(
        self,
        run_id: str,
        stage_name: str,
        status: str,
        progress: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Update stage status, timing, and progress metrics."""
        now = time.time()
        with self.write() as conn:
            stmt = select(tables.profiling_stages).where(
                tables.profiling_stages.c.run_id == run_id,
                tables.profiling_stages.c.stage_name == stage_name,
            )
            current = conn.execute(stmt).mappings().first()
            if not current:
                return

            update_values: dict[str, Any] = {
                "status": status,
                "updated_at": now,
            }
            if status == "running" and current["started_at"] is None:
                update_values["started_at"] = now
            elif status in ("completed", "failed", "cancelled", "skipped"):
                update_values["finished_at"] = now

            if error is not None:
                update_values["error_message"] = error
            elif status == "completed":
                update_values["error_message"] = None

            if progress:
                if "fraction" in progress:
                    update_values["progress_fraction"] = float(progress["fraction"])
                elif "progress_fraction" in progress:
                    update_values["progress_fraction"] = float(progress["progress_fraction"])
                if "label" in progress:
                    update_values["progress_label"] = str(progress["label"])
                elif "progress_label" in progress:
                    update_values["progress_label"] = str(progress["progress_label"])
                if "bytes_done" in progress:
                    update_values["bytes_done"] = int(progress["bytes_done"])
                if "bytes_total" in progress:
                    update_values["bytes_total"] = int(progress["bytes_total"])

            conn.execute(
                update(tables.profiling_stages)
                .where(
                    tables.profiling_stages.c.run_id == run_id,
                    tables.profiling_stages.c.stage_name == stage_name,
                )
                .values(**update_values)
            )

            # Sync run's current_stage and updated_at
            run_updates: dict[str, Any] = {
                "current_stage": stage_name,
                "updated_at": now,
            }
            if status == "running":
                run_updates["status"] = "running"
                if not current["started_at"]:
                    run_updates["started_at"] = now
            conn.execute(
                update(tables.profiling_runs)
                .where(tables.profiling_runs.c.run_id == run_id)
                .values(**run_updates)
            )

    def reset_stage(self, run_id: str, stage_name: str, status: str = "pending") -> None:
        """Reset a stage for retry, incrementing its attempt count."""
        now = time.time()
        with self.write() as conn:
            stmt = select(tables.profiling_stages).where(
                tables.profiling_stages.c.run_id == run_id,
                tables.profiling_stages.c.stage_name == stage_name,
            )
            current = conn.execute(stmt).mappings().first()
            attempt = (current["attempt"] + 1) if current else 1

            conn.execute(
                update(tables.profiling_stages)
                .where(
                    tables.profiling_stages.c.run_id == run_id,
                    tables.profiling_stages.c.stage_name == stage_name,
                )
                .values(
                    status=status,
                    attempt=attempt,
                    progress_fraction=0.0,
                    progress_label="",
                    bytes_done=0,
                    started_at=None,
                    finished_at=None,
                    error_message=None,
                    updated_at=now,
                )
            )

    # -------------------------------------------------------------------------
    # Node & Subtask Management
    # -------------------------------------------------------------------------

    def get_nodes(self, run_id: str) -> list[dict[str, Any]]:
        """Fetch participating nodes for a run."""
        with self.read() as conn:
            stmt = select(tables.profiling_nodes).where(tables.profiling_nodes.c.run_id == run_id)
            rows = conn.execute(stmt).mappings().all()
            nodes = []
            for r in rows:
                d = dict(r)
                try:
                    d["devices"] = json.loads(d.get("devices_json") or "[]")
                except Exception:
                    d["devices"] = []
                nodes.append(d)
            return nodes

    def update_node(self, run_id: str, node_id: str, **fields: Any) -> None:
        """Update node status or remote lease information."""
        with self.write() as conn:
            values = dict(fields)
            values["updated_at"] = values.get("updated_at", time.time())
            if "devices" in values:
                values["devices_json"] = json.dumps(values.pop("devices"))
            conn.execute(
                update(tables.profiling_nodes)
                .where(
                    tables.profiling_nodes.c.run_id == run_id,
                    tables.profiling_nodes.c.node_id == node_id,
                )
                .values(**values)
            )

    def update_node_task(
        self,
        run_id: str,
        stage_name: str,
        node_id: str,
        status: str,
        progress: dict[str, Any] | None = None,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Upsert granular per-node stage subtask (e.g. chunk transfer progress)."""
        now = time.time()
        task_id = f"{run_id}:{stage_name}:{node_id}"
        row = {
            "id": task_id,
            "run_id": run_id,
            "stage_name": stage_name,
            "node_id": node_id,
            "status": status,
            "progress_fraction": float((progress or {}).get("fraction", 0.0)),
            "bytes_done": int((progress or {}).get("bytes_done", 0)),
            "bytes_total": int((progress or {}).get("bytes_total", 0)),
            "details_json": json.dumps(details or {}),
            "error_message": error,
        }
        if status == "running":
            row["started_at"] = now
        elif status in ("completed", "failed", "cancelled"):
            row["finished_at"] = now

        update_fields = [
            "status",
            "progress_fraction",
            "bytes_done",
            "bytes_total",
            "details_json",
            "error_message",
        ]
        if "started_at" in row:
            update_fields.append("started_at")
        if "finished_at" in row:
            update_fields.append("finished_at")

        stmt = _upsert(tables.profiling_node_stage_tasks, row, ["run_id", "stage_name", "node_id"], update_fields)
        with self.write() as conn:
            conn.execute(stmt)

    def get_node_tasks(self, run_id: str, stage_name: str | None = None) -> list[dict[str, Any]]:
        """Fetch granular subtasks for a run/stage."""
        with self.read() as conn:
            stmt = select(tables.profiling_node_stage_tasks).where(
                tables.profiling_node_stage_tasks.c.run_id == run_id
            )
            if stage_name:
                stmt = stmt.where(tables.profiling_node_stage_tasks.c.stage_name == stage_name)
            rows = conn.execute(stmt).mappings().all()
            tasks = []
            for r in rows:
                d = dict(r)
                try:
                    d["details"] = json.loads(d.get("details_json") or "{}")
                except Exception:
                    d["details"] = {}
                tasks.append(d)
            return tasks

    # -------------------------------------------------------------------------
    # Hardware & Link Measurement Persistence
    # -------------------------------------------------------------------------

    def record_node_profile(self, run_id: str, node_id: str, profile: Any) -> None:
        """Upsert authoritative measured node profile."""
        if hasattr(profile, "model_dump"):
            profile_dict = profile.model_dump()
        else:
            profile_dict = dict(profile)

        profile_key = profile_dict.get("hardware_digest") or profile_dict.get("settings_digest") or "default"
        profile_json = json.dumps(profile_dict)

        row = {
            "id": f"{run_id}:{node_id}",
            "run_id": run_id,
            "node_id": node_id,
            "profile_key": profile_key,
            "measured_at": float(profile_dict.get("measured_at", time.time())),
            "backend": str(profile_dict.get("backend", "cpu")),
            "measurement_kind": str(profile_dict.get("measurement_kind", "kernel_estimate")),
            "cpu_layer_ms": float(profile_dict.get("cpu_layer_ms", 1.0)),
            "gpu_layer_ms": profile_dict.get("gpu_layer_ms"),
            "disk_read_bytes_s": profile_dict.get("disk_read_bytes_s"),
            "disk_random_read_bytes_s": profile_dict.get("disk_random_read_bytes_s"),
            "memory_available_bytes": int(profile_dict.get("memory_available_bytes", 0)),
            "peak_memory_bytes": profile_dict.get("peak_memory_bytes"),
            "prefill_ms": profile_dict.get("prefill_ms"),
            "decode_ms": profile_dict.get("decode_ms"),
            "gpu_layers": profile_dict.get("gpu_layers"),
            "total_layers": profile_dict.get("total_layers"),
            "profile_json": profile_json,
        }
        update_fields = [k for k in row.keys() if k not in ("id", "run_id", "node_id")]
        stmt = _upsert(tables.measured_node_profiles, row, ["run_id", "node_id"], update_fields)
        with self.write() as conn:
            conn.execute(stmt)

    def get_node_profiles(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Fetch all measured node profiles for a run keyed by node_id."""
        with self.read() as conn:
            stmt = select(tables.measured_node_profiles).where(
                tables.measured_node_profiles.c.run_id == run_id
            )
            rows = conn.execute(stmt).mappings().all()
            profiles = {}
            for r in rows:
                try:
                    p = json.loads(r["profile_json"])
                except Exception:
                    p = dict(r)
                profiles[r["node_id"]] = p
            return profiles

    def record_link_profile(
        self, run_id: str, source: str, target: str, measurement: dict[str, Any]
    ) -> None:
        """Upsert pairwise Ring-TCP link measurement."""
        row = {
            "id": f"{run_id}:{source}->{target}",
            "run_id": run_id,
            "source_node_id": source,
            "target_node_id": target,
            "grant_id": str(measurement.get("grant_id", "")),
            "network_id": measurement.get("network_id"),
            "source_address": str(measurement.get("source_address", "")),
            "target_address": str(measurement.get("target_address", "")),
            "latency_ms": float(measurement.get("latency_ms", 0.0)),
            "bandwidth_bytes_s": float(measurement.get("bandwidth_bytes_s", 0.0)),
            "samples": int(measurement.get("samples", 3)),
            "sample_seconds_json": json.dumps(measurement.get("sample_seconds") or []),
            "measured_at": float(measurement.get("measured_at", time.time())),
            "receipt_json": json.dumps(measurement.get("receipt") or {}),
        }
        update_fields = [k for k in row.keys() if k not in ("id", "run_id", "source_node_id", "target_node_id")]
        stmt = _upsert(tables.measured_link_profiles, row, ["run_id", "source_node_id", "target_node_id"], update_fields)
        with self.write() as conn:
            conn.execute(stmt)

    def get_link_profiles(self, run_id: str) -> list[dict[str, Any]]:
        """Fetch all measured link profiles for a run."""
        with self.read() as conn:
            stmt = select(tables.measured_link_profiles).where(
                tables.measured_link_profiles.c.run_id == run_id
            )
            rows = conn.execute(stmt).mappings().all()
            links = []
            for r in rows:
                d = dict(r)
                try:
                    d["sample_seconds"] = json.loads(d.get("sample_seconds_json") or "[]")
                except Exception:
                    d["sample_seconds"] = []
                try:
                    d["receipt"] = json.loads(d.get("receipt_json") or "{}")
                except Exception:
                    d["receipt"] = {}
                links.append(d)
            return links

    # -------------------------------------------------------------------------
    # Qualification & HiGHS Schedule Results
    # -------------------------------------------------------------------------

    def record_qualification(self, run_id: str, result: dict[str, Any]) -> None:
        """Upsert plan qualification / HiGHS result."""
        row = {
            "run_id": run_id,
            "fit_status": str(result.get("fit_status", "fit")),
            "predicted_decode_ms": result.get("predicted_decode_ms"),
            "predicted_weight_read_ms": result.get("predicted_weight_read_ms"),
            "scheduler_strategy": str(result.get("scheduler_strategy", "highs_halda")),
            "scheduler_report_json": json.dumps(result.get("scheduler_report") or {}),
            "calculated_at": float(result.get("calculated_at", time.time())),
            "qualification_passed": bool(result.get("qualification_passed", False)),
            "qualification_errors_json": json.dumps(result.get("qualification_errors") or []),
        }
        update_fields = [k for k in row.keys() if k != "run_id"]
        stmt = _upsert(tables.profiling_qualification_results, row, ["run_id"], update_fields)
        with self.write() as conn:
            conn.execute(stmt)

    def get_qualification(self, run_id: str) -> dict[str, Any] | None:
        """Fetch qualification result."""
        with self.read() as conn:
            stmt = select(tables.profiling_qualification_results).where(
                tables.profiling_qualification_results.c.run_id == run_id
            )
            row = conn.execute(stmt).mappings().first()
            if not row:
                return None
            d = dict(row)
            try:
                d["scheduler_report"] = json.loads(d.get("scheduler_report_json") or "{}")
            except Exception:
                d["scheduler_report"] = {}
            try:
                d["qualification_errors"] = json.loads(d.get("qualification_errors_json") or "[]")
            except Exception:
                d["qualification_errors"] = []
            return d

    # -------------------------------------------------------------------------
    # Structured Diagnostics & Logs
    # -------------------------------------------------------------------------

    def append_log(
        self,
        run_id: str,
        level: str,
        category: str,
        message: str,
        stage: str | None = None,
        node: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append a structured log record."""
        row = {
            "run_id": run_id,
            "stage_name": stage,
            "node_id": node,
            "timestamp": time.time(),
            "level": level.upper(),
            "category": category,
            "message": message,
            "details_json": json.dumps(details or {}),
        }
        with self.write() as conn:
            conn.execute(tables.profiling_logs.insert().values(row))

    def get_logs(
        self,
        run_id: str,
        stage: str | None = None,
        node: str | None = None,
        level: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Fetch structured logs filtered by stage, node, or minimum level."""
        with self.read() as conn:
            stmt = select(tables.profiling_logs).where(tables.profiling_logs.c.run_id == run_id)
            if stage:
                stmt = stmt.where(tables.profiling_logs.c.stage_name == stage)
            if node:
                stmt = stmt.where(tables.profiling_logs.c.node_id == node)
            if level:
                stmt = stmt.where(tables.profiling_logs.c.level == level.upper())
            stmt = stmt.order_by(tables.profiling_logs.c.timestamp.asc()).limit(limit)
            rows = conn.execute(stmt).mappings().all()
            logs = []
            for r in rows:
                d = dict(r)
                try:
                    d["details"] = json.loads(d.get("details_json") or "{}")
                except Exception:
                    d["details"] = {}
                logs.append(d)
            return logs

    # -------------------------------------------------------------------------
    # Crash Safety & Startup Reconciliation
    # -------------------------------------------------------------------------

    def reconcile_on_startup(self) -> list[str]:
        """Reconcile in-progress runs on agent process start.

        Transitions any interrupted RUNNING stages and runs to 'failed' (resumable)
        without discarding already completed stages or measured hardware data.
        """
        now = time.time()
        reconciled_runs: list[str] = []
        with self.write() as conn:
            # Find all active runs
            stmt = select(tables.profiling_runs.c.run_id).where(
                tables.profiling_runs.c.status.in_(["running", "queued"])
            )
            active_ids = [row[0] for row in conn.execute(stmt).fetchall()]

            for run_id in active_ids:
                # Find any stages marked running
                stage_stmt = select(tables.profiling_stages.c.stage_name).where(
                    tables.profiling_stages.c.run_id == run_id,
                    tables.profiling_stages.c.status == "running",
                )
                running_stages = [r[0] for r in conn.execute(stage_stmt).fetchall()]
                for sname in running_stages:
                    conn.execute(
                        update(tables.profiling_stages)
                        .where(
                            tables.profiling_stages.c.run_id == run_id,
                            tables.profiling_stages.c.stage_name == sname,
                        )
                        .values(
                            status="failed",
                            error_message="Agent process restarted during execution; stage is ready for resumption",
                            finished_at=now,
                            updated_at=now,
                        )
                    )

                # Transition run to failed (resumable)
                conn.execute(
                    update(tables.profiling_runs)
                    .where(tables.profiling_runs.c.run_id == run_id)
                    .values(
                        status="failed",
                        error_message="Agent process restarted during execution; stage is ready for resumption",
                        finished_at=now,
                        updated_at=now,
                    )
                )
                reconciled_runs.append(run_id)

        return reconciled_runs
