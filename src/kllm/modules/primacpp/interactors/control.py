"""Dispatch paired Prima commands and master-owned preparation jobs."""

from __future__ import annotations

import json
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

from kllm.modules.primacpp.interactors.execution import Asset, require_execution_capabilities, validate_asset_batch
from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor
from kllm.modules.primacpp.interactors.preparation import get_profile_jobs
from kllm.modules.primacpp.entities import DeviceProfile, ProfileRequest, RankAllocation
from kllm.modules.primacpp.interactors.planner import materialize, preview, candidates, candidate_profile_edges
from kllm.modules.primacpp.interactors.transfers import AssetReceiver, PushReceiver, TransferGrants
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import atomic_json, file_digest
from kllm.shared import native
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices, vulkan_environment
from kllm.modules.primacpp.interactors.launch import rank_arguments, execution_settings, materialize_template, device_arguments
from kllm.modules.primacpp.interactors.execution import authority_owner, ExecutionManifest
from kllm.modules.primacpp.interactors.distributed_runs import PrimaRuns
from kllm.modules.primacpp.interactors.participants import (
    observe_participant, ParticipantEvidence, require_participant, require_shared_identity)
from kllm.shared.runtime_resources import node_resources, prima_scope
from kllm.modules.primacpp.interactors.draft import draft_file, draft_mode
from kllm.modules.primacpp.interactors.extras import materialize_template_kwargs
from kllm.modules.primacpp.interactors.preparation_metrics import (
    ProgressWindow, diagnostics_scope, digest_file, local_chunk_bytes, measure, check_owned, wire_status, channel_timing,
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

    def members(self, body):
        if body.network_id:
            return self.service.prima_admission.members(body.network_id)
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
        if body.network_id:
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

    def plan(self, runtime_id, body, *, members=None, model=None):
        runtime = self.registry.capture(runtime_id)
        if runtime.get("engine") != "primacpp":
            raise ValueError("Prima preparation requires a Prima runtime")
        model = model or self.resolve_model(body.model)
        return preview(body, runtime, members if members is not None else self.members(body), model,
            lambda b, m: self._capacity_plan(runtime, b, m),
            score=lambda feasible: self._measured_plan(runtime, body, model, feasible))

    def start_preparation(
        self, runtime_id, body, expected_digest, request_id, *, include_profile=False
    ):
        if self.service.state().role != "master":
            raise ValueError("Prima preparation requires the Kluster master")
        from uuid import UUID

        UUID(request_id)
        payload = {
            "runtime_id": runtime_id,
            "settings": body.model_dump(),
            "plan_digest": expected_digest,
            "include_profile": include_profile,
        }
        with self.lock:
            if self.closed:
                raise ValueError("Prima preparation is closing")
            previous = next((j for j in self.jobs.values() if j["request_id"] == request_id), None)
            if previous:
                if previous["request_digest"] != body_digest(payload):
                    raise ValueError("Preparation request ID reused with different inputs")
                return deepcopy(previous)
            chunk_bytes = local_chunk_bytes()
            if self.service.state().process_pid or self.rank.owner or self.runs.busy or any(
                j["phase"] not in TERMINAL for j in self.jobs.values()
            ):
                raise ValueError("Stop inference or the active preparation before preparing Prima")
            identity = str(uuid4())
            lease = self.resources.acquire(prima_scope(identity, authority_owner(self.service.channels.master_authority()), expected_digest), "prepare")
            self.preparation_reservations[identity] = lease
            now = time.time()
            self.jobs[identity] = {
                "job_id": identity,
                "request_id": request_id,
                "request_digest": body_digest(payload),
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
            try:
                self._save()
                future = self.pool.submit(self._prepare, identity, runtime_id, body, expected_digest, include_profile)
                self.preparation_futures[identity] = future
                future.add_done_callback(lambda _: self._settle_preparation(identity))
            except BaseException:
                self.jobs.pop(identity, None)
                self.preparation_reservations.pop(identity, None)
                lease.release()
                raise
            return deepcopy(self.jobs[identity])

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
            latest = max(self.jobs.values(), key=lambda item: float(item.get("created_at") or 0.0))
            result = deepcopy(latest)
            if result["phase"] == "prepared" and result["job_id"] in self.preparation_reservations:
                result["phase"] = "settling"
            return result

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

    def _prepare(self, identity, runtime_id, body, digest, include_profile):
        with diagnostics_scope(self.jobs[identity], self.lock, lambda: self._check(identity),
                               self.service.state().node_id):
            return self._prepare_owned(identity, runtime_id, body, digest, include_profile)

    def _prepare_owned(self, identity, runtime_id, body, digest, include_profile):
        operation = identity
        from kllm.modules.primacpp.interactors.transfers import PreparationLeases

        def renew(node, job_id, progress):
            self._check(identity)
            return self._call(node, operation, "prima_prepare_renew", {"job_id": job_id, "progress": progress})
        leases = PreparationLeases(renew)
        def check():
            self._check(identity)
            leases.check()
        try:
            self._check(identity)
            self._update_job_progress(
                identity,
                phase="planning",
                label="Materializing placement manifest & verifying assets…",
                step_index=1,
                step_total=2 if include_profile else 1,
                fraction=0.04,
            )
            runtime = self.registry.capture(runtime_id)
            companions = {
                kind: self.resolve_model(value)
                for kind, value in [("draft", body.draft_model if body.draft_enabled() else None), ("projector", body.mmproj)]
                if value
            }
            from kllm.modules.primacpp.interactors.adapters import adapter_paths
            adapters = adapter_paths(body.model_dump())
            members = self.members(body)
            model = self.resolve_model(body.model)
            planned = self.plan(runtime_id, body, members=members, model=model)
            manifest, files = materialize(
                body,
                runtime,
                members,
                model,
                lambda b, m: planned,
                expected_digest=digest,
                companions=companions,
                adapters=adapters,
                score=lambda feasible: (feasible[0], planned["profiling"]),
            )
            manifest = manifest.model_copy(update={"settings": execution_settings(body, planned, model,
                getattr(self.service.state(), "infer_port", 18080))})
            step_total = 1 + (1 if include_profile else 0) + (1 if include_profile and len(manifest.ranks) > 1 else 0)
            stage_span = 0.45 if include_profile else 0.96
            if manifest.settings.get("prima_lora"):
                for rank in manifest.ranks:
                    if (rank.node_id != self.service.state().node_id
                            and (type(members[rank.node_id].get("prima_lora_assets_version")) is not int
                                 or members[rank.node_id]["prima_lora_assets_version"] != 1)):
                        raise ValueError(f"{rank.node_id}: upgrade the worker for verified Prima LoRA assets")
            if include_profile and len(manifest.ranks) > 1:
                feasible = candidates(body, members, lambda b, m: self._capacity_plan(runtime, b, m))
                manifest = ExecutionManifest.model_validate(manifest.model_copy(update={
                    "profile_edges": candidate_profile_edges(manifest, feasible, segmented=
                        "segmented_placement" in runtime.get("capabilities", {}).get("features", []))}).model_dump())
            if len(manifest.ranks) > 1:
                for rank in manifest.ranks:
                    if (rank.node_id != self.service.state().node_id
                            and (type(members[rank.node_id].get("prima_resources_version")) is not int
                                 or members[rank.node_id]["prima_resources_version"] != 1)):
                        raise ValueError(f"{rank.node_id}: upgrade the worker for Prima resource reservations")
            if include_profile:
                for rank in manifest.ranks:
                    if (rank.node_id != self.service.state().node_id
                            and members[rank.node_id].get("prima_profile_version") != 2):
                        raise ValueError(f"{rank.node_id}: upgrade the worker for preparation-bound Prima profiling")
                    if (len(manifest.ranks) > 1 and rank.node_id != self.service.state().node_id
                            and (type(members[rank.node_id].get("prima_link_profile_version")) is not int
                                 or members[rank.node_id]["prima_link_profile_version"] not in
                                 ((2,) if manifest.profile_edges else (1, 2)))):
                        raise ValueError(f"{rank.node_id}: upgrade the worker for signed candidate private-link profiling")
                    if (len(manifest.ranks) > 1 and rank.node_id != self.service.state().node_id
                            and members[rank.node_id].get("prima_profile_context_version") != 1):
                        raise ValueError(f"{rank.node_id}: upgrade the worker for fresh Prima profile context")
            if len(manifest.ranks) > 1:
                self.jobs[identity]["command_owner"] = authority_owner(self.service.channels.master_authority())
                evidence = {}
                for rank in manifest.ranks:
                    self._check(identity)
                    if rank.node_id == self.service.state().node_id:
                        value = self.observe_participant(runtime_id, rank, manifest.backends[rank.node_id])
                    else:
                        if members[rank.node_id].get("prima_inventory_version") != 1:
                            raise ValueError(f"{rank.node_id}: upgrade the worker agent for signed Prima hardware inventory")
                        raw = self._call(rank.node_id, operation, "prima_inventory", {
                            "runtime_id": runtime_id, "rank": rank.model_dump(), "backend": manifest.backends[rank.node_id]})
                        value = ParticipantEvidence.model_validate(raw)
                    require_shared_identity(value, rank.node_id, runtime_id, runtime)
                    evidence[rank.node_id] = value
                manifest = ExecutionManifest.model_validate(manifest.model_copy(update={
                    "participant_evidence": evidence}).model_dump())
            require_execution_capabilities(manifest, runtime.get("capabilities", {}))
            self.preparation_reservations[identity].bind(prima_scope(identity,
                authority_owner(self.service.channels.master_authority()), manifest.digest))
            manifest_file = self.root / identity / "execution.json"
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
            manifest_file.write_text(
                json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":"))
            )
            files.append(
                (
                    Asset(
                        name="execution.json",
                        kind="manifest",
                        sha256=manifest.digest,
                        size=manifest_file.stat().st_size,
                    ),
                    manifest_file,
                )
            )
            validate_asset_batch([asset for asset, _ in files], include_manifest=True)
            node_asset_bytes = sum(asset.size for asset, _ in files)
            total_stage_bytes = node_asset_bytes * max(1, len(manifest.ranks))
            with self.lock:
                self._check(identity)
                self.jobs[identity].update(
                    phase="preparing", manifest=manifest.model_dump(), manifest_digest=manifest.digest
                )
            self._update_job_progress(
                identity,
                phase="staging",
                label=f"Staging model assets across {len(manifest.ranks)} node{'s' if len(manifest.ranks) != 1 else ''}…",
                step_index=1,
                step_total=step_total,
                fraction=0.05,
                bytes_done=0,
                bytes_total=total_stage_bytes,
            )
            self._save()
            completed_nodes_bytes = 0
            for rank_idx, rank in enumerate(manifest.ranks, start=1):
                self._check(identity)
                if rank.node_id == self.service.state().node_id:
                    self.jobs[identity]["nodes"][rank.node_id] = {
                        "phase": "preparing",
                        "transferred_bytes": 0,
                        "asset_size": node_asset_bytes,
                    }
                    prior_asset_bytes = 0
                    for asset, path in files:
                        logical_done = 0
                        copy_start = 0
                        def _on_local_stage(stage, done: int, size: int, _node=rank.node_id, _prior=prior_asset_bytes, _name=asset.name):
                            nonlocal logical_done, copy_start
                            if stage == "resume":
                                copy_start = done
                            if stage in ("resume", "local_copy", "cache_hit", "publication"):
                                logical_done = done
                            node_done = _prior + logical_done
                            agg_done = completed_nodes_bytes + node_done
                            frac = 0.05 + (agg_done / max(1, total_stage_bytes)) * stage_span
                            with self.lock:
                                self._check(identity)
                                self.jobs[identity]["nodes"][_node].update(
                                    phase="preparing", transferred_bytes=node_done, asset_size=node_asset_bytes,
                                )
                            self._update_job_progress(
                                identity,
                                phase="staging",
                                label=lambda: f"{stage.replace('_', ' ').capitalize()}: {_name} on {_node} ({rank_idx}/{len(manifest.ranks)})…",
                                step_index=1,
                                step_total=step_total,
                                fraction=frac,
                                bytes_done=agg_done,
                                bytes_total=total_stage_bytes,
                                stage=stage, scope=(_node, _name),
                                rate_done=done - copy_start if stage == "local_copy" else done,
                                rate_total=size - copy_start if stage == "local_copy" else size,
                                rate_kind="local_copy" if stage == "local_copy" else
                                    "hash" if stage in ("cache_verify", "final_verify") else None,
                                force=stage in ("publication", "cache_hit"),
                            )
                        self.receiver.stager.prepare(
                            asset.sha256,
                            asset.size,
                            lambda offset, path=path: self._file_chunks(path, offset, self.jobs[identity]["local_chunk_bytes"], check),
                            check=lambda: self._check(identity),
                            stage_progress=_on_local_stage,
                        )
                        prior_asset_bytes += asset.size
                    result = {"phase": "prepared", "transferred_bytes": node_asset_bytes, "asset_size": node_asset_bytes}
                else:
                    if (members[rank.node_id].get("prima_control_version") != 1
                            or type(members[rank.node_id].get("prima_asset_lease_version")) is not int
                            or members[rank.node_id]["prima_asset_lease_version"] != 1):
                        raise ValueError(
                            f"{rank.node_id}: upgrade the worker agent for Prima preparation"
                        )
                    result = self._call(
                        rank.node_id,
                        operation,
                        "prima_prepare",
                        {
                            "runtime_id": runtime_id,
                            "assets": [a.model_dump() for a, _ in files],
                            "operation_id": operation,
                        },
                    )
                    remote_job = result["job_id"]
                    leases.enroll(rank.node_id, remote_job)
                    self.jobs[identity]["nodes"][rank.node_id] = result
                    self._save()
                    offsets = dict(result["offsets"])
                    prior_asset_bytes = 0
                    for asset, path in files:
                        offset = offsets.get(asset.sha256, 0)
                        if type(offset) is not int or not 0 <= offset <= asset.size:
                            raise ValueError("Worker prepared offset is outside the declared asset")
                        transfer_start = offset
                        with measure("remote_resume", node=rank.node_id, asset=asset.sha256) as counts:
                            counts["resume_bytes" if offset < asset.size else "cache_hit_bytes"] = offset
                        if offset < asset.size:
                            self._update_job_progress(identity, phase="staging", stage="grant_hash",
                                scope=(rank.node_id, asset.sha256), label=f"Verifying transfer source: {asset.name}…",
                                bytes_done=completed_nodes_bytes + prior_asset_bytes + offset, bytes_total=total_stage_bytes,
                                step_total=step_total,
                                fraction=0.05 + (completed_nodes_bytes + prior_asset_bytes + offset) / max(1, total_stage_bytes) * stage_span)
                            # Future assets do not spend their lease while another file transfers.
                            self.grants.grant(rank.node_id, operation, asset, path,
                                progress=leases.advance, check=check)
                        self._update_job_progress(identity, phase="staging",
                            stage="remote_payload" if offset < asset.size else "remote_cache_hit",
                            scope=(rank.node_id, asset.sha256),
                            label=f"Transferring {asset.name} to {rank.node_id}…" if offset < asset.size else
                                f"Reusing verified {asset.name} on {rank.node_id}",
                            bytes_done=completed_nodes_bytes + prior_asset_bytes + offset, bytes_total=total_stage_bytes,
                            fraction=0.05 + (completed_nodes_bytes + prior_asset_bytes + offset) / max(1, total_stage_bytes) * stage_span,
                            step_total=step_total, rate_done=0, rate_total=asset.size - transfer_start,
                            rate_kind="acknowledged_payload" if offset < asset.size else None)
                        while offset < asset.size:
                            check()
                            chunk = self.grants.chunk(rank.node_id, operation, asset.sha256, offset)
                            reply = self._call(
                                rank.node_id,
                                operation,
                                "prima_asset_chunk",
                                {"job_id": remote_job, "chunk": chunk},
                            )
                            acknowledged = reply.get("offset")
                            with measure("remote_acknowledged", node=rank.node_id, asset=asset.sha256, sample=64) as counts:
                                self.grants.acknowledge(rank.node_id, operation, asset.sha256, acknowledged)
                                counts["acknowledged_bytes"] = acknowledged - offset
                            leases.advance(acknowledged - offset)
                            offset = acknowledged
                            offsets[asset.sha256] = offset
                            node_done = prior_asset_bytes + offset
                            agg_done = completed_nodes_bytes + node_done
                            frac = 0.05 + (agg_done / max(1, total_stage_bytes)) * stage_span
                            check()
                            with self.lock:
                                self._check(identity)
                                self.jobs[identity]["nodes"][rank.node_id].update(
                                    phase="preparing", transferred_bytes=offset, asset_size=asset.size
                                )
                            self._update_job_progress(
                                identity,
                                phase="staging",
                                label=lambda: f"Transferring {asset.name} to {rank.node_id} ({rank_idx}/{len(manifest.ranks)})…",
                                step_index=1,
                                step_total=step_total,
                                fraction=frac,
                                bytes_done=agg_done,
                                bytes_total=total_stage_bytes,
                                stage="remote_payload", scope=(rank.node_id, asset.sha256),
                                rate_done=offset - transfer_start, rate_total=asset.size - transfer_start,
                                rate_kind="acknowledged_payload",
                                force=offset == asset.size,
                            )
                            if offset % (24 * 1024 * 256) == 0 or offset == asset.size:
                                self._save()
                        prior_asset_bytes += asset.size
                    result = self._call(
                        rank.node_id, operation, "prima_status", {"job_id": remote_job}
                    )
                    if result["phase"] != "prepared":
                        raise ValueError(result.get("error") or "Worker preparation failed")
                completed_nodes_bytes += node_asset_bytes
                self.jobs[identity]["nodes"][rank.node_id] = result
                leases.advance()
                self._save()
            self._check(identity)
            if self.plan(runtime_id, body)["plan_digest"] != digest:
                raise ValueError("Inventory or settings changed during preparation; re-plan")
            if include_profile:
                profile_span = 0.35 if len(manifest.ranks) > 1 else 0.49
                rank_count = max(1, len(manifest.ranks))
                for rank_idx, rank in enumerate(manifest.ranks, start=1):
                    self._check(identity)
                    rank_base = 0.50 + ((rank_idx - 1) / rank_count) * profile_span
                    rank_width = profile_span / rank_count
                    self._update_job_progress(
                        identity,
                        phase="profiling",
                        label=f"Profiling hardware on {rank.node_id} ({rank_idx}/{rank_count})…",
                        step_index=2,
                        step_total=step_total,
                        fraction=rank_base,
                        scope=(rank.node_id,),
                    )
                    request = ProfileRequest(
                        runtime_id=runtime_id,
                        model=next(a.sha256 for a in manifest.assets if a.kind == "model"),
                        context=manifest.settings["context"],
                        threads=manifest.settings["threads"],
                        backend=manifest.backends[rank.node_id],
                        devices=rank.devices if manifest.backends[rank.node_id] == "vulkan" and (rank.ngl or rank.output_gpu) else None,
                        mode="generation" if len(manifest.ranks) == 1 and rank.ngl
                            and manifest.backends[rank.node_id] == "vulkan"
                            and "gpu_execution_evidence_v1" in runtime.get("capabilities", {}).get("features", []) else "kernel",
                        gpu_layers=rank.ngl if len(manifest.ranks) == 1 and rank.ngl
                            and manifest.backends[rank.node_id] == "vulkan"
                            and "gpu_execution_evidence_v1" in runtime.get("capabilities", {}).get("features", []) else None,
                        request_id=str(uuid4()),
                        output_gpu=rank.output_gpu,
                    )
                    if request.mode == "generation":
                        request = ProfileRequest.model_validate({**request.model_dump(), **{
                            key: manifest.settings.get(key, default) for key, default in
                            (("cache_type_k", "f16"), ("cache_type_v", "f16"), ("flash_attention", "off"))}})
                    if rank.node_id == self.service.state().node_id:
                        scope = {**authority_owner(self.service.channels.master_authority()), "sender": rank.node_id,
                            "operation_id": operation, "preparation_job_id": identity, "manifest_digest": manifest.digest,
                            "runtime_id": runtime_id, "model_sha256": request.model, "node_id": rank.node_id,
                            "backend": request.backend, "context": request.context, "threads": request.threads,
                            **({"total_layers": manifest.total_layers} if request.mode == "generation" else {})}
                        profile = self.profiles.start(request, control_owner=scope, check=lambda: self._check(identity))
                        self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                        while profile["phase"] not in TERMINAL:
                            if profile["phase"] == "cleanup_pending":
                                raise ValueError("Owned Prima profile cleanup is pending")
                            check()
                            if profile.get("pid") and profile.get("process_created_at"):
                                leases.advance()
                            prof_prog = profile.get("progress") or {}
                            sub_frac = float(prof_prog.get("fraction") or 0.1)
                            sub_label = prof_prog.get("label") or f"Profiling {rank.node_id}…"
                            self._update_job_progress(
                                identity,
                                phase="profiling",
                                label=f"{rank.node_id}: {sub_label}",
                                step_index=2,
                                step_total=step_total,
                                fraction=rank_base + sub_frac * rank_width,
                                bytes_done=int(prof_prog.get("bytes_done") or 0),
                                bytes_total=int(prof_prog.get("bytes_total") or 0),
                                stage=prof_prog.get("stage", "profiling"), scope=(rank.node_id,),
                            )
                            time.sleep(0.2)
                            profile = self.profiles.job(profile["job_id"])
                            self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                    else:
                        remote_preparation = self.jobs[identity]["nodes"][rank.node_id]["job_id"]
                        scope = {**self.jobs[identity]["command_owner"], "sender": manifest.ranks[0].node_id,
                            "operation_id": operation, "preparation_job_id": remote_preparation,
                            "manifest_digest": manifest.digest, "runtime_id": runtime_id,
                            "model_sha256": request.model, "node_id": rank.node_id, "backend": request.backend,
                            "context": request.context, "threads": request.threads}
                        profile = self._call(
                            rank.node_id, operation, "prima_profile", {"preparation_job_id": remote_preparation,
                                "manifest_digest": manifest.digest, "profile": request.model_dump(exclude_none=True)}
                        )
                        self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                        while profile["phase"] not in TERMINAL:
                            if profile["phase"] == "cleanup_pending":
                                raise ValueError("Owned Prima profile cleanup is pending")
                            check()
                            if profile.get("pid") and profile.get("process_created_at"):
                                leases.advance()
                            prof_prog = profile.get("progress") or {}
                            sub_frac = float(prof_prog.get("fraction") or 0.1)
                            sub_label = prof_prog.get("label") or f"Profiling {rank.node_id}…"
                            self._update_job_progress(
                                identity,
                                phase="profiling",
                                label=f"{rank.node_id}: {sub_label}",
                                step_index=2,
                                step_total=step_total,
                                fraction=rank_base + sub_frac * rank_width,
                                bytes_done=int(prof_prog.get("bytes_done") or 0),
                                bytes_total=int(prof_prog.get("bytes_total") or 0),
                                stage=prof_prog.get("stage", "profiling"), scope=(rank.node_id,),
                            )
                            time.sleep(0.2)
                            profile = self._call(
                                rank.node_id,
                                operation,
                                "prima_status",
                                {"profile_job_id": profile["job_id"]},
                            )
                            self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                    self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                    if profile["phase"] != "prepared":
                        raise ValueError(profile.get("error") or "Rank profiling failed")
                    if len(manifest.ranks) > 1:
                        self._retain_profile(profile, manifest, rank, request, operation, scope)
                    self._save()
            if include_profile and len(manifest.ranks) > 1:
                from kllm.modules.primacpp.interactors.link_profile import collect_links

                self._update_job_progress(
                    identity,
                    phase="links",
                    label="Measuring Ring-TCP private links…",
                    step_index=3,
                    step_total=step_total,
                    fraction=0.85,
                )
                with measure("private_link_profile"):
                    self.jobs[identity]["links"] = collect_links(self, manifest, identity, check, leases.advance)
                self._save()
            leases.advance()
            leases.check(force=True)  # Align every complete worker approval with final preparation.
            self._update_job_progress(
                identity,
                phase="prepared",
                label="Prima assets prepared and profiled" if include_profile else "Prima assets prepared",
                step_index=step_total,
                step_total=step_total,
                fraction=1.0,
                bytes_done=total_stage_bytes,
                bytes_total=total_stage_bytes,
            )
            with self.lock:
                self._check(identity)
                self.jobs[identity].update(phase="prepared", finished_at=time.time(), expires_at=time.time() + 600)
        except Exception as error:
            final_phase = "cancelled" if identity in self.cancelled else "failed"
            prev_prog = self.jobs.get(identity, {}).get("progress") or {}
            exact_bytes = self.jobs.get(identity, {}).get("progress_bytes") or {}
            self._update_job_progress(
                identity,
                phase=final_phase,
                label=str(error)[:240],
                step_index=int(prev_prog.get("step_index") or 1),
                step_total=int(prev_prog.get("step_total") or 1),
                fraction=float(prev_prog.get("fraction") or 0.0),
                bytes_done=int(exact_bytes.get("done", prev_prog.get("bytes_done")) or 0),
                bytes_total=int(exact_bytes.get("total", prev_prog.get("bytes_total")) or 0),
            )
            with self.lock:
                if self.jobs[identity]["phase"] != "cancelled":
                    self.jobs[identity].update(
                        phase=final_phase, error=str(error)[:1000], finished_at=time.time(),
                    )
        finally:
            if self.jobs[identity]["phase"] in ("failed", "cancelled"):
                for node, result in self.jobs[identity]["nodes"].items():
                    profile_job = (result.get("profile") or {}).get("job_id")
                    if profile_job:
                        try:
                            if node == self.service.state().node_id:
                                self.profiles.cancel(profile_job)
                            else:
                                self._call(
                                    node, operation, "prima_cancel", {"profile_job_id": profile_job}
                                )
                        except Exception:
                            pass
                    if node != self.service.state().node_id and result.get("job_id"):
                        try:
                            self._call(
                                node, operation, "prima_cancel", {"job_id": result["job_id"]}
                            )
                        except Exception:
                            pass  # Worker preparation expires independently; partial assets remain resumable.
            self.grants.revoke(operation)
            self._save()

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

    def job(self, identity):
        with self.lock:
            result = deepcopy(self.jobs[identity])
            if result["phase"] == "prepared" and identity in self.preparation_reservations:
                result["phase"] = "settling"
            return result

    def cancel(self, identity, *, reason=None):
        with self.lock:
            job = self.jobs[identity]
            if job["phase"] not in {"failed", "interrupted"} or identity in self.preparation_reservations:
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
            return deepcopy(job)

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
                return self.ports.launch(manifest, command_owner, launch_rank, check) if len(manifest.ranks) > 1 else launch_rank()
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
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.profiles.close()
        if self.rank.owner:
            self.rank.stop(self.rank.owner["run_id"])
        if self.ports.owner:
            manifest = ExecutionManifest.model_validate(self.ports.owner["manifest"])
            self.ports.release(manifest.network_id, manifest.run_id)
