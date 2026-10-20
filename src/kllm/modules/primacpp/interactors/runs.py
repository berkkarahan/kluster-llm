"""Local Prima run dispatch through the existing owned rank supervisor."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from pathlib import Path

import httpx

from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices
from kllm.modules.primacpp.interactors.launch import rank_arguments, execution_settings
from kllm.modules.primacpp.interactors.planner import preview, materialize
from kllm.modules.primacpp.interactors.qualification import manifest_context
from kllm.modules.primacpp.interactors.recipe import require_qualified
from kllm.modules.primacpp.interactors.execution import inventory_identity, authority_owner, require_execution_capabilities
from kllm.modules.runtimes.interactors.selection import snapshot
from kllm.modules.primacpp.interactors.adapters import adapter_paths

RANK_LOG_TAIL_BYTES = 4096


def rank_log_tail(path: Path, *, max_bytes: int = RANK_LOG_TAIL_BYTES) -> str:
    """Trailing text of a rank log, so a native startup failure stays diagnosable."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read(max_bytes)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()


def _exit_before_readiness(root: Path, run_id: str, *, max_chars: int = 2000) -> str:
    """Explain recognized settings failures; retain log context for other exits."""
    tail = rank_log_tail(root / run_id / "rank.log")
    if any(
        line.strip().startswith("ggml_vulkan:")
        and "Requested buffer size exceeds device memory allocation limit" in line
        for line in tail.splitlines()
    ):
        return (
            "Model settings exceed this GPU's buffer limit. "
            "The model could not start because the current settings require a GPU buffer "
            "larger than the device supports. Reduce Micro batch or Context size, or enable "
            "Flash Attention if supported, then deploy again."
        )
    if not tail:
        return "Prima exited before model readiness"
    return f"Prima exited before model readiness; rank log tail: {tail[-max_chars:]}"


class LocalPrimaRuns:
    def __init__(self, control):
        self.control = control
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.manifest = None
        self.inventory_digest = None
        self.authority = None
        self.starting = False
        self.dispatch_generation = None

    def launch(self, body, model: Path, plan: dict, members: dict) -> dict:
        with self.lock:
            dispatcher = getattr(self.control, "runs", None)
            if self.dispatch_generation is not None and self.dispatch_generation != dispatcher.generation:
                raise ValueError("Prima run cancelled before local startup")
            if self.control.service.state().process_pid:
                raise ValueError("Stop the current inference run before starting Prima")
            if self.control.rank.owner:
                raise ValueError("Reconcile the previous owned Prima rank before starting another run")
            self.starting = True
            self.cancelled.clear()
            try:
                self.inventory_digest = inventory_identity(members)
                self.authority = self.control.service.channels.master_authority()
                profiles = getattr(self.control, "profiles", None)
                if profiles is not None:
                    with profiles.lock:
                        if any(j["phase"] not in ("prepared", "failed", "cancelled", "interrupted") for j in profiles.jobs.values()):
                            raise ValueError("Stop Prima profiling before starting inference")
                self._check()
                return self._launch(body, model, plan, members)
            except BaseException:
                try:
                    self.stop()
                except BaseException:
                    # Cleanup failures must never mask the startup failure; the
                    # rank supervisor keeps cleanup_pending ownership and the
                    # monitor retries it.
                    pass
                raise
            finally:
                self.starting = False

    def _launch(self, body, model: Path, plan: dict, members: dict) -> dict:
        pinned = snapshot()
        runtime = self.control.registry.capture((pinned or {}).get("runtime_id"))
        require_qualified(runtime)
        if body.network_id:
            raise ValueError("Network-bound Prima runs require separate distributed qualification")
        def score(feasible):
            return feasible[0], plan.get("profiling", {"status": "unmeasured", "predicted_decode_ms": None})
        planned = preview(body, runtime, members, model, lambda *_: plan, score=score)
        manifest, files = materialize(body, runtime, members, model, lambda *_: plan,
                                      expected_digest=planned["plan_digest"], score=score,
                                      adapters=adapter_paths(body.model_dump()),
                                      companions={kind: self.control.resolve_model(value) for kind, value in
                                                  (("draft", body.draft_model if body.draft_enabled() else None),
                                                   ("projector", body.mmproj)) if value})
        if len(manifest.ranks) != 1:
            raise ValueError("Physical distributed Prima qualification is pending; local selection cannot launch workers")
        rank = manifest.ranks[0]
        selected = select_vulkan_devices(discover_vulkan_devices(runtime), rank.devices) if rank.devices else []
        # Resolve all launch behavior before stopping an existing process or staging assets.
        settings = execution_settings(body, plan, model, self.control.service.state().infer_port)
        rank_arguments(Path(runtime["llama_server"]), settings)
        manifest = manifest.model_copy(update={"settings": settings})
        require_execution_capabilities(manifest, runtime.get("capabilities", {}))
        dispatcher = getattr(self.control, "runs", None)
        if dispatcher is not None and hasattr(dispatcher, "bind_resources"):
            dispatcher.bind_resources(manifest, authority_owner(self.authority))
        qualification_context = manifest_context(manifest, runtime, devices=selected)
        require_qualified(runtime, context=qualification_context)
        with self.lock:
            if self.control.service.state().process_pid:
                raise ValueError("Stop the current inference run before starting Prima")
            if self.control.rank.owner:
                raise ValueError("Reconcile the previous owned Prima rank before starting another run")
            self._check()
            import inspect
            from kllm.modules.inference.interactors import load_progress

            total_asset_bytes = sum(asset.size for asset, _ in files)
            load_progress.begin(
                model.name,
                None,
                total_asset_bytes,
                detail="Staging model assets…",
            )
            stager_prepare = self.control.receiver.stager.prepare
            supports_progress = (
                "progress" in inspect.signature(stager_prepare).parameters
                if callable(stager_prepare)
                else False
            )
            prior_bytes = 0
            try:
                for asset, path in files:
                    def _on_stage(done: int, size: int, _prior=prior_bytes, _name=asset.name):
                        agg = _prior + done
                        frac = 0.02 + (agg / max(1, total_asset_bytes)) * 0.26
                        load_progress.update(
                            bytes_loaded=agg,
                            total_bytes=total_asset_bytes,
                            fraction=frac,
                            phase="loading",
                            detail=f"Staging {_name}…",
                        )
                    stage_kwargs = {"check": self._check}
                    if supports_progress:
                        stage_kwargs["progress"] = _on_stage
                    stager_prepare(
                        asset.sha256,
                        asset.size,
                        lambda offset, path=path: self.control._file_chunks(path, offset),
                        **stage_kwargs,
                    )
                    prior_bytes += asset.size
            except BaseException as error:
                load_progress.fail(str(error))
                raise
            self.manifest = manifest
            try:
                model_asset = next(a for a in manifest.assets if a.kind == "model")
                # Retain companion basenames through the same prepared-layout resolver.
                with self.control.lock:
                    self.control.jobs[manifest.run_id] = {"job_id": manifest.run_id, "request_id": manifest.run_id,
                        "phase": "prepared", "assets": [a.model_dump() for a in manifest.assets]}
                    self.control._save()
                load_progress.update(
                    bytes_loaded=0,
                    total_bytes=model_asset.size,
                    fraction=0.28,
                    phase="loading",
                    detail="Starting Prima rank 0 & loading model weights…",
                )
                start = getattr(self.control, "start_rank", self.control.rank.start)
                launch = start(manifest, self.control.cached_model(model_asset.sha256), self._check)
                if launch.get("pid"):
                    load_progress.update(
                        pid=int(launch["pid"]),
                        total_bytes=model_asset.size,
                        fraction=0.30,
                        phase="loading",
                        detail="Loading model weights into memory…",
                    )
                report = self._wait_ready(manifest, int(settings["assessment_port"]))
                self.control.rank.ready(manifest.run_id, report, self._check)
                load_progress.sample(int(settings["assessment_port"]))
                self.control.registry.claim_running({**runtime, "pid": launch["pid"], "run_id": manifest.run_id})
                return {"plan": {**plan, **planned}, "launch": {**launch, "binary": runtime["llama_server"], "role": "master"},
                        "run_id": manifest.run_id, "engine": "primacpp", "runtime_id": runtime["runtime_id"],
                        "qualification_context": qualification_context}
            except BaseException as error:
                load_progress.fail(str(error))
                self.stop()
                raise

    def _check(self):
        if self.cancelled.is_set() or self.control.closed or self.control.service.state().role != "master":
            raise ValueError("Prima run cancelled")
        if self.authority is not None and self.control.service.channels.master_authority() != self.authority:
            raise ValueError("Prima master authority changed; start a new run")
        if (self.inventory_digest is not None
                and inventory_identity(self.control.service.state().members) != self.inventory_digest):
            raise ValueError("Prima inventory changed; refresh the plan")

    def _wait_ready(self, manifest, port):
        from kllm.modules.inference.interactors import load_progress

        until = time.monotonic() + 300
        renewed = time.monotonic()
        last_marker: tuple[int, float, int] | None = None
        log_path = self.control.rank.root / manifest.run_id / "rank.log"
        while time.monotonic() < until:
            self._check()
            pid = None
            with self.control.rank.lock:
                if not self.control.rank._owned_child_alive():
                    raise ValueError(_exit_before_readiness(self.control.rank.root, manifest.run_id))
                if self.control.rank.owner:
                    pid = self.control.rank.owner.get("pid")
            if pid is not None:
                rss = 0
                cpu_total = 0.0
                try:
                    import psutil

                    proc = psutil.Process(int(pid))
                    rss = int(proc.memory_info().rss)
                    ct = proc.cpu_times()
                    cpu_total = round(float(ct.user + ct.system), 2)
                except Exception:
                    pass
                try:
                    log_bytes = log_path.stat().st_size if log_path.exists() else 0
                except OSError:
                    log_bytes = 0
                marker = (rss // (4 * 1024 * 1024), cpu_total, log_bytes)
                if last_marker is not None and marker != last_marker and log_bytes <= 4 * 1024 * 1024:
                    until = max(until, time.monotonic() + 300)
                last_marker = marker
            if time.monotonic() - renewed >= 5:
                self.control.rank.renew(manifest.run_id, self._check)
                renewed = time.monotonic()
            report = self.control.rank.readiness_report(manifest.run_id, self._check)
            if report is not None:
                load_progress.update(
                    fraction=0.95,
                    phase="loading",
                    detail="Warming up model & verifying HTTP health…",
                )
                try:
                    with httpx.Client(timeout=1, trust_env=False) as client:
                        if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                            return report
                except httpx.HTTPError:
                    pass
            time.sleep(0.1)
        raise ValueError("Prima model readiness timed out")

    def stop(self):
        self.cancelled.set()
        with self.lock:
            owner = deepcopy(self.control.rank.owner)
            owned_run = owner["run_id"] if owner else self.manifest.run_id if self.manifest is not None else None
            if owner:
                self.control.rank.stop(owner["run_id"])
            if owned_run is not None:
                self.control.registry.clear_running(engine="primacpp", run_id=owned_run)
            self.manifest = None
            self.inventory_digest = self.authority = None
            return {"phase": "idle"}

    def monitor_once(self):
        if self.manifest is None:
            return
        if not self.lock.acquire(blocking=False):
            return  # Startup owns renewal while it waits at the readiness barrier.
        try:
            try:
                if self.cancelled.is_set():
                    raise ValueError("Prima cleanup is pending")
                self.control.rank.renew(self.manifest.run_id, self._check)
            except Exception as error:
                state = self.control.service.state()
                retired_run = self.manifest.run_id
                config = state.active_config
                owned_config = bool(
                    isinstance(config, dict) and config.get("engine") == "primacpp"
                    and config.get("run_id") == retired_run
                )
                try:
                    self.stop()
                except Exception as cleanup_error:
                    if state.active_config is config and (config is None or owned_config):
                        state.apply_error = f"Prima cleanup pending ({error}): {cleanup_error}"
                        state.save()
                    return  # Retain ownership and retry next tick; never discard a failed stop.
                if owned_config and state.active_config is config:
                    state.process_pid = state.process_role = state.active_config = None
                    state.apply_error = "Prima rank stopped; restart the model after resolving its failure"
                    state.save()
        finally:
            self.lock.release()
