"""Owned Prima rank supervision, independent of normal runtime selection."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import UUID

from kllm.modules.primacpp.interactors.execution import (
    ExecutionManifest,
    require_execution_capabilities,
)
from kllm.modules.runtimes.interactors.registry import atomic_json

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
        self.owner = None
        self.deadline = 0.0
        self.record_path = root / "rank.json"
        try:
            import json

            self.owner = json.loads(self.record_path.read_text())
            if self.owner:
                self.owner["phase"] = "cleanup_pending"
        except FileNotFoundError:
            pass

    def _save(self):
        atomic_json(self.record_path, self.owner)

    def _check_owner(self, run_id: str):
        UUID(run_id)
        if self.owner and self.owner["run_id"] != run_id:
            raise ValueError("Rank is owned by a different run")

    def _owned_child_alive(self):
        if not self.owner or not self.alive(self.owner["pid"]):
            return False
        if self.identity(self.owner["pid"]) != self.owner["start_token"]:
            self.owner["phase"] = "cleanup_pending"
            self._save()
            raise ValueError("Rank process identity changed; cleanup retained")
        return True

    def start(self, manifest: ExecutionManifest, model: Path, authorize, *, assessment=False):
        # Production callers cannot opt into assessment; the isolated harness owns that composition.
        authorize()
        runtime = self.capture(manifest.runtime_id)
        if not assessment:
            from kllm.modules.primacpp.interactors.recipe import require_qualified

            require_qualified(runtime)
        require_execution_capabilities(manifest, runtime.get("capabilities", {}))
        rank = next((r for r in manifest.ranks if r.node_id == self.node_id), None)
        if rank is None:
            raise ValueError("This node is not a manifest participant")
        backend = manifest.backends[self.node_id]
        if rank.ngl and backend not in runtime.get("capabilities", {}).get("backends", []):
            raise ValueError("Requested rank GPU backend is unavailable")
        with self.lock:
            self._check_owner(manifest.run_id)
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
            result = self.spawn(
                runtime, rank, model, manifest_path, directory / "rank.log", manifest.settings
            )
            self.owner = {
                "run_id": manifest.run_id,
                "manifest_digest": manifest.digest,
                "runtime_id": manifest.runtime_id,
                "pid": result["pid"],
                "start_token": result["start_token"],
                "phase": "starting",
                "rank": rank.rank,
                "ngl": rank.ngl,
                "backend": backend,
            }
            self.deadline = self.clock() + LEASE_SECONDS
            self._save()
            return dict(self.owner)

    def ready(self, run_id: str, report: dict, authorize):
        authorize()
        with self.lock:
            self._check_owner(run_id)
            if not self._owned_child_alive():
                raise ValueError("Rank exited before readiness")
            expected = {k: self.owner[k] for k in ("runtime_id", "rank", "ngl", "manifest_digest")}
            if (
                any(report.get(k) != v for k, v in expected.items())
                or report.get("manifest_mode") is not True
            ):
                raise ValueError("Rank execution acknowledgement disagrees with the manifest")
            self.owner["phase"] = "ready"
            self._save()
            return dict(self.owner)

    def status(self):
        with self.lock:
            return dict(self.owner) if self.owner else {"phase": "idle"}

    def renew(self, run_id: str, authorize):
        authorize()
        with self.lock:
            self._check_owner(run_id)
            if not self._owned_child_alive():
                raise ValueError("Owned rank is no longer alive")
            self.deadline = self.clock() + LEASE_SECONDS
            return dict(self.owner)

    def stop(self, run_id: str, authorize=lambda: None):
        authorize()
        with self.lock:
            self._check_owner(run_id)
            if not self.owner:
                return {"phase": "idle"}
            pid = self.owner["pid"]
            if self.alive(pid):
                if self.identity(pid) != self.owner["start_token"]:
                    self.owner["phase"] = "cleanup_pending"
                    self._save()
                    raise ValueError("Rank process identity changed; cleanup retained")
                if not self.stop_process(pid) or self.alive(pid):
                    self.owner["phase"] = "cleanup_pending"
                    self._save()
                    raise ValueError("Owned rank has not stopped; cleanup retained")
            self.owner = None
            self.deadline = 0.0
            self._save()
            return {"phase": "idle"}

    def expire(self):
        with self.lock:
            if self.owner and (
                self.owner["phase"] == "cleanup_pending" or self.clock() >= self.deadline
            ):
                return self.stop(self.owner["run_id"])
        return self.status()


class PrimaCoordinator:
    def __init__(self, root: Path, *, command, admit, release):
        self.root, self.command, self.admit, self.release = root, command, admit, release
        self.cancelled = threading.Event()
        self.active = None
        self.started: list[str] = []
        self.misses = 0
        self.retries = 0

    def start(self, manifest: ExecutionManifest):
        if self.active:
            raise ValueError("Stop the previous Prima run before starting another")
        self.active = manifest
        self.cancelled.clear()
        atomic_json(
            self.root / "run.json", {"manifest": manifest.model_dump(), "phase": "admitting"}
        )
        try:
            self.admit(manifest.transfer_edges)
            for rank in manifest.ranks[1:] + manifest.ranks[:1]:
                if self.cancelled.is_set():
                    raise ValueError("Prima run cancelled")
                self.started.append(rank.node_id)
                result = self.command(rank.node_id, "prima_start", manifest)
                if result.get("phase") not in ("starting", "ready"):
                    raise ValueError(f"{rank.node_id}: rank startup refused")
            # Readiness is a barrier, never inferred from process presence.
            until = time.monotonic() + 300
            renewed = time.monotonic()
            while time.monotonic() < until:
                if self.cancelled.is_set():
                    raise ValueError("Prima run cancelled")
                if time.monotonic() - renewed >= MONITOR_SECONDS:
                    for rank in manifest.ranks:
                        self.command(rank.node_id, "prima_renew", manifest)
                    renewed = time.monotonic()
                results = [
                    self.command(r.node_id, "prima_status", manifest) for r in manifest.ranks
                ]
                if all(
                    r.get("phase") == "ready" and r.get("manifest_digest") == manifest.digest
                    for r in results
                ):
                    atomic_json(
                        self.root / "run.json",
                        {"manifest": manifest.model_dump(), "phase": "serving"},
                    )
                    return {
                        "phase": "serving",
                        "run_id": manifest.run_id,
                        "manifest_digest": manifest.digest,
                    }
                if any(r.get("phase") in ("failed", "cleanup_pending", "idle") for r in results):
                    raise ValueError("A Prima rank failed before distributed readiness")
                time.sleep(0.1)
            raise ValueError("Distributed readiness timed out")
        except BaseException:
            self.stop()
            raise

    def monitor_once(self):
        if self.active is None or self.cancelled.is_set():
            return {"phase": "idle"}
        healthy = True
        for rank in self.active.ranks:
            try:
                result = self.command(rank.node_id, "prima_renew", self.active)
                healthy = (
                    healthy
                    and result.get("phase") == "ready"
                    and result.get("manifest_digest") == self.active.digest
                )
            except Exception:
                healthy = False
        self.misses = 0 if healthy else self.misses + 1
        if self.misses < RECOVERY_MISSES:
            return {"phase": "serving" if healthy else "degraded"}
        saved = self.active
        self.stop()
        if self.retries >= MAX_RECOVERY_ATTEMPTS:
            return {"phase": "failed", "error": "Prima recovery exhausted; start a new run"}
        self.retries += 1
        self.cancelled.clear()
        return self.start(saved)

    def stop(self):
        self.cancelled.set()
        if self.active is None:
            return {"phase": "idle"}
        pending = []
        # Stop rank zero first to stop accepting inference, then workers.
        for node in [r.node_id for r in self.active.ranks if r.node_id in self.started]:
            try:
                if self.command(node, "prima_stop", self.active).get("phase") != "idle":
                    pending.append(node)
            except Exception:
                pending.append(node)
        self.started = pending
        atomic_json(
            self.root / "run.json",
            {
                "manifest": self.active.model_dump(),
                "phase": "cleanup_pending" if pending else "idle",
            },
        )
        if pending:
            raise ValueError("Prima cleanup pending on " + ", ".join(pending))
        self.release()
        self.active = None
        return {"phase": "idle"}
