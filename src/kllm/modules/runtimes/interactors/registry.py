"""Durable source library and supervised, isolated local build operations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
import psutil
from typing import Callable
from uuid import uuid4

from kllm.modules.runtimes.entities import (
    UPSTREAM,
    BuildRequest,
    RuntimeVersion,
    RuntimeTarget,
    body_digest,
)
from kllm.modules.runtimes.interactors.source import git_environment
from kllm.modules.primacpp.interactors.recipe import qualification_errors, require_qualified, recipe_digest, recipe
from kllm.shared.atomic_io import replace_file

TERMINAL = ("prepared", "failed", "cancelled", "interrupted")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + str(uuid4()) + ".tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class RuntimeRegistry:
    # llama.cpp's Windows toolchain (cl.exe, MSBuild tlogs) is not long-path
    # aware, so staged builds use short directory names: a nested
    # vulkan-shaders-gen configure adds ~80 characters below the build root.
    _VERSION_DIR_CHARS = 16
    _JOB_DIR_CHARS = 12

    def __init__(self, home: Path, repository: Callable[[], Path | None], *, builder=None):
        self.root = home / "runtimes"
        self.path = self.root / "registry.json"
        self.repository = repository
        self.lock = threading.RLock()
        self.data = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {
                "schema": 1,
                "versions": {},
                "jobs": {},
                "selected": None,
                "target": None,
                "running": None,
                "requests": {},
            }
        )
        if self.data.get("schema") != 1:
            raise ValueError("Unsupported runtime registry schema")
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kllm-runtime-build")
        self.cancel_events: dict[str, threading.Event] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.builder = builder or self._install
        self.verified: dict[str, tuple] = {}
        self.closed = False
        changed = False
        for job in self.data["jobs"].values():
            if job["phase"] not in TERMINAL:
                if job.get("pid"):
                    try:
                        process = psutil.Process(job["pid"])
                        if process.create_time() == job.get("process_created_at"):
                            self._kill(process)
                        elif process.is_running():
                            job.update(
                                phase="failed",
                                error="Previous build process identity changed; review it locally before retrying",
                            )
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.Error, OSError, ValueError) as error:
                        job.update(
                            phase="failed",
                            error=f"Previous build cleanup requires local recovery: {error}",
                        )
                    if job["phase"] == "failed":
                        changed = True
                        continue
                job.update(
                    phase="interrupted",
                    error="Agent restarted; retry preparation",
                    finished_at=time.time(),
                )
                changed = True
        if changed:
            self._save()

    def _version_dir(self, runtime_id: str) -> Path:
        return self.root / runtime_id[: self._VERSION_DIR_CHARS]

    def _job_dir(self, runtime_id: str, job_id: str) -> Path:
        return self._version_dir(runtime_id) / job_id[: self._JOB_DIR_CHARS]

    def _save(self):
        atomic_json(self.path, self.data)

    def add(self, version: RuntimeVersion) -> dict:
        with self.lock:
            item = self.data["versions"].get(version.runtime_id)
            if item is None:
                item = {"version": version.model_dump(), "build": None}
                self.data["versions"][version.runtime_id] = item
                self._save()
            return deepcopy(item)

    def snapshot(self) -> dict:
        with self.lock:
            value = deepcopy(self.data)
        value.pop("requests", None)
        value["versions"] = list(value["versions"].values())
        value["jobs"] = list(value["jobs"].values())[-50:]
        for entry in value["versions"]:
            entry["qualification_errors"] = qualification_errors(entry["version"])
            entry["inference_supported"] = not entry["qualification_errors"]
            if entry["version"].get("engine") == "primacpp" and entry.get("build"):
                from kllm.modules.primacpp.interactors.qualification import qualification_status

                status = qualification_status(entry["build"])
                if entry["qualification_errors"]:
                    status["qualification_errors"] = list(
                        dict.fromkeys([*entry["qualification_errors"], *status["qualification_errors"]])
                    )
                    status["inference_supported"] = False
                entry.update(status)
        prima = recipe()
        source_entry = {
            "engine": "primacpp", "repository": prima["source_repository"],
            "ref": prima["source_commit"], "ref_type": "commit",
            # The digest every saved pin is checked against: the dashboard marks
            # a saved source stale instead of offering a rebuild that cannot run.
            "recipe_digest": recipe_digest(),
        }
        if prima.get("source_release"):
            source_entry["release"] = prima["source_release"]
            source_entry["release_commit"] = prima["source_release_commit"]
        value["engine_sources"] = [{**source_entry,
            "qualification_errors": qualification_errors({"engine": "primacpp"}),
        }]
        repo = self.repository()
        pin = repo / "llama-release" if repo else None
        if pin and pin.exists():
            tag = next(
                (
                    line.strip()
                    for line in pin.read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ),
                "",
            )
            value["bootstrap"] = {"repository": UPSTREAM, "ref": tag}
        if repo and not value["versions"]:
            stamp = repo / "vendor/bin/llama-release.txt"
            value["legacy"] = {
                "label": stamp.read_text()[:512] if stamp.exists() else "Bootstrap runtime",
                "phase": "verification_required",
                "message": "Save and prepare a version to establish source identity",
            }
        return value

    def target(self) -> dict | None:
        with self.lock:
            return deepcopy(self.data["target"])

    def set_target(self, target: RuntimeTarget):
        with self.lock:
            self.add(target.version)
            if self.data["target"] != target.model_dump():
                self.data["target"] = target.model_dump()
                self._save()

    def select(self, identity: str):
        require_qualified(self.capture(identity))
        with self.lock:
            if self.data["running"]:
                raise ValueError("Stop the active run before changing local runtime selection")
            self.data["selected"] = identity
            self._save()

    def capture(self, identity: str | None = None) -> dict:
        with self.lock:
            identity = (
                identity
                or (self.data["target"] or {}).get("version", {}).get("runtime_id")
                or self.data["selected"]
            )
            entry = deepcopy(self.data["versions"].get(identity))
        if not entry or not entry["build"]:
            raise ValueError(
                "Runtime is not prepared; open Settings → llama.cpp and download/build the target"
            )
        manifest = entry["build"]
        if (manifest.get("runtime_id"), manifest.get("repository"), manifest.get("commit")) != (
            entry["version"]["runtime_id"],
            entry["version"]["repository"],
            entry["version"]["commit"],
        ):
            raise ValueError("Runtime build provenance does not match the selected source")
        self.verify(manifest)
        if manifest.get("engine", "llamacpp") != entry["version"].get("engine", "llamacpp"):
            raise ValueError("Runtime engine does not match the selected source")
        if manifest.get("recipe_digest", "") != entry["version"].get("recipe_digest", ""):
            raise ValueError("Runtime recipe does not match the selected source")
        return {**entry["version"], **manifest}

    def verify(self, manifest: dict):
        directory = Path(manifest["artifact_dir"]).resolve()
        if not directory.is_relative_to(self.root.resolve()):
            raise ValueError("Runtime artifact is not verified")
        # Prebuilt manifests make no transport claims by design (an upstream
        # release archive cannot prove RDMA is off); only source manifests must
        # carry the verified TCP-only evidence the managed network gate needs.
        prima = manifest.get("engine", "llamacpp") == "primacpp"
        if prima and (manifest.get("transport") != "prima-ring-tcp"
                      or manifest.get("recipe_digest") != recipe_digest()):
            raise ValueError("Prima runtime recipe or transport is not verified; resolve and rebuild")
        if not prima and not manifest.get("prebuilt") and (
            manifest.get("ggml_rpc") != "ON" or manifest.get("ggml_rpc_rdma") != "OFF"
        ):
            raise ValueError("Runtime RPC transport is not verified")
        artifact_paths = {}
        for name in manifest["artifacts"]:
            relative = Path(name)
            artifact = (directory / relative).resolve()
            if relative.is_absolute() or ".." in relative.parts or not artifact.is_relative_to(directory):
                raise ValueError("Runtime artifact is not verified")
            artifact_paths[name] = artifact
        stats = tuple(
            (name, path.stat().st_mtime_ns, path.stat().st_ctime_ns, path.stat().st_size)
            for name, path in artifact_paths.items()
        )
        key = body_digest(manifest)
        if self.verified.get(key) == stats:
            return
        for name, expected in manifest["artifacts"].items():
            if file_digest(artifact_paths[name]) != expected:
                raise ValueError("Runtime artifacts changed; rebuild this version before Run")
        executables = ("llama_server", "llama_cli", "profile_tool") if prima else ("llama_server", "rpc_server")
        for name in executables:
            if Path(manifest[name]).resolve().parent != directory:
                raise ValueError("Runtime executable is outside its verified artifact directory")
            if prima and Path(manifest[name]).name not in manifest["artifacts"]:
                raise ValueError("Prima executable digest is absent from the manifest")
        self.verified[key] = stats

    def running(self, value: dict | None):
        with self.lock:
            self.data["running"] = deepcopy(value)
            self._save()

    def claim_running(self, value: dict) -> None:
        """Publish a run without replacing another engine or run's record."""
        if (not isinstance(value, dict) or not isinstance(value.get("engine"), str)
                or not isinstance(value.get("run_id"), str) or not value["run_id"]):
            raise ValueError("Running runtime requires an engine and run ID")
        with self.lock:
            current = self.data["running"]
            if current is not None and (not isinstance(current, dict)
                    or current.get("engine") != value["engine"]
                    or current.get("run_id") != value["run_id"]):
                raise ValueError("Another run owns the runtime registry")
            self.data["running"] = deepcopy(value)
            try:
                self._save()
            except BaseException:
                self.data["running"] = current
                raise

    def clear_running(self, *, engine: str, run_id: str) -> bool:
        """Retire a recorded run only while it still owns the registry slot."""
        with self.lock:
            current = self.data["running"]
            if (not isinstance(current, dict) or current.get("engine") != engine
                    or current.get("run_id") != run_id):
                return False
            self.data["running"] = None
            try:
                self._save()
            except BaseException:
                self.data["running"] = current
                raise
            return True

    def remove(self, identity: str):
        with self.lock:
            target = (self.data["target"] or {}).get("version", {}).get("runtime_id")
            if identity in (
                self.data["selected"],
                target,
                (self.data["running"] or {}).get("runtime_id"),
            ) or any(
                j["runtime_id"] == identity and j["phase"] not in TERMINAL
                for j in self.data["jobs"].values()
            ):
                raise ValueError(
                    "Running, selected, targeted, or building versions cannot be removed"
                )
            if identity not in self.data["versions"]:
                raise KeyError(identity)
            del self.data["versions"][identity]
            self._save()
            # Also drop any pre-shortening directory left by an older checkout.
            for directory in (self._version_dir(identity), self.root / identity):
                if directory.exists():
                    shutil.rmtree(directory, ignore_errors=False)

    def build(self, request: BuildRequest) -> dict:
        with self.lock:
            if self.closed:
                raise ValueError("Runtime builder is shutting down; retry after the agent restarts")
            digest = body_digest(request.model_dump())
            prior = self.data["requests"].get(request.request_id)
            if prior:
                if prior["digest"] != digest:
                    raise ValueError("Request ID reused with a different build")
                previous = self.data["jobs"][prior["job_id"]]
                if previous["phase"] != "interrupted":
                    return deepcopy(previous)
            if request.runtime_id not in self.data["versions"]:
                raise KeyError(request.runtime_id)
            for job in self.data["jobs"].values():
                if (
                    job["runtime_id"] == request.runtime_id
                    and job["backend"] == request.backend
                    and job["phase"] not in TERMINAL
                ):
                    return deepcopy(job)
            entry = self.data["versions"][request.runtime_id]
            version = RuntimeVersion.model_validate(entry["version"])
            # A saved pin from an earlier build recipe can never satisfy the
            # installer's recipe contract: refuse before spawning it so the
            # retry keeps an actionable error instead of a doomed build log.
            if version.engine == "primacpp" and version.recipe_digest != recipe_digest():
                raise ValueError(
                    "Prima build recipe changed; resolve the source again and re-apply the target"
                )
            if entry["build"]:
                try:
                    self.verify(entry["build"])
                    built_backend = str(entry["build"]["backend"] or "")
                    if request.backend in ("auto", built_backend, *built_backend.split("+")):
                        return {
                            "job_id": None,
                            "runtime_id": request.runtime_id,
                            "phase": "prepared",
                        }
                except (OSError, ValueError):
                    pass
            identity = str(uuid4())
            job = {
                "job_id": identity,
                **request.model_dump(exclude={"request_id"}),
                "phase": "queued",
                "created_at": time.time(),
                "error": None,
                "finished_at": None,
            }
            self.data["jobs"][identity] = job
            self.data["requests"][request.request_id] = {"digest": digest, "job_id": identity}
            self.cancel_events[identity] = threading.Event()
            self._save()
            self.pool.submit(self._build_job, identity)
            return deepcopy(job)

    def job(self, identity: str) -> dict:
        with self.lock:
            return deepcopy(self.data["jobs"][identity])

    def cancel(self, identity: str) -> dict:
        with self.lock:
            job = self.data["jobs"][identity]
            if job["phase"] not in TERMINAL:
                self.cancel_events[identity].set()
                process = self.processes.get(identity)
                if process:
                    self._kill(process)
                job.update(phase="cancelled", finished_at=time.time())
                self._save()
            return deepcopy(job)

    @staticmethod
    def _kill(process):
        if isinstance(process, subprocess.Popen) and process.poll() is not None:
            return
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10
            )
            if result.returncode and (
                process.poll() is None
                if isinstance(process, subprocess.Popen)
                else process.is_running()
            ):
                raise ValueError("Build process cleanup is pending; retry cancellation")
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _phase(self, identity: str, phase: str):
        with self.lock:
            job = self.data["jobs"][identity]
            if job["phase"] not in TERMINAL:
                job["phase"] = phase
                self._save()

    def _build_job(self, identity: str):
        with self.lock:
            job = deepcopy(self.data["jobs"][identity])
            version = RuntimeVersion.model_validate(
                self.data["versions"][job["runtime_id"]]["version"]
            )
        if self.cancel_events[identity].is_set():
            return
        try:
            manifest = self.builder(version, job["backend"], identity)
            if manifest.get("engine", "llamacpp") != version.engine or manifest.get("recipe_digest", "") != version.recipe_digest:
                raise ValueError("Build engine/recipe provenance does not match the requested snapshot")
            if (manifest.get("runtime_id"), manifest.get("repository"), manifest.get("commit")) != (
                version.runtime_id,
                version.repository,
                version.commit,
            ):
                raise ValueError("Build provenance does not match the requested source snapshot")
            self.verify(manifest)
            with self.lock:
                if self.cancel_events[identity].is_set():
                    return
                self.data["versions"][version.runtime_id]["build"] = manifest
                self.data["jobs"][identity].update(phase="prepared", finished_at=time.time())
                self._save()
        except Exception as error:
            with self.lock:
                if not self.cancel_events[identity].is_set():
                    self.data["jobs"][identity].update(
                        phase="failed", error=str(error)[:1000], finished_at=time.time()
                    )
                    self._save()

    def _installer_command(
        self,
        repo: Path,
        version: RuntimeVersion,
        backend: str,
        destination: Path,
        *,
        prebuilt: bool,
    ) -> list[str]:
        """Installer invocation for one preparation attempt.

        A source build always passes ``--rpc-tcp-only``: that verified TCP-only
        RPC evidence is what the managed private network gate consumes.
        """
        command = [
            sys.executable,
            "-u",
            str(repo / "scripts/install.py"),
            "--llama-only",
            "--source-repo",
            version.repository,
            "--revision",
            version.commit,
            "--runtime-dir",
            str(destination),
            "--backend",
            backend,
        ]
        if version.engine == "primacpp":
            command.extend(["--engine", "primacpp", "--recipe-digest", version.recipe_digest, "--from-source"])
        else:
            command.extend(["--prebuilt"] if prebuilt else ["--from-source", "--rpc-tcp-only"])
        return command

    def _spawn_installer(
        self, repo: Path, command: list[str], identity: str, output, log: Path
    ) -> int:
        """Run one installer attempt, streaming its log and tracking its process."""
        self._phase(identity, "downloading")
        process = subprocess.Popen(
            command,
            env=git_environment(),
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name != "nt",
        )
        try:
            with self.lock:
                self.processes[identity] = process
                try:
                    created_at = psutil.Process(process.pid).create_time()
                except psutil.NoSuchProcess:
                    created_at = None
                self.data["jobs"][identity].update(pid=process.pid, process_created_at=created_at)
                self._save()
                if self.cancel_events[identity].is_set():
                    self._kill(process)
            assert process.stdout is not None
            for line in iter(lambda: process.stdout.readline(8192), ""):
                output.write(line)
                output.flush()
                if log.stat().st_size > 1024 * 1024:
                    output.seek(0)
                    output.truncate()
                    output.write("[Earlier build output truncated]\n")
                if line.startswith("KLLM_RUNTIME_PHASE="):
                    phase = line.strip().split("=", 1)[1]
                    if phase in ("downloading", "building", "verifying"):
                        self._phase(identity, phase)
            return process.wait()
        finally:
            if process.poll() is None:
                self._kill(process)
                process.wait(timeout=10)
            process.stdout.close()
            with self.lock:
                self.processes.pop(identity, None)

    def _prepare_runtime(
        self,
        repo: Path,
        version: RuntimeVersion,
        backend: str,
        destination: Path,
        identity: str,
        output,
        log: Path,
    ) -> str:
        """Return the mode that produced the manifest: ``prebuilt`` or ``source``."""
        if version.engine == "primacpp":
            if self._spawn_installer(
                repo, self._installer_command(repo, version, backend, destination, prebuilt=False),
                identity, output, log,
            ) != 0:
                raise ValueError("Prima source build failed; inspect this operation’s build log")
            if self.cancel_events[identity].is_set():
                raise ValueError("Runtime build cancelled")
            return "source"
        code = self._spawn_installer(
            repo,
            self._installer_command(repo, version, backend, destination, prebuilt=True),
            identity,
            output,
            log,
        )
        if self.cancel_events[identity].is_set():
            raise ValueError("Runtime build cancelled")
        if code != 0:
            # No matching pack, an ineligible contract, or a download failure.
            output.write("Prebuilt runtime unavailable; compiling from source\n")
            output.flush()
            if (
                self._spawn_installer(
                    repo,
                    self._installer_command(repo, version, backend, destination, prebuilt=False),
                    identity,
                    output,
                    log,
                )
                != 0
            ):
                raise ValueError("Runtime build failed; inspect this operation’s build log")
            return "source"
        return "prebuilt"

    def _install(self, version: RuntimeVersion, backend: str, identity: str) -> dict:
        repo = self.repository()
        if repo is None:
            raise ValueError("Kluster checkout unavailable; set KLLM_REPO_ROOT")
        destination = self._job_dir(version.runtime_id, identity)
        destination.mkdir(parents=True, exist_ok=True)
        log = destination / "build.log"
        with log.open("w", encoding="utf-8") as output:
            mode = self._prepare_runtime(repo, version, backend, destination, identity, output, log)
        manifest = json.loads((destination / "manifest.json").read_text())
        with self.lock:
            self.data["jobs"][identity]["mode"] = mode
            self._save()
        return manifest

    def logs(self, identity: str) -> str:
        job = self.job(identity)
        path = self._job_dir(job["runtime_id"], identity) / "build.log"
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 65536))
            return handle.read(65536).decode("utf-8", errors="replace")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if getattr(self, "prima_profiles", None) is not None:
            self.prima_profiles.close()
        for identity in list(self.cancel_events):
            was_active = self.job(identity)["phase"] not in TERMINAL
            self.cancel(identity)
            if was_active:
                with self.lock:
                    self.data["jobs"][identity].update(
                        phase="interrupted",
                        error="Agent stopped during preparation; reconnect to resume",
                    )
                    self._save()
        self.pool.shutdown(wait=True, cancel_futures=True)
