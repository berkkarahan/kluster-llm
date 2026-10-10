"""Explicit local profiling jobs with bounded output and owned process cleanup."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4
import psutil

from kllm.modules.primacpp.entities import DeviceProfile, ProfileRequest
from kllm.modules.primacpp.interactors.profiles import ProfileCache
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import atomic_json, file_digest

TERMINAL = {"prepared", "failed", "cancelled", "interrupted"}


def profiling_backend(runtime: dict, member: dict) -> str:
    available = str(runtime["backend"]).split("+")
    if len(available) == 1:
        return available[0]
    selected = member.get("actual_backend") or member.get("backend")
    if selected not in available:
        raise ValueError("Select an available Prima accelerator backend before profiling")
    return str(selected)


def gpu_profile_gflops(metrics: dict, backend: str) -> float | None:
    if backend == "cpu":
        return None
    field = {
        "metal": "metal_q4k_gflops",
        "cuda": "cuda_q4k_gflops",
        "vulkan": "vulkan_q4k_gflops",
        "rocm": "rocm_q4k_gflops",
    }.get(backend)
    value = metrics.get(field) if field else None
    if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{backend} GPU profiling is unavailable in this Prima runtime; "
            "another accelerator or CPU measurement cannot substitute for it"
        )
    return float(value)


class ProfileJobs:
    def __init__(self, root: Path, capture, resolve_model, state, kill):
        self.root, self.capture, self.resolve_model = root, capture, resolve_model
        self.state, self.kill = state, kill
        self.cache = ProfileCache(root / "cache")
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.cancelled: set[str] = set()
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prima-profile")
        try:
            self.jobs = json.loads((root / "jobs.json").read_text())
        except (OSError, ValueError):
            self.jobs = {}
        for job in self.jobs.values():
            if job["phase"] not in TERMINAL:
                pid = job.get("pid")
                if pid:
                    try:
                        process = psutil.Process(pid)
                        if process.create_time() == job.get("process_created_at"):
                            self.kill(process)
                    except psutil.NoSuchProcess:
                        pass
                job.update(phase="interrupted", error="Agent stopped; start a new profile job")

    def _save(self):
        atomic_json(self.root / "jobs.json", self.jobs)

    def start(self, request: ProfileRequest) -> dict:
        # Verify before persisting any work. Profiling must not disturb a serving process.
        runtime = self.capture(request.runtime_id)
        if runtime.get("engine") != "primacpp":
            raise ValueError("Profiling requires a prepared Prima runtime")
        model = self.resolve_model(request.model)
        if self.state().process_pid:
            raise ValueError("Stop local inference before profiling; the active run is retained")
        with self.lock:
            if self.closed:
                raise ValueError("Profiler is shutting down")
            digest = body_digest(request.model_dump())
            prior = next(
                (j for j in self.jobs.values() if j["request_id"] == request.request_id), None
            )
            if prior:
                if prior["request_digest"] != digest:
                    raise ValueError("Profile request ID reused with different inputs")
                return deepcopy(prior)
            if any(j["phase"] not in TERMINAL for j in self.jobs.values()):
                raise ValueError("A Prima profile job is already active on this node")
            identity = str(uuid4())
            self.jobs[identity] = {
                "job_id": identity,
                "request_id": request.request_id,
                "request_digest": digest,
                "runtime_id": request.runtime_id,
                "phase": "queued",
                "error": None,
                "created_at": time.time(),
            }
            self._save()
            self.pool.submit(self._run, identity, request, runtime, model)
            return deepcopy(self.jobs[identity])

    def job(self, identity: str) -> dict:
        with self.lock:
            return deepcopy(self.jobs[identity])

    def cancel(self, identity: str) -> dict:
        with self.lock:
            job = self.jobs[identity]
            if job["phase"] not in TERMINAL:
                self.cancelled.add(identity)
                process = self.processes.get(identity)
                if process:
                    self.kill(process)
                job.update(phase="cancelled", finished_at=time.time())
                self._save()
            return deepcopy(job)

    def _check(self, identity: str):
        if identity in self.cancelled or self.closed:
            raise ValueError("Profile job cancelled")
        if self.state().process_pid:
            raise ValueError("Inference started during profiling; profile job stopped")

    def _run(self, identity: str, request: ProfileRequest, runtime: dict, model: Path):
        process = None
        try:
            self._check(identity)
            # Hashing preserves a provenance check across changes during the profile.
            initial = (model.stat().st_size, model.stat().st_mtime_ns)
            digest = file_digest(model)
            member = deepcopy(self.state().members.get(self.state().node_id, {}))
            hardware = {
                key: member.get(key) for key in ("devices", "backend", "total_ram_bytes", "gpu_ids")
            }
            settings = {"context": request.context, "threads": request.threads}
            directory = self.root / identity
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / "profile.log"
            self._check(identity)
            with output.open("w") as log:
                process = subprocess.Popen(
                    [
                        runtime["profile_tool"],
                        "-m",
                        str(model),
                        "-c",
                        str(request.context),
                        "-t",
                        str(request.threads),
                        "-n",
                        "16",
                    ],
                    cwd=directory,
                    env={**os.environ, **runtime.get("runtime_env", {}), "KLLM_PRIMA_PROFILE_JSON": "1"},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
                with self.lock:
                    self.processes[identity] = process
                    self._check(identity)
                    try:
                        created = psutil.Process(process.pid).create_time()
                    except psutil.NoSuchProcess:
                        created = None
                    self.jobs[identity].update(
                        phase="profiling",
                        pid=process.pid,
                        process_created_at=created,
                    )
                    self._save()
                until = time.monotonic() + 600
                while process.poll() is None:
                    self._check(identity)
                    if time.monotonic() >= until or output.stat().st_size > 1024 * 1024:
                        raise ValueError("Prima profile exceeded its time or output limit")
                    time.sleep(0.1)
            if process.returncode:
                raise ValueError("Prima profiler failed; inspect this profile job's log")
            if initial != (model.stat().st_size, model.stat().st_mtime_ns):
                raise ValueError("Model changed during profiling; discard this measurement")
            raw = next(
                (
                    line.split("=", 1)[1]
                    for line in output.read_text().splitlines()
                    if line.startswith("KLLM_PRIMA_PROFILE=")
                ),
                None,
            )
            if raw is None:
                raise ValueError("Prima profiler did not produce structured output")
            metrics = json.loads(raw)
            numeric = [
                metrics.get(key)
                for key in ("cpu_q4k_gflops", "layer_q4k_flops", "memory_available_gib")
            ]
            if metrics.get("schema_version") != 1 or any(
                not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in numeric
            ):
                raise ValueError("Prima profiler returned incomplete or invalid measurements")
            # Kernel-based Q4K estimates are not end-to-end decode benchmarks.
            backend = profiling_backend(runtime, member)
            gpu = gpu_profile_gflops(metrics, backend)
            profile = DeviceProfile(
                runtime_id=request.runtime_id,
                model_sha256=digest,
                node_id=self.state().node_id,
                hardware_digest=body_digest(hardware),
                settings_digest=body_digest(settings),
                backend=backend,
                measured_at=time.time(),
                cpu_layer_ms=metrics["layer_q4k_flops"] / (metrics["cpu_q4k_gflops"] * 1e6),
                gpu_layer_ms=metrics["layer_q4k_flops"] / (gpu * 1e6) if gpu is not None else None,
                memory_available_bytes=int(metrics["memory_available_gib"] * 1024**3),
            )
            self._check(identity)
            key = self.cache.put(profile)
            with self.lock:
                self.jobs[identity].update(
                    phase="prepared",
                    finished_at=time.time(),
                    profile_key=key,
                    profile=profile.model_dump(),
                    measurement_kind="Q4K kernel estimate",
                    metrics=metrics,
                )
                self._save()
        except Exception as error:
            with self.lock:
                if identity not in self.cancelled:
                    self.jobs[identity].update(
                        phase="failed", error=str(error)[:1000], finished_at=time.time()
                    )
                    self._save()
        finally:
            if process and process.poll() is None:
                self.kill(process)
                process.wait(timeout=10)
            with self.lock:
                self.processes.pop(identity, None)

    def logs(self, identity: str) -> str:
        self.job(identity)  # Don't accept an arbitrary path segment.
        path = self.root / identity / "profile.log"
        if not path.exists():
            return ""
        with path.open("rb") as source:
            source.seek(max(0, path.stat().st_size - 65536))
            return source.read(65536).decode("utf-8", errors="replace")

    def close(self):
        for identity in list(self.jobs):
            self.cancel(identity)
        self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
