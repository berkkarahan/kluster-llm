"""Thread-safe aggregate progress for master-orchestrated worker updates."""

from __future__ import annotations

import threading
from typing import Any


class UpdateProgressStore:
    """Own the mutable worker progress snapshot and expose detached copies."""

    def __init__(self) -> None:
        self._progress: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            if self._progress is None:
                return None
            result = dict(self._progress)
            workers = self._progress.get("workers")
            if isinstance(workers, list):
                result["workers"] = [dict(worker) for worker in workers]
            return result

    def clear(self) -> None:
        with self._lock:
            self._progress = None

    def initialize(
        self,
        workers: list[dict[str, Any]],
        identity: dict[str, str | None],
    ) -> None:
        rows = [
            {
                "node_id": worker.get("node_id"),
                "host": worker.get("host"),
                "phase": "pending",
                "error": None,
                "git_head": None,
                "llama_release": None,
                "rebuild": None,
            }
            for worker in workers
        ]
        with self._lock:
            self._progress = {
                "phase": "fanout",
                "pct": 0,
                "target_git_head": identity.get("git_head"),
                "target_llama_release": identity.get("llama_release"),
                "workers": rows,
                "error": None,
            }

    def set_phase(self, phase: str, *, pct: int | None = None, error: str | None = None) -> None:
        with self._lock:
            if self._progress is None:
                return
            self._progress["phase"] = phase
            if pct is not None:
                self._progress["pct"] = pct
            if error is not None:
                self._progress["error"] = error

    def apply_worker(
        self,
        *,
        node_id: str | None,
        host: str | None = None,
        phase: str,
        error: str | None = None,
        git_head: str | None = None,
        llama_release: str | None = None,
        rebuild: str | None = None,
    ) -> dict[str, Any] | None:
        """Merge one worker report and return the detached aggregate state."""
        with self._lock:
            if self._progress is None:
                return None
            workers = self._progress.get("workers") or []
            index = next(
                (i for i, row in enumerate(workers) if node_id and row.get("node_id") == node_id),
                None,
            )
            if index is None and host:
                index = next((i for i, row in enumerate(workers) if row.get("host") == host), None)
            if index is None:
                workers.append(
                    {
                        "node_id": node_id,
                        "host": host,
                        "phase": phase,
                        "error": error,
                        "git_head": git_head,
                        "llama_release": llama_release,
                        "rebuild": rebuild,
                    }
                )
            else:
                row = dict(workers[index])
                row["phase"] = phase
                if error is not None:
                    row["error"] = error
                if git_head is not None:
                    row["git_head"] = git_head
                if llama_release is not None:
                    row["llama_release"] = llama_release
                if rebuild is not None:
                    row["rebuild"] = rebuild
                workers[index] = row
            self._progress["workers"] = workers

            done = sum(worker.get("phase") in ("done", "error") for worker in workers)
            self._progress["pct"] = int(100 * done / max(len(workers), 1))
            if workers and all(worker.get("phase") == "done" for worker in workers):
                self._progress["phase"] = "done"
            elif workers and all(worker.get("phase") in ("done", "error") for worker in workers):
                self._progress["phase"] = "error"
                errors = [
                    worker.get("error") for worker in workers if worker.get("phase") == "error"
                ]
                self._progress["error"] = (
                    "; ".join(err for err in errors if err) or "worker update failed"
                )
            elif any(worker.get("phase") not in ("pending", "done", "error") for worker in workers):
                self._progress["phase"] = "updating"

            result = dict(self._progress)
            result["workers"] = [dict(worker) for worker in workers]
            return result
