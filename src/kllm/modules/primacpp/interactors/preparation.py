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
from kllm.modules.runtimes.interactors.registry import atomic_json
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices, vulkan_environment
from kllm.modules.primacpp.interactors.kernel_profile import (decode_kernel_profile,
    kernel_settings, kernel_hardware, storage_identity, require_vulkan_profile_device)
from kllm.modules.primacpp.interactors.qualification import execution_host
from kllm.shared.runtime_resources import node_resources, prima_scope

from kllm.modules.primacpp.interactors.preparation_metrics import (
    ProgressWindow, diagnostics_scope, digest_file, measure,
)

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


def get_profile_jobs(registry, state, resolve_model, *, busy=None):
    """One node-owned profiler/cache for HTTP jobs, prepared assets and planning."""
    with registry.lock:
        service = getattr(registry, "prima_profiles", None)
        if service is None or service.closed:
            service = ProfileJobs(registry.root / "prima-profiles", registry.capture,
                                  resolve_model, state, registry._kill, busy=busy or (lambda: False),
                                  resources=node_resources(registry))
            registry.prima_profiles = service
        elif busy is not None:
            service.busy = busy
            service.resolve_model = resolve_model
        return service


class ProfileJobs:
    def __init__(self, root: Path, capture, resolve_model, state, kill, *, busy=lambda: False, resources=None):
        self.root, self.capture, self.resolve_model = root, capture, resolve_model
        self.state, self.kill = state, kill
        self.busy = busy
        self.resources = resources
        self.reservations = {}
        self.futures = {}
        self.cache = ProfileCache(root / "cache")
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.cancelled: set[str] = set()
        self.checks = {}
        self._progress_windows = {}
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prima-profile")
        try:
            self.jobs = json.loads((root / "jobs.json").read_text())
        except (OSError, ValueError):
            self.jobs = {}
        interrupted = False
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
                job.update(phase="interrupted", error="Agent stopped; start a new profile job",
                           finished_at=time.time())
                interrupted = True
        if interrupted:
            self._save()

    def _save(self):
        with self.lock, measure("profile_checkpoint") as counts:
            atomic_json(self.root / "jobs.json", self.jobs)
            counts["publications"] = 1

    def _update_progress(
        self,
        identity: str,
        *,
        phase: str,
        label: str,
        fraction: float,
        bytes_done: int = 0,
        bytes_total: int = 0,
        save: bool = False,
        stage: str = "",
    ) -> None:
        clamped = max(0.0, min(1.0, float(fraction)))
        with self.lock:
            job = self.jobs.get(identity)
            if not job or (job["phase"] in TERMINAL and phase != job["phase"]):
                return
            if identity in self.cancelled and phase != "cancelled":
                return
            window = self._progress_windows.setdefault(identity, ProgressWindow())
            if window.update((phase, stage, label), bytes_done, bytes_total, time.monotonic(),
                             force=save or phase in TERMINAL) is None:
                return
            job["progress"] = {
                "phase": phase,
                "stage": stage or phase,
                "label": label,
                "fraction": round(clamped, 4),
                "pct": round(clamped * 100.0, 1),
                "bytes_done": max(0, int(bytes_done)),
                "bytes_total": max(0, int(bytes_total)),
                "updated_at": time.time(),
            }
            if phase in TERMINAL:
                self._progress_windows.pop(identity, None)
            if save:
                self._save()

    def start(self, request: ProfileRequest, *, control_owner=None, check=None, resolve_model=None) -> dict:
        # Verify before persisting any work. Profiling must not disturb a serving process.
        runtime = self.capture(request.runtime_id)
        if runtime.get("engine") != "primacpp":
            raise ValueError("Profiling requires a prepared Prima runtime")
        if request.mode == "generation":
            from kllm.modules.primacpp.interactors.model_structure import require_model_inspection
            require_model_inspection(runtime)
        if check is not None:
            check()
        if self.state().process_pid:
            raise ValueError("Stop local inference before profiling; the active run is retained")
        with self.lock:
            if self.busy():
                raise ValueError("Prima inference is starting or active; stop it before profiling")
            if self.closed:
                raise ValueError("Profiler is shutting down")
            digest = body_digest(request.model_dump())
            prior = next(
                (j for j in self.jobs.values() if j["request_id"] == request.request_id), None
            )
            if prior:
                if prior["request_digest"] != digest or prior.get("control_owner") != control_owner:
                    raise ValueError("Profile request ID reused with different inputs")
                return deepcopy(prior)
            if any(j["phase"] not in TERMINAL for j in self.jobs.values()):
                raise ValueError("A Prima profile job is already active on this node")
            identity = str(uuid4())
            lease = None
            if self.resources is not None:
                scope = prima_scope(control_owner["operation_id"], control_owner, control_owner["manifest_digest"]) if control_owner else (
                    "primacpp", identity, "", digest)
                lease = self.resources.acquire(scope, "profile")
                self.reservations[identity] = lease
            diagnostic_job = {}
            try:
                with diagnostics_scope(diagnostic_job, self.lock, check or (lambda: None), self.state().node_id):
                    with measure("profile_model_layout"):
                        model = (resolve_model or self.resolve_model)(request.model)
            except BaseException:
                if lease is not None:
                    lease.release()
                    self.reservations.pop(identity, None)
                raise
            now = time.time()
            self.jobs[identity] = {
                "job_id": identity,
                "request_id": request.request_id,
                "request_digest": digest,
                "runtime_id": request.runtime_id,
                "phase": "queued",
                "error": None,
                "created_at": now,
                "control_owner": deepcopy(control_owner),
                **diagnostic_job,
                "progress": {
                    "phase": "queued",
                    "label": "Queued for device profiling…",
                    "fraction": 0.0,
                    "pct": 0.0,
                    "bytes_done": 0,
                    "bytes_total": 0,
                    "updated_at": now,
                },
            }
            if check is not None:
                self.checks[identity] = check
            try:
                self._save()
                future = self.pool.submit(self._run, identity, request, runtime, model)
                self.futures[identity] = future
                future.add_done_callback(lambda _: self._settle_reservation(identity))
            except BaseException:
                self.jobs.pop(identity, None)
                self.checks.pop(identity, None)
                if lease is not None:
                    lease.release()
                    self.reservations.pop(identity, None)
                raise
            return deepcopy(self.jobs[identity])

    def _settle_reservation(self, identity):
        with self.lock:
            # A completed/cancelled future does not prove its child stopped if
            # cleanup raised. Keep that reservation while ownership is pending.
            if identity in self.processes:
                return
            future = self.futures.get(identity)
            if future is not None and not future.done():
                return
            self.futures.pop(identity, None)
            self._progress_windows.pop(identity, None)
            lease = self.reservations.pop(identity, None)
            if lease is not None:
                lease.release()

    def job(self, identity: str) -> dict:
        with self.lock:
            result = deepcopy(self.jobs[identity])
            future = self.futures.get(identity)
            if result["phase"] == "prepared" and future is not None and not future.done():
                result["phase"] = "settling"
            return result

    def cancel(self, identity: str) -> dict:
        with self.lock:
            job = self.jobs[identity]
            if job["phase"] not in TERMINAL or identity in self.processes:
                self.cancelled.add(identity)
                process = self.processes.get(identity)
                if process and process.poll() is None:
                    try:
                        self.kill(process)
                        process.wait(timeout=10)
                    except BaseException:
                        job.update(phase="cleanup_pending", error="Owned profile process cleanup is pending")
                        self._save()
                        raise
                future = self.futures.get(identity)
                if future is None or future.done():
                    self.processes.pop(identity, None)
                job.update(phase="cancelled", finished_at=time.time())
                self._update_progress(identity, phase="cancelled", label="Profiling cancelled", fraction=0.0)
                self._save()
                self._settle_reservation(identity)
            return deepcopy(job)

    def _check(self, identity: str):
        if identity in self.cancelled or self.closed:
            raise ValueError("Profile job cancelled")
        if self.state().process_pid:
            raise ValueError("Inference started during profiling; profile job stopped")
        if self.busy():
            raise ValueError("Prima inference is starting; profile job stopped")
        if identity in self.checks:
            self.checks[identity]()
        if identity in self.cancelled or self.closed:
            raise ValueError("Profile job cancelled")

    def _run(self, identity: str, request: ProfileRequest, runtime: dict, model: Path):
        with diagnostics_scope(self.jobs[identity], self.lock, lambda: self._check(identity), self.state().node_id):
            return self._run_owned(identity, request, runtime, model)

    def _run_owned(self, identity: str, request: ProfileRequest, runtime: dict, model: Path):
        process = None
        validation_started = None
        try:
            self._check(identity)
            if request.mode == "generation":
                self._run_generation(identity, request, runtime, model)
                return
            # Hashing preserves a provenance check across changes during the profile.
            initial = (model.stat().st_size, model.stat().st_mtime_ns)
            model_size = initial[0]
            self._update_progress(
                identity,
                phase="profiling",
                label="Verifying model digest…",
                fraction=0.08,
                bytes_done=0,
                bytes_total=model_size,
            )
            storage = storage_identity(model)
            digest = self._digest_model(identity, model)
            self._update_progress(
                identity,
                phase="profiling",
                label="Probing device capabilities…",
                fraction=0.20,
                bytes_done=0,
                bytes_total=0,
            )
            member = deepcopy(self.state().members.get(self.state().node_id, {}))
            hardware = {
                key: member.get(key) for key in ("devices", "backend", "total_ram_bytes", "gpu_ids")
            }
            settings = {"context": request.context, "threads": request.threads}
            backend = request.backend or profiling_backend(runtime, member)
            if backend != "cpu" and backend not in str(runtime["backend"]).split("+"):
                raise ValueError("Requested kernel profile backend is unavailable")
            selected_devices = []
            host = execution_host()
            profile_environment = {**os.environ, **runtime.get("runtime_env", {}), "KLLM_PRIMA_PROFILE_JSON": "1",
                                   "KLLM_PRIMA_PROFILE_BACKEND": backend}
            if backend == "vulkan":
                features = set(runtime.get("capabilities", {}).get("features", []))
                if not {"vulkan_device_identity_v1", "vulkan_profile_device_identity_v1"} <= features:
                    raise ValueError("Upgrade Prima for physical Vulkan profiler device evidence")
                selected_devices = select_vulkan_devices(discover_vulkan_devices(runtime), request.devices)
                if len(selected_devices) != 1:
                    raise ValueError("Profile one physical Vulkan device per measurement")
                profile_environment.update(vulkan_environment(selected_devices))
                hardware["selected_devices"] = selected_devices
                settings["devices"] = [d["device_identity"] for d in selected_devices]
            directory = self.root / identity
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / "profile.log"
            self._check(identity)
            with measure("native_kernel_profiler"), output.open("w") as log:
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
                    env=profile_environment,
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
                    self._update_progress(
                        identity,
                        phase="profiling",
                        label=f"Profiling {backend.upper()} device kernels…",
                        fraction=0.30,
                        bytes_done=0,
                        bytes_total=0,
                    )
                    self._save()
                until = time.monotonic() + 600
                last_marker: tuple[int, float, int] | None = None
                while process.poll() is None:
                    self._check(identity)
                    log_bytes = output.stat().st_size if output.exists() else 0
                    rss = 0
                    cpu_total = 0.0
                    try:
                        proc = psutil.Process(process.pid)
                        rss = proc.memory_info().rss
                        ct = proc.cpu_times()
                        cpu_total = round(float(ct.user + ct.system), 2)
                    except (psutil.Error, OSError, AttributeError):
                        pass
                    marker = (rss // (4 * 1024 * 1024), cpu_total, log_bytes)
                    if last_marker is not None and marker != last_marker and log_bytes <= 1024 * 1024:
                        until = max(until, time.monotonic() + 600)
                    last_marker = marker
                    # RSS is a liveness observation, not bytes read or kernel completion.
                    with self.lock:
                        self.jobs[identity]["process_observation"] = {"rss_bytes": rss, "cpu_seconds": cpu_total}
                    self._update_progress(
                        identity,
                        phase="profiling",
                        label=f"Profiling {backend.upper()} device kernels…",
                        fraction=0.30,
                        bytes_done=0,
                        bytes_total=0,
                        stage="native_kernel_profiler",
                    )
                    if time.monotonic() >= until or log_bytes > 1024 * 1024:
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
            validation_started = time.monotonic()
            metrics = json.loads(raw)
            if backend == "vulkan":
                require_vulkan_profile_device(metrics, selected_devices[0])
            typed = metrics.get("schema_version") == 2
            values = decode_kernel_profile(metrics, request, backend) if typed else None
            if "typed_kernel_profile_v2" in runtime.get("capabilities", {}).get("features", []) and not typed:
                raise ValueError("Native typed kernel capability returned an incompatible profile")
            if (backend != "cpu" and "host_transfer_profile_v1" in runtime.get("capabilities", {}).get("features", [])
                    and (values is None or values.get("gpu_transfer_bytes") is None)):
                raise ValueError("Native host transfer capability returned an incompatible profile")
            if (backend != "cpu" and "logits_transfer_profile_v1" in runtime.get("capabilities", {}).get("features", [])
                    and (values is None or values.get("gpu_logits_bytes") is None)):
                raise ValueError("Native logits transfer capability returned an incompatible profile")
            numeric = [
                metrics.get(key)
                for key in ("cpu_q4k_gflops", "layer_q4k_flops", "memory_available_gib")
            ]
            if not typed and (type(metrics.get("schema_version")) is not int or metrics["schema_version"] != 1 or any(
                type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in numeric
            )):
                raise ValueError("Prima profiler returned incomplete or invalid measurements")
            # Kernel-based Q4K estimates are not end-to-end decode benchmarks.
            gpu = gpu_profile_gflops(metrics, backend) if not typed else None
            if typed:
                if storage_identity(model) != storage:
                    raise ValueError("Model filesystem changed during kernel profiling")
                values["storage_identity"] = storage
                hardware = kernel_hardware(host, selected_devices, backend)
                current_devices = select_vulkan_devices(discover_vulkan_devices(runtime), request.devices) if backend == "vulkan" else []
                if hardware != kernel_hardware(execution_host(), current_devices, backend):
                    raise ValueError("Host/device hardware changed during kernel profiling")
                settings = kernel_settings(request.context, request.threads, selected_devices)
            else:
                values = {"cpu_layer_ms": metrics["layer_q4k_flops"] / (metrics["cpu_q4k_gflops"] * 1e6),
                          "gpu_layer_ms": metrics["layer_q4k_flops"] / (gpu * 1e6) if gpu is not None else None,
                          "memory_available_bytes": int(metrics["memory_available_gib"] * 1024**3)}
            profile = DeviceProfile(
                runtime_id=request.runtime_id,
                model_sha256=digest,
                node_id=self.state().node_id,
                hardware_digest=body_digest(hardware),
                settings_digest=body_digest(settings),
                backend=backend,
                measured_at=time.time(),
                **values,
                device_identity=selected_devices[0]["device_identity"] if selected_devices else "",
                driver_identity=selected_devices[0]["driver_identity"] if selected_devices else "",
            )
            with self.lock:
                self._check(identity)
                with measure("profile_validation", started_at=validation_started):
                    pass
                validation_started = None
                with measure("profile_cache_publication") as counts:
                    key = self.cache.put(profile)
                    counts["publications"] = 1
                self.jobs[identity].update(
                    phase="prepared",
                    finished_at=time.time(),
                    profile_key=key,
                    profile=profile.model_dump(),
                    measurement_kind="typed kernel roofline estimate" if typed else "Q4K kernel estimate",
                    metrics=metrics,
                )
                self._update_progress(
                    identity,
                    phase="prepared",
                    label="Device profile complete",
                    fraction=1.0,
                    bytes_done=0,
                    bytes_total=0,
                )
                self._save()
        except Exception as error:
            if validation_started is not None:
                with measure("profile_validation", started_at=validation_started) as counts:
                    counts["failed_calls"] = 1
            with self.lock:
                if identity not in self.cancelled:
                    self._record_refusal(identity, error)
                    self.jobs[identity].update(
                        phase="failed", error=str(error)[:1000], finished_at=time.time()
                    )
                    self._update_progress(
                        identity,
                        phase="failed",
                        label=str(error)[:240],
                        fraction=self.jobs[identity].get("progress", {}).get("fraction", 0.0),
                    )
                    self._save()
        finally:
            owned = process or self.processes.get(identity)
            if owned and owned.poll() is None:
                try:
                    self.kill(owned)
                    owned.wait(timeout=10)
                except BaseException:
                    with self.lock:
                        self.jobs[identity].update(phase="cleanup_pending", error="Owned profile process cleanup is pending")
                        self._save()
                    raise
            with self.lock:
                self.processes.pop(identity, None)
                self.checks.pop(identity, None)

    def _digest_model(self, identity, model):
        return digest_file(model, check=lambda: self._check(identity), stage="profile_preflight_hash",
            progress=lambda done, total: self._update_progress(identity, phase="profiling",
                stage="profile_preflight_hash", label="Verifying model digest…", fraction=0.08,
                bytes_done=done, bytes_total=total))

    def _run_generation(self, identity, request, runtime, model):
        import inspect
        from kllm.modules.primacpp.interactors.generation_profile import profile_generation
        from kllm.modules.primacpp.interactors.profiles import generation_settings, generation_hardware

        initial = (model.stat().st_size, model.stat().st_mtime_ns)
        model_size = initial[0]
        self._update_progress(
            identity,
            phase="profiling",
            label="Verifying model digest…",
            fraction=0.05,
            bytes_done=0,
            bytes_total=model_size,
        )
        digest = self._digest_model(identity, model)
        member = deepcopy(self.state().members.get(self.state().node_id, {}))
        if (request.backend not in (None, "vulkan") or profiling_backend(runtime, member) != "vulkan"):
            raise ValueError("Model-backed generation profiling currently requires Vulkan")
        selected = select_vulkan_devices(discover_vulkan_devices(runtime), request.devices)
        hardware = generation_hardware(member, selected)
        directory = self.root / identity
        directory.mkdir(parents=True, exist_ok=True)
        def track(process):
            with self.lock:
                self.processes[identity] = process
                self.jobs[identity].update(phase="profiling", pid=process.pid,
                    process_created_at=psutil.Process(process.pid).create_time())
                self._save()
                self._check(identity)
        def on_progress(label: str, fraction: float, bytes_done: int, bytes_total: int):
            self._update_progress(
                identity,
                phase="profiling",
                label=label,
                fraction=0.10 + fraction * 0.88,
                bytes_done=0,
                bytes_total=0,
            )
        extra_kwargs = (
            {"progress": on_progress}
            if "progress" in inspect.signature(profile_generation).parameters
            else {}
        )
        scope = self.jobs[identity].get("control_owner")
        if scope is not None:
            if type(scope.get("total_layers")) is not int or scope["total_layers"] <= 0:
                raise ValueError("Generation profile lacks approved execution layers; prepare again with the current agent")
            extra_kwargs["expected_layers"] = scope["total_layers"]
        with measure("generation_profile"):
            result = profile_generation(runtime, model, request, selected, directory,
                check=lambda: self._check(identity), track=track, stop=self.kill, **extra_kwargs)
        self._check(identity)
        if initial != (model.stat().st_size, model.stat().st_mtime_ns):
            raise ValueError("Model changed during generation profiling")
        current_member = deepcopy(self.state().members.get(self.state().node_id, {}))
        current_devices = select_vulkan_devices(discover_vulkan_devices(runtime), request.devices)
        if generation_hardware(current_member, current_devices) != hardware:
            raise ValueError("Host/device hardware changed during generation profiling")
        settings = generation_settings(request.context, request.threads, request.gpu_layers, selected, request.output_gpu,
            cache_type_k=request.cache_type_k, cache_type_v=request.cache_type_v, flash_attention=request.flash_attention)
        profile = DeviceProfile(runtime_id=request.runtime_id, model_sha256=digest, node_id=self.state().node_id,
            hardware_digest=body_digest(hardware), settings_digest=body_digest(settings), backend="vulkan",
            measured_at=time.time(), cpu_layer_ms=result["cpu"]["decode_ms"] / result["layers"],
            memory_available_bytes=psutil.virtual_memory().available, measurement_kind="end_to_end",
            device_identity=selected[0]["device_identity"], driver_identity=selected[0]["driver_identity"],
            prefill_ms=result["vulkan"]["prefill_ms"], decode_ms=result["vulkan"]["decode_ms"],
            cpu_decode_ms=result["cpu"]["decode_ms"], gpu_layers=request.gpu_layers, total_layers=result["layers"], output_gpu=request.output_gpu,
            peak_memory_bytes=max(result[b]["peak_memory_bytes"] for b in ("cpu", "vulkan")))
        with self.lock:
            self._check(identity)
            with measure("profile_cache_publication") as counts:
                key = self.cache.put(profile)
                counts["publications"] = 1
            self.jobs[identity].update(phase="prepared", finished_at=time.time(), profile_key=key,
                profile=profile.model_dump(), measurement_kind="model-backed CPU and Vulkan generation", metrics=result)
            self._update_progress(
                identity,
                phase="prepared",
                label="Device profile complete",
                fraction=1.0,
                bytes_done=0,
                bytes_total=0,
            )
            self._save()

    def logs(self, identity: str) -> str:
        self.job(identity)  # Don't accept an arbitrary path segment.
        path = self.root / identity / "profile.log"
        if not path.exists():
            directory = self.root / identity
            from kllm.modules.primacpp.interactors.model_structure import log_tail
            paths = [directory / "inspection.log", *sorted(directory.glob("*/profile.log"))]
            return "\n".join(f"[{p.parent.name}/{p.name}]\n{log_tail(p, 32768)}" for p in paths if p.exists())[-65536:]
        with path.open("rb") as source:
            source.seek(max(0, path.stat().st_size - 65536))
            return source.read(65536).decode("utf-8", errors="replace")

    def _record_refusal(self, identity: str, error: Exception) -> None:
        """Leave a refused job's owned log diagnosable instead of missing/empty."""
        path = self.root / identity / "profile.log"
        try:
            if path.exists() and path.stat().st_size:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Profile refused before profiling: {str(error)[:1000]}\n")
        except OSError:
            pass

    def close(self):
        for identity in list(self.jobs):
            self.cancel(identity)
        self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
