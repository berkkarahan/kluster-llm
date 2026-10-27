"""Dispatch paired Prima commands and master-owned preparation jobs."""

from __future__ import annotations

import json
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from kllm.modules.primacpp.interactors.execution import Asset, validate_asset_batch
from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor
from kllm.modules.primacpp.interactors.preparation import get_profile_jobs
from kllm.modules.primacpp.gateways.store import ProfilingStore
from kllm.modules.primacpp.interactors.coordinator import ProfilingCoordinator
from kllm.modules.primacpp.entities import DeviceProfile, ProfileRequest, RankAllocation
from kllm.modules.primacpp.interactors.planner import preview
from kllm.modules.primacpp.interactors.transfers import AssetReceiver, PushReceiver, TransferGrants
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import atomic_json, file_digest
from kllm.shared import native
from kllm.shared.prima_diagnostics import ProgressLog, operation, scope as log_scope
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices, vulkan_environment
from kllm.modules.primacpp.interactors.launch import rank_arguments, materialize_template, device_arguments
from kllm.modules.primacpp.interactors.execution import authority_owner, ExecutionManifest
from kllm.modules.primacpp.interactors.distributed_runs import PrimaRuns
from kllm.modules.primacpp.interactors.participants import (
    observe_participant, ParticipantEvidence, require_participant)
from kllm.shared.runtime_resources import node_resources, prima_scope
from kllm.modules.primacpp.interactors.draft import draft_file, draft_mode
from kllm.modules.primacpp.interactors.extras import materialize_template_kwargs
from kllm.modules.primacpp.interactors.preparation_metrics import (
    ProgressWindow, diagnostics_scope, digest_file, measure, check_owned, wire_status, channel_timing,
)

TERMINAL = {"prepared", "failed", "cancelled", "interrupted"}


def _layout_asset(asset: Asset, source: Path, directory: Path) -> Path:
    """Publish one verified basename from the digest cache into a run layout."""
    if source.is_symlink() or not source.is_file() or source.stat().st_size != asset.size:
        raise ValueError(f"Prepared Prima asset is unavailable: {asset.name}")
    target = directory / asset.name
    if target.is_symlink():
        raise ValueError(f"Prepared Prima layout contains a symlink: {asset.name}")
    if target.exists():
        if not target.is_file() or target.stat().st_size != asset.size or digest_file(target, stage="layout_verify", asset=asset.sha256) != asset.sha256:
            raise ValueError(f"Prepared Prima layout differs from the manifest: {asset.name}")
        return target

    temporary = directory / f".{asset.name}.{uuid4().hex}.tmp"
    before = source.stat()
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    try:
        try:
            with measure("layout_hardlink", asset=asset.sha256) as counts:
                check_owned()
                temporary.hardlink_to(source)
                counts["linked_bytes"] = asset.size
        except OSError:
            if temporary.exists() or temporary.is_symlink():
                raise ValueError(f"Prepared Prima layout temporary path is occupied: {asset.name}")
            with measure("layout_copy", asset=asset.sha256) as counts:
                with source.open("rb") as input_file, temporary.open("xb") as output:
                    while True:
                        check_owned()
                        chunk = input_file.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        counts["copied_bytes"] = counts.get("copied_bytes", 0) + len(chunk)
        digest = digest_file(temporary, stage="layout_verify", asset=asset.sha256)
        after = source.stat()
        if (temporary.stat().st_size != asset.size or digest != asset.sha256
                or source.is_symlink() or
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != identity):
            raise ValueError(f"Prepared Prima asset differs from the manifest: {asset.name}")
        if target.is_symlink() or target.exists():
            raise ValueError(f"Prepared Prima layout changed during publication: {asset.name}")
        check_owned()
        with measure("layout_publication", asset=asset.sha256) as counts:
            temporary.replace(target)
            counts["publications"] = 1
    finally:
        temporary.unlink(missing_ok=True)
    return target


def spawn_rank(runtime, rank, model, manifest_path, log_path, settings):
    from kllm.shared import process_registry
    from kllm.modules.primacpp.interactors.adapters import adapter_arguments

    binary = Path(runtime["llama_server"] if rank.rank == 0 else runtime["llama_cli"])
    if binary.parent.resolve() != Path(runtime["artifact_dir"]).resolve():
        raise ValueError("Prima binary is outside verified artifacts")
    env = {**runtime.get("runtime_env", {}), "KLLM_PRIMA_BIND": rank.address}
    selected = []
    if (rank.ngl or rank.output_gpu) and (runtime.get("backend", "").lower() == "vulkan" or
                     rank.devices and all(d.startswith("Vulkan") for d in rank.devices)):
        selected = select_vulkan_devices(discover_vulkan_devices(runtime), rank.devices)
        env.update(vulkan_environment(selected))
    n = native.require_native()
    if not hasattr(n, "start_prima_rank"):
        raise ValueError("Rebuild Kluster for Prima rank launch support")
    template_file = materialize_template(settings, manifest_path.parent) if rank.rank == 0 else None
    kwargs_file = materialize_template_kwargs(settings, manifest_path.parent) if rank.rank == 0 else None
    contract = ExecutionManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    lora_args = adapter_arguments(contract, model)
    draft_args = []
    if rank.rank == 0 and draft_mode(settings) != "none":
        contract = ExecutionManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        draft_path = draft_file(contract, model, rank=rank.rank)
        draft_args = ["--model-draft", str(draft_path)]
    result = native._launch_record(
        n.start_prima_rank(
            {
                "binary_path": str(binary),
                "rank": rank.rank,
                "model": str(model),
                "manifest_path": str(manifest_path),
                "log_path": str(log_path),
                "env": env,
                "context": settings.get("context", 4096),
                "threads": settings.get("threads", 8),
                "port": settings.get("assessment_port", 18080),
                "extra_args": rank_arguments(binary, settings, rank=rank.rank, template_file=template_file, kwargs_file=kwargs_file) +
                              device_arguments(binary, runtime, rank, selected=selected) + draft_args + lora_args,
            }
        )
    )
    if not result.get("process_start_token"):
        native.stop_process(result["pid"], force=True)
        raise ValueError("Prima child process identity is unavailable")
    registration_error = None
    try:
        process_registry.register_child(
            result["pid"], "llama-server" if rank.rank == 0 else "llama-cli"
        )
    except BaseException as error:
        # Return the known child identity so RankSupervisor can journal and
        # stop it; raising here would discard the only cleanup handle.
        registration_error = f"{type(error).__name__}: {error}"
    return {"pid": result["pid"], "start_token": result["process_start_token"],
            "registration_error": registration_error}


class PrimaControl:
    def __init__(self, service, registry, *, resolve_model, execute_plan):
        self.service, self.registry, self.resolve_model, self.execute_plan = (
            service,
            registry,
            resolve_model,
            execute_plan,
        )
        self.root = registry.root / "prima-control"
        self.receiver = AssetReceiver(self.root / "assets")
        self.grants = TransferGrants()
        self.push_receiver = PushReceiver(self.root / "assets")
        self.lock = threading.RLock()
        self.resources = node_resources(registry)
        self.preparation_reservations = {}
        self.preparation_futures = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prima-prepare")
        self.profiles = get_profile_jobs(
            registry,
            service.state,
            self.profile_model,
            busy=lambda: self.rank.owner is not None or self.ports.owner is not None or self.runs.busy,
        )
        self.closed = False
        self.cancelled: set[str] = set()
        self._job_samples: dict[str, ProgressWindow] = {}
        self.jobs = {}
        store_home = getattr(registry, "root", Path("."))
        self.store = ProfilingStore(store_home.parent if store_home.name == "runtimes" else store_home)
        self.coordinator = ProfilingCoordinator(self, self.store)
        if hasattr(service, "state") and callable(service.state) and service.state().role == "master":
            self.coordinator.reconcile_on_startup()
        path = self.root / "jobs.json"
        if path.exists():
            self.jobs = json.loads(path.read_text())
            changed = False
            for job in self.jobs.values():
                if job["phase"] not in TERMINAL:
                    job.update(phase="interrupted", error="Agent restarted; prepare again",
                               finished_at=time.time())
                    changed = True
                elif job["phase"] == "prepared" and job.get("expires_at", float("inf")) <= time.time():
                    job.update(phase="cancelled", error="Preparation approval expired; prepare again",
                               finished_at=time.time())
                    changed = True
            if changed:
                self._save()
        self.rank = RankSupervisor(
            self.root / "rank",
            service.state().node_id,
            capture=registry.capture,
            spawn=spawn_rank,
            stop=lambda pid: native.stop_process(pid, force=True),
            alive=native.process_alive,
            identity=lambda pid: str(native.require_native()._process_identity(pid)[0]),
            resources=self.resources,
        )
        from kllm.modules.primacpp.interactors.port_runs import PortSupervisor

        self.ports = PortSupervisor(self.root / "ports.json",
            platform=lambda: self.service.executor.platform, rank=lambda: self.rank, resources=self.resources)
        self.runs = PrimaRuns(self)

    def profile_model(self, value):
        if len(value) == 64 and all(c in "0123456789abcdef" for c in value):
            return self.cached_model(value)
        return self.resolve_model(value)

    def cached_model(self, digest, *, job_id=None):
        path, _ = self.receiver.stager._paths(digest)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Prepared model is unavailable")
        # Split GGUF readers resolve companions by basename. Restore that layout in
        # an owned directory while retaining digest-addressed cache publication.
        with self.lock:
            for job in reversed(list(self.jobs.values())):
                if job_id is not None and job["job_id"] != job_id:
                    continue
                assets = job.get("assets") or (job.get("manifest") or {}).get("assets", [])
                if not any(
                    a["sha256"] == digest and a["kind"] in ("model", "draft", "projector")
                    for a in assets
                ):
                    continue
                if str(UUID(job["job_id"])) != job["job_id"]:
                    raise ValueError("Prepared Prima job identity is invalid")
                layout_root = self.root / "model-layouts"
                if layout_root.is_symlink():
                    raise ValueError("Prepared Prima layout root cannot be a symlink")
                layout_root.mkdir(parents=True, exist_ok=True)
                directory = layout_root / job["job_id"]
                if directory.is_symlink():
                    raise ValueError("Prepared Prima layout cannot be a symlink")
                directory.mkdir(parents=True, exist_ok=True)
                selected = None
                for raw in assets:
                    asset = Asset.model_validate(raw)
                    source, _ = self.receiver.stager._paths(asset.sha256)
                    target = _layout_asset(asset, source, directory)
                    if asset.sha256 == digest and selected is None:
                        selected = target
                if selected is None:
                    raise ValueError("Prepared Prima layout has no requested model")
                return selected
        if digest_file(path, stage="cache_verify", asset=digest) != digest:
            raise ValueError("Prepared model differs from its digest identity")
        return path

    def _prepared_asset_manifest(self, assets: list[Asset], runtime_id: str) -> ExecutionManifest:
        """Require the received file set to be exactly the signed execution contract."""
        validate_asset_batch(assets, include_manifest=True)
        descriptor = next(asset for asset in assets if asset.kind == "manifest")
        path, _ = self.receiver.stager._paths(descriptor.sha256)
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != descriptor.size
                or file_digest(path) != descriptor.sha256):
            raise ValueError("Prepared Prima execution manifest differs from its asset identity")
        manifest = ExecutionManifest.model_validate_json(path.read_text(encoding="utf-8"))
        declared = {(asset.name, asset.kind, asset.sha256, asset.size) for asset in manifest.assets}
        received = {(asset.name, asset.kind, asset.sha256, asset.size)
                    for asset in assets if asset.kind != "manifest"}
        if (manifest.digest != descriptor.sha256 or manifest.runtime_id != runtime_id
                or self.service.state().node_id not in {rank.node_id for rank in manifest.ranks}
                or declared != received):
            raise ValueError("Prepared Prima assets differ from the exact execution manifest")
        return manifest

    def _approved_preparation(self, manifest, command_owner):
        # Content-addressed cache presence does not grant a new master's launch.
        with self.lock:
            for job in reversed(list(self.jobs.values())):
                if (job["phase"] != "prepared" or job.get("runtime_id") != manifest.runtime_id
                        or job.get("sender") != command_owner["sender"]
                        or job.get("authority_digest") != command_owner["authority_digest"]
                        or job.get("expires_at", 0) <= time.time()):
                    continue
                assets = [Asset.model_validate(a) for a in job.get("assets", [])]
                try:
                    prepared = self._prepared_asset_manifest(assets, manifest.runtime_id)
                except (ValueError, OSError):
                    continue
                if prepared.digest == manifest.digest:
                    return job["job_id"]
        raise ValueError("Prima start requires a current approved preparation for its complete manifest")

    def _command_job(self, identity, operation_id, command_owner):
        if not isinstance(identity, str) or identity not in self.jobs:
            raise ValueError("Unknown signed Prima preparation")
        job = self.jobs[identity]
        if (job.get("operation_id") != operation_id
                or job.get("sender") != command_owner["sender"]
                or job.get("authority_digest") != command_owner["authority_digest"]):
            raise ValueError("Command does not belong to this signed preparation")
        return job

    def _profile_scope(self, preparation_job_id, manifest_digest, request, operation_id, command_owner):
        with self.lock:
            job = self._command_job(preparation_job_id, operation_id, command_owner)
            if (job["phase"] != "prepared" or job.get("expires_at", 0) <= time.time()
                    or request.runtime_id != job["runtime_id"]
                    or not any(a["kind"] == "manifest" and a["sha256"] == manifest_digest for a in job["assets"])):
                raise ValueError("Profile requires a current complete signed preparation")
        manifest = ExecutionManifest.model_validate_json(self.cached_model(manifest_digest).read_text())
        rank = next((r for r in manifest.ranks if r.node_id == self.service.state().node_id), None)
        model = next(a for a in manifest.assets if a.kind == "model")
        backend = manifest.backends.get(self.service.state().node_id)
        if (manifest.digest != manifest_digest or rank is None or manifest.runtime_id != request.runtime_id
                or request.model != model.sha256 or request.backend != backend
                or request.context != manifest.settings["context"] or request.threads != manifest.settings["threads"]
                or (request.devices or []) != (rank.devices if backend == "vulkan" else [])):
            raise ValueError("Profile differs from the approved model, runtime, rank or settings")
        if len(manifest.ranks) > 1 and request.mode != "kernel":
            raise ValueError("Distributed preparation requires per-layer kernel profiling")
        if request.mode == "generation" and (request.gpu_layers != rank.ngl or request.output_gpu != rank.output_gpu):
            raise ValueError("Generation profile differs from the approved GPU/output placement")
        if request.mode == "generation" and any(
                (getattr(request, key) or default) != manifest.settings.get(key, default)
                for key, default in (("cache_type_k", "f16"), ("cache_type_v", "f16"), ("flash_attention", "off"))):
            raise ValueError("Generation profile differs from the approved cache/attention settings")
        return {**command_owner, "operation_id": operation_id, "preparation_job_id": preparation_job_id,
                "manifest_digest": manifest_digest, "runtime_id": request.runtime_id,
                "model_sha256": request.model, "node_id": rank.node_id, "backend": backend,
                "context": request.context, "threads": request.threads,
                **({"total_layers": manifest.total_layers} if request.mode == "generation" else {})}

    def _profile_owner_check(self, scope, authorize):
        authorize()
        with self.lock:
            job = self._command_job(scope["preparation_job_id"], scope["operation_id"], scope)
            if job["phase"] != "prepared" or job.get("expires_at", 0) <= time.time():
                raise ValueError("Signed profile preparation expired or was cancelled")

    def _owned_profile(self, profile_job_id, operation_id, command_owner):
        job = self.profiles.job(profile_job_id)
        scope = job.get("control_owner")
        if (not isinstance(scope, dict) or scope.get("operation_id") != operation_id
                or scope.get("sender") != command_owner["sender"]
                or scope.get("authority_digest") != command_owner["authority_digest"]):
            raise ValueError("Profile does not belong to this signed preparation")
        return job

    def _retain_profile(self, result, manifest, rank, request, operation_id, expected_owner):
        if result.get("phase") != "prepared" or result.get("runtime_id") != manifest.runtime_id:
            raise ValueError("Participant profile is incomplete or belongs to another runtime")
        scope = result.get("control_owner")
        if scope != expected_owner:
            raise ValueError("Participant profile belongs to another signed preparation")
        value = DeviceProfile.model_validate(result["profile"])
        if (value.node_id != rank.node_id or value.runtime_id != manifest.runtime_id
                or value.model_sha256 != request.model or value.backend != manifest.backends[rank.node_id]):
            raise ValueError("Participant profile does not match its node/model/runtime/backend")
        if request.mode == "generation" and (
                value.measurement_kind != "end_to_end" or value.total_layers != manifest.total_layers
                or value.gpu_layers != rank.ngl or value.output_gpu != rank.output_gpu):
            raise ValueError("Participant generation profile differs from approved execution ownership")
        if value.kernel_profile_version == 2:
            from kllm.modules.primacpp.interactors.kernel_profile import kernel_hardware, kernel_settings
            if rank.node_id == self.service.state().node_id:
                evidence = self.check_participant(manifest, rank.node_id)
            else:
                evidence = ParticipantEvidence.model_validate(self._call(rank.node_id, operation_id,
                    "prima_inventory", {"runtime_id": manifest.runtime_id, "rank": rank.model_dump(),
                                        "backend": manifest.backends[rank.node_id]}))
                require_participant(manifest, rank.node_id, evidence)
            devices = [device.model_dump() for device in evidence.devices]
            if value.backend == "vulkan" and len(devices) != 1:
                raise ValueError("Typed Vulkan profiling requires one selected physical device")
            physical = ((devices[0]["device_identity"], devices[0]["driver_identity"])
                        if value.backend == "vulkan" else ("", ""))
            if (value.total_layers != manifest.total_layers or value.kernel_context != request.context
                    or value.kernel_threads != request.threads
                    or (value.device_identity, value.driver_identity) != physical
                    or value.settings_digest != body_digest(kernel_settings(request.context, request.threads, devices))
                    or value.hardware_digest != body_digest(kernel_hardware(evidence.host.model_dump(), devices, value.backend))):
                raise ValueError("Participant typed profile changed hardware, model layers or settings")
        elif len(manifest.ranks) > 1:
            raise ValueError("Distributed planning requires typed per-layer profile costs")
        with self.profiles.lock:
            self.profiles.cache.put(value)
        return value

    def _check_worker_network(self, run_id):
        owner = self.rank.owner
        if owner is None or owner["rank"] == 0:
            return
        manifest = ExecutionManifest.model_validate_json((self.rank.root / run_id / "execution.json").read_text())
        if manifest.digest != owner["manifest_digest"]:
            raise ValueError("Owned Prima worker manifest changed")
        if len(manifest.ranks) > 1:
            self.service.prima_admission.validate_worker(manifest)
            self.check_participant(manifest, self.service.state().node_id)
            if manifest.network_id:
                self.ports.validate(manifest, owner.get("command_owner"))

    def observe_participant(self, runtime_id, rank, backend):
        return observe_participant(self.registry.capture(runtime_id), self.service.state().node_id, rank, backend)

    def check_participant(self, manifest, node):
        rank = next(r for r in manifest.ranks if r.node_id == node)
        current = self.observe_participant(manifest.runtime_id, rank, manifest.backends[node])
        require_participant(manifest, node, current)
        return current

    def check_participants(self, manifest, check):
        for rank in manifest.ranks:
            check()
            if rank.node_id == self.service.state().node_id:
                current = self.observe_participant(manifest.runtime_id, rank, manifest.backends[rank.node_id])
            else:
                current = ParticipantEvidence.model_validate(self._call(rank.node_id, manifest.run_id,
                    "prima_inventory", {"runtime_id": manifest.runtime_id, "rank": rank.model_dump(),
                    "backend": manifest.backends[rank.node_id]}))
            require_participant(manifest, rank.node_id, current)
            check()

    def _save(self):
        with self.lock, measure("checkpoint") as counts:
            atomic_json(self.root / "jobs.json", self.jobs)
            counts["publications"] = 1

    def members(self, body=None):
        network_id = getattr(body, "network_id", None) if not isinstance(body, dict) else body.get("network_id")
        if network_id:
            try:
                admitted = self.service.prima_admission.members(network_id)
                if admitted:
                    return admitted
            except Exception:
                pass
        return deepcopy(self.service.state().members)

    def _capacity_plan(self, runtime, body, members):
        from kllm.modules.runtimes.interactors.selection import use_runtime

        with use_runtime(runtime):
            return self.execute_plan(body, members)

    def _measured_plan(self, runtime, body, model, feasible):
        from kllm.modules.primacpp.interactors.measured_planning import measured_local_candidates, measured_distributed_candidates

        strategy = getattr(body, "placement_strategy", "auto")
        if strategy in ("capacity_first", "priority"):
            return feasible[0], {
                "status": "unmeasured",
                "predicted_decode_ms": None,
                "reason": f"Operator selected {strategy} placement strategy",
            }
        if body.network_id or any(len(inventory) > 1 for _, inventory in feasible):
            return measured_distributed_candidates(self, feasible, body, runtime, model)
        return measured_local_candidates(feasible, body, runtime, model, self.profiles.cache)

    def profile_context(self, manifest, profile, preparation_job_id):
        from kllm.modules.primacpp.interactors.kernel_profile import storage_identity

        evidence = self.check_participant(manifest, self.service.state().node_id)
        asset = next(a for a in manifest.assets if a.kind == "model")
        if profile["node_id"] != evidence.node_id or profile["model_sha256"] != asset.sha256:
            raise ValueError("Fresh profile context differs from its approved model/node")
        # Preview reads must not recreate removed model layouts or stage assets.
        path = self.root / "model-layouts" / preparation_job_id / asset.name
        return {"participant_evidence": evidence.model_dump(), "storage_identity": storage_identity(path).model_dump(),
                "manifest_digest": manifest.digest}

    @operation("deployment")
    def plan(self, runtime_id, body, *, members=None, model=None):
        runtime = self.registry.capture(runtime_id)
        if runtime.get("engine") != "primacpp":
            raise ValueError("Prima preparation requires a Prima runtime")
        model = model or self.resolve_model(body.model)
        from kllm.modules.primacpp.interactors.planner import is_prima_auto_deploy_active, auto_plan
        if is_prima_auto_deploy_active(body, runtime=runtime):
            effective_members = members if members is not None else self.members(body)
            return auto_plan(body, effective_members, model=model, runtime=runtime)
        return preview(body, runtime, members if members is not None else self.members(body), model,
            lambda b, m: self._capacity_plan(runtime, b, m),
            score=lambda feasible: self._measured_plan(runtime, body, model, feasible))

    def start_preparation(
        self, runtime_id, body, expected_digest, request_id, *, include_profile=False
    ):
        return self.coordinator.start_run(
            runtime_id, body, expected_digest, request_id, include_profile=include_profile
        )

    def _settle_preparation(self, identity):
        with self.lock:
            future = self.preparation_futures.get(identity)
            if future is not None and not future.done():
                return
            self.preparation_futures.pop(identity, None)
            self._job_samples.pop(identity, None)
            lease = self.preparation_reservations.pop(identity, None)
            if lease is not None:
                lease.release()

    def _update_job_progress(
        self,
        identity: str,
        *,
        phase: str,
        label: str,
        step_index: int = 1,
        step_total: int = 1,
        fraction: float = 0.0,
        bytes_done: int = 0,
        bytes_total: int = 0,
        stage: str = "",
        scope: tuple = (),
        rate_done: int | None = None,
        rate_total: int | None = None,
        rate_kind: str | None = None,
        force: bool = False,
    ) -> None:
        with self.lock:
            job = self.jobs.get(identity)
            if not job or (job["phase"] in TERMINAL and phase != job["phase"]):
                return
            if identity in self.cancelled and phase != "cancelled":
                return
            done, total = max(0, int(bytes_done)), max(0, int(bytes_total))
            # Cheap exact accounting is independent of presentation cadence.
            job["progress_bytes"] = {"done": done, "total": total}
            statistics = job.setdefault("progress_statistics", {"callbacks": 0, "publications": 0})
            statistics["callbacks"] += 1
            window = self._job_samples.setdefault(identity, ProgressWindow())
            result = window.update((phase, stage, scope), rate_done if rate_done is not None else done,
                                   rate_total if rate_total is not None else total, time.monotonic(),
                                   force=force or phase in TERMINAL)
            if result is None:
                return
            statistics["publications"] += 1
            rate_bps, eta_seconds = result if rate_kind else (None, None)
            clamped = max(0.0, min(1.0 if phase in TERMINAL else .99, float(fraction)))
            job["progress"] = {
                "phase": phase,
                "stage": stage or phase,
                "label": label() if callable(label) else label,
                "step_index": max(1, int(step_index)),
                "step_total": max(1, int(step_total)),
                "fraction": round(clamped, 4),
                "pct": round(clamped * 100.0, 1),
                "bytes_done": done,
                "bytes_total": total,
                "stage_bytes_done": rate_done if rate_done is not None else 0,
                "stage_bytes_total": rate_total if rate_total is not None else 0,
                "rate_bps": rate_bps,
                "eta_seconds": eta_seconds,
                "rate_kind": rate_kind,
                "updated_at": time.time(),
            }
            with measure("progress") as counts:
                counts["publications"] = 1
            if not hasattr(self, "_log_progress"):
                self._log_progress = ProgressLog()
            self._log_progress.update("deployment", identity, job["progress"])
            if phase in TERMINAL:
                self._job_samples.pop(identity, None)

    def _on_link_progress(
        self, identity: str, completed_pairs: int, total_pairs: int, source: str, target: str
    ) -> None:
        pair_frac = (completed_pairs / max(1, total_pairs)) if total_pairs else 1.0
        overall = 0.85 + pair_frac * 0.14
        self._update_job_progress(
            identity,
            phase="links",
            label=f"Measuring Ring-TCP link {source} → {target} ({completed_pairs}/{total_pairs})…",
            step_index=3,
            step_total=3,
            fraction=overall,
        )

    def latest_preparation(self) -> dict | None:
        with self.lock:
            if not self.jobs:
                return None
            identity = max(self.jobs, key=lambda key: float(self.jobs[key].get("created_at") or 0.0))
            return self._public_job(identity)

    def start_rank(self, manifest, model, check, *, command_owner=None):
        owner = command_owner or authority_owner(self.service.channels.master_authority())
        with self.resources.hold(prima_scope(manifest.run_id, owner, manifest.digest), "rank_start"):
            with self.profiles.lock:
                if self.profiles.processes or any(j["phase"] not in TERMINAL for j in self.profiles.jobs.values()):
                    raise ValueError("Stop Prima profiling before starting a rank")
            return self.rank.start(manifest, model, check, command_owner=owner)

    def _check(self, job_id):
        if self.jobs.get(job_id, {}).get("manifest") and self.service.state().role != "master":
            raise ValueError("Master authority changed during Prima preparation")
        if self.closed or job_id in self.cancelled:
            raise ValueError("Prima preparation cancelled")
        if self.service.state().process_pid:
            raise ValueError("Inference started during preparation")
        owner = self.jobs.get(job_id, {}).get("command_owner")
        if owner:
            if authority_owner(self.service.channels.master_authority()) != owner:
                raise ValueError("Prima master authority changed during preparation")

    def _call(self, node, run_id, action, body):
        asset = body["chunk"]["sha256"] if action == "prima_asset_chunk" else ""
        with measure("signed_command_roundtrip:" + action, node=node, asset=asset,
                     sample=64 if action == "prima_asset_chunk" else 1):
            return self._call_owned(node, run_id, action, body)

    def _call_owned(self, node, run_id, action, body):
        session = self.service.channels.sessions.get(node)
        if session is None:
            raise ValueError(f"{node}: paired Prima command channel unavailable")
        result = self.service.admission.call(
            session.request_runtime(
                run_id, action, ttl_ms=60000 if action in ("prima_link_profile", "prima_asset_chunk", "prima_inventory", "prima_prepare", "prima_prepare_renew", "prima_profile", "prima_cancel", "prima_start", "prima_stop", "prima_profile_context", "prima_status", "prima_renew",
                    "prima_admit_ports", "prima_release_ports", "prima_renew_ports") else 10000, body=body,
                **({"timing": channel_timing(node, 64 if action == "prima_asset_chunk" else 1,
                                            body["chunk"]["sha256"] if action == "prima_asset_chunk" else "")}
                   if getattr(session, "runtime_timing", False) else {}),
            )
        )
        if result.get("status") == "failed":
            raise ValueError(result.get("message") or "Prima command failed")
        return result

    def _prepare(self, identity, runtime_id, body, digest, include_profile, start_from_stage=None):
        with log_scope(identity), diagnostics_scope(self.jobs.get(identity, {}), self.lock, lambda: self._check(identity),
                               self.service.state().node_id):
            if start_from_stage is not None:
                return self._prepare_owned(identity, runtime_id, body, digest, include_profile, start_from_stage)
            return self._prepare_owned(identity, runtime_id, body, digest, include_profile)

    def _prepare_owned(self, identity, runtime_id, body, digest, include_profile, start_from_stage=None):
        return self.coordinator._execute_run(
            identity, runtime_id, body, digest, include_profile, start_from_stage=start_from_stage
        )

    @staticmethod
    def _file_chunks(path, offset, chunk_bytes=4 * 1024 * 1024, check=lambda: None):
        # Local disk staging is independent of the signed network frame limit.
        with path.open("rb") as source:
            source.seek(offset)
            while True:
                check()
                with measure("local_read", asset=path.name, sample=64) as counts:
                    chunk = source.read(chunk_bytes)
                    counts["read_bytes"] = len(chunk)
                if not chunk:
                    break
                yield chunk

    def _public_job(self, identity):
        """Called under self.lock; local work is not proof of remote cleanup."""
        result = deepcopy(self.jobs[identity])
        future = self.preparation_futures.get(identity)
        result["local_work_pending"] = (
            identity in self.preparation_reservations
            or (future is not None and not future.done())
        )
        if result["phase"] == "prepared" and identity in self.preparation_reservations:
            result["phase"] = "settling"
        return result

    def job(self, identity):
        with self.lock:
            if hasattr(self, "coordinator"):
                result = self.coordinator.get_run(identity)
                if result is not None:
                    return result
            return self._public_job(identity)

    def cancel(self, identity, *, reason=None):
        with self.lock:
            job = self.jobs.get(identity)
            if job and (job["phase"] not in {"failed", "interrupted"} or identity in self.preparation_reservations):
                self.cancelled.add(identity)
                job.update(phase="cancelled", finished_at=time.time())
                if reason is not None:
                    job["error"] = reason
                prev_prog = job.get("progress") or {}
                exact_bytes = job.get("progress_bytes") or {}
                self._update_job_progress(
                    identity,
                    phase="cancelled",
                    label=reason or "Preparation cancelled",
                    step_index=int(prev_prog.get("step_index") or 1),
                    step_total=int(prev_prog.get("step_total") or 1),
                    fraction=float(prev_prog.get("fraction") or 0.0),
                    bytes_done=int(exact_bytes.get("done", prev_prog.get("bytes_done")) or 0),
                    bytes_total=int(exact_bytes.get("total", prev_prog.get("bytes_total")) or 0),
                )
                self._save()
                self._settle_preparation(identity)
            if hasattr(self, "store"):
                run_rec = self.store.get_run(identity)
                if run_rec:
                    current_st = run_rec.get("current_stage")
                    if current_st:
                        self.store.transition_stage(
                            identity, current_st, "cancelled", error=reason or "Preparation cancelled"
                        )
                    self.store.update_run(
                        identity,
                        status="cancelled",
                        error_message=reason or "Preparation cancelled",
                        finished_at=time.time(),
                    )
            if hasattr(self, "coordinator"):
                res = self.coordinator.get_run(identity)
                if res is not None:
                    return res
            return self._public_job(identity)

    def retry_preparation(
        self, identity: str, *, stage_name: str | None = None, node_id: str | None = None
    ) -> dict[str, Any]:
        return self.coordinator.retry_run(identity, stage_name=stage_name, node_id=node_id)

    def preparation_coverage(self, identity: str) -> dict[str, Any]:
        return self.coordinator.get_coverage(identity)

    def preparation_logs(
        self,
        identity: str,
        *,
        stage: str | None = None,
        node: str | None = None,
        level: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        return self.coordinator.get_logs(identity, stage=stage, node=node, level=level, limit=limit)

    async def execute(self, command, deadline, authorize):
        def check():
            if time.monotonic() >= deadline:
                raise ValueError("Prima signed command expired")
            authorize()

        check()
        action, value = command.action, command.body
        command_owner = {**authority_owner(command), "sender": command.sender_node_id}
        authority_digest = command_owner["authority_digest"]
        if action == "prima_profile_context":
            if set(value) != {"profile_job_id"}:
                raise ValueError("Invalid signed Prima profile context request")
            def profile_context_owned():
                check()
                job = self._owned_profile(value["profile_job_id"], str(command.run_id), command_owner)
                if job["phase"] != "prepared":
                    raise ValueError("Fresh profile context requires a complete profile")
                scope = job["control_owner"]
                self._profile_owner_check(scope, authorize)
                manifest = ExecutionManifest.model_validate_json(self.cached_model(scope["manifest_digest"]).read_text())
                result = self.profile_context(manifest, job["profile"], scope["preparation_job_id"])
                self._profile_owner_check(scope, authorize)
                check()
                return result
            return await asyncio.to_thread(profile_context_owned)
        if action == "prima_link_profile":
            from kllm.modules.primacpp.interactors.link_profile import execute_link

            return await execute_link(self, value, str(command.run_id), command_owner, check)
        if action == "prima_port_capabilities":
            if value:
                raise ValueError("Invalid Prima listener capability request")
            return await asyncio.to_thread(self.ports.capability)
        if action == "prima_admit_ports":
            from kllm.modules.managed_network.interactors.prima_ports import PrimaPortContract, port_contract
            from kllm.modules.primacpp.interactors.recipe import require_qualified

            if set(value) != {"contract"}:
                raise ValueError("Invalid Prima listener admission request")
            contract = PrimaPortContract.model_validate(value["contract"])
            def admit_owned():
                check()
                manifest = ExecutionManifest.model_validate_json(self.cached_model(contract.manifest_digest).read_text())
                if manifest.run_id != str(command.run_id) or manifest.digest != contract.manifest_digest:
                    raise ValueError("Signed listener admission and execution manifest disagree")
                self._approved_preparation(manifest, command_owner)
                if self.rank.owner or self.service.state().process_pid:
                    raise ValueError("Stop existing inference before admitting Prima listeners")
                self.service.prima_admission.validate_worker(manifest)
                self.check_participant(manifest, self.service.state().node_id)
                preparation = self.service.executor.preparation.get(manifest.network_id)
                if preparation is None or contract != port_contract(manifest, self.service.state().node_id,
                                                                   preparation.participants):
                    raise ValueError("Prima listeners differ from the approved private manifest")
                require_qualified(self.registry.capture(manifest.runtime_id))
                platform = self.service.executor.platform
                if not hasattr(platform, "apply_prima_ports"):
                    raise ValueError("Upgrade/enable the native helper for Prima private listener admission")
                check()
                try:
                    return self.ports.admit(manifest, command_owner,
                        lambda: platform.apply_prima_ports(command), check)
                except BaseException:
                    # This command cannot spawn a child. Its owned partial firewall
                    # intent may be narrowed locally even after the signed deadline.
                    self.ports.release(manifest.network_id, manifest.run_id, command_owner)
                    raise
            return await asyncio.to_thread(admit_owned)
        if action == "prima_renew_ports":
            from kllm.modules.managed_network.interactors.prima_ports import PrimaPortContract, port_contract

            if set(value) != {"contract"}:
                raise ValueError("Prima native renewal requires its owned listener contract")
            contract = PrimaPortContract.model_validate(value["contract"])
            def renew_ports_owned():
                check()
                if self.ports.owner is None:
                    raise ValueError("Prima native renewal requires its owned listener contract")
                manifest = ExecutionManifest.model_validate(self.ports.owner["manifest"])
                if manifest.run_id != str(command.run_id) or manifest.digest != contract.manifest_digest:
                    raise ValueError("Prima native renewal changed run/manifest ownership")
                status = self.rank.status(str(command.run_id), command_owner=command_owner)
                if status.get("phase") not in ("starting", "ready"):
                    raise ValueError("Prima native renewal requires a live owned rank")
                self._check_worker_network(str(command.run_id))
                preparation = self.service.executor.preparation.get(manifest.network_id)
                if preparation is None or contract != port_contract(manifest, self.service.state().node_id, preparation.participants):
                    raise ValueError("Prima native renewal changed approved private listeners")
                check()
                result = self.ports.renew(manifest, command_owner,
                    lambda: self.service.executor.platform.renew_prima_ports(command))
                check()
                return result
            return await asyncio.to_thread(renew_ports_owned)
        if action == "prima_release_ports":
            if (set(value) != {"network_id"} or not isinstance(value["network_id"], str)
                    or str(UUID(value["network_id"])) != value["network_id"]):
                raise ValueError("Invalid Prima listener cleanup request")
            if self.rank.owner and self.rank.owner["run_id"] == str(command.run_id):
                raise ValueError("Stop the owned Prima rank before releasing its listeners")
            platform = self.service.executor.platform
            if not hasattr(platform, "release_prima_ports"):
                raise ValueError("Native Prima listener cleanup support is unavailable")
            result = await asyncio.to_thread(self.ports.release, value["network_id"], str(command.run_id), command_owner)
            check()
            return result
        if action == "prima_inventory":
            if set(value) != {"runtime_id", "rank", "backend"}:
                raise ValueError("Invalid Prima inventory request")
            rank = RankAllocation.model_validate(value["rank"])
            result = await asyncio.to_thread(self.observe_participant, value["runtime_id"], rank, value["backend"])
            check()
            return result.model_dump()
        if action == "prima_asset_chunk":
            if set(value) != {"job_id", "chunk"}:
                raise ValueError("Invalid prepared asset push")
            def accept_chunk():
                with self.lock:
                    job = self._command_job(value["job_id"], str(command.run_id), command_owner)
                    with diagnostics_scope(job, self.lock, check, self.service.state().node_id):
                        return accept_chunk_owned()
            def accept_chunk_owned():
                with self.lock:
                    job = self._command_job(value["job_id"], str(command.run_id), command_owner)
                    if job.get("expires_at", 0) <= time.time():
                        raise ValueError("Signed asset preparation expired; prepare again")
                    if job["phase"] != "preparing":
                        raise ValueError("Asset push does not belong to this preparation")
                    self._check(job["job_id"])
                    asset = next(
                        (
                            Asset.model_validate(a)
                            for a in job["assets"]
                            if a["sha256"] == value["chunk"].get("sha256")
                        ),
                        None,
                    )
                    if asset is None:
                        raise ValueError("Asset is absent from the signed preparation")
                    offset = self.push_receiver.push(asset, value["chunk"], check)
                    if job.get("expires_at", 0) <= time.time():
                        raise ValueError("Signed asset preparation expired during transfer")
                    job["offsets"][asset.sha256] = offset
                    job["expires_at"] = time.time() + 600
                    if all(job["offsets"][a["sha256"]] == a["size"] for a in job["assets"]):
                        try:
                            self._prepared_asset_manifest(
                                [Asset.model_validate(a) for a in job["assets"]], job["runtime_id"])
                        except (ValueError, OSError) as error:
                            job.update(phase="failed", error=str(error)[:1000], finished_at=time.time())
                            self._save()
                            self._settle_preparation(job["job_id"])
                            raise
                        job.update(phase="prepared", finished_at=time.time())
                    self._save()
                    if job["phase"] == "prepared":
                        self._settle_preparation(job["job_id"])
                    return {"offset": offset, "phase": job["phase"]}
            return await asyncio.to_thread(accept_chunk)
        if action == "prima_prepare_renew":
            if (set(value) != {"job_id", "progress"} or type(value["progress"]) is not int
                    or not 0 <= value["progress"] <= (1 << 53) - 1):
                raise ValueError("Invalid signed preparation progress renewal")
            def renew_preparation():
                with self.lock:
                    job = self._command_job(value["job_id"], str(command.run_id), command_owner)
                    if (job["phase"] not in ("preparing", "prepared") or job.get("expires_at", 0) <= time.time()
                            or value["progress"] <= job.get("lease_progress", -1)):
                        raise ValueError("Preparation expired, cancelled or has no new progress")
                    check()
                    job.update(lease_progress=value["progress"], expires_at=time.time() + 600)
                    self._save()
                    return {"job_id": job["job_id"], "phase": job["phase"], "lease_progress": value["progress"],
                        "expires_at": job["expires_at"]}
            return await asyncio.to_thread(renew_preparation)
        if action == "prima_profile":
            if set(value) != {"preparation_job_id", "manifest_digest", "profile"}:
                raise ValueError("Upgrade the master for preparation-bound Prima profiling")
            request = ProfileRequest.model_validate(value["profile"])
            def start_profile():
                check()
                scope = self._profile_scope(value["preparation_job_id"], value["manifest_digest"],
                                            request, str(command.run_id), command_owner)
                return wire_status(self.profiles.start(request, control_owner=scope,
                    check=lambda: self._profile_owner_check(scope, authorize),
                    resolve_model=lambda digest: self.cached_model(digest, job_id=scope["preparation_job_id"])))
            return await asyncio.to_thread(start_profile)
        if action == "prima_status":
            if "profile_job_id" in value:
                if set(value) != {"profile_job_id"}:
                    raise ValueError("Invalid signed profile status request")
                return wire_status(self._owned_profile(value["profile_job_id"], str(command.run_id), command_owner))
            if "job_id" in value:
                with self.lock:
                    return wire_status(deepcopy(self._command_job(value["job_id"], str(command.run_id), command_owner)))
            def rank_status():
                check()
                status = self.rank.status(str(command.run_id), command_owner=command_owner)
                self._check_worker_network(str(command.run_id))
                if status.get("phase") == "starting" and status["rank"] != 0:
                    report = self.rank.readiness_report(str(command.run_id), check, command_owner=command_owner)
                    if report is not None:
                        return self.rank.ready(str(command.run_id), report, check)
                return status
            return await asyncio.to_thread(rank_status)
        if action == "prima_cancel":
            if "profile_job_id" in value:
                if set(value) != {"profile_job_id"}:
                    raise ValueError("Invalid signed profile cancellation request")
                def cancel_profile():
                    check()
                    self._owned_profile(value["profile_job_id"], str(command.run_id), command_owner)
                    return wire_status(self.profiles.cancel(value["profile_job_id"]))
                return await asyncio.to_thread(cancel_profile)
            if set(value) != {"job_id"}:
                raise ValueError("Invalid signed Prima preparation cancellation request")
            def cancel_preparation():
                check()
                with self.lock:
                    self._command_job(value["job_id"], str(command.run_id), command_owner)
                    return wire_status(self.cancel(value["job_id"]))
            return await asyncio.to_thread(cancel_preparation)
        if action == "prima_stop":
            return await asyncio.to_thread(self.rank.stop, str(command.run_id), check, command_owner=command_owner)
        if action == "prima_renew":
            def renew_rank():
                check()
                self.rank.status(str(command.run_id), command_owner=command_owner)
                self._check_worker_network(str(command.run_id))
                if self.ports.owner is not None:
                    manifest = ExecutionManifest.model_validate(self.ports.owner["manifest"])
                    self.ports.renew(manifest, command_owner)
                return self.rank.renew(str(command.run_id), check, command_owner=command_owner)
            return await asyncio.to_thread(renew_rank)
        if action == "prima_start":
            # Never accept an assessment flag or arbitrary process/asset path from the wire.
            if set(value) != {"manifest_digest"} or not isinstance(value["manifest_digest"], str):
                raise ValueError("Invalid Prima start request")
            from kllm.modules.primacpp.interactors.recipe import require_qualified

            def start_owned():
                check()
                manifest_path = self.cached_model(value["manifest_digest"])
                manifest = ExecutionManifest.model_validate_json(manifest_path.read_text())
                if (
                    manifest.run_id != str(command.run_id)
                    or manifest.digest != value["manifest_digest"]
                ):
                    raise ValueError("Signed start and execution manifest identity disagree")
                preparation = self._approved_preparation(manifest, command_owner)
                if self.service.state().process_pid:
                    raise ValueError("Stop the worker's existing inference process before Prima startup")
                if len(manifest.ranks) > 1:
                    self.service.prima_admission.validate_worker(manifest)
                    self.check_participant(manifest, self.service.state().node_id)
                    if manifest.network_id:
                        self.ports.validate(manifest, command_owner)
                require_qualified(self.registry.capture(manifest.runtime_id))
                model_asset = next(a for a in manifest.assets if a.kind == "model")
                def startup_check():
                    check()
                    if self._approved_preparation(manifest, command_owner) != preparation:
                        raise ValueError("Prima preparation changed before rank startup")
                def launch_rank():
                    return self.start_rank(manifest, self.cached_model(model_asset.sha256, job_id=preparation), startup_check,
                                           command_owner=command_owner)
                return self.ports.launch(manifest, command_owner, launch_rank, check) if (len(manifest.ranks) > 1 and manifest.network_id) else launch_rank()
            return await asyncio.to_thread(start_owned)
        if action == "prima_prepare":
            if set(value) != {"runtime_id", "assets", "operation_id"} or value[
                "operation_id"
            ] != str(command.run_id):
                raise ValueError("Invalid Prima asset preparation request")
            self.registry.capture(value["runtime_id"])
            assets = [Asset.model_validate(a) for a in value["assets"]]
            validate_asset_batch(assets, include_manifest=True)
            def prepare_assets():
                contract = next(a.sha256 for a in assets if a.kind == "manifest")
                lease = self.resources.acquire(prima_scope(command.run_id, command_owner, contract), "prepare")
                try:
                    return prepare_assets_owned(lease)
                except BaseException:
                    if lease not in self.preparation_reservations.values():
                        lease.release()
                    raise
            def prepare_assets_owned(lease):
                with self.lock:
                    if self.service.state().process_pid or self.rank.owner or self.ports.owner or any(
                        j["phase"] not in TERMINAL for j in self.jobs.values()
                    ):
                        raise ValueError("Worker is busy with inference or preparation")
                    job_id = str(uuid4())
                    diagnostic_job = {}
                    with diagnostics_scope(diagnostic_job, self.lock, check, self.service.state().node_id):
                        offsets = {a.sha256: self.push_receiver.offset(a) for a in assets}
                    check()
                    ready = all(offsets[a.sha256] == a.size for a in assets)
                    if ready:
                        self._prepared_asset_manifest(assets, value["runtime_id"])
                    self.jobs[job_id] = {
                        "job_id": job_id,
                        "runtime_id": value["runtime_id"],
                        "phase": "prepared" if ready else "preparing",
                        "error": None,
                        "request_id": str(command.request_id),
                        "sender": command.sender_node_id,
                        "authority_digest": authority_digest,
                        "operation_id": str(command.run_id),
                        "assets": [a.model_dump() for a in assets],
                        "offsets": offsets,
                        **diagnostic_job,
                        "expires_at": time.time() + 600,
                    }
                    if not ready:
                        self.preparation_reservations[job_id] = lease
                    try:
                        self._save()
                    except BaseException:
                        self.jobs.pop(job_id, None)
                        self.preparation_reservations.pop(job_id, None)
                        raise
                    if ready:
                        lease.release()
                return wire_status(self.job(job_id))
            return await asyncio.to_thread(prepare_assets)
        raise ValueError("Prima profiling or execution capability is not implemented")

    def expire(self):
        self.runs.monitor_once()
        self.rank.expire()
        self.ports.expire()
        with self.lock:
            for identity, job in self.jobs.items():
                if (
                    job.get("expires_at", float("inf")) <= time.time()
                    and job["phase"] not in {"failed", "cancelled", "interrupted"}
                ):
                    self.cancel(identity, reason="Preparation lease expired; prepare again")

    def close(self):
        self.closed = True
        self.runs.stop()
        if hasattr(self, "coordinator"):
            self.coordinator.close()
        if hasattr(self, "store"):
            self.store.close()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.profiles.close()
        if self.rank.owner:
            self.rank.stop(self.rank.owner["run_id"])
        if self.ports.owner:
            manifest = ExecutionManifest.model_validate(self.ports.owner["manifest"])
            self.ports.release(manifest.network_id, manifest.run_id)
