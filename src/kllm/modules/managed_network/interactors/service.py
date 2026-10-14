"""Master coordination and resource ownership for explicitly selected networks."""

from __future__ import annotations

import asyncio
import socket
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from kllm.shared import native
from kllm.modules.managed_network.interactors.allocation import (
    AdapterSelection,
    OperationRequest,
    StartRequest,
    assign,
    propose,
)
from kllm.modules.managed_network.interactors.executor import LocalNetworkExecutor, platform_adapter
from kllm.modules.managed_network.interactors.identity import IdentityError, LocalIdentity, SignedCommand, SignedRuntimeCommand
from kllm.modules.managed_network.entities import (
    AdmissionLease,
    AssignmentState,
    ManagedNetwork,
    MasterAuthority,
    NetworkOperation,
    NetworkState,
    NodeAssignment,
    NodeNetworkIdentity,
    ParticipantResult,
    Phase,
    RPCChildIdentity,
    VerificationEvidence,
    utc_now,
)
from kllm.shared.platform.base import PlatformAdapter, PlatformRefusal, is_admin
from kllm.modules.managed_network.gateways.store import NetworkStore, StoreConflict, digest
from kllm.modules.managed_network.interactors.verification import Freshness
from kllm.shared.exceptions import PersistenceError
from kllm.shared.state import repo_root
from kllm.modules.cluster.presenters.heartbeat_payload import local_hostname
from kllm.modules.managed_network.interactors.network_channel import NetworkChannelHub


class ManagedNetworkService:
    def __init__(
        self,
        home: Path,
        state: Callable[[], Any],
        master_base: Callable[[], str | None],
        *,
        platform: PlatformAdapter | None = None,
        repository: Callable[[], Path | None] = repo_root,
    ):
        self.state, self.home = state, home
        self.store = NetworkStore(home)
        try:
            self.identity = LocalIdentity(home, state().node_id, self.store)
            from kllm.modules.managed_network.interactors.bootstrap import IdentityBootstrap
            from kllm.modules.managed_network.interactors.guided_setup import GuidedSetup

            self.bootstrap = IdentityBootstrap(self)
            self.guided = GuidedSetup(self)
            self.executor = LocalNetworkExecutor(
                self.store, self.identity, state, platform or platform_adapter(), repository
            )
            self.executor.guided_status = self.guided.local_status
            self._default_platform = platform is None
            if platform is None and not is_admin():
                from kllm.modules.managed_network.interactors.helper_platform import HelperPlatform

                self.executor.platform = HelperPlatform(
                    self.executor.platform, self.guided.helper, self.guided.grant
                )
            self.worker_rpc = None
            self.runtimes = None
            self.rpc_runs = None
            self.rpc_monitor_task = None
            self.worker_expiry_task = None
            self.channels = NetworkChannelHub(
                self.store, self.identity, state, master_base, self.execute_command
            )
            self.channels.bootstrap = self.bootstrap.connect
            self.lock = asyncio.Lock()  # Shared by topology changes and run reservations.
            self.tasks: dict[str, asyncio.Task] = {}
            self.local_authority: MasterAuthority | None = None
            self.phase_sequences: dict[str, int] = {}
            self.local_revisions: dict[tuple[str, str], int] = {}
            self.verified_results: dict[tuple[str, str], dict[str, Any]] = {}
            self.results: dict[tuple[str, str], dict[str, Any]] = {}
            self.master_bridges: dict[tuple[str, str], Any] = {}
            self.freshness = Freshness()
            self.probe_leases: dict[tuple[str, str], AdmissionLease] = {}
            self.monitor_task: asyncio.Task | None = None
            self.closed = False
            self._close_complete = False
            self._close_jobs: set[asyncio.Task] = set()
            from kllm.modules.managed_network.interactors.run_admission import RunAdmission
            from kllm.modules.managed_network.interactors.prima_admission import PrimaNetworkAdmission

            self.admission = RunAdmission(self)
            self.prima_admission = PrimaNetworkAdmission(self)
            self.loop: asyncio.AbstractEventLoop | None = None
        except BaseException:
            self.store.close()
            raise

    def _prima_busy(self):
        prima = getattr(getattr(self, "runtimes", None), "_prima", None)
        return bool(prima and (prima.rank.owner or prima.ports.owner or prima.runs.busy))

    def _configure_direct_platform(self):
        import json
        import os
        from kllm.modules.managed_network.interactors.helper_platform import HelperPlatform
        from kllm.modules.managed_network.interactors.direct_platform import DirectPortPlatform
        from kllm.modules.managed_network.interactors.direct_client import DirectClient

        if not self._default_platform or os.name != "nt" or isinstance(self.executor.platform, DirectPortPlatform):
            return
        saved_direct = (self.home / "prima-direct" / "authorization.json").exists()
        if not saved_direct and not is_admin():
            return
        # Older production port ownership belongs to the protected service. Never
        # release it through a different, empty ledger merely because privileges changed.
        old_ports = None
        if self.runtimes is not None:
            path = self.runtimes.registry.root / "prima-control" / "ports.json"
            if path.exists():
                old_ports = json.loads(path.read_text())
        if not saved_direct and (old_ports or self.guided.helper.status(self.guided.grant()).get("ready")):
            if not isinstance(self.executor.platform, HelperPlatform):
                self.executor.platform = HelperPlatform(self.executor.platform, self.guided.helper, self.guided.grant)
            return
        underlying = self.executor.platform.underlying if isinstance(self.executor.platform, HelperPlatform) else self.executor.platform
        self.executor.platform = DirectPortPlatform(underlying,
            DirectClient(self.home, self.executor.repository, self.guided.grant), self.guided.grant, self._prima_busy)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        (await self.store.arun(self.store.recover))
        await asyncio.to_thread(self._configure_direct_platform)
        if self.state().role == "master":
            await asyncio.to_thread(self.sync_directory)
        await self.channels.start()
        if self.runtimes is not None:
            prima_root = self.runtimes.registry.root / "prima-control"
            if ((prima_root / "rank" / "rank.json").exists()
                    or (prima_root / "distributed" / "run.json").exists()
                    or (prima_root / "ports.json").exists()):
                prima = self.runtimes.prima
                try:
                    await asyncio.to_thread(prima.expire)
                except (PlatformRefusal, OSError, RuntimeError, ValueError) as error:
                    self.state().apply_error = f"Prima cleanup pending after restart: {error}"
                    self.state().save()
            self.runtimes.task = asyncio.create_task(
                self.runtimes.reconcile(), name="kllm-runtime-target"
            )
        self.monitor_task = asyncio.create_task(self._monitor(), name="kllm-managed-network-health")
        if self.worker_rpc is not None:
            await asyncio.to_thread(self.worker_rpc.reconcile_startup)
            self.worker_expiry_task = asyncio.create_task(
                self._expire_worker_rpc(), name="kllm-rpc-expiry"
            )
        if self.rpc_runs is not None:
            await asyncio.to_thread(self.rpc_runs.reconcile_startup)
            self.rpc_monitor_task = asyncio.create_task(
                self.rpc_runs.monitor(), name="kllm-rpc-run-health"
            )

    async def execute_command(
        self, command: SignedCommand | SignedRuntimeCommand, deadline: float
    ) -> dict[str, Any]:
        if isinstance(command, SignedCommand):
            return await self.executor.execute(command, deadline)
        if self.worker_rpc is None:
            return {
                "status": "failed",
                "error_code": "UNSUPPORTED_CAPABILITY",
                "message": "Upgrade this worker for RPC runtime control",
            }
        try:
            # The channel already verifies signatures, authority and sequence.
            # Recheck its persisted fence immediately before local mutation.
            def authorize_sync() -> None:
                self.store.check_channel(
                    command.sender_node_id,
                    MasterAuthority(
                        cluster_binding_id=command.cluster_binding_id,
                        master_node_id=command.master_node_id,
                        master_key_id=command.master_key_id,
                        master_term=command.master_term,
                        master_session_id=command.master_session_id,
                        accepted_channel_id=command.channel_id,
                    ),
                )

            if command.action.startswith("prima_"):
                if self.runtimes is None:
                    raise ValueError("Upgrade this worker for Prima control")
                return await self.runtimes.prima.execute(command, deadline, authorize_sync)
            prima = getattr(self.runtimes, "_prima", None)
            if command.action == "start" and prima is not None and (prima.rank.owner or prima.ports.owner):
                raise ValueError("Stop the owned Prima rank or listener admission before starting RPC inference")
            if command.action in (
                "prepare_runtime",
                "runtime_status",
                "cancel_runtime_build",
                "runtime_build_logs",
            ):
                if self.runtimes is None:
                    raise ValueError("Upgrade this worker for runtime management")
                if time.monotonic() >= deadline:
                    raise ValueError("Runtime command expired")
                await self.store.arun(authorize_sync)
                return await self.runtimes.execute(command, lambda: None)
            return await asyncio.to_thread(
                self.worker_rpc.execute, command, deadline, authorize_sync
            )
        except (
            PlatformRefusal,
            IdentityError,
            StoreConflict,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            return {
                "status": "failed",
                "error_code": getattr(error, "code", "RPC_START_FAILED"),
                "message": str(error)[:512],
            }

    async def _expire_worker_rpc(self) -> None:
        while not self.closed:
            await asyncio.sleep(1)
            try:
                await asyncio.to_thread(self.worker_rpc.expire)
                prima = getattr(self.runtimes, "_prima", None)
                if prima is not None:
                    await asyncio.to_thread(prima.expire)
            except (PlatformRefusal, OSError, RuntimeError, ValueError):
                pass  # Owned process and cleanup_pending remain available for retry.

    def release_worker_bridges(self, pid: int) -> None:
        for key, bridge in list(self.executor.bridges.items()):
            status = native.network_bridge_status(bridge)
            if status.get("child_pid") not in (None, pid):
                continue
            if not native.stop_network_bridge(bridge, status["instance_id"], terminate=True):
                raise PlatformRefusal("CLEANUP_PENDING", "Worker relay has not drained")
            self.executor.bridges.pop(key, None)

    def _authority(self) -> MasterAuthority:
        authority = self.channels.master_authority()
        if self.local_authority is None:
            self.local_authority = authority.model_copy(update={"accepted_channel_id": uuid4()})
            self.store.accept_authority(self.local_authority, self.identity.node_id)
        return self.local_authority

    def local_inventory(self) -> dict[str, Any]:
        return {**self.executor.inspect_view(), "setup": self.guided.local_status()}

    def select_adapter(self, selection: AdapterSelection) -> dict[str, Any]:
        snapshot = self.executor.platform.inspect(selection.adapter_id)
        if snapshot.public()["inventory_revision"] != selection.expected_inventory_revision:
            raise StoreConflict("adapter inventory changed")
        if any(n.observed_state != "stopped" for n in self.store.networks()):
            raise PlatformRefusal(
                "OPERATION_IN_PROGRESS",
                "Stop or resolve the current network before changing selection",
            )
        import json

        value = selection.model_dump(mode="json")
        self.store.set_metadata("network_selection", json.dumps(value))
        return value

    async def inventories(self) -> dict[str, Any]:
        if self.state().role != "master":
            raise PlatformRefusal("NOT_MASTER", "Cluster inventories require master role")
        authority = await self.store.arun(self._authority)
        result = {
            self.identity.node_id: await asyncio.wait_for(
                asyncio.to_thread(self.local_inventory), 5
            )
        }

        async def worker(node_id: str) -> None:
            peer = await self.store.arun(self.store.peer, node_id)
            session = self.channels.sessions.get(node_id)
            if (
                peer is None
                or peer.expected_role != "worker"
                or peer.cluster_binding_id != authority.cluster_binding_id
                or session is None
            ):
                result[node_id] = {
                    "node_id": node_id,
                    "adapters": [],
                    "error_code": "UNPAIRED_IDENTITY" if peer is None else "UNSUPPORTED_CAPABILITY",
                }
                return
            try:
                result[node_id] = await session.request(
                    network_id=uuid4(),
                    operation_id=uuid4(),
                    operation_revision=1,
                    phase_sequence=1,
                    phase="inspect",
                    assignment_revision=1,
                    expected_local_revision=0,
                    body={},
                    ttl_ms=5000,
                )
            except Exception:
                result[node_id] = {"node_id": node_id, "adapters": [], "error_code": "TIMEOUT"}

        await asyncio.gather(
            *(worker(node) for node in self.state().members if node != self.identity.node_id)
        )
        return result

    @staticmethod
    def _accepted(operation: NetworkOperation, network: ManagedNetwork) -> dict[str, Any]:
        return {
            "network_id": str(network.network_id),
            "operation_id": str(operation.operation_id),
            "state": network.observed_state,
            "revision": network.revision,
        }

    async def begin_start(self, request: StartRequest) -> dict[str, Any]:
        async with self.lock:
            authority = await self.store.arun(self._authority)
            prior = await self.store.arun(
                self.store.operation_for_request, authority.cluster_binding_id, request.request_id
            )
            body_digest = digest(request.model_dump(mode="json"))
            if prior:
                if prior.proposal_digest != body_digest:
                    raise StoreConflict("request ID reused with changed body")
                network = await self.store.arun(self.store.network, prior.network_id)
                assert network is not None
                return self._accepted(prior, network)
            if any(
                n.observed_state != "stopped" for n in (await self.store.arun(self.store.networks))
            ):
                raise PlatformRefusal(
                    "OPERATION_IN_PROGRESS",
                    "One non-stopped network is allowed per cluster binding",
                )
            if sum(p.node_id == self.identity.node_id for p in request.participants) != 1:
                raise ValueError("select the local master exactly once")
            network = ManagedNetwork(
                network_id=uuid4(),
                cluster_binding_id=authority.cluster_binding_id,
                master_node_id=self.identity.node_id,
                master_key_id=self.identity.key_id,
                subnet=request.subnet or "10.10.10.0/24",
            )
            operation = NetworkOperation(
                operation_id=uuid4(),
                request_id=request.request_id,
                network_id=network.network_id,
                cluster_binding_id=authority.cluster_binding_id,
                action="start",
                master_term=authority.master_term,
                master_session_id=authority.master_session_id,
                expected_network_revision=0,
                operation_revision=1,
                participants=[p.node_id for p in request.participants],
                proposal_digest=body_digest,
                phase="preparing",
                deadline=utc_now() + timedelta(seconds=60),
            )
            network.current_operation_id = operation.operation_id
            (await self.store.arun(self.store.put_network, network))
            (await self.store.arun(self.store.put_operation, operation))
            task = asyncio.create_task(
                self._start_operation(network, operation, request),
                name=f"kllm-network-{operation.operation_id}",
            )
            self.tasks[str(operation.operation_id)] = task
            return self._accepted(operation, network)

    def _publish(self, network: ManagedNetwork, state: NetworkState) -> None:
        before = network.revision
        # Normal orchestration states are checked by the persistence boundary.
        network.observed_state = state
        network.revision += 1
        network.updated_at = utc_now()
        self.store.put_network(network, expected_revision=before)

    def _sequence(self, operation_id: UUID) -> int:
        key = str(operation_id)
        sequence = self.phase_sequences.get(key, 0) + 1
        self.phase_sequences[key] = sequence
        return sequence

    async def _request(
        self,
        operation: NetworkOperation,
        assignment: NodeAssignment,
        phase: Phase,
        body: dict[str, Any],
        timeout: float,
        sequence: int,
    ) -> dict[str, Any]:
        network_key, node_id = str(operation.network_id), assignment.node_id
        key = (network_key, node_id)
        revision = self.local_revisions.get(
            key,
            self.executor.local_revision(operation.network_id)
            if node_id == self.identity.node_id
            else 0,
        )
        try:
            if node_id == self.identity.node_id:
                authority = await self.store.arun(self._authority)
                command = SignedCommand.model_validate(
                    {
                        **authority.model_dump(exclude={"accepted_channel_id"}),
                        "channel_id": authority.accepted_channel_id,
                        "version": 1,
                        "type": "command",
                        "sender_node_id": node_id,
                        "target_node_id": node_id,
                        "sequence": sequence,
                        "network_id": operation.network_id,
                        "operation_id": operation.operation_id,
                        "operation_revision": operation.operation_revision,
                        "phase_sequence": sequence,
                        "phase": phase,
                        "assignment_revision": assignment.assignment_revision,
                        "expected_local_revision": revision,
                        "ttl_ms": max(1, min(60000, int(timeout * 1000))),
                        "body": body,
                    }
                )
                command._wire = self.identity.sign(command.model_dump(mode="json"))
                result = await self.executor.execute(command, time.monotonic() + timeout)
            else:
                session = self.channels.sessions.get(node_id)
                if session is None:
                    raise IdentityError("worker control channel unavailable")
                result = await session.request(
                    network_id=operation.network_id,
                    operation_id=operation.operation_id,
                    operation_revision=operation.operation_revision,
                    phase_sequence=sequence,
                    phase=phase,
                    assignment_revision=assignment.assignment_revision,
                    expected_local_revision=revision,
                    body=body,
                    ttl_ms=max(1, min(60000, int(timeout * 1000))),
                )
            if not isinstance(result.get("local_revision"), int) or isinstance(
                result["local_revision"], bool
            ):
                raise IdentityError("worker result lacks a valid local fence")
            self.local_revisions[key] = result["local_revision"]
            previous_result = self.results.get(key, {})
            preserve_error = (
                phase == "rollback"
                and result.get("status") == "stopped"
                and previous_result.get("error_code")
            )
            self.results[key] = (
                {
                    **result,
                    "error_code": previous_result["error_code"],
                    "message": previous_result.get("message", ""),
                }
                if preserve_error
                else result
            )
            if phase in ("prepare", "apply", "rollback", "cleanup") or (
                phase == "verify" and result.get("status") == "failed"
            ):
                current = next(
                    (
                        a
                        for a in (
                            await self.store.arun(self.store.assignments, operation.network_id)
                        )
                        if a.node_id == node_id
                    ),
                    None,
                )
                if current is not None:
                    current.state = result["status"]
                    if result.get("error_code") == "OWNERSHIP_CONFLICT":
                        current.ownership = "uncertain"
                    if not preserve_error:
                        current.error_code = result.get("error_code")
                        current.message = result.get("message", "")
                    (
                        await self.store.arun(
                            self.store.put_assignment,
                            current,
                            expected_revision=current.assignment_revision,
                        )
                    )
            if phase == "verify" and result.get("status") != "failed":
                self.verified_results[key] = {**self.verified_results.get(key, {}), **result}
            participant = ParticipantResult(
                node_id=node_id,
                assignment_revision=assignment.assignment_revision,
                expected_local_revision=revision,
                stage=phase,
                phase_sequence=sequence,
                status=result["status"],
                signed_result_digest=digest(result),
                prepared_snapshot_digest=result.get("prepared_snapshot_digest"),
                ownership_journal_id=result.get("journal_id"),
                error_code=result.get("error_code"),
            )
            (await self.store.arun(self.store.result, operation.operation_id, participant))
            return result
        except (Exception, asyncio.CancelledError) as error:
            code = (
                error.code
                if isinstance(error, PlatformRefusal)
                else "TIMEOUT"
                if isinstance(error, TimeoutError)
                else "CLEANUP_PENDING"
                if phase in ("rollback", "cleanup")
                else "UNSUPPORTED_CAPABILITY"
            )
            result = {
                "status": "cleanup_pending" if phase in ("rollback", "cleanup") else "failed",
                "error_code": code,
                "message": "Participant phase did not complete with a current authenticated result",
            }
            self.results[key] = result
            (
                await self.store.arun(
                    self.store.result,
                    operation.operation_id,
                    ParticipantResult(
                        node_id=node_id,
                        assignment_revision=assignment.assignment_revision,
                        expected_local_revision=revision,
                        stage=phase,
                        phase_sequence=sequence,
                        status=result["status"],
                        error_code=code,
                    ),
                )
            )
            return result

    async def _phase(
        self,
        operation: NetworkOperation,
        assignments: list[NodeAssignment],
        phase: Phase,
        bodies: dict[str, dict],
        budget: float,
    ) -> dict[str, dict]:
        sequence = self._sequence(operation.operation_id)
        result = await asyncio.gather(
            *(
                self._request(operation, a, phase, bodies[a.node_id], budget, sequence)
                for a in assignments
            )
        )
        return dict(zip((a.node_id for a in assignments), result))

    def _management_hosts(self) -> list[str]:
        addresses: list[str] = []
        for member in self.state().members.values():
            for key in ("host", "lan_host", "wg_address"):
                host = member.get(key)
                if host:
                    addresses.extend(
                        str(item[4][0])
                        for item in socket.getaddrinfo(
                            host, None, socket.AF_INET, socket.SOCK_STREAM
                        )
                    )
        host = getattr(self.state(), "master_host", None)
        if host:
            addresses.extend(
                str(item[4][0])
                for item in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
            )
        return sorted(set(addresses))

    async def _start_operation(
        self,
        network: ManagedNetwork,
        operation: NetworkOperation,
        request: StartRequest,
        existing: list[NodeAssignment] | None = None,
    ) -> None:
        async with self.lock:
            assignments: list[NodeAssignment] = []
            applied = False
            changed: list[NodeAssignment] = []
            try:
                prepare_deadline = time.monotonic() + 15
                inventories = await self.inventories()
                selected = {}
                groups = []
                for participant in request.participants:
                    if participant.node_id not in self.state().members:
                        raise PlatformRefusal(
                            "INVALID_ADAPTER",
                            "Every selected participant must be currently enrolled",
                        )
                    node_inventory = inventories.get(participant.node_id, {})
                    adapter = next(
                        (
                            a
                            for a in node_inventory.get("adapters", [])
                            if a["adapter_id"] == participant.adapter_id
                        ),
                        None,
                    )
                    if adapter is None:
                        raise PlatformRefusal(
                            node_inventory.get("error_code", "INVALID_ADAPTER"),
                            "Selected worker is unpaired, unavailable or lacks this adapter",
                        )
                    selected[participant.node_id] = adapter
                    groups.append(
                        (
                            adapter,
                            [a for a in node_inventory.get("adapters", []) if a is not adapter],
                        )
                    )
                if request.subnet is None:
                    proposals = propose(groups, len(request.participants))
                    if not proposals or network.subnet != proposals[0]:
                        # Proposals are fixed before network persistence; do not rewrite
                        # an authority-bound subnet after the operation was committed.
                        raise PlatformRefusal(
                            "SUBNET_CONFLICT",
                            "Default proposal conflicts; select an available private subnet explicitly",
                        )
                assignments = assign(
                    network.network_id,
                    network.subnet,
                    self.identity.node_id,
                    request.participants,
                    selected,
                    existing,
                )
                old = {a.node_id: a for a in existing or []}
                changed = [
                    a for a in assignments if operation.action != "add" or a.node_id not in old
                ]
                for assignment in changed:
                    if assignment.node_id in old:
                        assignment.assignment_revision += 1
                        assignment.state = "preparing"
                    (
                        await self.store.arun(
                            self.store.put_assignment,
                            assignment,
                            expected_revision=old[assignment.node_id].assignment_revision - 1
                            if assignment.node_id in old
                            else None,
                        )
                    )
                management_hosts = await asyncio.to_thread(self._management_hosts)
                preparation_bodies = {
                    a.node_id: {
                        "network": network.model_dump(mode="json"),
                        "operation": operation.model_dump(mode="json"),
                        "assignment": a.model_dump(mode="json"),
                        "inventory_revision": selected[a.node_id]["inventory_revision"],
                        "management_hosts": management_hosts,
                        "participants": [item.model_dump(mode="json") for item in assignments],
                        "bridge_port": request.bridge_port,
                        "diagnostic_port": request.diagnostic_port,
                    }
                    for a in assignments
                }
                remaining = prepare_deadline - time.monotonic()
                if remaining <= 0:
                    raise PlatformRefusal(
                        "TIMEOUT", "Participant inspection exhausted preparation budget"
                    )
                if operation.action == "add":
                    reconciled = await self._phase(
                        operation,
                        [a for a in assignments if a.node_id in old],
                        "reconcile",
                        preparation_bodies,
                        remaining,
                    )
                    if any(r["status"] == "failed" for r in reconciled.values()):
                        raise PlatformRefusal(
                            "CLEANUP_PENDING",
                            "Existing participants could not acknowledge added topology",
                        )
                prepared = await self._phase(
                    operation, changed, "prepare", preparation_bodies, remaining
                )
                if time.monotonic() > prepare_deadline:
                    raise PlatformRefusal(
                        "TIMEOUT", "Preparation deadline expired; no addresses were applied"
                    )
                if any(result["status"] != "prepared" for result in prepared.values()):
                    raise PlatformRefusal(
                        "INVALID_ADAPTER",
                        "A participant refused preparation; no addresses were applied",
                    )
                (await self.store.arun(self._publish, network, "applying"))
                operation.phase = "applying"
                (
                    await self.store.arun(
                        self.store.update_operation, operation, operation.operation_revision
                    )
                )
                commitment = digest(
                    {node: result["prepared_snapshot_digest"] for node, result in prepared.items()}
                )
                applied = True  # From this point a lost result can conceal an OS mutation.
                results = await self._phase(
                    operation,
                    changed,
                    "apply",
                    {
                        a.node_id: {
                            "prepared_snapshot_digest": prepared[a.node_id][
                                "prepared_snapshot_digest"
                            ],
                            "all_prepared_digest": commitment,
                        }
                        for a in changed
                    },
                    20,
                )
                if any(result["status"] != "configured" for result in results.values()):
                    raise PlatformRefusal(
                        "CLEANUP_PENDING",
                        "Address application failed; immediate rollback is required",
                    )
                for assignment in changed:
                    result = results[assignment.node_id]
                    assignment.state = "configured"
                    assignment.ownership = result["ownership"]
                    (
                        await self.store.arun(
                            self.store.put_assignment,
                            assignment,
                            expected_revision=assignment.assignment_revision,
                        )
                    )
                (await self.store.arun(self._publish, network, "verifying"))
                operation.phase = "verifying"
                (
                    await self.store.arun(
                        self.store.update_operation, operation, operation.operation_revision
                    )
                )
                idle = await asyncio.wait_for(
                    self._verify_configuration(network, operation, assignments, request), 20
                )
                operation.phase = "complete"
                operation.terminal_result = {
                    "state": network.observed_state,
                    "error_code": None if idle else "RPC_BUSY",
                }
                (
                    await self.store.arun(
                        self.store.update_operation, operation, operation.operation_revision
                    )
                )
            except (Exception, asyncio.CancelledError) as error:
                current = await self.store.arun(self.store.operation, operation.operation_id)
                if current:
                    operation = current
                code = (
                    error.code if isinstance(error, PlatformRefusal) else "UNSUPPORTED_CAPABILITY"
                )
                rollback = {}
                if applied or changed:
                    rollback = await self._phase(
                        operation,
                        changed,
                        "rollback",
                        {a.node_id: {"desired_state": "stopped"} for a in changed},
                        60,
                    )
                pending = any(result["status"] != "stopped" for result in rollback.values())
                (
                    await self.store.arun(
                        self._publish,
                        network,
                        "cleanup_pending"
                        if pending
                        else "degraded"
                        if operation.action == "add"
                        else "failed",
                    )
                )
                operation.phase = "failed"
                operation.terminal_result = {
                    "error_code": code,
                    "message": "Setup failed; review participant prerequisites and cleanup state",
                    "rollback": rollback,
                }
                (
                    await self.store.arun(
                        self.store.update_operation, operation, operation.operation_revision
                    )
                )
                await asyncio.to_thread(self.sync_directory)

    async def _verify_configuration(
        self,
        network: ManagedNetwork,
        operation: NetworkOperation,
        assignments: list[NodeAssignment],
        request: StartRequest,
    ) -> bool:
        # Start the master listener before workers attempt their reverse echo.
        master = next(a for a in assignments if a.node_id == self.identity.node_id)
        first = await self._request(
            operation,
            master,
            "verify",
            {"health_only": False},
            5,
            self._sequence(operation.operation_id),
        )
        if first["status"] == "failed":
            raise PlatformRefusal(first["error_code"], "Master dedicated diagnostics failed")
        workers = [a for a in assignments if a.node_id != self.identity.node_id]
        verified = await self._phase(
            operation, workers, "verify", {a.node_id: {"health_only": False} for a in workers}, 5
        )
        if any(result["status"] == "failed" for result in verified.values()):
            raise PlatformRefusal("TIMEOUT", "Worker dedicated diagnostics failed")
        diagnostic = self.executor.diagnostics[str(network.network_id)]
        for assignment in workers:
            result = verified[assignment.node_id]
            proof = await diagnostic.challenge(
                assignment.node_id, assignment.assignment_revision, result["diagnostic_port"]
            )
            assignment.logical_rpc_endpoint = f"{assignment.address}:{request.bridge_port}"
            self.freshness.publish(
                network.network_id,
                assignment.node_id,
                {"assignment_revision": assignment.assignment_revision, "dedicated_proof": proof},
            )
            (
                await self.store.arun(
                    self.store.put_assignment,
                    assignment,
                    expected_revision=assignment.assignment_revision,
                )
            )
        self.freshness.publish(
            network.network_id,
            self.identity.node_id,
            {"assignment_revision": master.assignment_revision},
        )
        # Persist participant readiness before the network becomes observable as
        # ready. Readers poll observed_state and must not see a ready network
        # whose assignments are still configured.
        for assignment in assignments:
            assignment.state = "ready"
            (
                await self.store.arun(
                    self.store.put_assignment,
                    assignment,
                    expected_revision=assignment.assignment_revision,
                )
            )
        (await self.store.arun(self._publish, network, "ready"))
        await asyncio.to_thread(self.sync_directory)
        return True

    async def prepare_rpc(
        self,
        network: ManagedNetwork,
        operation: NetworkOperation,
        assignments: list[NodeAssignment],
    ) -> None:
        """Create relays and protocol evidence only for this run's workers."""
        master = next(a for a in assignments if a.node_id == self.identity.node_id)
        workers = [a for a in assignments if a.node_id != self.identity.node_id]
        verified = await self._phase(
            operation,
            workers,
            "verify",
            {a.node_id: {"health_only": False, "runtime": True} for a in workers},
            5,
        )
        if any(result["status"] == "failed" for result in verified.values()):
            raise PlatformRefusal(
                "RPC_PROTOCOL_UNSUPPORTED", "Worker RPC runtime or relay verification failed"
            )
        for assignment in workers:
            result = verified[assignment.node_id]
            assignment.rpc_child_identity = RPCChildIdentity.model_validate(result["child"])
            assignment.logical_rpc_endpoint = result["logical_endpoint"]
            bridge = self.master_bridges.get((str(network.network_id), assignment.node_id))
            if bridge is None:
                bridge = await asyncio.to_thread(
                    native.start_network_bridge,
                    {
                        "role": "master",
                        "instance_id": str(uuid4()),
                        "network_id": str(network.network_id),
                        "assignment_revision": assignment.assignment_revision,
                        "bind_host": "127.0.0.1",
                        "bind_port": 0,
                        "target_host": assignment.address,
                        "target_port": int(result["logical_endpoint"].rsplit(":", 1)[1]),
                        "source_host": master.address,
                        "allowed_peer": "127.0.0.1",
                        "child_pid": None,
                        "child_start_token": None,
                    },
                )
            self.master_bridges[(str(network.network_id), assignment.node_id)] = bridge
            assignment.launch_rpc_endpoint = native.network_bridge_status(bridge)["listener"]
            (
                await self.store.arun(
                    self.store.put_assignment,
                    assignment,
                    expected_revision=assignment.assignment_revision,
                )
            )

    async def verify_idle(
        self,
        network: ManagedNetwork,
        operation: NetworkOperation,
        assignments: list[NodeAssignment],
    ) -> bool:
        workers = [a for a in assignments if a.node_id != self.identity.node_id]
        leases: dict[str, AdmissionLease] = {}
        try:
            for assignment in workers:
                key = (str(network.network_id), assignment.node_id)
                worker_result = self.verified_results[key]
                worker_bridge_id = worker_result["bridge"]["instance_id"]
                master_bridge = self.master_bridges[key]
                master_status = native.network_bridge_status(master_bridge)
                if assignment.rpc_child_identity is None:
                    raise PlatformRefusal("RPC_BUSY", "Worker child identity is unavailable")
                lease = AdmissionLease(
                    expected_rpc_child_identity=assignment.rpc_child_identity,
                    lease_id=uuid4(),
                    network_id=network.network_id,
                    node_id=assignment.node_id,
                    generation=master_status["generation"] + 1,
                    kind="probe",
                    assignment_revision=assignment.assignment_revision,
                    authority=(await self.store.arun(self._authority)),
                    relay_instance_ids=[UUID(worker_bridge_id), UUID(master_status["instance_id"])],
                    state="requested",
                )
                (await self.store.arun(self.store.lease, lease))
                leases[assignment.node_id] = lease
                self.probe_leases[key] = lease
            granted = await self._phase(
                operation,
                workers,
                "admit_probe",
                {node: lease.model_dump(mode="json") for node, lease in leases.items()},
                2,
            )
            if any(result["status"] == "failed" for result in granted.values()):
                return False

            async def probe(assignment: NodeAssignment) -> None:
                key = (str(network.network_id), assignment.node_id)
                lease = leases[assignment.node_id]
                bridge = self.master_bridges[key]
                bridge.grant(lease.generation, 15000, False)
                diagnostic = self.executor.diagnostics[str(network.network_id)]
                proof = await diagnostic.challenge(
                    assignment.node_id,
                    assignment.assignment_revision,
                    self.executor.preparation[str(network.network_id)].diagnostic_port,
                )
                if assignment.launch_rpc_endpoint is None:
                    raise PlatformRefusal("STALE_REVISION", "Master bridge endpoint disappeared")
                child = assignment.rpc_child_identity
                installed_identity = child.llama_tag if child else "v0.5.0"
                result = await asyncio.to_thread(
                    native.probe_network_rpc,
                    assignment.launch_rpc_endpoint,
                    "127.0.0.1",
                    installed_identity,
                    2000,
                )
                self.freshness.publish(
                    network.network_id,
                    assignment.node_id,
                    {
                        "idle_probe_at": time.monotonic(),
                        "idle_observed_at": utc_now().isoformat(),
                        "rpc_released": False,
                        "rpc_result": result,
                        "dedicated_proof": proof,
                        "worker_bridge_id": self.verified_results[key]["bridge"]["instance_id"],
                        "assignment_revision": assignment.assignment_revision,
                        "child": assignment.rpc_child_identity.model_dump(mode="json")
                        if assignment.rpc_child_identity
                        else None,
                    },
                )

            await asyncio.gather(*(probe(a) for a in workers))
        finally:
            if leases:
                released = await self._phase(
                    operation,
                    [a for a in workers if a.node_id in leases],
                    "release_admission",
                    {node: lease.model_dump(mode="json") for node, lease in leases.items()},
                    2,
                )
                for assignment in workers:
                    released_lease = leases.get(assignment.node_id)
                    if released_lease is None:
                        continue
                    lease = released_lease
                    bridge = self.master_bridges[(str(network.network_id), assignment.node_id)]
                    local_drained = (
                        bool(bridge.release(lease.generation))
                        if native.network_bridge_status(bridge)["generation"] == lease.generation
                        else True
                    )
                    result = released[assignment.node_id]
                    drained = local_drained and result.get("drained") is True
                    lease.state = "released" if drained else "uncertain"
                    lease.release_ack, lease.drain_ack = result.get("drained") is not None, drained
                    (await self.store.arun(self.store.lease, lease))
                    evidence = self.freshness.live.get(
                        (str(network.network_id), assignment.node_id)
                    )
                    if evidence:
                        evidence["rpc_released"] = drained
                        if drained and assignment.rpc_child_identity is not None:
                            authority = await self.store.arun(self._authority)
                            verified = self.verified_results[
                                (str(network.network_id), assignment.node_id)
                            ]
                            (
                                await self.store.arun(
                                    self.store.evidence,
                                    VerificationEvidence(
                                        network_id=network.network_id,
                                        node_id=assignment.node_id,
                                        assignment_revision=assignment.assignment_revision,
                                        master_term=authority.master_term,
                                        master_session_id=authority.master_session_id,
                                        adapter_fingerprint=assignment.identity_fingerprint,
                                        address=assignment.address,
                                        route_interface_source={
                                            "worker": verified.get("routes_verified"),
                                            "master_source": evidence["dedicated_proof"]["source"],
                                        },
                                        signed_bidirectional_proof={
                                            "forward": evidence["dedicated_proof"],
                                            "reverse": verified.get("dedicated_proof"),
                                        },
                                        idle_rpc_probe_result={
                                            **evidence["rpc_result"],
                                            "released": True,
                                        },
                                        runtime_identity=assignment.rpc_child_identity,
                                        relay_instance_ids=lease.relay_instance_ids,
                                    ),
                                )
                            )
                if any(lease.state != "released" for lease in leases.values()):
                    self.freshness.invalidate(
                        network.network_id, "Admission release was not acknowledged as drained"
                    )
        for assignment in workers:
            self.freshness.require(network.network_id, assignment.node_id)
        return True

    async def begin_operation(self, network_id: UUID, request: OperationRequest) -> dict[str, Any]:
        async with self.lock:
            authority = await self.store.arun(self._authority)
            network = await self.store.arun(self.store.network, network_id)
            if network is None:
                raise KeyError("unknown network")
            body_digest = digest({"network_id": str(network_id), **request.model_dump(mode="json")})
            prior = await self.store.arun(
                self.store.operation_for_request, authority.cluster_binding_id, request.request_id
            )
            if prior:
                if prior.proposal_digest != body_digest:
                    raise StoreConflict("request ID reused with changed body")
                return self._accepted(prior, network)
            if network.revision != request.expected_revision:
                raise StoreConflict("network revision changed")
            if any(
                run.state != "released"
                for run in (await self.store.arun(self.store.runs, network_id))
            ) or any(
                lease.state != "released"
                for lease in (await self.store.arun(self.store.leases, network_id))
            ):
                raise PlatformRefusal(
                    "INFERENCE_ACTIVE", "A reserved, active or uncertain run blocks network changes"
                )
            if request.action in ("add", "retry"):
                return await self._begin_reconfigure(network, request, authority, body_digest)
            assignments = await self.store.arun(self.store.assignments, network_id)
            previous = (
                (await self.store.arun(self.store.operation, network.current_operation_id))
                if network.current_operation_id
                else None
            )
            participants = [a.node_id for a in assignments] or (
                previous.participants if previous else []
            )
            operation = NetworkOperation(
                operation_id=uuid4(),
                request_id=request.request_id,
                network_id=network_id,
                cluster_binding_id=authority.cluster_binding_id,
                action="stop",
                master_term=authority.master_term,
                master_session_id=authority.master_session_id,
                expected_network_revision=request.expected_revision,
                operation_revision=1,
                participants=participants,
                proposal_digest=body_digest,
                phase="cleaning",
                deadline=utc_now() + timedelta(seconds=60),
            )
            network.desired_state = "stopped"
            network.current_operation_id = operation.operation_id
            (await self.store.arun(self.store.put_operation, operation))
            (await self.store.arun(self._publish, network, "stopping"))
            self.freshness.invalidate(network_id, "Stop requested")
            self.tasks[str(operation.operation_id)] = asyncio.create_task(
                self._stop_operation(network, operation, assignments)
            )
            return self._accepted(operation, network)

    async def _begin_reconfigure(
        self,
        network: ManagedNetwork,
        request: OperationRequest,
        authority: MasterAuthority,
        body_digest: str,
    ) -> dict[str, Any]:
        from kllm.modules.managed_network.interactors.allocation import ParticipantSelection

        existing = await self.store.arun(self.store.assignments, network.network_id)
        if not existing:
            raise PlatformRefusal(
                "INVALID_ADAPTER", "No assignments to retry; stop the failed setup and start again"
            )
        if request.action == "add" and network.observed_state != "ready":
            raise PlatformRefusal("STALE_REVISION", "Add requires a currently ready idle network")
        if request.action == "retry" and network.observed_state not in (
            "failed",
            "degraded",
            "verifying",
        ):
            raise PlatformRefusal("CLEANUP_PENDING", "Resolve cleanup before retrying")
        inventories = await self.inventories()
        selections = {p.node_id: p for p in request.participants}
        for assignment in existing:
            inventory = inventories.get(assignment.node_id, {})
            adapter = next(
                (
                    a
                    for a in inventory.get("adapters", [])
                    if a["adapter_id"] == assignment.adapter_id
                ),
                None,
            )
            if adapter is None:
                raise PlatformRefusal(
                    "INVALID_ADAPTER", "Refresh or reconnect every existing participant"
                )
            selections.setdefault(
                assignment.node_id,
                ParticipantSelection(
                    node_id=assignment.node_id,
                    adapter_id=assignment.adapter_id,
                    inventory_revision=adapter["inventory_revision"],
                ),
            )
        if request.action == "retry" and any(
            p.node_id not in {a.node_id for a in existing} for p in selections.values()
        ):
            raise ValueError("use add for new participants")
        preparation = self.executor.preparation.get(str(network.network_id))
        configuration = StartRequest(
            request_id=request.request_id,
            subnet=network.subnet,
            participants=list(selections.values()),
            bridge_port=preparation.bridge_port if preparation else 50052,
            diagnostic_port=preparation.diagnostic_port if preparation else 18767,
        )
        operation = NetworkOperation(
            operation_id=uuid4(),
            request_id=request.request_id,
            network_id=network.network_id,
            cluster_binding_id=authority.cluster_binding_id,
            action="add" if request.action == "add" else "retry",
            master_term=authority.master_term,
            master_session_id=authority.master_session_id,
            expected_network_revision=request.expected_revision,
            operation_revision=1,
            participants=list(selections),
            proposal_digest=body_digest,
            phase="preparing",
            deadline=utc_now() + timedelta(seconds=60),
        )
        (await self.store.arun(self.store.put_operation, operation))
        network.current_operation_id = operation.operation_id
        self.freshness.invalidate(network.network_id, "Topology verification requested")
        (
            await self.store.arun(
                self._publish,
                network,
                "preparing" if network.observed_state == "failed" else "verifying",
            )
        )

        async def reconfigure():
            if request.action == "retry":
                async with self.lock:
                    await self._phase(
                        operation, existing, "inspect", {a.node_id: {} for a in existing}, 5
                    )
                    cleaned = await self._phase(
                        operation,
                        existing,
                        "rollback",
                        {a.node_id: {"desired_state": "stopped"} for a in existing},
                        60,
                    )
                    if any(r["status"] != "stopped" for r in cleaned.values()):
                        (await self.store.arun(self._publish, network, "cleanup_pending"))
                        operation.phase = "failed"
                        operation.terminal_result = {"error_code": "CLEANUP_PENDING"}
                        (
                            await self.store.arun(
                                self.store.update_operation, operation, operation.operation_revision
                            )
                        )
                        return
                    for key, bridge in list(self.master_bridges.items()):
                        if key[0] == str(network.network_id):
                            status = native.network_bridge_status(bridge)
                            await asyncio.to_thread(
                                native.stop_network_bridge, bridge, status["instance_id"]
                            )
                            self.master_bridges.pop(key)
            await self._start_operation(network, operation, configuration, existing)

        self.tasks[str(operation.operation_id)] = asyncio.create_task(reconfigure())
        return self._accepted(operation, network)

    async def _stop_operation(
        self,
        network: ManagedNetwork,
        operation: NetworkOperation,
        assignments: list[NodeAssignment],
    ) -> None:
        async with self.lock:
            pending = False
            for key, bridge in list(self.master_bridges.items()):
                if key[0] != str(network.network_id):
                    continue
                status = native.network_bridge_status(bridge)
                drained = await asyncio.to_thread(
                    native.stop_network_bridge, bridge, status["instance_id"]
                )
                if not drained:
                    pending = True
                else:
                    self.master_bridges.pop(key)
            await self._phase(
                operation, assignments, "inspect", {a.node_id: {} for a in assignments}, 5
            )
            results = await self._phase(
                operation,
                assignments,
                "cleanup",
                {a.node_id: {"desired_state": "stopped"} for a in assignments},
                60,
            )
            pending = pending or any(result["status"] != "stopped" for result in results.values())
            for assignment in assignments:
                assignment.state = (
                    "cleanup_pending"
                    if results[assignment.node_id]["status"] != "stopped"
                    else "stopped"
                )
                (
                    await self.store.arun(
                        self.store.put_assignment,
                        assignment,
                        expected_revision=assignment.assignment_revision,
                    )
                )
            (
                await self.store.arun(
                    self._publish, network, "cleanup_pending" if pending else "stopped"
                )
            )
            operation.phase = "failed" if pending else "complete"
            operation.terminal_result = {
                "state": network.observed_state,
                "cleanup_pending": [
                    node for node, result in results.items() if result["status"] != "stopped"
                ],
            }
            (
                await self.store.arun(
                    self.store.update_operation, operation, operation.operation_revision
                )
            )
            await asyncio.to_thread(self.sync_directory)

    async def cancel(self, operation_id: UUID, request) -> dict[str, Any]:
        operation = await asyncio.to_thread(
            self.store.cancel_operation,
            operation_id,
            request.request_id,
            request.expected_operation_revision,
        )
        network = await self.store.arun(self.store.network, operation.network_id)
        if network is None:
            raise KeyError("unknown network")
        if operation.terminal_result is None:
            task = self.tasks.get(str(operation_id))
            if task:
                task.cancel()
        return self._accepted(operation, network)

    async def wait_operation(self, operation_id: UUID | str) -> None:
        task = self.tasks.get(str(operation_id))
        if task:
            await asyncio.shield(task)

    def operation_view(self, operation_id: UUID | str) -> dict[str, Any]:
        operation = self.store.operation(operation_id)
        if operation is None:
            raise KeyError("unknown operation")
        return {
            **operation.model_dump(mode="json"),
            "results": [item.model_dump(mode="json") for item in self.store.results(operation_id)],
        }

    def network_view(self, network_id: UUID | str) -> dict[str, Any]:
        network = self.store.network(network_id)
        if network is None:
            raise KeyError("unknown network")
        participants = []
        for assignment in self.store.assignments(network_id):
            evidence = self.freshness.live.get((str(network_id), assignment.node_id))
            result = self.results.get((str(network_id), assignment.node_id), {})
            participants.append(
                {
                    **assignment.model_dump(mode="json", exclude={"launch_rpc_endpoint"}),
                    "last_verification_at": evidence.get(
                        "health_observed_at", evidence.get("idle_observed_at")
                    )
                    if evidence
                    else None,
                    "cleanup_status": assignment.state
                    if assignment.state in ("cleanup_pending", "cleaning", "stopped")
                    else None,
                    "verification_freshness": "fresh"
                    if evidence and evidence["health_deadline"] > time.monotonic()
                    else "unverified",
                    "error": {"code": result.get("error_code"), "message": result.get("message")}
                    if result.get("error_code")
                    else None,
                }
            )
        return {
            **network.model_dump(mode="json"),
            "participants": participants,
            "blocking_runs": [
                run.model_dump(mode="json", exclude={"launch_endpoints"})
                for run in self.store.runs(network_id)
                if run.state != "released"
            ],
        }

    def summaries(self) -> list[dict[str, Any]]:
        return [self.network_view(n.network_id) for n in self.store.networks()]

    def directory_payload(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.store.directory()]

    def directory_view(self) -> dict[str, dict[str, Any]]:
        return {item.node_id: item.model_dump(mode="json") for item in self.store.directory()}

    def remove_directory(self, node_id: str) -> None:
        self.store.remove_directory(node_id)

    @staticmethod
    def _directory_host(value: Any) -> str:
        return "".join(char for char in str(value or "") if ord(char) >= 32 and ord(char) != 127)[
            :253
        ]

    def sync_directory(self) -> None:
        """Refresh the durable node directory from membership plus assignments.

        The directory is a display/identity read model: membership owns liveness
        and the managed-network store owns the dedicated address. Rows are only
        rewritten when an identity field changes so ``updated_at`` stays stable.
        """
        state = self.state()
        if state.role != "master":
            return
        assignments: dict[str, tuple[int, NodeAssignment]] = {}
        for network in self.store.networks():
            rank = (
                2
                if network.observed_state == "ready"
                else 1
                if network.observed_state != "stopped"
                else 0
            )
            for assignment in self.store.assignments(network.network_id):
                previous = assignments.get(assignment.node_id)
                if previous is None or rank >= previous[0]:
                    assignments[assignment.node_id] = (rank, assignment)
        current = {item.node_id: item for item in self.store.directory()}
        items = []
        for node_id, member in state.members.items():
            rank, assignment = assignments.get(node_id, (0, None))
            private: str | None = None
            prefix: int | None = None
            network_id: UUID | None = None
            assignment_state: AssignmentState | None = None
            link: int | None = None
            if (
                assignment is not None
                and rank > 0
                and assignment.state in ("configured", "verification_pending", "ready", "degraded")
            ):
                private, prefix, network_id = (
                    assignment.address,
                    assignment.prefix_length,
                    assignment.network_id,
                )
                assignment_state, link = assignment.state, assignment.link_speed_mbps
            hostname = self._directory_host(member.get("hostname")) or (
                local_hostname() or "" if node_id == state.node_id else ""
            )
            item = NodeNetworkIdentity(
                node_id=node_id,
                hostname=hostname,
                role=member.get("role") or ("master" if node_id == state.node_id else "worker"),
                lan_host=self._directory_host(member.get("lan_host") or member.get("host")),
                rpc_bind=self._directory_host(member.get("rpc_bind") or member.get("host")),
                private_address=private,
                private_prefix_length=prefix,
                network_id=network_id,
                assignment_state=assignment_state,
                link_speed_mbps=link,
            )
            prior = current.pop(node_id, None)
            if prior is not None and prior.model_dump(exclude={"updated_at"}) == item.model_dump(
                exclude={"updated_at"}
            ):
                item = prior
            items.append(item)
        self.store.put_directory_many(items)

    async def _health(
        self,
        network: ManagedNetwork,
        operation: NetworkOperation,
        assignments: list[NodeAssignment],
    ) -> None:
        if any(a.node_id not in self.state().members for a in assignments):
            raise PlatformRefusal("STALE_REVISION", "A selected participant left or was evicted")
        results = await self._phase(
            operation,
            assignments,
            "verify",
            {a.node_id: {"health_only": True} for a in assignments},
            2,
        )
        diagnostic = self.executor.diagnostics.get(str(network.network_id))
        if diagnostic is None:
            raise PlatformRefusal("STALE_REVISION", "Dedicated diagnostic listener disappeared")
        for assignment in assignments:
            key = (str(network.network_id), assignment.node_id)
            result = results[assignment.node_id]
            if result["status"] == "failed":
                raise PlatformRefusal(
                    result.get("error_code", "TIMEOUT"), "Dedicated health failed"
                )
            previous = self.freshness.live.get(key, {})
            if assignment.node_id != self.identity.node_id:
                proof = await diagnostic.challenge(
                    assignment.node_id, assignment.assignment_revision, result["diagnostic_port"]
                )
                self.freshness.publish(
                    network.network_id,
                    assignment.node_id,
                    {
                        **previous,
                        "assignment_revision": assignment.assignment_revision,
                        "dedicated_proof": proof,
                    },
                )
            else:
                self.freshness.publish(
                    network.network_id,
                    assignment.node_id,
                    {"assignment_revision": assignment.assignment_revision},
                )

    async def _monitor(self) -> None:
        while not self.closed:
            await asyncio.sleep(5)
            pending_cleanup = []
            async with self.lock:
                for network in await self.store.arun(self.store.networks):
                    if (
                        network.desired_state == "stopped"
                        and network.observed_state == "cleanup_pending"
                        and not any(
                            run.state != "released"
                            for run in (await self.store.arun(self.store.runs, network.network_id))
                        )
                    ):
                        pending_cleanup.append(network)
                    if (
                        network.observed_state not in ("ready", "verifying", "degraded")
                        or self.state().role != "master"
                    ):
                        continue
                    operation = (
                        (await self.store.arun(self.store.operation, network.current_operation_id))
                        if network.current_operation_id
                        else None
                    )
                    if operation is None:
                        continue
                    assignments = await self.store.arun(self.store.assignments, network.network_id)
                    runtime_stage = False
                    try:
                        await asyncio.wait_for(self._health(network, operation, assignments), 2)
                        active = [
                            run
                            for run in (await self.store.arun(self.store.runs, network.network_id))
                            if run.state == "active"
                        ]
                        if (
                            not active
                            and not any(
                                run.state != "released"
                                for run in (
                                    await self.store.arun(self.store.runs, network.network_id)
                                )
                            )
                            and not any(
                                lease.state != "released"
                                for lease in (
                                    await self.store.arun(self.store.leases, network.network_id)
                                )
                            )
                        ):
                            if network.observed_state == "degraded":
                                (await self.store.arun(self._publish, network, "verifying"))
                            if network.observed_state == "verifying":
                                (await self.store.arun(self._publish, network, "ready"))
                        runtime_stage = True
                        for run in active:
                            if run.engine == "primacpp":
                                continue  # Prima's coordinator owns all-rank leases and cleanup.
                            if run.child_pid and (
                                not native.process_alive(run.child_pid)
                                or native._managed_native()._process_identity(run.child_pid)[0]
                                != run.child_start_token
                            ):
                                await self.admission.release(run)
                                continue
                            for assignment in assignments:
                                if assignment.node_id == self.identity.node_id:
                                    continue
                                if assignment.node_id not in run.assignment_revisions:
                                    continue
                                key = (str(network.network_id), assignment.node_id)
                                statuses = [
                                    native.network_bridge_status(self.master_bridges[key]),
                                    self.verified_results[key]["bridge"],
                                ]
                                if any(
                                    status.get("admission_consumed")
                                    and not status["active_streams"]
                                    and not status["pending_connects"]
                                    for status in statuses
                                ):
                                    raise PlatformRefusal(
                                        "STALE_REVISION", "An admitted run stream ended"
                                    )
                            leases = {
                                lease.node_id: lease
                                for lease in (
                                    await self.store.arun(self.store.leases, network.network_id)
                                )
                                if lease.run_id == run.run_id and lease.state != "released"
                            }
                            workers = [a for a in assignments if a.node_id in leases]
                            renewed = await self._phase(
                                operation,
                                workers,
                                "renew_admission",
                                {
                                    node: lease.model_dump(mode="json")
                                    for node, lease in leases.items()
                                },
                                2,
                            )
                            for worker in workers:
                                lease = leases[worker.node_id]
                                if renewed[worker.node_id].get("grant_ack") is not True:
                                    raise PlatformRefusal(
                                        "TIMEOUT", "Run lease renewal was not acknowledged"
                                    )
                                self.master_bridges[
                                    (str(network.network_id), worker.node_id)
                                ].grant(lease.generation, 15000, True)
                    except Exception as error:
                        if runtime_stage:
                            if self.rpc_runs is not None:
                                self.rpc_runs.runtime_error = str(error)
                            continue
                        self.freshness.invalidate(
                            network.network_id,
                            "Dedicated route, identity or admission health failed",
                        )
                        if network.observed_state != "degraded":
                            (await self.store.arun(self._publish, network, "degraded"))

            for network in pending_cleanup:
                try:
                    accepted = await self.begin_operation(
                        network.network_id,
                        OperationRequest(
                            request_id=uuid4(), expected_revision=network.revision, action="stop"
                        ),
                    )
                    await self.wait_operation(accepted["operation_id"])
                except (PlatformRefusal, StoreConflict):
                    pass  # Pending state survives until the missing node reconciles.

    async def close(self, *, deadline: float | None = None) -> None:
        if self._close_complete:
            return
        self.closed = True
        deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + self.store._store.options.shutdown_timeout
        )
        try:
            await self._close_until(deadline)
        except BaseException:
            # Stop external admission even when an earlier producer phase runs
            # past the deadline. Protected continuations keep their own ownership.
            self.store._store.reject_new_work()
            raise

    async def _close_until(self, deadline: float) -> None:
        if self.runtimes is not None:
            await self._await_close(self.runtimes.close(), deadline)
        if self.rpc_runs is not None:
            await self._await_close(self.rpc_runs.close(), deadline)
        for task in (self.rpc_monitor_task, self.worker_expiry_task):
            if task:
                task.cancel()
                await self._await_close(asyncio.gather(task, return_exceptions=True), deadline)
        if self.monitor_task:
            self.monitor_task.cancel()
            await self._await_close(
                asyncio.gather(self.monitor_task, return_exceptions=True), deadline
            )
        pending = set(self.tasks.values())
        if pending:
            _, pending = await asyncio.wait(pending, timeout=max(0.0, deadline - time.monotonic()))
        if pending:
            raise PersistenceError(
                "managed network shutdown deadline expired; protected operations remain tracked"
            )
        await self._await_close(self.channels.close(), deadline)
        await self._await_close(self.executor.close(), deadline)
        for bridge in list(self.master_bridges.values()):
            status = native.network_bridge_status(bridge)
            await self._await_close(
                asyncio.to_thread(
                    native.stop_network_bridge, bridge, status["instance_id"], terminate=True
                ),
                deadline,
            )
        self.master_bridges.clear()
        close_platform = getattr(self.executor.platform, "close", None)
        if close_platform is not None:
            await self._await_close(asyncio.to_thread(close_platform), deadline)
        await self.store.aclose(deadline=deadline)
        self._close_complete = True

    async def _await_close(self, awaitable, deadline: float):
        task = asyncio.ensure_future(awaitable)
        self._close_jobs.add(task)
        task.add_done_callback(self._close_jobs.discard)
        done, _ = await asyncio.wait({task}, timeout=max(0.0, deadline - time.monotonic()))
        if not done:
            raise PersistenceError(
                "managed network shutdown deadline expired; cleanup remains tracked"
            )
        return task.result()

    async def aclose(self, *, deadline: float | None = None) -> None:
        await self.close(deadline=deadline)
