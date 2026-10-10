"""Dispatch paired Prima commands and master-owned preparation jobs."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from kllm.modules.primacpp.interactors.execution import Asset, require_execution_capabilities
from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor
from kllm.modules.primacpp.interactors.preparation import ProfileJobs
from kllm.modules.primacpp.entities import ProfileRequest
from kllm.modules.primacpp.interactors.planner import materialize, preview
from kllm.modules.primacpp.interactors.transfers import AssetReceiver, PushReceiver, TransferGrants
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import atomic_json
from kllm.shared import native

TERMINAL = {"prepared", "failed", "cancelled", "interrupted"}


def spawn_rank(runtime, rank, model, manifest_path, log_path, settings):
    from kllm.shared import process_registry

    binary = Path(runtime["llama_server"] if rank.rank == 0 else runtime["llama_cli"])
    if binary.parent.resolve() != Path(runtime["artifact_dir"]).resolve():
        raise ValueError("Prima binary is outside verified artifacts")
    env = {**runtime.get("runtime_env", {}), "KLLM_PRIMA_BIND": rank.address}
    ids = [d.removeprefix("Vulkan") for d in rank.devices if d.startswith("Vulkan")]
    if ids:
        env["GGML_VK_VISIBLE_DEVICES"] = ",".join(ids)
    n = native.require_native()
    if not hasattr(n, "start_prima_rank"):
        raise ValueError("Rebuild Kluster for Prima rank launch support")
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
            }
        )
    )
    if not result.get("process_start_token"):
        native.stop_process(result["pid"], force=True)
        raise ValueError("Prima child process identity is unavailable")
    process_registry.register_child(
        result["pid"], "llama-server" if rank.rank == 0 else "llama-cli"
    )
    return {"pid": result["pid"], "start_token": result["process_start_token"]}


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
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prima-prepare")
        self.profiles = ProfileJobs(
            self.root / "profiles",
            registry.capture,
            self.cached_model,
            service.state,
            registry._kill,
        )
        self.closed = False
        self.cancelled: set[str] = set()
        self.jobs = {}
        path = self.root / "jobs.json"
        if path.exists():
            self.jobs = json.loads(path.read_text())
            for job in self.jobs.values():
                if job["phase"] not in TERMINAL:
                    job.update(phase="interrupted", error="Agent restarted; prepare again")
        self.rank = RankSupervisor(
            self.root / "rank",
            service.state().node_id,
            capture=registry.capture,
            spawn=spawn_rank,
            stop=lambda pid: native.stop_process(pid, force=True),
            alive=native.process_alive,
            identity=lambda pid: str(native.require_native()._process_identity(pid)[0]),
        )

    def cached_model(self, digest):
        path, _ = self.receiver.stager._paths(digest)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Prepared model is unavailable")
        # Split GGUF readers resolve companions by basename. Restore that layout in
        # an owned directory while retaining digest-addressed cache publication.
        with self.lock:
            for job in reversed(list(self.jobs.values())):
                assets = job.get("assets") or (job.get("manifest") or {}).get("assets", [])
                if not any(
                    a["sha256"] == digest and a["kind"] in ("model", "draft", "projector")
                    for a in assets
                ):
                    continue
                directory = self.root / "model-layouts" / job["job_id"]
                directory.mkdir(parents=True, exist_ok=True)
                for raw in assets:
                    asset = Asset.model_validate(raw)
                    source, _ = self.receiver.stager._paths(asset.sha256)
                    target = directory / asset.name
                    if source.is_file() and not target.exists():
                        import shutil

                        try:
                            target.hardlink_to(source)
                        except OSError:
                            shutil.copyfile(source, target)
                    if asset.sha256 == digest:
                        selected = target
                return selected
        return path

    def _save(self):
        atomic_json(self.root / "jobs.json", self.jobs)

    def members(self, body):
        if body.network_id:
            return self.service.admission.members(body.network_id)
        return deepcopy(self.service.state().members)

    def plan(self, runtime_id, body):
        runtime = self.registry.capture(runtime_id)
        if runtime.get("engine") != "primacpp":
            raise ValueError("Prima preparation requires a Prima runtime")
        return preview(
            body, runtime, self.members(body), self.resolve_model(body.model), self.execute_plan
        )

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
            if self.service.state().process_pid or any(
                j["phase"] not in TERMINAL for j in self.jobs.values()
            ):
                raise ValueError("Stop inference or the active preparation before preparing Prima")
            identity = str(uuid4())
            self.jobs[identity] = {
                "job_id": identity,
                "request_id": request_id,
                "request_digest": body_digest(payload),
                "phase": "queued",
                "runtime_id": runtime_id,
                "plan_digest": expected_digest,
                "nodes": {},
                "error": None,
                "created_at": time.time(),
            }
            self._save()
            self.pool.submit(
                self._prepare, identity, runtime_id, body, expected_digest, include_profile
            )
            return deepcopy(self.jobs[identity])

    def _check(self, job_id):
        if self.jobs.get(job_id, {}).get("manifest") and self.service.state().role != "master":
            raise ValueError("Master authority changed during Prima preparation")
        if self.closed or job_id in self.cancelled:
            raise ValueError("Prima preparation cancelled")
        if self.service.state().process_pid:
            raise ValueError("Inference started during preparation")

    def _call(self, node, run_id, action, body):
        session = self.service.channels.sessions.get(node)
        if session is None:
            raise ValueError(f"{node}: paired Prima command channel unavailable")
        result = self.service.admission.call(
            session.request_runtime(
                run_id, action, ttl_ms=60000 if action == "prima_asset_chunk" else 10000, body=body
            )
        )
        if result.get("status") == "failed":
            raise ValueError(result.get("message") or "Prima command failed")
        return result

    def _prepare(self, identity, runtime_id, body, digest, include_profile):
        operation = identity
        try:
            self._check(identity)
            runtime = self.registry.capture(runtime_id)
            companions = {
                kind: self.resolve_model(value)
                for kind, value in [("draft", body.draft_model), ("projector", body.mmproj)]
                if value
            }
            members = self.members(body)
            manifest, files = materialize(
                body,
                runtime,
                members,
                self.resolve_model(body.model),
                self.execute_plan,
                expected_digest=digest,
                companions=companions,
            )
            require_execution_capabilities(manifest, runtime.get("capabilities", {}))
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
            self.jobs[identity].update(
                phase="preparing", manifest=manifest.model_dump(), manifest_digest=manifest.digest
            )
            self._save()
            for rank in manifest.ranks:
                self._check(identity)
                if rank.node_id == self.service.state().node_id:
                    for asset, path in files:
                        self.receiver.stager.prepare(
                            asset.sha256,
                            asset.size,
                            lambda offset, path=path: self._file_chunks(path, offset),
                            check=lambda: self._check(identity),
                        )
                    result = {"phase": "prepared"}
                else:
                    if members[rank.node_id].get("prima_control_version") != 1:
                        raise ValueError(
                            f"{rank.node_id}: upgrade the worker agent for Prima preparation"
                        )
                    for asset, path in files:
                        self.grants.grant(rank.node_id, operation, asset, path)
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
                    self.jobs[identity]["nodes"][rank.node_id] = result
                    self._save()
                    for asset, path in files:
                        offset = result["offsets"].get(asset.sha256, 0)
                        while offset < asset.size:
                            self._check(identity)
                            chunk = self.grants.chunk(rank.node_id, operation, asset.sha256, offset)
                            reply = self._call(
                                rank.node_id,
                                operation,
                                "prima_asset_chunk",
                                {"job_id": remote_job, "chunk": chunk},
                            )
                            offset = reply["offset"]
                            self.jobs[identity]["nodes"][rank.node_id].update(
                                phase="preparing", transferred_bytes=offset, asset_size=asset.size
                            )
                            if offset % (24 * 1024 * 256) == 0 or offset == asset.size:
                                self._save()
                    result = self._call(
                        rank.node_id, operation, "prima_status", {"job_id": remote_job}
                    )
                    if result["phase"] != "prepared":
                        raise ValueError(result.get("error") or "Worker preparation failed")
                self.jobs[identity]["nodes"][rank.node_id] = result
                self._save()
            self._check(identity)
            if self.plan(runtime_id, body)["plan_digest"] != digest:
                raise ValueError("Inventory or settings changed during preparation; re-plan")
            if include_profile:
                for rank in manifest.ranks:
                    self._check(identity)
                    request = ProfileRequest(
                        runtime_id=runtime_id,
                        model=next(a.sha256 for a in manifest.assets if a.kind == "model"),
                        context=body.context,
                        threads=body.threads,
                        request_id=str(uuid4()),
                    )
                    if rank.node_id == self.service.state().node_id:
                        profile = self.profiles.start(request)
                        self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                        while profile["phase"] not in TERMINAL:
                            self._check(identity)
                            time.sleep(0.2)
                            profile = self.profiles.job(profile["job_id"])
                    else:
                        profile = self._call(
                            rank.node_id, operation, "prima_profile", request.model_dump()
                        )
                        self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                        while profile["phase"] not in TERMINAL:
                            self._check(identity)
                            time.sleep(0.2)
                            profile = self._call(
                                rank.node_id,
                                operation,
                                "prima_status",
                                {"profile_job_id": profile["job_id"]},
                            )
                    self.jobs[identity]["nodes"][rank.node_id]["profile"] = profile
                    if profile["phase"] != "prepared":
                        raise ValueError(profile.get("error") or "Rank profiling failed")
                    self._save()
            self.jobs[identity].update(phase="prepared", finished_at=time.time())
        except Exception as error:
            self.jobs[identity].update(
                phase="cancelled" if identity in self.cancelled else "failed",
                error=str(error)[:1000],
                finished_at=time.time(),
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
    def _file_chunks(path, offset):
        from kllm.modules.primacpp.interactors.transfers import CHUNK_BYTES

        with path.open("rb") as source:
            source.seek(offset)
            while chunk := source.read(CHUNK_BYTES):
                yield chunk

    def job(self, identity):
        with self.lock:
            return deepcopy(self.jobs[identity])

    def cancel(self, identity):
        with self.lock:
            job = self.jobs[identity]
            if job["phase"] not in TERMINAL:
                self.cancelled.add(identity)
                job.update(phase="cancelled", finished_at=time.time())
                self._save()
            return deepcopy(job)

    async def execute(self, command, deadline, authorize):
        def check():
            if time.monotonic() >= deadline:
                raise ValueError("Prima signed command expired")
            authorize()

        check()
        action, value = command.action, command.body
        authority_digest = body_digest(
            {
                k: str(getattr(command, k))
                for k in (
                    "cluster_binding_id",
                    "master_node_id",
                    "master_key_id",
                    "master_term",
                    "master_session_id",
                )
            }
        )
        if action == "prima_asset_chunk":
            if set(value) != {"job_id", "chunk"}:
                raise ValueError("Invalid prepared asset push")
            with self.lock:
                job = self.jobs[value["job_id"]]
                if (
                    job.get("operation_id") != str(command.run_id)
                    or job.get("sender") != command.sender_node_id
                    or job.get("authority_digest") != authority_digest
                    or job["phase"] != "preparing"
                ):
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
                job["offsets"][asset.sha256] = offset
                if all(job["offsets"][a["sha256"]] == a["size"] for a in job["assets"]):
                    job.update(phase="prepared", finished_at=time.time())
                self._save()
                return {"offset": offset, "phase": job["phase"]}
        if action == "prima_profile":
            request = ProfileRequest.model_validate(value)
            return self.profiles.start(request)
        if action == "prima_status":
            if "profile_job_id" in value:
                return self.profiles.job(value["profile_job_id"])
            return self.job(value["job_id"]) if "job_id" in value else self.rank.status()
        if action == "prima_cancel":
            if "profile_job_id" in value:
                return self.profiles.cancel(value["profile_job_id"])
            return self.cancel(value["job_id"])
        if action == "prima_stop":
            return self.rank.stop(str(command.run_id), check)
        if action == "prima_renew":
            return self.rank.renew(str(command.run_id), check)
        if action == "prima_start":
            # Never accept an assessment flag or arbitrary process/asset path from the wire.
            if set(value) != {"manifest_digest"}:
                raise ValueError("Invalid Prima start request")
            from kllm.modules.primacpp.interactors.execution import ExecutionManifest
            from kllm.modules.primacpp.interactors.recipe import require_qualified

            manifest_path = self.cached_model(value["manifest_digest"])
            manifest = ExecutionManifest.model_validate_json(manifest_path.read_text())
            if (
                manifest.run_id != str(command.run_id)
                or manifest.digest != value["manifest_digest"]
            ):
                raise ValueError("Signed start and execution manifest identity disagree")
            require_qualified(self.registry.capture(manifest.runtime_id))
            model_asset = next(a for a in manifest.assets if a.kind == "model")
            return self.rank.start(manifest, self.cached_model(model_asset.sha256), check)
        if action == "prima_prepare":
            if set(value) != {"runtime_id", "assets", "operation_id"} or value[
                "operation_id"
            ] != str(command.run_id):
                raise ValueError("Invalid Prima asset preparation request")
            self.registry.capture(value["runtime_id"])
            assets = [Asset.model_validate(a) for a in value["assets"]]
            with self.lock:
                if self.service.state().process_pid or any(
                    j["phase"] not in TERMINAL for j in self.jobs.values()
                ):
                    raise ValueError("Worker is busy with inference or preparation")
                job_id = str(uuid4())
                offsets = {a.sha256: self.push_receiver.offset(a) for a in assets}
                ready = all(offsets[a.sha256] == a.size for a in assets)
                self.jobs[job_id] = {
                    "job_id": job_id,
                    "phase": "prepared" if ready else "preparing",
                    "error": None,
                    "request_id": str(command.request_id),
                    "sender": command.sender_node_id,
                    "authority_digest": authority_digest,
                    "operation_id": str(command.run_id),
                    "assets": [a.model_dump() for a in assets],
                    "offsets": offsets,
                    "expires_at": time.time() + 600,
                }
                self._save()
            return self.job(job_id)
        raise ValueError("Prima profiling or execution capability is not implemented")

    def expire(self):
        self.rank.expire()
        with self.lock:
            for identity, job in self.jobs.items():
                if (
                    job.get("expires_at", float("inf")) <= time.time()
                    and job["phase"] not in TERMINAL
                ):
                    self.cancel(identity)

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.profiles.close()
        if self.rank.owner:
            self.rank.stop(self.rank.owner["run_id"])
