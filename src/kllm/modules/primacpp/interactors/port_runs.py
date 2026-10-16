"""Node-local native listener intent, leases and owned cleanup."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from kllm.modules.primacpp.interactors.execution import ExecutionManifest
from kllm.modules.primacpp.interactors.lifecycle import LEASE_SECONDS, PrimaCoordinator
from kllm.modules.runtimes.interactors.registry import atomic_json
from kllm.shared.runtime_resources import prima_scope

ADMISSION_LEASE_SECONDS = 600


class PortSupervisor:
    def __init__(self, path: Path, *, platform, rank, clock=time.monotonic, resources=None):
        self.path, self.platform, self.rank, self.clock = path, platform, rank, clock
        self.lock = threading.RLock()
        self.resources, self.reservation = resources, None
        self.owner = None
        self.deadline = 0.0
        try:
            record = json.loads(path.read_text())
        except FileNotFoundError:
            return
        if record is None:
            return
        if (
            not isinstance(record, dict)
            or set(record) != {"manifest", "manifest_digest", "command_owner", "phase"}
            or record["phase"] not in {"applying", "admitted", "cleanup_pending"}
        ):
            raise ValueError("Invalid persisted Prima listener ownership")
        manifest = ExecutionManifest.model_validate(record["manifest"])
        if manifest.digest != record["manifest_digest"]:
            raise ValueError("Persisted Prima listener manifest changed")
        PrimaCoordinator._validate_command_owner(record["command_owner"])
        if record["command_owner"] is None:
            raise ValueError("Prima listener ownership lacks signed authority")
        self.owner = {**record, "phase": "cleanup_pending"}
        self._save()
        if self.resources is not None:
            self.reservation = self.resources.acquire(
                prima_scope(manifest.run_id, record["command_owner"], manifest.digest), "listener"
            )

    def _save(self):
        atomic_json(self.path, self.owner)

    def capability(self):
        platform = self.platform()
        available = (
            self.owner is None
            and all(
                hasattr(platform, name)
                for name in (
                    "apply_prima_ports",
                    "renew_prima_ports",
                    "prima_ports_status",
                    "release_prima_ports",
                    "prima_ports_available",
                )
            )
            and platform.prima_ports_available() is True
        )
        return {
            "version": 2,
            "available": bool(available),
            "cleanup_pending": self.owner is not None,
        }

    def _owned(self, manifest, command_owner=None):
        if self.owner is None:
            raise ValueError("Prima listener admission is absent")
        if self.owner["manifest_digest"] != manifest.digest:
            raise ValueError("Prima listeners are owned by another manifest/run")
        if command_owner is not None and self.owner["command_owner"] != command_owner:
            raise ValueError("Prima listener command authority differs from its owner")

    def admit(self, manifest, command_owner, apply, check):
        with self.lock:
            check()
            PrimaCoordinator._validate_command_owner(command_owner)
            if command_owner is None or self.owner is not None:
                raise ValueError(
                    "Previous Prima listener ownership must be reconciled before admission"
                )
            if self.resources is not None:
                self.reservation = self.resources.acquire(prima_scope(manifest.run_id, command_owner, manifest.digest), "listener")
            self.owner = {
                "manifest": manifest.model_dump(),
                "manifest_digest": manifest.digest,
                "command_owner": dict(command_owner),
                "phase": "applying",
            }
            try:
                self._save()  # A lost helper reply can still leave native rules behind.
            except BaseException:
                # The helper has not run yet. Only discard the claim when the
                # journal definitely contains no owner; an atomic replacement
                # may have succeeded even if its final cleanup raised.
                try:
                    persisted = json.loads(self.path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    persisted = None
                except (OSError, UnicodeError, ValueError):
                    persisted = object()
                if persisted is None:
                    self.owner = None
                    if self.reservation is not None:
                        self.reservation.release()
                        self.reservation = None
                raise
            try:
                result = apply()
                check()
                if result != {"phase": "admitted", "manifest_digest": manifest.digest}:
                    raise ValueError("Prima helper did not acknowledge the exact listener manifest")
                self.owner["phase"] = "admitted"
                # Bound the pre-spawn barrier separately from running rank leases.
                self.deadline = self.clock() + ADMISSION_LEASE_SECONDS
                self._save()
                return result
            except BaseException:
                self.owner["phase"] = "cleanup_pending"
                self._save()
                raise

    def validate(self, manifest, command_owner=None):
        with self.lock:
            self._owned(manifest, command_owner)
            if self.owner["phase"] != "admitted" or self.clock() >= self.deadline:
                raise ValueError("Prima listener admission expired or requires cleanup")
            platform = self.platform()
            result = platform.prima_ports_status(manifest.network_id, manifest.run_id)
            if result != {"phase": "admitted", "manifest_digest": manifest.digest}:
                raise ValueError("Prima listener ownership changed")
            return result

    def renew(self, manifest, command_owner=None, native_renew=None):
        with self.lock:
            result = self.validate(manifest, command_owner)
            if native_renew is not None:
                result = native_renew()
                if result != {"phase": "admitted", "manifest_digest": manifest.digest}:
                    raise ValueError("Prima native lease renewal changed ownership")
            self.deadline = self.clock() + LEASE_SECONDS
            return result

    def launch(self, manifest, command_owner, start, check):
        # Expiry/release and the validation-to-spawn interval share this lock.
        with self.lock:
            self.validate(manifest, command_owner)
            try:
                result = start()
                check()
                self.renew(manifest, command_owner)
                return result
            except BaseException:
                self.owner["phase"] = "cleanup_pending"
                self._save()
                rank = self.rank()
                if rank.owner:
                    rank.stop(manifest.run_id, command_owner=command_owner)
                self.release(manifest.network_id, manifest.run_id, command_owner)
                raise

    def release(self, network_id, run_id, command_owner=None):
        with self.lock:
            if self.owner is not None:
                manifest = ExecutionManifest.model_validate(self.owner["manifest"])
                if manifest.network_id != network_id or manifest.run_id != run_id:
                    raise ValueError("Prima listener cleanup does not own this run/network")
                self._owned(manifest, command_owner)
                self.owner["phase"] = "cleanup_pending"
                self._save()
            rank = self.rank()
            if rank.owner and rank.owner["run_id"] == run_id:
                raise ValueError("Stop the owned Prima rank before releasing its listeners")
            result = self.platform().release_prima_ports(network_id, run_id)
            if result != {"phase": "released"}:
                raise ValueError("Prima native listeners have not drained")
            drained = self.owner
            self.owner = None
            try:
                self._save()
            except BaseException:
                self.owner = drained
                raise
            self.deadline = 0.0
            if self.reservation is not None:
                self.reservation.release()
                self.reservation = None
            return result

    def expire(self):
        with self.lock:
            if self.owner is None or (
                self.owner["phase"] == "admitted" and self.clock() < self.deadline
            ):
                return
            self.owner["phase"] = "cleanup_pending"
            self._save()
            manifest = ExecutionManifest.model_validate(self.owner["manifest"])
            rank = self.rank()
            if rank.owner:
                rank.stop(manifest.run_id, command_owner=self.owner["command_owner"])
            return self.release(manifest.network_id, manifest.run_id)

    def _recovery_owner(self, network_id, run_id):
        if self.owner is not None:
            manifest = ExecutionManifest.model_validate(self.owner["manifest"])
            if manifest.network_id != network_id or manifest.run_id != run_id:
                raise ValueError("Prima listener recovery does not own this run/network")
            if self.owner["phase"] != "cleanup_pending" and self.clock() < self.deadline:
                raise ValueError("Prima listener admission requires cleanup before local recovery")
        rank = self.rank()
        if rank.owner and rank.owner["run_id"] == run_id:
            raise ValueError("Stop the owned Prima rank before resolving its listeners")

    def review(self, network_id, run_id):
        with self.lock:
            self._recovery_owner(network_id, run_id)
            return self.platform().review_prima_ports(network_id, run_id)

    def resolve(self, network_id, run_id, decision, snapshot_digest):
        with self.lock:
            self._recovery_owner(network_id, run_id)
            if self.owner is not None:
                self.owner["phase"] = "cleanup_pending"
                self._save()
            result = self.platform().resolve_prima_ports(network_id, run_id, decision, snapshot_digest)
            if result != {"phase": "released", "decision": decision}:
                raise ValueError("Prima helper did not acknowledge the exact local recovery decision")
            previous = self.owner
            self.owner = None
            try:
                self._save()
            except BaseException:
                self.owner = previous
                raise
            self.deadline = 0.0
            if self.reservation is not None:
                self.reservation.release()
                self.reservation = None
            return result
