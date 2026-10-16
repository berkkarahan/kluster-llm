"""Owned Prima rank supervision, independent of normal runtime selection."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from uuid import UUID

from kllm.modules.primacpp.interactors.execution import (
    ExecutionManifest,
    require_execution_capabilities,
)
from kllm.modules.primacpp.interactors.model_support import require_model_support
from kllm.modules.runtimes.interactors.registry import atomic_json
from kllm.shared.runtime_resources import prima_scope

LEASE_SECONDS = 60
MONITOR_SECONDS = 5
RECOVERY_MISSES = 2
MAX_RECOVERY_ATTEMPTS = 3


class RankSupervisor:
    def __init__(
        self,
        root: Path,
        node_id: str,
        *,
        capture,
        spawn,
        stop,
        alive,
        identity,
        clock=time.monotonic,
        resources=None,
    ):
        self.root, self.node_id, self.capture = root, node_id, capture
        self.spawn, self.stop_process, self.alive, self.identity, self.clock = (
            spawn,
            stop,
            alive,
            identity,
            clock,
        )
        self.lock = threading.RLock()
        self.resources, self.reservation = resources, None
        self.owner = None
        self.deadline = 0.0
        self.record_path = root / "rank.json"
        try:
            import json

            self.owner = json.loads(self.record_path.read_text())
            if self.owner:
                self.owner["phase"] = "cleanup_pending"
                if self.resources is not None:
                    self.reservation = self.resources.acquire(prima_scope(
                        self.owner["run_id"], self.owner.get("command_owner"), self.owner["manifest_digest"]), "run")
        except FileNotFoundError:
            pass

    def _save(self):
        atomic_json(self.record_path, self.owner)

    def _check_owner(self, run_id: str):
        UUID(run_id)
        if self.owner and self.owner["run_id"] != run_id:
            raise ValueError("Rank is owned by a different run")

    def _check_command_owner(self, command_owner):
        if (self.owner and command_owner is not None
                and self.owner.get("command_owner") != command_owner):
            raise ValueError("Rank command authority differs from its persisted owner")

    def _identity_or_exited(self, pid):
        """Native identity token, or None when the process no longer exists."""
        try:
            return self.identity(pid)
        except RuntimeError as error:
            # Windows keeps a terminated-but-unreaped child visible to a liveness
            # probe; the native identity read is what reports the exit.
            if "process_exited" in str(error):
                return None
            raise

    def _owned_child_alive(self):
        if not self.owner or not self.alive(self.owner["pid"]):
            return False
        try:
            token = self._identity_or_exited(self.owner["pid"])
        except RuntimeError as error:
            self.owner["phase"] = "cleanup_pending"
            self._save()
            raise ValueError("Rank process identity unavailable; cleanup retained") from error
        if token is None:
            return False
        if token != self.owner["start_token"]:
            self.owner["phase"] = "cleanup_pending"
            self._save()
            raise ValueError("Rank process identity changed; cleanup retained")
        return True

    def start(self, manifest: ExecutionManifest, model: Path, authorize, *, assessment=False,
              command_owner=None):
        # Production callers cannot opt into assessment; the isolated harness owns that composition.
        authorize()
        runtime = self.capture(manifest.runtime_id)
        if not assessment:
            from kllm.modules.primacpp.interactors.recipe import require_qualified
            from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices
            from kllm.modules.primacpp.interactors.qualification import manifest_context
            from kllm.modules.primacpp.interactors.participants import observe_participant, require_participant

            require_qualified(runtime)
            local_rank = next((r for r in manifest.ranks if r.node_id == self.node_id), None)
            selected = (select_vulkan_devices(discover_vulkan_devices(runtime), local_rank.devices)
                        if local_rank and local_rank.devices else [])
            if len(manifest.ranks) > 1:
                if local_rank is None:
                    raise ValueError("This node is not a manifest participant")
                require_participant(manifest, self.node_id,
                    observe_participant(runtime, self.node_id, local_rank, manifest.backends[self.node_id]))
            require_qualified(runtime, context=manifest_context(manifest, runtime, devices=selected))
        require_execution_capabilities(manifest, runtime.get("capabilities", {}))
        # Refuse a family this build cannot load before any process or reservation exists.
        require_model_support(model, runtime.get("capabilities", {}))
        rank = next((r for r in manifest.ranks if r.node_id == self.node_id), None)
        if rank is None:
            raise ValueError("This node is not a manifest participant")
        backend = manifest.backends[self.node_id]
        available = {str(name).lower() for name in runtime.get("capabilities", {}).get("backends", [])}
        if (rank.ngl or rank.output_gpu) and backend.lower() not in available:
            raise ValueError("Requested rank GPU backend is unavailable")
        with self.lock:
            self._check_owner(manifest.run_id)
            self._check_command_owner(command_owner)
            if self.owner:
                if self.owner["manifest_digest"] != manifest.digest:
                    raise ValueError("Duplicate start changed the execution manifest")
                if self.owner["phase"] != "cleanup_pending" and self._owned_child_alive():
                    self.deadline = self.clock() + LEASE_SECONDS
                    return dict(self.owner)
                raise ValueError("Previous rank requires cleanup before restart")
            directory = self.root / manifest.run_id
            directory.mkdir(parents=True, exist_ok=True)
            manifest_path = directory / "execution.json"
            manifest_path.write_text(
                __import__("json").dumps(
                    manifest.model_dump(), sort_keys=True, separators=(",", ":")
                )
            )
            authorize()
            if self.resources is not None:
                self.reservation = self.resources.acquire(prima_scope(
                    manifest.run_id, command_owner, manifest.digest), "run")
            try:
                result = self.spawn(
                    runtime, rank, model, manifest_path, directory / "rank.log", manifest.settings
                )
            except BaseException:
                if self.reservation is not None:
                    self.reservation.release()
                    self.reservation = None
                raise
            self.owner = {
                "run_id": manifest.run_id,
                "manifest_digest": manifest.digest,
                "runtime_id": manifest.runtime_id,
                "pid": result["pid"],
                "start_token": result["start_token"],
                "phase": "starting",
                "rank": rank.rank,
                "ngl": rank.ngl,
                "layers": sum(window.end - window.start for window in rank.layers),
                "backend": backend,
            }
            if command_owner is not None:
                self.owner["command_owner"] = dict(command_owner)
            self.deadline = self.clock() + LEASE_SECONDS
            try:
                self._save()
            except BaseException:
                # A child exists even if publishing its first owner record
                # failed. Every caller must drain that exact child before the
                # startup reservation can be released.
                self.owner["phase"] = "cleanup_pending"
                self.stop(manifest.run_id, command_owner=command_owner)
                raise
            if result.get("registration_error"):
                self.owner["phase"] = "cleanup_pending"
                self.stop(manifest.run_id, command_owner=command_owner)
                raise RuntimeError(f"Prima child registration failed: {result['registration_error']}")
            return dict(self.owner)

    def ready(self, run_id: str, report: dict, authorize):
        authorize()
        with self.lock:
            self._check_owner(run_id)
            if not self._owned_child_alive():
                raise ValueError("Rank exited before readiness")
            if self.owner["phase"] not in ("starting", "ready"):
                raise ValueError("Reconcile the rank before acknowledging readiness")
            expected = {k: self.owner[k] for k in ("runtime_id", "rank", "ngl", "layers", "manifest_digest")}
            if (
                any(report.get(k) != v for k, v in expected.items())
                or report.get("manifest_mode") is not True
            ):
                raise ValueError("Rank execution acknowledgement disagrees with the manifest")
            self.owner["phase"] = "ready"
            self._save()
            return dict(self.owner)

    def readiness_report(self, run_id: str, authorize, *, command_owner=None):
        """Read the owned child's post-initialization acknowledgement, never wire data."""
        authorize()
        with self.lock:
            self._check_owner(run_id)
            self._check_command_owner(command_owner)
            if not self._owned_child_alive():
                raise ValueError("Rank exited before model readiness")
            if self.owner["phase"] not in ("starting", "ready"):
                raise ValueError("Reconcile the rank before checking readiness")
            log = self.root / run_id / "rank.log"
            try:
                with log.open("rb") as handle:
                    data = handle.read(4 * 1024 * 1024 + 1)
            except FileNotFoundError:
                return None
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("Prima startup log exceeds the readiness limit")
            reports = []
            # An in-flight final line can be incomplete until the native fflush.
            for line in data.splitlines(keepends=True):
                if line.startswith(b"KLLM_PRIMA_RANK_READY=") and line.endswith(b"\n"):
                    try:
                        reports.append(json.loads(line.split(b"=", 1)[1]))
                    except ValueError as error:
                        raise ValueError("Malformed Prima rank readiness acknowledgement") from error
            if not reports:
                return None
            if len(reports) != 1 or not isinstance(reports[0], dict):
                raise ValueError("Duplicate or invalid Prima rank readiness acknowledgement")
            report = reports[0]
            expected = {k: self.owner[k] for k in ("runtime_id", "rank", "ngl", "layers", "manifest_digest")}
            expected.update(schema_version=1, manifest_mode=True, phase="ready",
                readiness_stage="server_request_loop" if self.owner["rank"] == 0 else "worker_decode_loop")
            if (any(report.get(k) != value for k, value in expected.items())
                    or any(type(report.get(k)) is not int for k in ("schema_version", "rank", "ngl", "layers"))
                    or report.get("manifest_mode") is not True):
                raise ValueError("Rank readiness acknowledgement disagrees with the manifest")
            authorize()
            return report

    def status(self, run_id=None, *, command_owner=None):
        with self.lock:
            if run_id is not None:
                self._check_owner(run_id)
            self._check_command_owner(command_owner)
            if self.owner and self.owner["phase"] in ("starting", "ready") and not self._owned_child_alive():
                self.owner["phase"] = "failed"
                self._save()
            return dict(self.owner) if self.owner else {"phase": "idle"}

    def renew(self, run_id: str, authorize, *, command_owner=None):
        authorize()
        with self.lock:
            self._check_owner(run_id)
            self._check_command_owner(command_owner)
            if not self._owned_child_alive():
                raise ValueError("Owned rank is no longer alive")
            self.deadline = self.clock() + LEASE_SECONDS
            return dict(self.owner)

    def stop(self, run_id: str, authorize=lambda: None, *, command_owner=None):
        authorize()
        with self.lock:
            self._check_owner(run_id)
            self._check_command_owner(command_owner)
            if not self.owner:
                return {"phase": "idle"}
            pid = self.owner["pid"]
            if self.alive(pid):
                try:
                    token = self._identity_or_exited(pid)
                except RuntimeError as error:
                    self.owner["phase"] = "cleanup_pending"
                    self._save()
                    raise ValueError("Rank process identity unavailable; cleanup retained") from error
                # A None token means the child exited between the liveness probe
                # and the identity read: there is no live process left to fence
                # or stop, so ownership is cleared below.
                if token is not None:
                    if token != self.owner["start_token"]:
                        self.owner["phase"] = "cleanup_pending"
                        self._save()
                        raise ValueError("Rank process identity changed; cleanup retained")
                    if not self.stop_process(pid) or self.alive(pid):
                        self.owner["phase"] = "cleanup_pending"
                        self._save()
                        raise ValueError("Owned rank has not stopped; cleanup retained")
            stopped = self.owner
            self.owner = None
            try:
                self._save()
            except BaseException:
                # The child is stopped, but a failed journal write may still
                # leave durable ownership that must fence another launch.
                self.owner = stopped
                self.owner["phase"] = "cleanup_pending"
                raise
            self.deadline = 0.0
            if self.reservation is not None:
                self.reservation.release()
                self.reservation = None
            return {"phase": "idle"}

    def expire(self):
        with self.lock:
            if self.owner and (
                self.owner["phase"] == "cleanup_pending" or self.clock() >= self.deadline
            ):
                return self.stop(self.owner["run_id"])
        return self.status()


class PrimaCoordinator:
    def __init__(self, root: Path, *, command, admit, release, check=lambda: None):
        self.root, self.command, self.admit, self.release = root, command, admit, release
        self.check = check
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.generation = 0
        self.active = None
        self.phase = "idle"
        self.command_owner = None
        self.started: list[str] = []
        self.misses = 0
        self.retries = 0
        self.record_path = root / "run.json"
        self._restore()

    def _restore(self):
        try:
            record = json.loads(self.record_path.read_text())
        except FileNotFoundError:
            return
        if (not isinstance(record, dict) or type(record.get("schema_version", 1)) is not int
                or record.get("schema_version", 1) != 1
                or record.get("phase") not in {"idle", "admitting", "starting", "serving",
                    "degraded", "recovering", "cleanup_pending", "failed"}):
            raise ValueError("Invalid persisted Prima coordinator ownership")
        if record["phase"] == "idle":
            if record.get("pending_nodes"):
                raise ValueError("Idle Prima record still owns pending ranks")
            return
        if record["phase"] == "failed" and record.get("manifest") is None:
            if record.get("pending_nodes") or record.get("command_owner") or record.get("manifest_digest"):
                raise ValueError("Failed Prima record has incomplete ownership")
            self.phase = "failed"
            return
        self.active = ExecutionManifest.model_validate(record["manifest"])
        if record.get("manifest_digest", self.active.digest) != self.active.digest:
            raise ValueError("Persisted Prima manifest identity changed")
        # Older records did not journal starts. Conservatively retain every rank;
        # a lost acknowledgement does not establish that a remote child was absent.
        pending = record.get("pending_nodes", [r.node_id for r in self.active.ranks])
        if (not isinstance(pending, list) or any(not isinstance(n, str) for n in pending)
                or len(set(pending)) != len(pending)
                or not set(pending) <= {r.node_id for r in self.active.ranks}):
            raise ValueError("Invalid persisted Prima cleanup participants")
        retries = record.get("recovery_attempts", 0)
        if type(retries) is not int or not 0 <= retries <= MAX_RECOVERY_ATTEMPTS:
            raise ValueError("Invalid persisted Prima recovery attempts")
        self.started, self.retries = pending, retries
        self.command_owner = record.get("command_owner")
        self._validate_command_owner(self.command_owner)
        self.cancelled.set()
        self.phase = "cleanup_pending"
        self._persist()

    @staticmethod
    def _validate_command_owner(owner):
        if owner is not None and (not isinstance(owner, dict)
                or set(owner) != {"sender", "authority_digest"}
                or not isinstance(owner["sender"], str) or not owner["sender"]
                or not isinstance(owner["authority_digest"], str)
                or len(owner["authority_digest"]) != 64
                or any(c not in "0123456789abcdef" for c in owner["authority_digest"])):
            raise ValueError("Invalid Prima coordinator command authority")

    def _persist(self):
        atomic_json(self.record_path, {"schema_version": 1,
            "manifest": self.active.model_dump() if self.active else None,
            "manifest_digest": self.active.digest if self.active else None,
            "phase": self.phase, "pending_nodes": list(self.started),
            "command_owner": self.command_owner, "recovery_attempts": self.retries})

    def _check(self, generation):
        if self.cancelled.is_set() or generation != self.generation:
            raise ValueError("Prima run cancelled")
        self.check()

    def _validate_result(self, rank, result):
        expected = {"run_id": self.active.run_id, "runtime_id": self.active.runtime_id,
            "manifest_digest": self.active.digest, "rank": rank.rank, "ngl": rank.ngl,
            "layers": sum(w.end - w.start for w in rank.layers),
            "backend": self.active.backends[rank.node_id]}
        if (not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items())
                or any(type(result.get(k)) is not int for k in ("rank", "ngl", "layers"))
                or (self.command_owner is not None and result.get("command_owner") != self.command_owner)):
            raise ValueError(f"{rank.node_id}: rank readiness identity/allocation disagrees with the manifest")

    def start(self, manifest: ExecutionManifest, *, command_owner=None):
        self._validate_command_owner(command_owner)
        generation = self.generation
        with self.lock:
            if generation != self.generation:
                raise ValueError("Prima run cancelled before startup")
            if self.active:
                raise ValueError("Stop the previous Prima run before starting another")
            self.cancelled.clear()
            self.command_owner = dict(command_owner) if command_owner is not None else None
            self.retries = self.misses = 0
            return self._start(manifest, generation)

    def _start(self, manifest, generation):
        from kllm.modules.inference.interactors import load_progress

        self.active = manifest
        self.started = []
        self.phase = "admitting"
        total_ranks = max(1, len(manifest.ranks))
        try:
            self._persist()
            self._check(generation)
            load_progress.update(
                fraction=0.08,
                phase="starting",
                detail="Admitting private Ring-TCP listener ports…",
            )
            self.admit(manifest.transfer_edges)
            ordered_ranks = manifest.ranks[1:] + manifest.ranks[:1]
            for idx, rank in enumerate(ordered_ranks, start=1):
                self._check(generation)
                load_progress.update(
                    fraction=0.12 + (idx / total_ranks) * 0.25,
                    phase="starting",
                    detail=f"Starting Ring-TCP rank {idx}/{total_ranks} ({rank.node_id})…",
                )
                self.started.append(rank.node_id)
                # Journal intent before sending. The remote may start even if its
                # response or the master process is lost immediately afterwards.
                self.phase = "starting"
                self._persist()
                result = self.command(rank.node_id, "prima_start", manifest)
                if result.get("phase") not in ("starting", "ready"):
                    raise ValueError(f"{rank.node_id}: rank startup refused")
                self._validate_result(rank, result)
            # Readiness is a barrier, never inferred from process presence.
            until = time.monotonic() + 300
            renewed = time.monotonic()
            last_ready_count = -1
            while time.monotonic() < until:
                self._check(generation)
                if time.monotonic() - renewed >= MONITOR_SECONDS:
                    for rank in manifest.ranks:
                        self._check(generation)
                        result = self.command(rank.node_id, "prima_renew", manifest)
                        self._validate_result(rank, result)
                    renewed = time.monotonic()
                results = []
                for rank in manifest.ranks:
                    self._check(generation)
                    result = self.command(rank.node_id, "prima_status", manifest)
                    if result.get("phase") not in ("starting", "ready"):
                        raise ValueError("A Prima rank failed before distributed readiness")
                    self._validate_result(rank, result)
                    results.append(result)
                ready_count = sum(1 for r in results if r["phase"] == "ready")
                if ready_count > last_ready_count:
                    if last_ready_count >= 0:
                        until = max(until, time.monotonic() + 300)
                    last_ready_count = ready_count
                load_progress.update(
                    fraction=0.40 + (ready_count / total_ranks) * 0.55,
                    phase="loading",
                    detail=f"Loading model weights across Ring-TCP ranks ({ready_count}/{total_ranks} ready)…",
                )
                if ready_count == len(results):
                    self._check(generation)
                    self.phase = "serving"
                    self.misses = 0
                    self._persist()
                    self._check(generation)
                    return {
                        "phase": "serving",
                        "run_id": manifest.run_id,
                        "manifest_digest": manifest.digest,
                    }
                time.sleep(0.1)
            raise ValueError("Distributed readiness timed out")
        except BaseException:
            self.cancelled.set()
            self._stop()
            raise

    def monitor_once(self):
        if not self.lock.acquire(blocking=False):
            return {"phase": self.phase}
        try:
            return self._monitor_once()
        finally:
            self.lock.release()

    def _monitor_once(self):
        if self.active is None:
            return {"phase": self.phase}
        if self.cancelled.is_set():
            # Cancellation can leave failed remote stops or admission release.
            # Finish that cleanup without starting another inference run.
            return self._stop()
        generation = self.generation
        try:
            self._check(generation)
        except Exception:
            self.cancelled.set()
            self._stop()
            raise
        healthy = True
        for rank in self.active.ranks:
            try:
                self._check(generation)
                result = self.command(rank.node_id, "prima_renew", self.active)
                self._validate_result(rank, result)
                healthy = healthy and result.get("phase") == "ready"
            except Exception:
                healthy = False
        try:
            self._check(generation)
        except Exception:
            self.cancelled.set()
            self._stop()
            raise
        self.misses = 0 if healthy else self.misses + 1
        if self.misses < RECOVERY_MISSES:
            self.phase = "serving" if healthy else "degraded"
            self._persist()
            return {"phase": self.phase}
        saved = self.active
        owner = self.command_owner
        self.phase = "recovering"
        self._persist()
        self._stop()
        self._check(generation)
        if self.retries >= MAX_RECOVERY_ATTEMPTS:
            self.phase = "failed"
            self._persist()
            return {"phase": "failed", "error": "Prima recovery exhausted; start a new run"}
        self.retries += 1
        self.command_owner = owner
        return self._start(saved, generation)

    def stop(self):
        self.cancelled.set()
        self.generation += 1
        with self.lock:
            return self._stop()

    def _stop(self):
        try:
            return self._cleanup()
        except BaseException:
            # Persistence failures are cleanup failures too. In particular, a
            # recovery must never restart after an unrecorded stop/release.
            self.cancelled.set()
            if self.active is not None:
                self.phase = "cleanup_pending"
            raise

    def _cleanup(self):
        if self.active is None:
            if self.phase != "idle":
                self.phase = "idle"
                self._persist()
            return {"phase": "idle"}
        self.phase = "cleanup_pending"
        self._persist()
        pending = []
        # Stop rank zero first to stop accepting inference, then workers.
        nodes = [r.node_id for r in self.active.ranks if r.node_id in self.started]
        for index, node in enumerate(nodes):
            try:
                if self.command(node, "prima_stop", self.active).get("phase") != "idle":
                    pending.append(node)
            except Exception:
                pending.append(node)
            # Each acknowledgement narrows durable ownership before the next call.
            self.started = pending + nodes[index + 1:]
            self._persist()
        self.started = pending
        self._persist()
        if pending:
            self.cancelled.set()
            raise ValueError("Prima cleanup pending on " + ", ".join(pending))
        try:
            self.release()
        except BaseException:
            self.cancelled.set()
            raise
        self.phase = "idle"
        self._persist()
        self.active = None
        self.command_owner = None
        return {"phase": "idle"}
