"""Coordinator for Prima Prepare & Profile 5-stage lifecycle, retries, and resumption."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any
from uuid import UUID, uuid4

from kllm.modules.primacpp.entities import (
    STAGE_ORDERS,
    STAGE_SEQUENCE,
    ProfilingRunStatus,
    ProfilingStageName,
    StageStatus,
    TERMINAL,
)
from kllm.modules.primacpp.gateways.store import ProfilingStore
from kllm.modules.primacpp.interactors import (
    stage_asset_sync,
    stage_link_profile,
    stage_materialize,
    stage_node_profile,
    stage_qualification,
)
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, authority_owner
from kllm.modules.primacpp.interactors.preparation_metrics import diagnostics_scope, local_chunk_bytes
from kllm.modules.primacpp.interactors.transfers import PreparationLeases
from kllm.modules.runtimes.entities import body_digest
from kllm.shared.prima_diagnostics import scope as log_scope
from kllm.shared.runtime_resources import prima_scope


class ProfilingCoordinator:
    """Coordinates SQLite-backed 5-stage Prepare & Profile state machine and stage retries."""

    def __init__(self, control: Any, store: ProfilingStore):
        self.control = control
        self.store = store
        self.pool = getattr(control, "pool", None) or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="prima-coord"
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.pool is not getattr(self.control, "pool", None):
            self.pool.shutdown(wait=False, cancel_futures=True)

    def reconcile_on_startup(self) -> list[str]:
        """Reconcile in-progress runs on agent process start."""
        reconciled = self.store.reconcile_on_startup()
        for run_id in reconciled:
            self.store.append_log(
                run_id,
                "WARN",
                "lifecycle",
                "Interrupted run was reconciled to failed (resumable) on daemon restart",
            )
        return reconciled

    # -------------------------------------------------------------------------
    # Run Submission & Resumption
    # -------------------------------------------------------------------------

    def start_run(
        self,
        runtime_id: str,
        body: Any,
        expected_digest: str,
        request_id: str,
        *,
        include_profile: bool = False,
    ) -> dict[str, Any]:
        """Submit a new profiling run or return existing run for identical request ID."""
        if self.control.service.state().role != "master":
            raise ValueError("Prima preparation requires the Kluster master")

        UUID(request_id)
        payload = {
            "runtime_id": runtime_id,
            "settings": body.model_dump(),
            "plan_digest": expected_digest,
            "include_profile": include_profile,
        }
        digest = body_digest(payload)

        # Idempotency check via SQLite
        existing = self.store.get_run_by_request_id(request_id)
        if existing:
            if existing["request_digest"] != digest:
                raise ValueError("Preparation request ID reused with different inputs")
            return self.get_run(existing["run_id"])

        chunk_bytes = local_chunk_bytes()
        service_state = self.control.service.state()
        if (
            service_state.process_pid
            or self.control.rank.owner
            or self.control.runs.busy
            or any(j["phase"] not in TERMINAL for j in self.control.jobs.values())
        ):
            raise ValueError("Stop inference or the active preparation before preparing Prima")

        run_id = str(uuid4())
        lease = self.control.resources.acquire(
            prima_scope(
                run_id,
                authority_owner(self.control.service.channels.master_authority()),
                expected_digest,
            ),
            "prepare",
        )
        self.control.preparation_reservations[run_id] = lease
        now = time.time()

        # Build initial stage records
        stages_data = [
            {
                "stage_name": s.value,
                "stage_order": STAGE_ORDERS[s],
                "status": StageStatus.PENDING.value,
            }
            for s in STAGE_SEQUENCE
        ]

        # Initial nodes from members
        members = self.control.members(body)
        nodes_data = [
            {
                "node_id": node_id,
                "role": m.get("role", "worker"),
                "rank_index": idx,
                "assigned_address": m.get("host") or m.get("lan_host") or "127.0.0.1",
                "backend": m.get("backend", "cpu"),
            }
            for idx, (node_id, m) in enumerate(members.items())
        ]

        run_data = {
            "run_id": run_id,
            "request_id": request_id,
            "request_digest": digest,
            "runtime_id": runtime_id,
            "model_path": str(body.model),
            "model_sha256": getattr(body, "model_sha256", "") or "",
            "network_id": getattr(body, "network_id", None),
            "status": ProfilingRunStatus.RUNNING.value,
            "current_stage": ProfilingStageName.MATERIALIZE.value,
            "include_profile": include_profile,
            "plan_digest": expected_digest,
            "command_owner": authority_owner(self.control.service.channels.master_authority()),
            "settings": body.model_dump(),
            "created_at": now,
            "started_at": now,
            "updated_at": now,
        }

        self.store.create_run(run_data, stages_data, nodes_data)

        # Update control.jobs for backward compatibility
        with self.control.lock:
            self.control.jobs[run_id] = {
                "job_id": run_id,
                "request_id": request_id,
                "request_digest": digest,
                "phase": "queued",
                "runtime_id": runtime_id,
                "plan_digest": expected_digest,
                "nodes": {},
                "error": None,
                "created_at": now,
                "request_settings_digest": body_digest(body.model_dump()),
                "local_chunk_bytes": chunk_bytes,
                "progress": {
                    "phase": "queued",
                    "label": "Queued for Prima preparation…",
                    "step_index": 1,
                    "step_total": 2 if include_profile else 1,
                    "fraction": 0.0,
                    "pct": 0.0,
                    "bytes_done": 0,
                    "bytes_total": 0,
                    "rate_bps": None,
                    "eta_seconds": None,
                    "updated_at": now,
                },
            }

        # Launch background execution
        future = self.pool.submit(
            self.control._prepare, run_id, runtime_id, body, expected_digest, include_profile
        )
        self.control.preparation_futures[run_id] = future
        future.add_done_callback(lambda _: self.control._settle_preparation(run_id))

        return self.get_run(run_id)

    def retry_run(
        self,
        run_id: str,
        stage_name: str | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        """Retry a failed or interrupted stage, preserving all already-completed earlier stages."""
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError(f"Profiling run {run_id} not found")

        if run["status"] == ProfilingRunStatus.RUNNING.value:
            raise ValueError(f"Profiling run {run_id} is already actively running")

        stages = self.store.get_stages(run_id)
        if not stages:
            raise ValueError(f"No stages found for run {run_id}")

        # Determine which stage to retry
        target_stage_name = stage_name
        if not target_stage_name:
            # Pick first failed or pending stage
            failed_stage = next(
                (s for s in stages if s["status"] in (StageStatus.FAILED.value, StageStatus.PENDING.value)),
                None,
            )
            target_stage_name = failed_stage["stage_name"] if failed_stage else stages[0]["stage_name"]

        target_stage = next((s for s in stages if s["stage_name"] == target_stage_name), None)
        if not target_stage:
            raise ValueError(f"Stage '{target_stage_name}' is invalid for run {run_id}")

        target_order = target_stage["stage_order"]

        # Invariant: preserve completed stages that ran before the target stage
        preserved_stages = [
            s["stage_name"]
            for s in stages
            if s["stage_order"] < target_order and s["status"] == StageStatus.COMPLETED.value
        ]

        # Reset target stage and any subsequent stages for retry
        self.store.reset_stage(run_id, target_stage_name, StageStatus.PENDING.value)
        for s in stages:
            if s["stage_order"] > target_order and s["status"] != StageStatus.SKIPPED.value:
                self.store.reset_stage(run_id, s["stage_name"], StageStatus.PENDING.value)

        # Clear cancelled flag if previously cancelled
        self.control.cancelled.discard(run_id)

        # Ensure lease
        if run_id not in self.control.preparation_reservations:
            lease = self.control.resources.acquire(
                prima_scope(
                    run_id,
                    authority_owner(self.control.service.channels.master_authority()),
                    run["plan_digest"],
                ),
                "prepare",
            )
            self.control.preparation_reservations[run_id] = lease

        # Update run status to running
        self.store.update_run(
            run_id,
            status=ProfilingRunStatus.RUNNING.value,
            error_message=None,
            current_stage=target_stage_name,
        )
        self.store.append_log(
            run_id,
            "INFO",
            "lifecycle",
            f"Retrying run from stage '{target_stage_name}' (preserved stages: {preserved_stages})",
            stage=target_stage_name,
        )

        with self.control.lock:
            if run_id in self.control.jobs:
                self.control.jobs[run_id]["phase"] = "preparing"
                self.control.jobs[run_id]["error"] = None

        from kllm.modules.inference.entities import PlanBody

        body = PlanBody.model_validate(run["settings"])

        future = self.pool.submit(
            self.control._prepare,
            run_id,
            run["runtime_id"],
            body,
            run["plan_digest"],
            run["include_profile"],
            start_from_stage=target_stage_name,
        )
        self.control.preparation_futures[run_id] = future
        future.add_done_callback(lambda _: self.control._settle_preparation(run_id))

        result = self.get_run(run_id) or {}
        result.update(
            run_id=run_id,
            job_id=run_id,
            status=ProfilingRunStatus.RUNNING.value,
            phase="preparing",
            retrying_stage=target_stage_name,
            resumed_from=target_stage_name,
            preserved_stages=preserved_stages,
        )
        return result

    def cancel_run(self, run_id: str, reason: str | None = None) -> dict[str, Any]:
        """Cancel an in-progress or queued run."""
        self.control.cancelled.add(run_id)
        now = time.time()
        err_msg = reason or "Preparation cancelled"

        # Update active stage
        current = self.store.get_run(run_id)
        if current and current.get("current_stage"):
            self.store.transition_stage(
                run_id, current["current_stage"], StageStatus.CANCELLED.value, error=err_msg
            )

        self.store.update_run(
            run_id,
            status=ProfilingRunStatus.CANCELLED.value,
            error_message=err_msg,
            finished_at=now,
        )
        self.store.append_log(run_id, "WARN", "lifecycle", f"Run cancelled: {err_msg}")

        # Cancel in control
        self.control.cancel(run_id, reason=reason)
        return self.get_run(run_id)

    # -------------------------------------------------------------------------
    # Core Resumable Execution Engine
    # -------------------------------------------------------------------------

    def _execute_run(
        self,
        run_id: str,
        runtime_id: str,
        body: Any,
        expected_digest: str,
        include_profile: bool,
        start_from_stage: str | None = None,
    ) -> None:
        """Run the 5-stage pipeline, skipping completed stages and resuming from the requested stage."""
        def renew(node, job_id, progress):
            self.control._check(run_id)
            return self.control._call(
                node, run_id, "prima_prepare_renew", {"job_id": job_id, "progress": progress}
            )

        leases = PreparationLeases(renew)

        def check():
            self.control._check(run_id)
            leases.check()

        job_entry = self.control.jobs.get(run_id) or {}
        with log_scope(run_id), diagnostics_scope(
            job_entry, self.control.lock, lambda: self.control._check(run_id), self.control.service.state().node_id
        ):
            manifest: ExecutionManifest | None = None
            files: list[tuple[Any, Any]] = []

            try:
                check()
                # Inspect stages in SQLite
                stages_by_name = {s["stage_name"]: s for s in self.store.get_stages(run_id)}

                # Stage 1: MATERIALIZE
                self.store.update_run(run_id, current_stage=ProfilingStageName.MATERIALIZE.value)
                mat_stage = stages_by_name.get("materialize")
                if mat_stage and mat_stage["status"] == StageStatus.COMPLETED.value:
                    run_record = self.store.get_run(run_id)
                    manifest_data = run_record.get("manifest")
                    if manifest_data:
                        manifest = ExecutionManifest.model_validate(manifest_data)
                    # Reconstruct files list
                    model = self.control.resolve_model(body.model)
                    from kllm.modules.primacpp.interactors.planner import materialize as staging_mat
                    companions = {
                        kind: self.control.resolve_model(value)
                        for kind, value in [
                            ("draft", body.draft_model if body.draft_enabled() else None),
                            ("mmproj", body.mmproj),
                        ]
                        if value
                    }
                    adapters = stage_materialize.adapter_paths(body.model_dump())
                    runtime = self.control.registry.capture(runtime_id)
                    planned = self.control.plan(runtime_id, body, model=model)
                    _, files = staging_mat(
                        body,
                        runtime,
                        self.control.members(body),
                        model,
                        lambda b, m: planned,
                        expected_digest=expected_digest,
                        companions=companions,
                        adapters=adapters,
                    )
                    manifest_file = self.control.root / run_id / "execution.json"
                    files.append(
                        (
                            stage_materialize.Asset(
                                name="execution.json",
                                kind="manifest",
                                sha256=manifest.digest,
                                size=manifest_file.stat().st_size if manifest_file.exists() else 0,
                            ),
                            manifest_file,
                        )
                    )
                    self.store.append_log(
                        run_id,
                        "INFO",
                        "lifecycle",
                        "Preserving completed Stage 1: Materialize",
                        stage="materialize",
                    )
                else:
                    manifest, files = stage_materialize.execute_materialize(
                        self, run_id, runtime_id, body, expected_digest, include_profile, check
                    )

                step_total = 1 + (1 if include_profile else 0) + (1 if include_profile and len(manifest.ranks) > 1 else 0)
                stage_span = 0.45 if include_profile else 0.96

                # Stage 2: ASSET_SYNC
                check()
                self.store.update_run(run_id, current_stage=ProfilingStageName.ASSET_SYNC.value)
                sync_stage = stages_by_name.get("asset_sync")
                if sync_stage and sync_stage["status"] == StageStatus.COMPLETED.value:
                    self.store.append_log(
                        run_id,
                        "INFO",
                        "asset",
                        "Preserving completed Stage 2: Asset Sync",
                        stage="asset_sync",
                    )
                else:
                    stage_asset_sync.execute_asset_sync(
                        self,
                        run_id,
                        manifest,
                        files,
                        leases,
                        check,
                        step_total=step_total,
                        stage_span=stage_span,
                    )

                # Stage 3: NODE_PROFILE
                check()
                self.store.update_run(run_id, current_stage=ProfilingStageName.NODE_PROFILE.value)
                node_stage = stages_by_name.get("node_profile")
                if node_stage and node_stage["status"] in (StageStatus.COMPLETED.value, StageStatus.SKIPPED.value):
                    self.store.append_log(
                        run_id,
                        "INFO",
                        "kernel",
                        "Preserving completed Stage 3: Node Profile",
                        stage="node_profile",
                    )
                else:
                    stage_node_profile.execute_node_profile(
                        self,
                        run_id,
                        manifest,
                        include_profile,
                        leases,
                        check,
                        step_total=step_total,
                    )

                # Stage 4: LINK_PROFILE
                check()
                self.store.update_run(run_id, current_stage=ProfilingStageName.LINK_PROFILE.value)
                link_stage = stages_by_name.get("link_profile")
                if link_stage and link_stage["status"] in (StageStatus.COMPLETED.value, StageStatus.SKIPPED.value):
                    self.store.append_log(
                        run_id,
                        "INFO",
                        "network",
                        "Preserving completed Stage 4: Link Profile",
                        stage="link_profile",
                    )
                else:
                    stage_link_profile.execute_link_profile(
                        self,
                        run_id,
                        manifest,
                        include_profile,
                        leases,
                        check,
                        step_total=step_total,
                    )

                # Stage 5: PLAN_QUALIFICATION
                check()
                self.store.update_run(run_id, current_stage=ProfilingStageName.PLAN_QUALIFICATION.value)
                stage_qualification.execute_qualification(self, run_id, manifest, check)

                # Mark prepared in control
                leases.advance()
                leases.check(force=True)
                total_stage_bytes = sum(a.size for a, _ in files) * max(1, len(manifest.ranks))
                self.control._update_job_progress(
                    run_id,
                    phase="prepared",
                    label="Prima assets prepared and profiled" if include_profile else "Prima assets prepared",
                    step_index=step_total,
                    step_total=step_total,
                    fraction=1.0,
                    bytes_done=total_stage_bytes,
                    bytes_total=total_stage_bytes,
                )
                with self.control.lock:
                    self.control._check(run_id)
                    self.control.jobs[run_id].update(
                        phase="prepared", finished_at=time.time(), expires_at=time.time() + 86400
                    )

            except Exception as error:
                final_phase = "cancelled" if run_id in self.control.cancelled else "failed"
                err_str = str(error)[:1000]

                # Update current running stage in SQLite
                run_rec = self.store.get_run(run_id)
                current_st = run_rec.get("current_stage") if run_rec else None
                if current_st:
                    self.store.transition_stage(
                        run_id, current_st, final_phase, error=err_str
                    )

                self.store.update_run(
                    run_id,
                    status=final_phase,
                    error_message=err_str,
                    finished_at=time.time(),
                )
                self.store.append_log(run_id, "ERROR", "lifecycle", f"Run failed: {err_str}", stage=current_st)

                prev_prog = self.control.jobs.get(run_id, {}).get("progress") or {}
                exact_bytes = self.control.jobs.get(run_id, {}).get("progress_bytes") or {}
                self.control._update_job_progress(
                    run_id,
                    phase=final_phase,
                    label=str(error)[:240],
                    step_index=int(prev_prog.get("step_index") or 1),
                    step_total=int(prev_prog.get("step_total") or 1),
                    fraction=float(prev_prog.get("fraction") or 0.0),
                    bytes_done=int(exact_bytes.get("done", prev_prog.get("bytes_done")) or 0),
                    bytes_total=int(exact_bytes.get("total", prev_prog.get("bytes_total")) or 0),
                )
                with self.control.lock:
                    if self.control.jobs.get(run_id, {}).get("phase") != "cancelled":
                        self.control.jobs.setdefault(run_id, {}).update(
                            phase=final_phase, error=err_str, finished_at=time.time()
                        )
            finally:
                run_rec = self.store.get_run(run_id)
                if run_rec and run_rec["status"] in ("failed", "cancelled"):
                    for node, result in self.control.jobs.get(run_id, {}).get("nodes", {}).items():
                        profile_job = (result.get("profile") or {}).get("job_id")
                        if profile_job:
                            try:
                                if node == self.control.service.state().node_id:
                                    self.control.profiles.cancel(profile_job)
                                else:
                                    self.control._call(node, run_id, "prima_cancel", {"profile_job_id": profile_job})
                            except Exception:
                                pass
                        if node != self.control.service.state().node_id and result.get("job_id"):
                            try:
                                self.control._call(node, run_id, "prima_cancel", {"job_id": result["job_id"]})
                            except Exception:
                                pass
                try:
                    self.control.grants.revoke(run_id)
                except Exception:
                    pass
                self.control._save()

    # -------------------------------------------------------------------------
    # Status, Coverage & Logs Queries
    # -------------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Fetch unified SQLite-backed run status with full backward compatibility."""
        run = self.store.get_run(run_id)
        if not run:
            # Fall back to control.jobs if in-memory test fixture
            with self.control.lock:
                if run_id in self.control.jobs:
                    return self.control._public_job(run_id)
            return None

        stages = self.store.get_stages(run_id)
        nodes = self.store.get_nodes(run_id)
        qualification = self.store.get_qualification(run_id)

        # Construct backward-compatible nodes dict
        nodes_dict: dict[str, Any] = {}
        with self.control.lock:
            cached_nodes = self.control.jobs.get(run_id, {}).get("nodes", {})
        for n in nodes:
            node_id = n["node_id"]
            cached = cached_nodes.get(node_id, {})
            nodes_dict[node_id] = {
                "role": n["role"],
                "status": n["status"],
                "backend": n["backend"],
                "phase": "prepared" if n["status"] == "prepared" else n["status"],
                "job_id": n.get("remote_job_id"),
                "progress": cached.get("progress") or {"fraction": 1.0 if n["status"] == "prepared" else 0.0},
                **({k: v for k, v in cached.items() if k not in ("role", "status", "backend", "phase")}),
            }

        future = self.control.preparation_futures.get(run_id)
        local_work_pending = (
            run_id in self.control.preparation_reservations
            or (future is not None and not future.done())
        )

        phase_map = {
            ProfilingRunStatus.QUEUED.value: "queued",
            ProfilingRunStatus.RUNNING.value: "preparing",
            ProfilingRunStatus.COMPLETED.value: "prepared",
            ProfilingRunStatus.FAILED.value: "failed",
            ProfilingRunStatus.CANCELLED.value: "cancelled",
            ProfilingRunStatus.INTERRUPTED.value: "interrupted",
        }
        phase = phase_map.get(run["status"], "preparing")
        if phase == "prepared" and local_work_pending:
            phase = "settling"

        # Calculate progress summary
        completed_count = sum(1 for s in stages if s["status"] in ("completed", "skipped"))
        fraction = round(completed_count / max(1, len(stages)), 4) if stages else 0.0
        cur_stage = next((s for s in stages if s["stage_name"] == run["current_stage"]), None)
        bytes_done = sum(s["bytes_done"] for s in stages)
        bytes_total = max(bytes_done, sum(s["bytes_total"] for s in stages))

        progress = {
            "phase": phase,
            "stage": run["current_stage"],
            "fraction": 1.0 if phase == "prepared" else fraction,
            "pct": 100.0 if phase == "prepared" else round(fraction * 100.0, 1),
            "label": cur_stage["progress_label"] if cur_stage else (run.get("error_message") or ""),
            "bytes_done": bytes_done,
            "bytes_total": bytes_total,
            "step_index": cur_stage["stage_order"] if cur_stage else 1,
            "step_total": len(stages),
            "updated_at": run["updated_at"],
        }

        # Merge in cached memory progress if present
        with self.control.lock:
            cached_job = self.control.jobs.get(run_id)
            if cached_job and cached_job.get("progress"):
                for k, v in cached_job["progress"].items():
                    if k in ("rate_bps", "eta_seconds", "stage_bytes_done", "stage_bytes_total", "rate_kind"):
                        progress[k] = v

        return {
            "run_id": run["run_id"],
            "job_id": run["run_id"],
            "request_id": run["request_id"],
            "status": run["status"],
            "phase": phase,
            "current_stage": run["current_stage"],
            "include_profile": run["include_profile"],
            "runtime_id": run["runtime_id"],
            "plan_digest": run["plan_digest"],
            "manifest_digest": run.get("manifest_digest"),
            "manifest": run.get("manifest"),
            "created_at": run["created_at"],
            "started_at": run["started_at"],
            "updated_at": run["updated_at"],
            "finished_at": run["finished_at"],
            "expires_at": run["expires_at"],
            "local_work_pending": local_work_pending,
            "local_chunk_bytes": (cached_job or {}).get("local_chunk_bytes", 4 * 1024 * 1024),
            "diagnostics": (cached_job or {}).get("diagnostics", {"stages": []}),
            "error": run["error_message"],
            "stages": stages,
            "nodes": nodes_dict,
            "progress": progress,
            "qualification": qualification,
        }

    def get_coverage(self, run_id: str) -> dict[str, Any]:
        """Fetch measured hardware profiles per node and directed pairwise link matrix."""
        node_profiles = self.store.get_node_profiles(run_id)
        links = self.store.get_link_profiles(run_id)
        qualification = self.store.get_qualification(run_id)

        link_matrix = [
            {
                "source": link_item["source_node_id"],
                "target": link_item["target_node_id"],
                "latency_ms": link_item["latency_ms"],
                "bandwidth_bytes_s": link_item["bandwidth_bytes_s"],
            }
            for link_item in links
        ]

        coverage_status = "profiled" if (node_profiles and (len(node_profiles) <= 1 or link_matrix)) else "unmeasured"

        return {
            "run_id": run_id,
            "coverage_status": coverage_status,
            "node_profiles": node_profiles,
            "link_matrix": link_matrix,
            "highs_scheduler": (qualification or {}).get("scheduler_report") or {},
            "qualification": qualification,
        }

    def get_logs(
        self,
        run_id: str,
        stage: str | None = None,
        node: str | None = None,
        level: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Fetch structured diagnostics logs and text summary."""
        logs = self.store.get_logs(run_id, stage=stage, node=node, level=level, limit=limit)
        text_lines = [
            f"[{time.strftime('%H:%M:%S', time.gmtime(log_item['timestamp']))}] [{log_item['level']}] "
            f"({log_item.get('stage_name') or 'run'}/{log_item.get('node_id') or 'all'}) {log_item['message']}"
            for log_item in logs
        ]
        return {
            "run_id": run_id,
            "logs": logs,
            "text": "\n".join(text_lines)[-65536:],
        }
