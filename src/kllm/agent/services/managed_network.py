"""Master coordination and resource ownership for explicitly selected networks."""
from __future__ import annotations

import asyncio
import socket
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from kllm import native
from kllm.managed_network.allocation import AdapterSelection, OperationRequest, StartRequest, assign, propose
from kllm.managed_network.executor import LocalNetworkExecutor, platform_adapter
from kllm.managed_network.identity import IdentityError, LocalIdentity, SignedCommand
from kllm.managed_network.models import (
    AdmissionLease, ManagedNetwork, MasterAuthority, NetworkOperation,
    NetworkState, Phase, NodeAssignment, ParticipantResult, RPCChildIdentity, VerificationEvidence, utc_now,
)
from kllm.managed_network.platform.base import PlatformAdapter, PlatformRefusal
from kllm.managed_network.store import NetworkStore, StoreConflict, digest
from kllm.managed_network.verification import Freshness
from kllm.state import repo_root
from .network_channel import NetworkChannelHub


class ManagedNetworkService:
    def __init__(self, home: Path, state: Callable[[], Any], master_base: Callable[[], str | None], *, platform: PlatformAdapter | None = None, repository: Callable[[], Path | None] = repo_root):
        self.state, self.home = state, home
        self.store = NetworkStore(home)
        self.identity = LocalIdentity(home, state().node_id, self.store)
        self.executor = LocalNetworkExecutor(self.store, self.identity, state, platform or platform_adapter(), repository)
        self.channels = NetworkChannelHub(self.store, self.identity, state, master_base, self.executor.execute)
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
        from kllm.managed_network.run_admission import RunAdmission
        self.admission = RunAdmission(self)
        self.loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.store.recover()
        await self.channels.start()
        self.monitor_task = asyncio.create_task(self._monitor(), name='kllm-managed-network-health')

    def _authority(self) -> MasterAuthority:
        authority = self.channels.master_authority()
        if self.local_authority is None:
            self.local_authority = authority.model_copy(update={'accepted_channel_id': uuid4()})
            self.store.accept_authority(self.local_authority, self.identity.node_id)
        return self.local_authority

    def local_inventory(self) -> dict[str, Any]:
        return self.executor.inspect_view()

    def select_adapter(self, selection: AdapterSelection) -> dict[str, Any]:
        snapshot = self.executor.platform.inspect(selection.adapter_id)
        if snapshot.public()['inventory_revision'] != selection.expected_inventory_revision:
            raise StoreConflict('adapter inventory changed')
        if any(n.observed_state != 'stopped' for n in self.store.networks()):
            raise PlatformRefusal('OPERATION_IN_PROGRESS', 'Stop or resolve the current network before changing selection')
        import json
        value = selection.model_dump(mode='json')
        self.store.set_metadata('network_selection', json.dumps(value))
        return value

    async def inventories(self) -> dict[str, Any]:
        if self.state().role != 'master':
            raise PlatformRefusal('NOT_MASTER', 'Cluster inventories require master role')
        authority = self._authority()
        result = {self.identity.node_id: await asyncio.wait_for(asyncio.to_thread(self.local_inventory), 5)}
        async def worker(node_id: str) -> None:
            peer = self.store.peer(node_id)
            session = self.channels.sessions.get(node_id)
            if peer is None or peer.expected_role != 'worker' or peer.cluster_binding_id != authority.cluster_binding_id or session is None:
                result[node_id] = {'node_id': node_id, 'adapters': [], 'error_code': 'UNPAIRED_IDENTITY' if peer is None else 'UNSUPPORTED_CAPABILITY'}
                return
            try:
                result[node_id] = await session.request(network_id=uuid4(), operation_id=uuid4(), operation_revision=1, phase_sequence=1, phase='inspect', assignment_revision=1, expected_local_revision=0, body={}, ttl_ms=5000)
            except Exception:
                result[node_id] = {'node_id': node_id, 'adapters': [], 'error_code': 'TIMEOUT'}
        await asyncio.gather(*(worker(node) for node in self.state().members if node != self.identity.node_id))
        return result

    @staticmethod
    def _accepted(operation: NetworkOperation, network: ManagedNetwork) -> dict[str, Any]:
        return {'network_id': str(network.network_id), 'operation_id': str(operation.operation_id), 'state': network.observed_state, 'revision': network.revision}

    async def begin_start(self, request: StartRequest) -> dict[str, Any]:
        async with self.lock:
            authority = self._authority()
            prior = self.store.operation_for_request(authority.cluster_binding_id, request.request_id)
            body_digest = digest(request.model_dump(mode='json'))
            if prior:
                if prior.proposal_digest != body_digest:
                    raise StoreConflict('request ID reused with changed body')
                network = self.store.network(prior.network_id)
                assert network is not None
                return self._accepted(prior, network)
            if any(n.observed_state != 'stopped' for n in self.store.networks()):
                raise PlatformRefusal('OPERATION_IN_PROGRESS', 'One non-stopped network is allowed per cluster binding')
            if sum(p.node_id == self.identity.node_id for p in request.participants) != 1:
                raise ValueError('select the local master exactly once')
            network = ManagedNetwork(network_id=uuid4(), cluster_binding_id=authority.cluster_binding_id, master_node_id=self.identity.node_id, master_key_id=self.identity.key_id, subnet=request.subnet or '192.168.234.0/24')
            operation = NetworkOperation(operation_id=uuid4(), request_id=request.request_id, network_id=network.network_id, cluster_binding_id=authority.cluster_binding_id, action='start', master_term=authority.master_term, master_session_id=authority.master_session_id, expected_network_revision=0, operation_revision=1, participants=[p.node_id for p in request.participants], proposal_digest=body_digest, phase='preparing', deadline=utc_now() + timedelta(seconds=60))
            network.current_operation_id = operation.operation_id
            self.store.put_network(network)
            self.store.put_operation(operation)
            task = asyncio.create_task(self._start_operation(network, operation, request), name=f'kllm-network-{operation.operation_id}')
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

    async def _request(self, operation: NetworkOperation, assignment: NodeAssignment, phase: Phase, body: dict[str, Any], timeout: float, sequence: int) -> dict[str, Any]:
        network_key, node_id = str(operation.network_id), assignment.node_id
        key = (network_key, node_id)
        revision = self.local_revisions.get(key, self.executor.local_revision(operation.network_id) if node_id == self.identity.node_id else 0)
        try:
            if node_id == self.identity.node_id:
                authority = self._authority()
                command = SignedCommand.model_validate({**authority.model_dump(exclude={'accepted_channel_id'}), 'channel_id': authority.accepted_channel_id, 'version': 1, 'type': 'command', 'sender_node_id': node_id, 'target_node_id': node_id, 'sequence': sequence, 'network_id': operation.network_id, 'operation_id': operation.operation_id, 'operation_revision': operation.operation_revision, 'phase_sequence': sequence, 'phase': phase, 'assignment_revision': assignment.assignment_revision, 'expected_local_revision': revision, 'ttl_ms': max(1, min(60000, int(timeout * 1000))), 'body': body})
                result = await self.executor.execute(command, time.monotonic() + timeout)
            else:
                session = self.channels.sessions.get(node_id)
                if session is None:
                    raise IdentityError('worker control channel unavailable')
                result = await session.request(network_id=operation.network_id, operation_id=operation.operation_id, operation_revision=operation.operation_revision, phase_sequence=sequence, phase=phase, assignment_revision=assignment.assignment_revision, expected_local_revision=revision, body=body, ttl_ms=max(1, min(60000, int(timeout * 1000))))
            if not isinstance(result.get('local_revision'), int) or isinstance(result['local_revision'], bool):
                raise IdentityError('worker result lacks a valid local fence')
            self.local_revisions[key] = result['local_revision']
            previous_result = self.results.get(key, {})
            preserve_error = phase == 'rollback' and result.get('status') == 'stopped' and previous_result.get('error_code')
            self.results[key] = {**result, 'error_code': previous_result['error_code'], 'message': previous_result.get('message', '')} if preserve_error else result
            if phase in ('prepare', 'apply', 'rollback', 'cleanup') or (phase == 'verify' and result.get('status') == 'failed'):
                current = next((a for a in self.store.assignments(operation.network_id) if a.node_id == node_id), None)
                if current is not None:
                    current.state = result['status']
                    if result.get('error_code') == 'OWNERSHIP_CONFLICT':
                        current.ownership = 'uncertain'
                    if not preserve_error:
                        current.error_code = result.get('error_code')
                        current.message = result.get('message', '')
                    self.store.put_assignment(current, expected_revision=current.assignment_revision)
            if phase == 'verify' and result.get('status') != 'failed':
                self.verified_results[key] = result
            participant = ParticipantResult(node_id=node_id, assignment_revision=assignment.assignment_revision, expected_local_revision=revision, stage=phase, phase_sequence=sequence, status=result['status'], signed_result_digest=digest(result), prepared_snapshot_digest=result.get('prepared_snapshot_digest'), ownership_journal_id=result.get('journal_id'), error_code=result.get('error_code'))
            self.store.result(operation.operation_id, participant)
            return result
        except (Exception, asyncio.CancelledError) as error:
            code = error.code if isinstance(error, PlatformRefusal) else 'TIMEOUT' if isinstance(error, TimeoutError) else 'CLEANUP_PENDING' if phase in ('rollback', 'cleanup') else 'UNSUPPORTED_CAPABILITY'
            result = {'status': 'cleanup_pending' if phase in ('rollback', 'cleanup') else 'failed', 'error_code': code, 'message': 'Participant phase did not complete with a current authenticated result'}
            self.results[key] = result
            self.store.result(operation.operation_id, ParticipantResult(node_id=node_id, assignment_revision=assignment.assignment_revision, expected_local_revision=revision, stage=phase, phase_sequence=sequence, status=result['status'], error_code=code))
            return result

    async def _phase(self, operation: NetworkOperation, assignments: list[NodeAssignment], phase: Phase, bodies: dict[str, dict], budget: float) -> dict[str, dict]:
        sequence = self._sequence(operation.operation_id)
        result = await asyncio.gather(*(self._request(operation, a, phase, bodies[a.node_id], budget, sequence) for a in assignments))
        return dict(zip((a.node_id for a in assignments), result))

    def _management_hosts(self) -> list[str]:
        addresses: list[str] = []
        for member in self.state().members.values():
            for key in ('host', 'lan_host', 'wg_address'):
                host = member.get(key)
                if host:
                    addresses.extend(str(item[4][0]) for item in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM))
        host = getattr(self.state(), 'master_host', None)
        if host:
            addresses.extend(str(item[4][0]) for item in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM))
        return sorted(set(addresses))

    async def _start_operation(self, network: ManagedNetwork, operation: NetworkOperation, request: StartRequest, existing: list[NodeAssignment] | None = None) -> None:
        async with self.lock:
            assignments: list[NodeAssignment] = []
            applied = False
            changed: list[NodeAssignment] = []
            try:
                prepare_deadline = time.monotonic() + 15
                inventories = await self.inventories()
                selected = {}
                for participant in request.participants:
                    if participant.node_id not in self.state().members:
                        raise PlatformRefusal('INVALID_ADAPTER', 'Every selected participant must be currently enrolled')
                    node_inventory = inventories.get(participant.node_id, {})
                    adapter = next((a for a in node_inventory.get('adapters', []) if a['adapter_id'] == participant.adapter_id), None)
                    if adapter is None:
                        raise PlatformRefusal(node_inventory.get('error_code', 'INVALID_ADAPTER'), 'Selected worker is unpaired, unavailable or lacks this adapter')
                    selected[participant.node_id] = adapter
                if request.subnet is None:
                    proposals = propose(list(selected.values()), len(request.participants))
                    if not proposals or network.subnet != proposals[0]:
                        # Proposals are fixed before network persistence; do not rewrite
                        # an authority-bound subnet after the operation was committed.
                        raise PlatformRefusal('SUBNET_CONFLICT', 'Default proposal conflicts; select an available private subnet explicitly')
                assignments = assign(network.network_id, network.subnet, self.identity.node_id, request.participants, selected, existing)
                old = {a.node_id: a for a in existing or []}
                changed = [a for a in assignments if operation.action != 'add' or a.node_id not in old]
                for assignment in changed:
                    if assignment.node_id in old:
                        assignment.assignment_revision += 1
                        assignment.state = 'preparing'
                    self.store.put_assignment(assignment, expected_revision=old[assignment.node_id].assignment_revision - 1 if assignment.node_id in old else None)
                management_hosts = await asyncio.to_thread(self._management_hosts)
                preparation_bodies = {a.node_id: {'network': network.model_dump(mode='json'), 'operation': operation.model_dump(mode='json'), 'assignment': a.model_dump(mode='json'), 'inventory_revision': selected[a.node_id]['inventory_revision'], 'management_hosts': management_hosts, 'participants': [item.model_dump(mode='json') for item in assignments], 'bridge_port': request.bridge_port, 'diagnostic_port': request.diagnostic_port} for a in assignments}
                remaining = prepare_deadline - time.monotonic()
                if remaining <= 0:
                    raise PlatformRefusal('TIMEOUT', 'Participant inspection exhausted preparation budget')
                if operation.action == 'add':
                    reconciled = await self._phase(operation, [a for a in assignments if a.node_id in old], 'reconcile', preparation_bodies, remaining)
                    if any(r['status'] == 'failed' for r in reconciled.values()):
                        raise PlatformRefusal('CLEANUP_PENDING', 'Existing participants could not acknowledge added topology')
                prepared = await self._phase(operation, changed, 'prepare', preparation_bodies, remaining)
                if time.monotonic() > prepare_deadline:
                    raise PlatformRefusal('TIMEOUT', 'Preparation deadline expired; no addresses were applied')
                if any(result['status'] != 'prepared' for result in prepared.values()):
                    raise PlatformRefusal('INVALID_ADAPTER', 'A participant refused preparation; no addresses were applied')
                self._publish(network, 'applying')
                operation.phase = 'applying'
                self.store.update_operation(operation, operation.operation_revision)
                commitment = digest({node: result['prepared_snapshot_digest'] for node, result in prepared.items()})
                applied = True  # From this point a lost result can conceal an OS mutation.
                results = await self._phase(operation, changed, 'apply', {a.node_id: {'prepared_snapshot_digest': prepared[a.node_id]['prepared_snapshot_digest'], 'all_prepared_digest': commitment} for a in changed}, 20)
                if any(result['status'] != 'configured' for result in results.values()):
                    raise PlatformRefusal('CLEANUP_PENDING', 'Address application failed; immediate rollback is required')
                for assignment in changed:
                    result = results[assignment.node_id]
                    assignment.state = 'configured'
                    assignment.ownership = result['ownership']
                    self.store.put_assignment(assignment, expected_revision=assignment.assignment_revision)
                self._publish(network, 'verifying')
                operation.phase = 'verifying'
                self.store.update_operation(operation, operation.operation_revision)
                idle = await asyncio.wait_for(self._verify_configuration(network, operation, assignments, request), 20)
                operation.phase = 'complete'
                operation.terminal_result = {'state': network.observed_state, 'error_code': None if idle else 'RPC_BUSY'}
                self.store.update_operation(operation, operation.operation_revision)
            except (Exception, asyncio.CancelledError) as error:
                current = self.store.operation(operation.operation_id)
                if current:
                    operation = current
                code = error.code if isinstance(error, PlatformRefusal) else 'UNSUPPORTED_CAPABILITY'
                rollback = {}
                if applied or changed:
                    rollback = await self._phase(operation, changed, 'rollback', {a.node_id: {'desired_state': 'stopped'} for a in changed}, 60)
                pending = any(result['status'] != 'stopped' for result in rollback.values())
                self._publish(network, 'cleanup_pending' if pending else 'degraded' if operation.action == 'add' else 'failed')
                operation.phase = 'failed'
                operation.terminal_result = {'error_code': code, 'message': 'Setup failed; review participant prerequisites and cleanup state', 'rollback': rollback}
                self.store.update_operation(operation, operation.operation_revision)

    async def _verify_configuration(self, network: ManagedNetwork, operation: NetworkOperation, assignments: list[NodeAssignment], request: StartRequest) -> bool:
        # Start the master listener before workers attempt their reverse echo.
        master = next(a for a in assignments if a.node_id == self.identity.node_id)
        first = await self._request(operation, master, 'verify', {'health_only': False}, 5, self._sequence(operation.operation_id))
        if first['status'] == 'failed':
            raise PlatformRefusal(first['error_code'], 'Master dedicated diagnostics failed')
        workers = [a for a in assignments if a.node_id != self.identity.node_id]
        verified = await self._phase(operation, workers, 'verify', {a.node_id: {'health_only': False} for a in workers}, 5)
        if any(result['status'] == 'failed' for result in verified.values()):
            raise PlatformRefusal('TIMEOUT', 'Worker dedicated diagnostics or bridge verification failed')
        for assignment in workers:
            result = verified[assignment.node_id]
            assignment.rpc_child_identity = RPCChildIdentity.model_validate(result['child'])
            assignment.logical_rpc_endpoint = result['logical_endpoint']
            bridge = self.master_bridges.get((str(network.network_id), assignment.node_id))
            if bridge is None:
                bridge = await asyncio.to_thread(native.start_network_bridge, {'role': 'master', 'instance_id': str(uuid4()), 'network_id': str(network.network_id), 'assignment_revision': assignment.assignment_revision, 'bind_host': '127.0.0.1', 'bind_port': 0, 'target_host': assignment.address, 'target_port': request.bridge_port, 'source_host': master.address, 'allowed_peer': '127.0.0.1', 'child_pid': None, 'child_start_token': None})
            self.master_bridges[(str(network.network_id), assignment.node_id)] = bridge
            assignment.launch_rpc_endpoint = native.network_bridge_status(bridge)['listener']
            self.store.put_assignment(assignment, expected_revision=assignment.assignment_revision)
        self.freshness.publish(network.network_id, self.identity.node_id, {'assignment_revision': master.assignment_revision})
        idle = await self.verify_idle(network, operation, assignments)
        if idle:
            self._publish(network, 'ready')
            for assignment in assignments:
                assignment.state = 'ready'
                self.store.put_assignment(assignment, expected_revision=assignment.assignment_revision)
        return idle

    async def verify_idle(self, network: ManagedNetwork, operation: NetworkOperation, assignments: list[NodeAssignment]) -> bool:
        workers = [a for a in assignments if a.node_id != self.identity.node_id]
        leases: dict[str, AdmissionLease] = {}
        try:
            for assignment in workers:
                key = (str(network.network_id), assignment.node_id)
                worker_result = self.verified_results[key]
                worker_bridge_id = worker_result['bridge']['instance_id']
                master_bridge = self.master_bridges[key]
                master_status = native.network_bridge_status(master_bridge)
                if assignment.rpc_child_identity is None:
                    raise PlatformRefusal('RPC_BUSY', 'Worker child identity is unavailable')
                lease = AdmissionLease(expected_rpc_child_identity=assignment.rpc_child_identity, lease_id=uuid4(), network_id=network.network_id, node_id=assignment.node_id, generation=master_status['generation'] + 1, kind='probe', assignment_revision=assignment.assignment_revision, authority=self._authority(), relay_instance_ids=[UUID(worker_bridge_id), UUID(master_status['instance_id'])], state='requested')
                self.store.lease(lease)
                leases[assignment.node_id] = lease
                self.probe_leases[key] = lease
            granted = await self._phase(operation, workers, 'admit_probe', {node: lease.model_dump(mode='json') for node, lease in leases.items()}, 2)
            if any(result['status'] == 'failed' for result in granted.values()):
                return False
            async def probe(assignment: NodeAssignment) -> None:
                key = (str(network.network_id), assignment.node_id)
                lease = leases[assignment.node_id]
                bridge = self.master_bridges[key]
                bridge.grant(lease.generation, 15000, False)
                diagnostic = self.executor.diagnostics[str(network.network_id)]
                proof = await diagnostic.challenge(assignment.node_id, assignment.assignment_revision, self.executor.preparation[str(network.network_id)].diagnostic_port)
                if assignment.launch_rpc_endpoint is None:
                    raise PlatformRefusal('STALE_REVISION', 'Master bridge endpoint disappeared')
                result = await asyncio.to_thread(native.probe_network_rpc, assignment.launch_rpc_endpoint, '127.0.0.1', 'v0.5.0', 2000)
                self.freshness.publish(network.network_id, assignment.node_id, {'idle_probe_at': time.monotonic(), 'idle_observed_at': utc_now().isoformat(), 'rpc_released': False, 'rpc_result': result, 'dedicated_proof': proof, 'worker_bridge_id': self.verified_results[key]['bridge']['instance_id'], 'assignment_revision': assignment.assignment_revision, 'child': assignment.rpc_child_identity.model_dump(mode='json') if assignment.rpc_child_identity else None})
            await asyncio.gather(*(probe(a) for a in workers))
        finally:
            if leases:
                released = await self._phase(operation, [a for a in workers if a.node_id in leases], 'release_admission', {node: lease.model_dump(mode='json') for node, lease in leases.items()}, 2)
                for assignment in workers:
                    released_lease = leases.get(assignment.node_id)
                    if released_lease is None:
                        continue
                    lease = released_lease
                    bridge = self.master_bridges[(str(network.network_id), assignment.node_id)]
                    local_drained = bool(bridge.release(lease.generation)) if native.network_bridge_status(bridge)['generation'] == lease.generation else True
                    result = released[assignment.node_id]
                    drained = local_drained and result.get('drained') is True
                    lease.state = 'released' if drained else 'uncertain'
                    lease.release_ack, lease.drain_ack = result.get('drained') is not None, drained
                    self.store.lease(lease)
                    evidence = self.freshness.live.get((str(network.network_id), assignment.node_id))
                    if evidence:
                        evidence['rpc_released'] = drained
                        if drained and assignment.rpc_child_identity is not None:
                            authority = self._authority()
                            verified = self.verified_results[(str(network.network_id), assignment.node_id)]
                            self.store.evidence(VerificationEvidence(network_id=network.network_id, node_id=assignment.node_id, assignment_revision=assignment.assignment_revision, master_term=authority.master_term, master_session_id=authority.master_session_id, adapter_fingerprint=assignment.identity_fingerprint, address=assignment.address, route_interface_source={'worker': verified.get('routes_verified'), 'master_source': evidence['dedicated_proof']['source']}, signed_bidirectional_proof={'forward': evidence['dedicated_proof'], 'reverse': verified.get('dedicated_proof')}, idle_rpc_probe_result={**evidence['rpc_result'], 'released': True}, runtime_identity=assignment.rpc_child_identity, relay_instance_ids=lease.relay_instance_ids))
                if any(lease.state != 'released' for lease in leases.values()):
                    self.freshness.invalidate(network.network_id, 'Admission release was not acknowledged as drained')
        for assignment in workers:
            self.freshness.require(network.network_id, assignment.node_id)
        return True

    async def begin_operation(self, network_id: UUID, request: OperationRequest) -> dict[str, Any]:
        async with self.lock:
            authority = self._authority()
            network = self.store.network(network_id)
            if network is None:
                raise KeyError('unknown network')
            body_digest = digest({'network_id': str(network_id), **request.model_dump(mode='json')})
            prior = self.store.operation_for_request(authority.cluster_binding_id, request.request_id)
            if prior:
                if prior.proposal_digest != body_digest:
                    raise StoreConflict('request ID reused with changed body')
                return self._accepted(prior, network)
            if network.revision != request.expected_revision:
                raise StoreConflict('network revision changed')
            if any(run.state != 'released' for run in self.store.runs(network_id)) or any(lease.state != 'released' for lease in self.store.leases(network_id)):
                raise PlatformRefusal('INFERENCE_ACTIVE', 'A reserved, active or uncertain run blocks network changes')
            if request.action in ('add', 'retry'):
                return await self._begin_reconfigure(network, request, authority, body_digest)
            assignments = self.store.assignments(network_id)
            previous = self.store.operation(network.current_operation_id) if network.current_operation_id else None
            participants = [a.node_id for a in assignments] or (previous.participants if previous else [])
            operation = NetworkOperation(operation_id=uuid4(), request_id=request.request_id, network_id=network_id, cluster_binding_id=authority.cluster_binding_id, action='stop', master_term=authority.master_term, master_session_id=authority.master_session_id, expected_network_revision=request.expected_revision, operation_revision=1, participants=participants, proposal_digest=body_digest, phase='cleaning', deadline=utc_now() + timedelta(seconds=60))
            network.desired_state = 'stopped'
            network.current_operation_id = operation.operation_id
            self.store.put_operation(operation)
            self._publish(network, 'stopping')
            self.freshness.invalidate(network_id, 'Stop requested')
            self.tasks[str(operation.operation_id)] = asyncio.create_task(self._stop_operation(network, operation, assignments))
            return self._accepted(operation, network)

    async def _begin_reconfigure(self, network: ManagedNetwork, request: OperationRequest, authority: MasterAuthority, body_digest: str) -> dict[str, Any]:
        from kllm.managed_network.allocation import ParticipantSelection
        existing = self.store.assignments(network.network_id)
        if not existing:
            raise PlatformRefusal('INVALID_ADAPTER', 'No assignments to retry; stop the failed setup and start again')
        if request.action == 'add' and network.observed_state != 'ready':
            raise PlatformRefusal('STALE_REVISION', 'Add requires a currently ready idle network')
        if request.action == 'retry' and network.observed_state not in ('failed', 'degraded', 'verifying'):
            raise PlatformRefusal('CLEANUP_PENDING', 'Resolve cleanup before retrying')
        inventories = await self.inventories()
        selections = {p.node_id: p for p in request.participants}
        for assignment in existing:
            inventory = inventories.get(assignment.node_id, {})
            adapter = next((a for a in inventory.get('adapters', []) if a['adapter_id'] == assignment.adapter_id), None)
            if adapter is None:
                raise PlatformRefusal('INVALID_ADAPTER', 'Refresh or reconnect every existing participant')
            selections.setdefault(assignment.node_id, ParticipantSelection(node_id=assignment.node_id, adapter_id=assignment.adapter_id, inventory_revision=adapter['inventory_revision']))
        if request.action == 'retry' and any(p.node_id not in {a.node_id for a in existing} for p in selections.values()):
            raise ValueError('use add for new participants')
        preparation = self.executor.preparation.get(str(network.network_id))
        configuration = StartRequest(request_id=request.request_id, subnet=network.subnet, participants=list(selections.values()), bridge_port=preparation.bridge_port if preparation else 50052, diagnostic_port=preparation.diagnostic_port if preparation else 18767)
        operation = NetworkOperation(operation_id=uuid4(), request_id=request.request_id, network_id=network.network_id, cluster_binding_id=authority.cluster_binding_id, action='add' if request.action == 'add' else 'retry', master_term=authority.master_term, master_session_id=authority.master_session_id, expected_network_revision=request.expected_revision, operation_revision=1, participants=list(selections), proposal_digest=body_digest, phase='preparing', deadline=utc_now() + timedelta(seconds=60))
        self.store.put_operation(operation)
        network.current_operation_id = operation.operation_id
        self.freshness.invalidate(network.network_id, 'Topology verification requested')
        self._publish(network, 'preparing' if network.observed_state == 'failed' else 'verifying')
        async def reconfigure():
            if request.action == 'retry':
                async with self.lock:
                    await self._phase(operation, existing, 'inspect', {a.node_id: {} for a in existing}, 5)
                    cleaned = await self._phase(operation, existing, 'rollback', {a.node_id: {'desired_state': 'stopped'} for a in existing}, 60)
                    if any(r['status'] != 'stopped' for r in cleaned.values()):
                        self._publish(network, 'cleanup_pending')
                        operation.phase = 'failed'
                        operation.terminal_result = {'error_code': 'CLEANUP_PENDING'}
                        self.store.update_operation(operation, operation.operation_revision)
                        return
                    for key, bridge in list(self.master_bridges.items()):
                        if key[0] == str(network.network_id):
                            status = native.network_bridge_status(bridge)
                            await asyncio.to_thread(native.stop_network_bridge, bridge, status['instance_id'])
                            self.master_bridges.pop(key)
            await self._start_operation(network, operation, configuration, existing)
        self.tasks[str(operation.operation_id)] = asyncio.create_task(reconfigure())
        return self._accepted(operation, network)

    async def _stop_operation(self, network: ManagedNetwork, operation: NetworkOperation, assignments: list[NodeAssignment]) -> None:
        async with self.lock:
            pending = False
            for key, bridge in list(self.master_bridges.items()):
                if key[0] != str(network.network_id):
                    continue
                status = native.network_bridge_status(bridge)
                drained = await asyncio.to_thread(native.stop_network_bridge, bridge, status['instance_id'])
                if not drained:
                    pending = True
                else:
                    self.master_bridges.pop(key)
            await self._phase(operation, assignments, 'inspect', {a.node_id: {} for a in assignments}, 5)
            results = await self._phase(operation, assignments, 'cleanup', {a.node_id: {'desired_state': 'stopped'} for a in assignments}, 60)
            pending = pending or any(result['status'] != 'stopped' for result in results.values())
            for assignment in assignments:
                assignment.state = 'cleanup_pending' if results[assignment.node_id]['status'] != 'stopped' else 'stopped'
                self.store.put_assignment(assignment, expected_revision=assignment.assignment_revision)
            self._publish(network, 'cleanup_pending' if pending else 'stopped')
            operation.phase = 'failed' if pending else 'complete'
            operation.terminal_result = {'state': network.observed_state, 'cleanup_pending': [node for node, result in results.items() if result['status'] != 'stopped']}
            self.store.update_operation(operation, operation.operation_revision)

    async def cancel(self, operation_id: UUID, request) -> dict[str, Any]:
        operation = await asyncio.to_thread(self.store.cancel_operation, operation_id, request.request_id, request.expected_operation_revision)
        network = self.store.network(operation.network_id)
        if network is None:
            raise KeyError('unknown network')
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
            raise KeyError('unknown operation')
        return {**operation.model_dump(mode='json'), 'results': [item.model_dump(mode='json') for item in self.store.results(operation_id)]}

    def network_view(self, network_id: UUID | str) -> dict[str, Any]:
        network = self.store.network(network_id)
        if network is None:
            raise KeyError('unknown network')
        participants = []
        for assignment in self.store.assignments(network_id):
            evidence = self.freshness.live.get((str(network_id), assignment.node_id))
            result = self.results.get((str(network_id), assignment.node_id), {})
            participants.append({**assignment.model_dump(mode='json', exclude={'launch_rpc_endpoint'}), 'last_verification_at': evidence.get('health_observed_at', evidence.get('idle_observed_at')) if evidence else None, 'cleanup_status': assignment.state if assignment.state in ('cleanup_pending', 'cleaning', 'stopped') else None, 'verification_freshness': 'fresh' if evidence and evidence['health_deadline'] > time.monotonic() else 'unverified', 'error': {'code': result.get('error_code'), 'message': result.get('message')} if result.get('error_code') else None})
        return {**network.model_dump(mode='json'), 'participants': participants, 'blocking_runs': [run.model_dump(mode='json', exclude={'launch_endpoints'}) for run in self.store.runs(network_id) if run.state != 'released']}

    def summaries(self) -> list[dict[str, Any]]:
        return [self.network_view(n.network_id) for n in self.store.networks()]

    async def _health(self, network: ManagedNetwork, operation: NetworkOperation, assignments: list[NodeAssignment]) -> None:
        if any(a.node_id not in self.state().members for a in assignments):
            raise PlatformRefusal('STALE_REVISION', 'A selected participant left or was evicted')
        results = await self._phase(operation, assignments, 'verify', {a.node_id: {'health_only': True} for a in assignments}, 2)
        diagnostic = self.executor.diagnostics.get(str(network.network_id))
        if diagnostic is None:
            raise PlatformRefusal('STALE_REVISION', 'Dedicated diagnostic listener disappeared')
        for assignment in assignments:
            key = (str(network.network_id), assignment.node_id)
            result = results[assignment.node_id]
            if result['status'] == 'failed':
                raise PlatformRefusal(result.get('error_code', 'TIMEOUT'), 'Dedicated health failed')
            previous = self.freshness.live.get(key, {})
            if assignment.node_id != self.identity.node_id:
                if result.get('child') != (assignment.rpc_child_identity.model_dump(mode='json') if assignment.rpc_child_identity else None):
                    raise PlatformRefusal('STALE_REVISION', 'Worker child identity changed')
                bridge = self.master_bridges.get(key)
                if bridge is None:
                    raise PlatformRefusal('STALE_REVISION', 'Master bridge disappeared')
                status = native.network_bridge_status(bridge)
                old_bridge = previous.get('worker_bridge_id')
                if status['closed'] or status['last_error'] or (old_bridge and result['bridge']['instance_id'] != old_bridge):
                    raise PlatformRefusal('STALE_REVISION', 'Relay identity changed')
                proof = await diagnostic.challenge(assignment.node_id, assignment.assignment_revision, result['diagnostic_port'])
                self.freshness.publish(network.network_id, assignment.node_id, {**previous, 'dedicated_proof': proof, 'worker_bridge_id': result['bridge']['instance_id']})
            else:
                self.freshness.publish(network.network_id, assignment.node_id, {'assignment_revision': assignment.assignment_revision})

    async def _monitor(self) -> None:
        while not self.closed:
            await asyncio.sleep(5)
            pending_cleanup = []
            async with self.lock:
                for network in self.store.networks():
                    if network.desired_state == 'stopped' and network.observed_state == 'cleanup_pending' and not any(run.state != 'released' for run in self.store.runs(network.network_id)):
                        pending_cleanup.append(network)
                    if network.observed_state not in ('ready', 'verifying', 'degraded') or self.state().role != 'master':
                        continue
                    operation = self.store.operation(network.current_operation_id) if network.current_operation_id else None
                    if operation is None:
                        continue
                    assignments = self.store.assignments(network.network_id)
                    try:
                        await asyncio.wait_for(self._health(network, operation, assignments), 2)
                        active = [run for run in self.store.runs(network.network_id) if run.state == 'active']
                        if not active and not any(run.state != 'released' for run in self.store.runs(network.network_id)) and not any(lease.state != 'released' for lease in self.store.leases(network.network_id)):
                            if network.observed_state == 'degraded':
                                self._publish(network, 'verifying')
                            idle = await self.verify_idle(network, operation, assignments)
                            if idle and network.observed_state == 'verifying':
                                self._publish(network, 'ready')
                            elif not idle and network.observed_state == 'ready':
                                self._publish(network, 'verifying')
                        for run in active:
                            if run.child_pid and (not native.process_alive(run.child_pid) or native._managed_native()._process_identity(run.child_pid)[0] != run.child_start_token):
                                await self.admission.release(run)
                                continue
                            for assignment in assignments:
                                if assignment.node_id == self.identity.node_id:
                                    continue
                                key = (str(network.network_id), assignment.node_id)
                                statuses = [native.network_bridge_status(self.master_bridges[key]), self.verified_results[key]['bridge']]
                                if any(status.get('admission_consumed') and not status['active_streams'] and not status['pending_connects'] for status in statuses):
                                    raise PlatformRefusal('STALE_REVISION', 'An admitted run stream ended')
                            leases = {lease.node_id: lease for lease in self.store.leases(network.network_id) if lease.run_id == run.run_id and lease.state != 'released'}
                            workers = [a for a in assignments if a.node_id in leases]
                            renewed = await self._phase(operation, workers, 'renew_admission', {node: lease.model_dump(mode='json') for node, lease in leases.items()}, 2)
                            for worker in workers:
                                lease = leases[worker.node_id]
                                if renewed[worker.node_id].get('grant_ack') is not True:
                                    raise PlatformRefusal('TIMEOUT', 'Run lease renewal was not acknowledged')
                                self.master_bridges[(str(network.network_id), worker.node_id)].grant(lease.generation, 15000, True)
                    except Exception:
                        self.freshness.invalidate(network.network_id, 'Dedicated route, identity or admission health failed')
                        if network.observed_state != 'degraded':
                            self._publish(network, 'degraded')

            for network in pending_cleanup:
                try:
                    accepted = await self.begin_operation(network.network_id, OperationRequest(request_id=uuid4(), expected_revision=network.revision, action='stop'))
                    await self.wait_operation(accepted['operation_id'])
                except (PlatformRefusal, StoreConflict):
                    pass  # Pending state survives until the missing node reconciles.

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.channels.close()
        await self.executor.close()
        for bridge in list(self.master_bridges.values()):
            status = native.network_bridge_status(bridge)
            await asyncio.to_thread(native.stop_network_bridge, bridge, status['instance_id'], terminate=True)
        self.master_bridges.clear()
        self.store.close()
