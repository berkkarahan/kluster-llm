"""Master target reconciliation over the existing signed node channel."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException

from kllm.modules.cluster.interactors.membership_state import LIVENESS_TIMEOUT_S
from kllm.modules.runtimes.entities import BuildRequest, RuntimeTarget, TargetRequest, body_digest
from kllm.modules.runtimes.interactors.registry import RuntimeRegistry, TERMINAL
from kllm.modules.runtimes.interactors.selection import snapshot, use_runtime

_RETRY_BASE_S = 30.0
_RETRY_MAX_S = 300.0
_RETRY_MAX_ATTEMPTS = 5
_RETRY_PHASES = ("failed", "interrupted", "cancelled")


def _status_error(
    selection_supported: bool,
    job: dict | None,
    preparing: bool,
    capture_error: str | None,
    probe_error: object,
) -> str | None:
    """Prefer a runtime-preparation failure, otherwise show a failed GPU warm-up."""
    if not selection_supported:
        return "Rebuild the native extension for runtime selection"
    reported = (job or {}).get("error") or (None if preparing else capture_error)
    if reported or not probe_error:
        return reported
    return str(probe_error)[:512]


class RuntimeControlService:
    def __init__(
        self,
        service,
        registry: RuntimeRegistry,
        backend,
        *,
        native_supported=lambda: True,
        warm_capabilities=lambda: None,
    ):
        self.service, self.registry, self.backend = service, registry, backend
        self.native_supported = native_supported
        self.warm_capabilities = warm_capabilities
        self.warmed_runtime_id: str | None = None
        self.task = None
        self.observed: dict[str, dict] = {}
        self.node_errors: dict[str, str] = {}
        self.closed = False
        self.reconcile_error: str | None = None
        self.retry_attempts: dict[str, int] = {}
        self.retry_after: dict[str, float] = {}
        self.retry_jobs: dict[str, str | None] = {}
        self._retry_marker: tuple[str, int] | None = None
        self.local_attempts = 0
        self.local_retry_after = 0.0

    def target_state(self) -> tuple[str, dict | None, str | None]:
        """Classify the stored target: current, label_stale, binding_stale or absent.

        A label rotation under the same signing binding is recoverable: the
        master re-stamps the target automatically. A binding change is surfaced
        so the operator re-applies the saved version instead of losing it.
        """
        target = self.registry.target()
        if target is None:
            return "absent", None, None
        state = self.service.state()
        if state.role == "master":
            try:
                authority = self.service.channels.master_authority()
            except ValueError as error:
                return "binding_stale", target, str(error)
            if target["cluster_binding_id"] != str(authority.cluster_binding_id):
                return (
                    "binding_stale",
                    target,
                    "The cluster signing binding changed; re-apply the saved target",
                )
            if target["master_node_id"] != state.node_id:
                return "label_stale", target, None
            return "current", target, None
        if state.role != "worker":
            return "absent", None, None
        if target["master_node_id"] not in state.members:
            return "label_stale", target, "Waiting for the master to re-send the current target"
        return "current", target, None

    def target(self):
        kind, target, _ = self.target_state()
        return target if kind in ("current", "label_stale") else None

    def _restamp_target(self, target: dict) -> dict:
        """Re-bind a stored target to the current master label after rotation."""
        state = self.service.state()
        if target["master_node_id"] == state.node_id:
            return target
        restamped = RuntimeTarget.model_validate(
            {**target, "master_node_id": state.node_id, "revision": target["revision"] + 1}
        )
        self.registry.set_target(restamped)
        return restamped.model_dump()

    def change_target(self, request: TargetRequest):
        if self.service.state().role != "master":
            raise ValueError("Set the cluster target on the master")
        with self.registry.lock:
            requests = self.registry.data.setdefault("target_requests", {})
            key = request.request_id
            authority = self.service.channels.master_authority()
            digest = body_digest(
                {**request.model_dump(), "cluster_binding_id": str(authority.cluster_binding_id)}
            )
            if key in requests:
                if requests[key]["digest"] != digest:
                    raise ValueError("Request ID reused with a different target")
                return deepcopy(requests[key]["target"])
            prior = self.registry.target()
            if request.expected_revision != (prior or {}).get("revision", 0):
                raise ValueError("Target changed; refresh before applying your selection")
            version = self.registry.data["versions"][request.runtime_id]["version"]
            target = RuntimeTarget(
                version=version,
                revision=request.expected_revision + 1,
                cluster_binding_id=str(authority.cluster_binding_id),
                master_node_id=self.service.state().node_id,
            )
            self.registry.set_target(target)
            requests[key] = {"digest": digest, "target": target.model_dump()}
            self.registry._save()
        self.prepare(target.model_dump())
        return target.model_dump()

    def prepare(self, target: dict, *, retry=False):
        value = RuntimeTarget.model_validate(target)
        with self.registry.lock:
            old = self.registry.target()
            if (
                old
                and old["cluster_binding_id"] == value.cluster_binding_id
                and old["revision"] > value.revision
            ):
                raise ValueError("Preparation targets an obsolete revision")
            if (
                old
                and old["cluster_binding_id"] == value.cluster_binding_id
                and old["revision"] == value.revision
                and old != value.model_dump()
            ):
                raise ValueError("Conflicting target at the same revision")
            if not old or old != value.model_dump():
                for job in self.registry.data["jobs"].values():
                    if job["runtime_id"] != value.version.runtime_id and job["phase"] not in (
                        "prepared",
                        "failed",
                        "cancelled",
                        "interrupted",
                    ):
                        self.registry.cancel(job["job_id"])
            self.registry.set_target(value)
        backend = self.backend()
        request = BuildRequest(
            runtime_id=value.version.runtime_id,
            backend=backend,
            request_id=str(uuid4())
            if retry
            else str(
                uuid5(
                    NAMESPACE_URL,
                    f"{value.cluster_binding_id}:{self.service.state().node_id}:{value.revision}:{backend}",
                )
            ),
        )
        self.registry.build(request)
        return self.local_status()

    def _warm_prepared(self, status: dict | None) -> None:
        """Run the freshly prepared binary once off the Run path.

        The first ``llama-server --list-devices`` after a macOS source build is
        the slow one; doing it here (through the capacity refresh scheduler)
        keeps the RPC start command inside its signed deadline.
        """
        identity = ((status or {}).get("prepared") or {}).get("runtime_id")
        if (
            (status or {}).get("phase") != "prepared"
            or not identity
            or identity == self.warmed_runtime_id
        ):
            return
        self.warmed_runtime_id = identity
        self.warm_capabilities()

    @staticmethod
    def _identity_fields(identity: dict, member: dict, node: str) -> dict:
        name = identity.get("hostname") or identity.get("lan_host") or member.get("host") or node
        address = (
            identity.get("private_address")
            or identity.get("rpc_bind")
            or identity.get("lan_host")
            or member.get("host")
        )
        return {"name": name, "address": address or None}

    def local_status(self, identity: str | None = None):
        state = self.service.state()
        kind, target, target_error = self.target_state()
        identity = identity or (target or {}).get("version", {}).get("runtime_id")
        data = self.registry.snapshot()
        job = next((j for j in reversed(data["jobs"]) if j["runtime_id"] == identity), None)
        prepared, error = None, None
        if identity:
            try:
                value = self.registry.capture(identity)
                prepared = {k: value[k] for k in ("runtime_id", "build_id", "backend", "commit")}
            except (OSError, ValueError, KeyError) as failure:
                error = str(failure)
        running = data["running"]
        selection_supported = self.native_supported()
        member = state.members.get(state.node_id) or {}
        fields = self._identity_fields(
            self.service.directory_view().get(state.node_id, {}), member, state.node_id
        )
        # An in-flight preparation is not an error: report the job phase until
        # the artifact exists, and only surface the capture failure after a
        # terminal attempt has actually failed.
        preparing = job is not None and job["phase"] not in TERMINAL
        return {
            "node_id": state.node_id,
            "role": state.role,
            "capability": 1,
            "selection_supported": selection_supported,
            "target_state": kind,
            "target_error": target_error,
            "target_revision": (target or {}).get("revision"),
            "prepared": prepared,
            "running": {k: running[k] for k in ("runtime_id", "build_id", "commit")}
            if running
            else None,
            "phase": "upgrade_required"
            if not selection_supported
            else "prepared"
            if prepared
            else (job or {}).get("phase", "not_installed"),
            **fields,
            "job": job,
            "error": _status_error(
                selection_supported, job, preparing, error, member.get("capacity_probe_error")
            ),
        }

    def status(self):
        data = self.registry.snapshot()
        kind, target, target_error = self.target_state()
        directory = self.service.directory_view()
        data["target"] = target
        data["target_state"] = kind
        data["target_error"] = target_error
        nodes = [self.local_status()]
        now = time.monotonic()
        is_master = self.service.state().role == "master"
        # Membership and liveness belong to the main cluster store, which the
        # dashboard already renders these nodes from: every node mirrors the
        # store, so workers see the same cluster as the cluster view. An
        # enrolled, live member is connected even before its signed runtime
        # channel has observed the target. The signed observation only refines
        # the preparation phase; only the store's liveness rule says offline.
        for node, member in list(self.service.state().members.items()):
            if node == self.service.state().node_id:
                continue
            role = member.get("role")
            observation = self.observed.get(node)
            # The master stamps its own last_seen only at startup, so a mirrored
            # master is live by definition while this node is enrolled; worker
            # liveness stays on the store's heartbeat rule. The master is also
            # always runtime-capable even though it never advertises the field.
            live = (
                role == "master"
                or time.time() - float(member.get("last_seen") or 0) <= LIVENESS_TIMEOUT_S
            )
            capability = 1 if role == "master" else member.get("runtime_management_version", 0)
            observed = observation is not None and now - observation["time"] < 10
            if not live:
                value = {
                    "node_id": node,
                    "role": role,
                    "phase": "offline",
                    "capability": capability,
                }
            elif observed:
                value = deepcopy(observation["value"])
            elif capability != 1:
                value = {
                    "node_id": node,
                    "role": role,
                    "phase": "upgrade_required",
                    "capability": capability,
                    "error": "Upgrade/rebuild this worker agent for runtime management",
                }
            else:
                value = {
                    "node_id": node,
                    "role": role,
                    "phase": "waiting_for_target",
                    "capability": 1,
                }
                # Only the master owns worker command channels and can explain
                # their state; a worker listing peers must not claim a channel.
                if is_master and node not in self.service.channels.sessions:
                    value["error"] = (
                        "Runtime control channel not connected: this worker is not paired with the master; "
                        "re-pair it, then the master sends the saved target automatically"
                    )
                elif is_master and node in self.node_errors:
                    value["error"] = (
                        f"the master could not sync this worker: {self.node_errors[node]}"
                    )
            if observed and target and value.get("target_revision") != target["revision"]:
                value["phase"] = "waiting_for_target"
            value.update(self._identity_fields(directory.get(node, {}), member, node))
            if is_master:
                value["channel"] = {
                    "connected": node in self.service.channels.sessions,
                    "error": self.node_errors.get(node),
                }
            nodes.append(value)
        data["nodes"] = nodes
        data["reconcile_error"] = self.reconcile_error or (
            target_error if kind == "binding_stale" else None
        )
        return data

    @contextmanager
    def operation(self, *, recovery: bool = False):
        if not self.native_supported():
            raise HTTPException(
                409, "Rebuild the native extension with python scripts/build.py before Run"
            )
        pinned = snapshot()
        if pinned is None:
            coordinator = self.service.rpc_runs
            pinned = coordinator.runtime_snapshot if recovery and coordinator else None
            try:
                target = self.target()
                pinned = pinned or self.registry.capture(
                    (target or {}).get("version", {}).get("runtime_id")
                )
            except (OSError, ValueError) as error:
                raise HTTPException(
                    409, detail={"code": "RUNTIME_NOT_READY", "message": str(error)}
                ) from None
        with use_runtime(pinned):
            yield pinned

    async def inspect_workers(self, workers: list[str], identity: str):
        results = {}
        for node in workers:
            if self.service.state().members.get(node, {}).get("runtime_management_version") != 1:
                raise HTTPException(
                    409, f"{node}: upgrade/rebuild the worker agent for llama.cpp version control"
                )
            session = self.service.channels.sessions.get(node)
            if session is None:
                raise HTTPException(409, f"{node}: signed runtime command channel is unavailable")
            results[node] = await session.request_runtime(
                uuid4(), "runtime_status", body={"runtime_id": identity}
            )
        for node, value in results.items():
            if (
                value.get("capability") != 1
                or value.get("selection_supported") is False
                or (value.get("prepared") or {}).get("runtime_id") != identity
            ):
                raise HTTPException(
                    409,
                    detail={
                        "code": "RUNTIME_NOT_READY",
                        "node_id": node,
                        "message": f"{node}: prepare the target in Settings → llama.cpp",
                        "runtime": value,
                    },
                )
        return results

    def _retry_due(self, node: str) -> bool:
        return 0 < self.retry_attempts.get(
            node, 0
        ) < _RETRY_MAX_ATTEMPTS and time.monotonic() >= self.retry_after.get(node, 0.0)

    @staticmethod
    def _backoff(attempts: int) -> float:
        return min(_RETRY_MAX_S, _RETRY_BASE_S * (2 ** (attempts - 1)))

    def _note_retry(self, node: str, value: dict) -> None:
        phases = {value.get("phase"), (value.get("job") or {}).get("phase")}
        if phases & set(_RETRY_PHASES):
            job_id = (value.get("job") or {}).get("job_id")
            if self.retry_jobs.get(node) == job_id:
                return  # The same failed attempt must not burn through the retry budget.
            self.retry_jobs[node] = job_id
            attempts = self.retry_attempts.get(node, 0) + 1
            self.retry_attempts[node] = attempts
            self.retry_after[node] = time.monotonic() + self._backoff(attempts)
        elif phases & {"prepared", "queued", "downloading", "building", "verifying"}:
            self.retry_attempts.pop(node, None)
            self.retry_after.pop(node, None)
            self.retry_jobs.pop(node, None)

    def _local_retry(self, identity: str | None) -> bool:
        job = next(
            (j for j in reversed(self.registry.snapshot()["jobs"]) if j["runtime_id"] == identity),
            None,
        )
        phase = (job or {}).get("phase")
        if phase not in _RETRY_PHASES:
            self.local_attempts = 0
            self.local_retry_after = 0.0
            return False
        now = time.monotonic()
        if self.local_attempts == 0:
            self.local_attempts = 1
            self.local_retry_after = now + self._backoff(1)
            return False
        if self.local_attempts >= _RETRY_MAX_ATTEMPTS or now < self.local_retry_after:
            return False
        self.local_attempts += 1
        self.local_retry_after = now + self._backoff(self.local_attempts)
        return True

    async def reconcile(self):
        while not self.closed:
            await asyncio.sleep(2)
            if self.service.state().role != "master":
                continue
            kind, target, target_error = self.target_state()
            if kind == "binding_stale":
                self.reconcile_error = (
                    target_error or "The cluster signing binding changed; re-apply the saved target"
                )
                continue
            if not target:
                continue
            if kind == "label_stale":
                target = await asyncio.to_thread(self._restamp_target, target)
            marker = (target["cluster_binding_id"], target["revision"])
            if marker != self._retry_marker:
                self._retry_marker = marker
                self.retry_attempts.clear()
                self.retry_after.clear()
                self.retry_jobs.clear()
                self.local_attempts = 0
                self.local_retry_after = 0.0
            retry = await asyncio.to_thread(self._local_retry, target["version"]["runtime_id"])
            try:
                status = await asyncio.to_thread(self.prepare, target, retry=retry)
                self._warm_prepared(status)
                self.reconcile_error = None
            except (OSError, ValueError, KeyError) as error:
                self.reconcile_error = str(error)[:1000]

            async def update(node, session):
                member = self.service.state().members.get(node, {})
                if member.get("runtime_management_version") != 1:
                    return
                try:
                    body = {"target": target}
                    if self._retry_due(node):
                        body["retry"] = True
                    value = await session.request_runtime(uuid4(), "prepare_runtime", body=body)
                    if self.target() == target and node in self.service.state().members:
                        self.observed[node] = {"time": time.monotonic(), "value": value}
                        self.node_errors.pop(node, None)
                        self._note_retry(node, value)
                except (Exception, asyncio.CancelledError) as error:
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    self.observed.pop(node, None)
                    self.node_errors[node] = str(error)[:300]

            semaphore = asyncio.Semaphore(4)

            async def bounded(node, session):
                async with semaphore:
                    await update(node, session)

            await asyncio.gather(
                *(bounded(n, s) for n, s in list(self.service.channels.sessions.items()))
            )

    async def execute(self, command, authorize):
        authorize()
        state = self.service.state()
        if state.role != "worker" or command.sender_node_id != command.master_node_id:
            raise ValueError("Runtime preparation requires the enrolled master")
        if command.action == "prepare_runtime":
            if (
                set(command.body) not in ({"target"}, {"target", "retry"})
                or type(command.body.get("retry", False)) is not bool
            ):
                raise ValueError("Invalid preparation body")
            target = RuntimeTarget.model_validate(command.body["target"])
            if (
                target.cluster_binding_id != str(command.cluster_binding_id)
                or target.master_node_id != command.master_node_id
            ):
                raise ValueError("Runtime target belongs to another cluster")
            status = await asyncio.to_thread(
                self.prepare, target.model_dump(), retry=command.body.get("retry", False)
            )
            self._warm_prepared(status)
            return status
        if command.action == "runtime_status":
            if set(command.body) != {"runtime_id"}:
                raise ValueError("Invalid runtime inspection body")
            return await asyncio.to_thread(self.local_status, command.body["runtime_id"])
        if command.action == "cancel_runtime_build":
            if set(command.body) != {"job_id"}:
                raise ValueError("Invalid cancellation body")
            return self.registry.cancel(command.body["job_id"])
        if command.action == "runtime_build_logs":
            if set(command.body) != {"job_id"}:
                raise ValueError("Invalid log request body")
            return {"text": self.registry.logs(command.body["job_id"])[-16000:]}
        raise ValueError("Unsupported runtime action")

    async def close(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await asyncio.to_thread(self.registry.close)
