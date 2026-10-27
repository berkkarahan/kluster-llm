"""Prepared distributed Prima dispatch through signed rank ownership."""

from __future__ import annotations

import time
from uuid import uuid4
from pathlib import Path

import httpx

from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, authority_owner, inventory_identity
from kllm.modules.primacpp.interactors.launch import execution_settings, rank_arguments
from kllm.modules.primacpp.interactors.lifecycle import PrimaCoordinator
from kllm.modules.primacpp.interactors.qualification import manifest_context
from kllm.modules.primacpp.interactors.recipe import require_qualified
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.selection import snapshot
from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns
from kllm.shared.runtime_resources import prima_scope


class DistributedPrimaRuns:
    def __init__(self, control):
        self.control = control
        self.starting = False
        self.preparation = None
        self.context = None
        self.runtime = None
        self.dispatch_generation = None
        self.coordinator = PrimaCoordinator(control.root / "distributed", command=self._command,
            admit=self._admit, release=self._release, check=self._check)
        self.run_id = self.coordinator.active.run_id if self.coordinator.active else None

    def _call_admission(self, coroutine):
        return self.control.service.admission.call(coroutine)

    def _prepared(self, body, plan, runtime):
        owner = authority_owner(self.control.service.channels.master_authority())
        with self.control.lock:
            for job in reversed(list(self.control.jobs.values())):
                if (job.get("phase") == "prepared" and job.get("runtime_id") == runtime["runtime_id"]
                        and job.get("plan_digest") == plan.get("plan_digest")
                        and job.get("request_settings_digest") == body_digest(body.model_dump())
                        and job.get("command_owner") == owner and job.get("expires_at", 0) > time.time()):
                    manifest = ExecutionManifest.model_validate(job["manifest"])
                    if manifest.digest != job.get("manifest_digest"):
                        raise ValueError("Prepared Prima execution identity changed")
                    self.preparation = job["job_id"]
                    return manifest, owner
        raise ValueError("Prepare the exact Prima plan and assets under the current master authority before Run")

    def _auto_stage(self, body, model, plan, members, runtime):
        import json
        import shutil
        from starlette.exceptions import HTTPException
        from kllm.modules.primacpp.interactors.planner import materialize
        from kllm.modules.primacpp.interactors.adapters import adapter_paths
        from kllm.modules.primacpp.interactors.execution import (
            Asset,
            ExecutionManifest,
            ParticipantEvidence,
            authority_owner,
            validate_asset_batch,
        )

        owner = authority_owner(self.control.service.channels.master_authority())
        manifest, files = materialize(
            body,
            runtime,
            members,
            model,
            lambda *_: plan,
            expected_digest=plan.get("plan_digest"),
            adapters=adapter_paths(body.model_dump()),
            companions={
                kind: self.control.resolve_model(value)
                for kind, value in (
                    ("draft", body.draft_model if body.draft_enabled() else None),
                    ("projector", body.mmproj),
                )
                if value
            },
        )

        created_paths = []
        remote_jobs: dict[str, str] = {}
        try:
            evidence = {}
            for rank in manifest.ranks:
                self._check()
                if rank.node_id == self.control.service.state().node_id:
                    evidence[rank.node_id] = self.control.observe_participant(
                        runtime["runtime_id"], rank, manifest.backends[rank.node_id]
                    )
                else:
                    try:
                        raw = self.control._call(
                            rank.node_id,
                            manifest.run_id,
                            "prima_inventory",
                            {
                                "runtime_id": runtime["runtime_id"],
                                "rank": rank.model_dump(),
                                "backend": manifest.backends[rank.node_id],
                            },
                        )
                        evidence[rank.node_id] = ParticipantEvidence.model_validate(raw)
                    except Exception as exc:
                        raise HTTPException(
                            504,
                            detail={
                                "code": "PRIMA_STAGING_FAILED",
                                "node_id": rank.node_id,
                                "message": f"Worker {rank.node_id} failed during inventory collection: {exc}",
                            },
                        ) from exc

            manifest = ExecutionManifest.model_validate(
                manifest.model_copy(update={"participant_evidence": evidence}).model_dump()
            )

            manifest_file = self.control.root / manifest.run_id / "execution.json"
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
            created_paths.append(self.control.root / manifest.run_id)
            manifest_file.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")))
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

            for rank in manifest.ranks:
                self._check()
                node_id = rank.node_id
                if node_id == self.control.service.state().node_id:
                    for asset, p in files:
                        self.control.receiver.stager.prepare(
                            asset.sha256,
                            asset.size,
                            lambda offset, path=p: self.control._file_chunks(path, offset, check=self._check),
                            check=self._check,
                        )
                else:
                    try:
                        prep = self.control._call(
                            node_id,
                            manifest.run_id,
                            "prima_prepare",
                            {
                                "runtime_id": manifest.runtime_id,
                                "assets": [a.model_dump() for a, _ in files],
                                "operation_id": manifest.run_id,
                            },
                        )
                        remote_jobs[node_id] = prep["job_id"]
                        offsets = dict(prep.get("offsets") or {})
                        for asset, p in files:
                            offset = offsets.get(asset.sha256, 0)
                            if offset < asset.size:
                                self.control.grants.grant(node_id, manifest.run_id, asset, p, check=self._check)
                            while offset < asset.size:
                                self._check()
                                chunk = self.control.grants.chunk(node_id, manifest.run_id, asset.sha256, offset)
                                reply = self.control._call(
                                    node_id,
                                    manifest.run_id,
                                    "prima_asset_chunk",
                                    {"job_id": remote_jobs[node_id], "chunk": chunk},
                                )
                                offset = reply.get("offset")
                        status = self.control._call(node_id, manifest.run_id, "prima_status", {"job_id": remote_jobs[node_id]})
                        if status.get("phase") != "prepared":
                            raise ValueError(status.get("error") or "Worker preparation failed")
                    except HTTPException:
                        raise
                    except Exception as exc:
                        raise HTTPException(
                            504,
                            detail={
                                "code": "PRIMA_STAGING_FAILED",
                                "node_id": node_id,
                                "message": f"Worker {node_id} staging failed or timed out: {exc}",
                            },
                        ) from exc

            with self.control.lock:
                self.control.jobs[manifest.run_id] = {
                    "job_id": manifest.run_id,
                    "phase": "prepared",
                    "runtime_id": runtime["runtime_id"],
                    "plan_digest": plan.get("plan_digest"),
                    "request_settings_digest": body_digest(body.model_dump()),
                    "command_owner": owner,
                    "expires_at": time.time() + 3600,
                    "manifest": manifest.model_dump(),
                    "manifest_digest": manifest.digest,
                    "nodes": {},
                }
            self.preparation = manifest.run_id
            return manifest, owner
        except Exception:
            for path in created_paths:
                shutil.rmtree(path, ignore_errors=True)
            for nid, jid in remote_jobs.items():
                try:
                    self.control._call(nid, manifest.run_id, "prima_cancel", {"job_id": jid})
                except Exception:
                    pass
            raise

    def launch(self, body, model, plan, members):
        if self.coordinator.active or self.control.rank.owner or self.control.service.state().process_pid:
            raise ValueError("Stop and reconcile the previous Prima run before starting another")
        self.starting = True
        from kllm.modules.inference.interactors import load_progress

        try:
            runtime = self.control.registry.capture((snapshot() or {}).get("runtime_id"))
            require_qualified(runtime)
            try:
                manifest, owner = self._prepared(body, plan, runtime)
            except ValueError:
                from kllm.modules.primacpp.interactors.planner import is_prima_auto_deploy_active
                if is_prima_auto_deploy_active(body):
                    manifest, owner = self._auto_stage(body, model, plan, members, runtime)
                else:
                    raise
            model_asset = next((a for a in manifest.assets if a.kind == "model"), None)
            total_bytes = model_asset.size if model_asset else (model.stat().st_size if model.exists() else 0)
            load_progress.begin(
                model.name,
                None,
                total_bytes,
                detail="Verifying distributed Prima contract…",
            )
            self.control.runs.bind_resources(manifest, owner)
            self.run_id = manifest.run_id
            from kllm.modules.primacpp.interactors.planner import is_prima_auto_deploy_active
            auto_active = is_prima_auto_deploy_active(body)
            if len(manifest.ranks) < 2:
                raise ValueError("Distributed Prima requires at least 2 ranks")
            if not auto_active and (not body.network_id or manifest.network_id != body.network_id):
                raise ValueError("Distributed Prima requires the prepared private-network manifest")
            if not manifest.participant_evidence:
                raise ValueError("Prepare signed participant hardware evidence before distributed Prima Run")
            self.control.check_participants(manifest, self._check)
            settings = execution_settings(body, plan, model, self.control.service.state().infer_port)
            if manifest.settings != settings or inventory_identity(members, body.network_id) != manifest.inventory_digest:
                raise ValueError("Prima settings or private inventory changed; prepare the current plan")
            rank_arguments(Path(runtime["llama_server"]), settings, rank=0)
            rank_arguments(Path(runtime["llama_cli"]), settings, rank=1)
            rank = manifest.ranks[0]
            devices = select_vulkan_devices(discover_vulkan_devices(runtime), rank.devices) if rank.devices else []
            self.context = manifest_context(manifest, runtime, devices=devices)
            require_qualified(runtime, context=self.context)
            self.runtime = runtime
            self.coordinator.start(manifest, command_owner=owner)
            self._serving(manifest)
            load_progress.sample(int(settings["assessment_port"]))
            launch = self.control.rank.status(manifest.run_id, command_owner=owner)
            return {"plan": plan, "launch": {**launch, "binary": runtime["llama_server"], "role": "master"},
                "run_id": manifest.run_id, "engine": "primacpp", "runtime_id": manifest.runtime_id,
                "qualification_context": self.context}
        except BaseException as error:
            load_progress.fail(str(error))
            self.stop()
            raise
        finally:
            self.starting = False

    def _check(self):
        control, manifest = self.control, self.coordinator.active
        if (control.runs.cancelled.is_set()
                or self.dispatch_generation is not None and self.dispatch_generation != control.runs.generation):
            raise ValueError("Prima run cancelled")
        if control.closed or control.service.state().role != "master":
            raise ValueError("Prima master authority changed; stop the owned run")
        if manifest is None:
            return
        if authority_owner(control.service.channels.master_authority()) != self.coordinator.command_owner:
            raise ValueError("Prima master authority changed; cleanup remains owned")
        members = (
            control.service.prima_admission.members(manifest.network_id)
            if manifest.network_id
            else control.service.state().members
        )
        if inventory_identity(members, manifest.network_id) != manifest.inventory_digest:
            raise ValueError("Prima private inventory changed; prepare a new plan")
        if self.runtime is not None and self.context is not None:
            require_qualified(self.runtime, context=self.context)

    def _admit(self, edges):
        manifest = self.coordinator.active
        generation = self.coordinator.generation
        def check():
            self.coordinator._check(generation)
        def command(node, action, current, body):
            return self._port_command(node, action, current, body, check=check)
        if edges != manifest.transfer_edges:
            raise ValueError("Prima admission edges changed")
        for rank in manifest.ranks:
            check()
            if rank.node_id == self.control.service.state().node_id:
                capability = self.control.ports.capability()
            else:
                member = self.control.service.state().members.get(rank.node_id, {})
                if type(member.get("prima_resources_version")) is not int or member["prima_resources_version"] != 1:
                    raise ValueError(f"{rank.node_id}: upgrade the agent for Prima resource reservations")
                if type(member.get("prima_ports_version")) is not int or member["prima_ports_version"] != 2:
                    raise ValueError(f"{rank.node_id}: upgrade the agent for Prima listener admission")
                capability = self.control._call(rank.node_id, manifest.run_id, "prima_port_capabilities", {})
            if (capability != {"version": 2, "available": True, "cleanup_pending": False}
                    or type(capability.get("version")) is not int
                    or capability.get("available") is not True or capability.get("cleanup_pending") is not False):
                raise ValueError(f"{rank.node_id}: upgrade/enable or reconcile native Prima listener support")
        self._call_admission(self.control.service.prima_admission.reserve(manifest, native_ports=True))
        self._call_admission(self.control.service.prima_admission.admit_ports(manifest, command, check))

    def _port_command(self, node, action, manifest, body, *, check=None):
        control = self.control
        check = check or self._check
        if action != "prima_release_ports":
            check()
        if node != control.service.state().node_id:
            return control._call(node, manifest.run_id, action, body)
        if action == "prima_release_ports":
            return control.ports.release(manifest.network_id, manifest.run_id, self.coordinator.command_owner)
        from uuid import uuid4
        from kllm.modules.managed_network.interactors.identity import SignedRuntimeCommand

        check()
        authority = control.service._authority()
        if authority_owner(authority) != self.coordinator.command_owner:
            raise ValueError("Prima listener authority changed before local admission")
        command = SignedRuntimeCommand.model_validate({
            **authority.model_dump(exclude={"accepted_channel_id"}), "version": 1, "type": "rpc_command",
            "channel_id": authority.accepted_channel_id, "sender_node_id": node, "target_node_id": node,
            "sequence": control.service._sequence(authority.accepted_channel_id), "request_id": uuid4(), "run_id": manifest.run_id,
            "action": action, "ttl_ms": 60000, "body": body})
        command._wire = control.service.identity.sign(command.model_dump(mode="json"))
        if action == "prima_renew_ports":
            if control.rank.status(manifest.run_id, command_owner=self.coordinator.command_owner).get("phase") not in ("starting", "ready"):
                raise ValueError("Prima native renewal requires a live owned rank")
            return control.ports.renew(manifest, self.coordinator.command_owner,
                lambda: control.service.executor.platform.renew_prima_ports(command))
        return control.ports.admit(manifest, self.coordinator.command_owner,
            lambda: control.service.executor.platform.apply_prima_ports(command), check)

    def _release(self):
        if self.coordinator.started:
            raise ValueError("Prima ranks still own private-network admission")
        self._call_admission(self.control.service.prima_admission.release(self.coordinator.active, self._port_command))
        self.control.registry.clear_running(engine="primacpp", run_id=self.coordinator.active.run_id)

    def _master_status(self, manifest):
        control, owner = self.control, self.coordinator.command_owner
        status = control.rank.status(manifest.run_id, command_owner=owner)
        if status.get("phase") not in ("starting", "ready"):
            return status
        report = None
        if status["phase"] == "starting":
            report = control.rank.readiness_report(manifest.run_id, self._check, command_owner=owner)
            if report is None:
                return status
        # Ready ownership already records the validated startup acknowledgement.
        # Subsequent tracing can exceed the bounded startup-log parser's limit.
        if status["phase"] == "ready" or report is not None:
            try:
                with httpx.Client(timeout=1, trust_env=False) as client:
                    healthy = client.get(f"http://127.0.0.1:{manifest.settings['assessment_port']}/health").status_code == 200
                if healthy:
                    return control.rank.ready(manifest.run_id, report, self._check) if report is not None else status
            except httpx.HTTPError:
                pass
        return {**status, "phase": "starting"}

    def _command(self, node, action, manifest):
        control, owner = self.control, self.coordinator.command_owner
        local = node == control.service.state().node_id
        if action != "prima_stop":
            self._check()
            self._call_admission(control.service.prima_admission.validate(manifest))
            if local:
                control.check_participant(manifest, node)
                control.ports.validate(manifest, owner)
        if action == "prima_renew":
            self._renew_ports(node, manifest)
        if not local:
            # A current channel must not turn a persisted previous authority's
            # cleanup into a command targeting another master's owned run.
            if authority_owner(control.service.channels.master_authority()) != owner:
                if action == "prima_stop":
                    status = control._call(node, manifest.run_id, "prima_status", {})
                    if status.get("phase") == "idle":
                        return status  # A fresh signed absence proof can settle an expired previous lease.
                raise ValueError("Previous Prima authority requires worker cleanup acknowledgement")
            result = control._call(node, manifest.run_id, action,
                {"manifest_digest": manifest.digest} if action == "prima_start" else {})
            if action == "prima_start":
                self._renew_ports(node, manifest)
            return result
        if action == "prima_start":
            asset = next(a for a in manifest.assets if a.kind == "model")
            result = control.ports.launch(manifest, owner,
                lambda: control.start_rank(manifest, control.cached_model(asset.sha256, job_id=self.preparation),
                    self._check, command_owner=owner), self._check)
            self._renew_ports(node, manifest)
            if result.get("pid"):
                from kllm.modules.inference.interactors import load_progress

                load_progress.update(
                    pid=int(result["pid"]),
                    phase="loading",
                    detail="Loading model weights into memory…",
                )
            return result
        if action == "prima_stop":
            owned_pid = (control.rank.owner or {}).get("pid")
            result = control.rank.stop(manifest.run_id, command_owner=owner)
            state = control.service.state()
            config = state.active_config
            if (owned_pid and state.process_pid == owned_pid
                    and (config is None or isinstance(config, dict)
                         and config.get("engine") == "primacpp"
                         and config.get("run_id") == manifest.run_id)):
                state.process_pid = state.process_role = None
                state.save()
            return result
        if action == "prima_renew":
            control.ports.renew(manifest, owner)
            control.rank.renew(manifest.run_id, self._check, command_owner=owner)
        return self._master_status(manifest)

    def _renew_ports(self, node, manifest):
        from kllm.modules.managed_network.interactors.prima_ports import port_contract

        _, assignments, _ = self.control.service.prima_admission._contract(manifest)
        contract = port_contract(manifest, node, assignments)
        result = self._port_command(node, "prima_renew_ports", manifest,
            {"contract": contract.model_dump(mode="json")})
        if result != {"phase": "admitted", "manifest_digest": manifest.digest}:
            raise ValueError("Prima native helper lease renewal was not acknowledged")

    def _serving(self, manifest):
        owner = self.control.rank.status(manifest.run_id, command_owner=self.coordinator.command_owner)
        self._call_admission(self.control.service.prima_admission.serving(manifest, owner))
        self.control.registry.claim_running({**self.runtime, "pid": owner["pid"], "run_id": manifest.run_id})

    def stop(self):
        owned_run = self.coordinator.active.run_id if self.coordinator.active else getattr(self, "run_id", None)
        result = self.coordinator.stop()
        if owned_run:
            state = self.control.service.state()
            config = state.active_config
            if config and config.get("engine") == "primacpp" and config.get("run_id") == owned_run:
                state.process_pid = state.process_role = state.active_config = None
                state.save()
        self.run_id = None
        self.preparation = self.runtime = self.context = None
        return result

    def monitor_once(self):
        if self.coordinator.active is None:
            return
        state = self.control.service.state()
        try:
            result = self.coordinator.monitor_once()
            if result["phase"] == "serving":
                manifest = self.coordinator.active
                config = state.active_config
                if config is not None and (not isinstance(config, dict)
                        or config.get("engine") != "primacpp"
                        or config.get("run_id") != manifest.run_id):
                    self.stop()
                    return
                self._serving(manifest)
                current = state.active_config
                if current is not config and (not isinstance(current, dict)
                        or current.get("engine") != "primacpp"
                        or current.get("run_id") != manifest.run_id):
                    self.stop()
                    return
                owner = self.control.rank.owner
                if state.process_pid != owner["pid"]:
                    state.process_pid, state.process_role = owner["pid"], "master"
                    if state.active_config:
                        state.active_config["process_pid"] = owner["pid"]
                    state.save()
            elif self.coordinator.active is None:
                retired_run = self.run_id
                config = state.active_config
                owned_config = bool(
                    retired_run and isinstance(config, dict)
                    and config.get("engine") == "primacpp"
                    and config.get("run_id") == retired_run
                )
                self.stop()
                if owned_config and state.active_config is None:
                    state.apply_error = "Prima ranks stopped; prepare a new run"
                    state.save()
        except Exception as error:
            config = state.active_config
            if (config is None or isinstance(config, dict)
                    and config.get("engine") == "primacpp"
                    and config.get("run_id") == self.run_id):
                state.apply_error = f"Prima lifecycle cleanup/recovery pending: {error}"
                state.save()


class PrimaRuns:
    """One dispatch/stop boundary for local and prepared distributed runs."""

    def __init__(self, control):
        self.control = control
        self.local = LocalPrimaRuns(control)
        self.distributed = DistributedPrimaRuns(control)
        self.lock = self.local.lock
        self.cancelled = self.local.cancelled
        self.generation = 0
        self._starting = False
        self.resource_reservation = None
        owner = control.rank.owner
        if owner is not None:
            self.resource_reservation = control.resources.acquire(prima_scope(
                owner["run_id"], owner.get("command_owner"), owner["manifest_digest"]), "run")
        elif self.distributed.coordinator.active is not None:
            manifest = self.distributed.coordinator.active
            self.resource_reservation = control.resources.acquire(prima_scope(
                manifest.run_id, self.distributed.coordinator.command_owner, manifest.digest), "run")

    def bind_resources(self, manifest, owner):
        if self.resource_reservation is not None:
            self.resource_reservation.bind(prima_scope(manifest.run_id, owner, manifest.digest))

    def _release_if_idle(self):
        ports = getattr(self.control, "ports", None)
        if (self.resource_reservation is not None and not self.starting
                and self.local.manifest is None and self.distributed.coordinator.active is None
                and self.control.rank.owner is None and (ports is None or ports.owner is None)):
            self.resource_reservation.release()
            self.resource_reservation = None

    @property
    def starting(self):
        return self._starting or self.local.starting or self.distributed.starting

    @property
    def busy(self):
        ports = getattr(self.control, "ports", None)
        return (self.starting or self.local.manifest is not None or self.distributed.coordinator.active is not None
                or self.control.rank.owner is not None or ports is not None and ports.owner is not None)

    @property
    def manifest(self):
        return self.distributed.coordinator.active or self.local.manifest

    def launch(self, body, model, plan, members):
        generation = self.generation
        with self.lock:
            if generation != self.generation:
                raise ValueError("Prima run cancelled before dispatch")
            self._release_if_idle()
            if self.busy:
                raise ValueError("Reconcile the previous Prima rank, run or listener before Run")
            self.resource_reservation = self.control.resources.acquire(prima_scope(
                str(uuid4()), authority_owner(self.control.service.channels.master_authority()), plan.get("plan_digest", "")), "run")
            self._starting = True
            self.cancelled.clear()
            try:
                if generation != self.generation:
                    raise ValueError("Prima run cancelled before validation")
                with self.control.profiles.lock:
                    if any(j["phase"] not in {"prepared", "failed", "cancelled", "interrupted"}
                            for j in self.control.profiles.jobs.values()):
                        raise ValueError("Stop Prima profiling before starting inference")
                with self.control.lock:
                    if any(j["phase"] not in {"prepared", "failed", "cancelled", "interrupted"}
                            for j in self.control.jobs.values()):
                        raise ValueError("Wait for Prima preparation before Run")
                if len((plan.get("prima_topology") or {}).get("ranks", [])) > 1:
                    self.distributed.dispatch_generation = generation
                    return self.distributed.launch(body, model, plan, members)
                self.local.dispatch_generation = generation
                from kllm.modules.primacpp.interactors.planner import is_prima_auto_deploy_active
                if is_prima_auto_deploy_active(body) and getattr(body, "network_id", None):
                    body = body.model_copy(update={"network_id": None})
                return self.local.launch(body, model, plan, members)
            finally:
                self._starting = False
                self._release_if_idle()

    def stop(self):
        self.generation += 1
        self.cancelled.set()
        self.distributed.coordinator.cancelled.set()
        with self.lock:
            self.distributed.stop()
            result = self.local.stop()
            if self.control.ports.owner:
                manifest = ExecutionManifest.model_validate(self.control.ports.owner["manifest"])
                if manifest.network_id:
                    self.control.ports.release(manifest.network_id, manifest.run_id)
            self._release_if_idle()
            return result

    def monitor_once(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            self.distributed.monitor_once()
            self.local.monitor_once()
            self._release_if_idle()
        finally:
            self.lock.release()
