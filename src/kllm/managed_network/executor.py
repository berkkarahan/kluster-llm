"""Local signed-phase execution; intent precedes every address change."""
from __future__ import annotations

import asyncio
import ipaddress
import platform as host_platform
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psutil
from pydantic import Field

from kllm import native
from .identity import LocalIdentity, SignedCommand
from .models import AdmissionLease, Digest, LocalMutationJournal, ManagedNetwork, MasterAuthority, NetworkOperation, NodeAssignment, Port, Record, RPCChildIdentity, NetworkState
from .platform.base import PlatformAdapter, PlatformRefusal, Prepared
from .store import NetworkStore, StoreConflict, digest
from .transport import require_tcp_runtime
from .verification import DedicatedDiagnostics


class PrepareBody(Record):
    network: ManagedNetwork
    operation: NetworkOperation
    assignment: NodeAssignment
    inventory_revision: Digest
    management_hosts: list[str] = Field(max_length=256)
    participants: list[NodeAssignment] = Field(min_length=2, max_length=256)
    bridge_port: Port
    diagnostic_port: Port


class ApplyBody(Record):
    prepared_snapshot_digest: Digest
    all_prepared_digest: Digest


class VerifyBody(Record):
    health_only: bool = False
    runtime: bool = False


class CleanupBody(Record):
    desired_state: str = Field(pattern=r'^stopped$')


def platform_adapter() -> PlatformAdapter:
    system = host_platform.system()
    if system == 'Windows':
        from .platform.windows import WindowsAdapter
        return WindowsAdapter()
    if system == 'Darwin':
        from .platform.macos import MacOSAdapter
        return MacOSAdapter()
    if system == 'Linux':
        from .platform.linux import LinuxAdapter
        return LinuxAdapter()
    raise PlatformRefusal('UNSUPPORTED_CAPABILITY', 'Managed networking is unsupported on this OS')


class LocalNetworkExecutor:
    def __init__(self, store: NetworkStore, identity: LocalIdentity, state: Callable[[], Any], platform: PlatformAdapter, repository: Callable[[], Path | None]):
        self.store, self.identity, self.state, self.platform, self.repository = store, identity, state, platform, repository
        self.guided_status: Callable[[], dict[str, Any]] | None = None
        self.lock = asyncio.Lock()
        self.prepared: dict[str, Prepared] = {}
        self.preparation: dict[str, PrepareBody] = {}
        self.journals: dict[str, LocalMutationJournal] = {}
        self.bridges: dict[str, Any] = {}
        self.diagnostics: dict[str, DedicatedDiagnostics] = {}
        self.boot_id = str(uuid4())
        self.executing: set[asyncio.Task] = set()

    def local_revision(self, network_id: UUID | str) -> int:
        return int(self.store.metadata(f'local_revision:{network_id}') or '0')

    def _fence(self, command: SignedCommand, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise PlatformRefusal('TIMEOUT', 'Signed phase expired before mutation')
        state = self.state()
        if command.target_node_id != state.node_id:
            raise PlatformRefusal('UNPAIRED_IDENTITY', 'Command targets another node')
        authority = MasterAuthority(cluster_binding_id=command.cluster_binding_id, master_node_id=command.master_node_id, master_key_id=command.master_key_id, master_term=command.master_term, master_session_id=command.master_session_id, accepted_channel_id=command.channel_id)
        self.store.check_channel(command.sender_node_id, authority)
        if command.sender_node_id == state.node_id:
            if state.role != 'master':
                raise PlatformRefusal('NOT_MASTER', 'Local master role changed')
        else:
            peer = self.store.peer(command.sender_node_id)
            if state.role != 'worker' or peer is None or peer.expected_role != 'master' or peer.key_id != command.master_key_id or peer.cluster_binding_id != command.cluster_binding_id:
                raise PlatformRefusal('UNPAIRED_IDENTITY', 'Worker is no longer paired to this master')
        operation = self.store.operation(command.operation_id)
        if operation and command.phase in ('cleanup', 'rollback') and command.operation_revision == operation.operation_revision + 1 and operation.master_term == command.master_term and operation.master_session_id == command.master_session_id:
            previous = operation.operation_revision
            operation.operation_revision = command.operation_revision
            self.store.update_operation(operation, previous)
        if operation and (operation.operation_revision != command.operation_revision or operation.master_term != command.master_term or operation.master_session_id != command.master_session_id):
            raise StoreConflict('operation was cancelled or superseded')

    def inspect_view(self) -> dict[str, Any]:
        return {'node_id': self.identity.node_id, 'capability': 'managed-network-v1', 'adapters': [item.public() for item in self.platform.inventory()], 'selection': self.store.metadata('network_selection')}

    def _prepare(self, command: SignedCommand, deadline: float) -> dict[str, Any]:
        body = PrepareBody.model_validate(command.body)
        assignment = body.assignment
        network = body.network
        if (network.network_id, body.operation.operation_id, assignment.network_id, assignment.node_id, assignment.assignment_revision, network.cluster_binding_id, network.master_node_id, network.master_key_id) != (command.network_id, command.operation_id, command.network_id, self.identity.node_id, command.assignment_revision, command.cluster_binding_id, command.master_node_id, command.master_key_id):
            raise PlatformRefusal('UNPAIRED_IDENTITY', 'Prepared assignment/operation authority mismatch')
        if len({a.node_id for a in body.participants}) != len(body.participants) or len({a.address for a in body.participants}) != len(body.participants) or assignment not in body.participants or {a.node_id for a in body.participants} != set(body.operation.participants):
            raise PlatformRefusal('ADDRESS_CONFLICT', 'Participant assignments must be distinct and complete')
        subnet = ipaddress.IPv4Network(network.subnet)
        for participant in body.participants:
            if participant.network_id != network.network_id or participant.prefix_length != subnet.prefixlen or ipaddress.IPv4Address(participant.address) not in subnet or participant.address in (str(subnet.network_address), str(subnet.broadcast_address)):
                raise PlatformRefusal('SUBNET_CONFLICT', 'Participant address is outside the usable subnet')
        if self.state().role == 'worker':
            import json
            selection = json.loads(self.store.metadata('network_selection') or '{}')
            if selection.get('adapter_id') != assignment.adapter_id and not (self.guided_status and self.guided_status().get('administrator_enabled')):
                raise PlatformRefusal('INVALID_ADAPTER', 'Worker must select this adapter in its local app first')
        existing_network = self.store.network(command.network_id)
        if self.store.metadata(f'stopped_network:{command.network_id}') or (existing_network is not None and existing_network.observed_state == 'stopped'):
            raise PlatformRefusal('STALE_REVISION', 'Stopped network tombstone cannot be revived')
        self.platform.eligible(self.platform.inspect(assignment.adapter_id))
        if self.store.network(network.network_id) is None:
            self.store.put_network(network.model_copy(update={'revision': 1, 'observed_state': 'preparing'}))
        self.store.put_operation(body.operation)
        current = next((a for a in self.store.assignments(network.network_id) if a.node_id == assignment.node_id), None)
        self.store.put_assignment(assignment, expected_revision=current.assignment_revision if current else None)
        key = str(network.network_id)
        if key in self.prepared:
            if key in self.journals and self.journals[key].phase != 'removed_observed':
                raise PlatformRefusal('CLEANUP_PENDING', 'Settle prior local ownership before preparing again')
            self.platform.close_prepared(self.prepared.pop(key))
        self.platform.authorize(command)
        prepared = self.platform.prepare(assignment, body.inventory_revision, body.management_hosts)
        self.prepared[key], self.preparation[key] = prepared, body
        assignment.state = 'prepared'
        self.store.put_assignment(assignment, expected_revision=assignment.assignment_revision)
        return {'status': 'prepared', 'prepared_snapshot_digest': prepared.snapshot_digest, 'ownership': 'user_owned' if prepared.user_owned else 'uncertain', 'adapter_fingerprint': prepared.snapshot.fingerprint, 'observation_epoch': prepared.epoch}

    def _apply(self, command: SignedCommand, deadline: float) -> dict[str, Any]:
        body = ApplyBody.model_validate(command.body)
        key = str(command.network_id)
        prepared = self.prepared.get(key)
        if prepared is None or prepared.assignment.assignment_revision != command.assignment_revision or body.prepared_snapshot_digest != prepared.snapshot_digest:
            raise PlatformRefusal('STALE_REVISION', 'No matching current prepared assignment')
        self.platform.authorize(command)
        authority = MasterAuthority(cluster_binding_id=command.cluster_binding_id, master_node_id=command.master_node_id, master_key_id=command.master_key_id, master_term=command.master_term, master_session_id=command.master_session_id, accepted_channel_id=command.channel_id)
        journal = None
        if not prepared.user_owned:
            journal = LocalMutationJournal(journal_id=uuid4(), network_id=command.network_id, operation_id=command.operation_id, node_id=self.identity.node_id, adapter_fingerprint=prepared.snapshot.fingerprint, exact_address_prefix=f'{prepared.assignment.address}/{prepared.assignment.prefix_length}', boot_id=self.boot_id, observation_epoch=UUID(prepared.epoch), event_high_water=prepared.high_water, observer_health='continuous', pre_snapshot=prepared.snapshot.public(), intended_delta={'add': f'{prepared.assignment.address}/{prepared.assignment.prefix_length}'}, phase='intent_written', authority_fence=authority)
            self.store.journal(journal)
            self.journals[key] = journal
        try:
            snapshot = self.platform.apply(prepared, journal, lambda: self._fence(command, deadline))
        except BaseException:
            if journal:
                journal.phase = 'cleanup_pending'
                journal.retained_error = 'Address application did not finish with confirmed observation'
                self.store.journal(journal)
            raise
        if journal:
            journal.phase = 'applied_observed'
            journal.event_high_water = prepared.high_water
            journal.observed_snapshot_digest = digest(snapshot.public())
            self.store.journal(journal)
        assignment = prepared.assignment
        assignment.ownership = 'user_owned' if prepared.user_owned else 'app_owned'
        assignment.state = 'configured'
        self.store.put_assignment(assignment, expected_revision=assignment.assignment_revision)
        return {'status': 'configured', 'ownership': assignment.ownership, 'observed_snapshot_digest': digest(snapshot.public()), 'journal_id': str(journal.journal_id) if journal else None}

    def rpc_child(self) -> RPCChildIdentity:
        state = self.state()
        if state.role != 'worker' or state.process_role != 'worker' or not state.process_pid or not native.process_alive(state.process_pid):
            raise PlatformRefusal('RPC_BUSY', 'A current managed worker RPC process is required')
        backend = native._managed_native()
        token, _ = backend._process_identity(state.process_pid)
        bind = state.rpc_bind or '127.0.0.1'
        if bind == '0.0.0.0':
            raise PlatformRefusal('RPC_PROTOCOL_UNSUPPORTED', 'Existing RPC must bind one private address; wildcard listeners cannot share the dedicated bridge port')
        repo = self.repository()
        if repo is None:
            raise PlatformRefusal('RPC_PROTOCOL_UNSUPPORTED', 'Runtime installation is unavailable')
        llama, rpc = native.resolve_llama_server(str(repo)), native.resolve_rpc_server(str(repo))
        if not llama or not rpc:
            raise PlatformRefusal('RPC_PROTOCOL_UNSUPPORTED', 'Runtime binaries disappeared')
        evidence = require_tcp_runtime(repo, Path(llama), Path(rpc))
        try:
            process = psutil.Process(state.process_pid)
            if Path(process.exe()).resolve() != Path(rpc).resolve() or process.create_time() < evidence['installed_at']:
                raise ValueError('RPC child predates the proven installation')
        except (psutil.Error, OSError, ValueError):
            raise PlatformRefusal('RPC_PROTOCOL_UNSUPPORTED', 'Restart the worker after installing the proven TCP-only runtime') from None
        return RPCChildIdentity(pid=state.process_pid, start_token=token, bind=bind, port=state.rpc_port, llama_tag=evidence.get('runtime_id', 'v0.5.0'), tcp_only=True, compatibility_digest=digest(evidence))

    def require_rpc_idle(self, child: RPCChildIdentity) -> None:
        try:
            connections = psutil.Process(child.pid).net_connections(kind='tcp')
        except (psutil.Error, OSError):
            raise PlatformRefusal('RPC_BUSY', 'RPC ownership could not be established') from None
        if any(connection.status != psutil.CONN_LISTEN for connection in connections):
            raise PlatformRefusal('RPC_BUSY', 'RPC has an existing or draining connection')

    async def _verify(self, command: SignedCommand, deadline: float) -> dict[str, Any]:
        options = VerifyBody.model_validate(command.body)
        key = str(command.network_id)
        prepared, body = self.prepared.get(key), self.preparation.get(key)
        if prepared is None or body is None:
            raise PlatformRefusal('CLEANUP_PENDING', 'Restart requires local ownership reconciliation before verification')
        snapshot = await asyncio.to_thread(self.platform.observe, prepared)
        self.platform.eligible(snapshot)
        self.platform.check_conflict(prepared)
        if prepared.ownership_conflict:
            raise PlatformRefusal('OWNERSHIP_CONFLICT', 'Observed address identity changed')
        exact = ipaddress.IPv4Interface(f'{prepared.assignment.address}/{prepared.assignment.prefix_length}')
        if exact not in snapshot.state.addresses:
            raise PlatformRefusal('ADDRESS_CONFLICT', 'Dedicated address is absent')
        authority = MasterAuthority(cluster_binding_id=command.cluster_binding_id, master_node_id=command.master_node_id, master_key_id=command.master_key_id, master_term=command.master_term, master_session_id=command.master_session_id)
        peers = {a.node_id: a.address for a in body.participants if a.node_id != self.identity.node_id}
        for peer in peers.values():
            await asyncio.to_thread(self.platform.check_source_route, snapshot, prepared.assignment.address, peer)
        self._fence(command, deadline)
        if key not in self.diagnostics:
            diagnostic = DedicatedDiagnostics(self.identity, self.store, command.network_id, command.assignment_revision, authority, prepared.assignment.address, body.diagnostic_port, peers)
            await diagnostic.start()
            self.diagnostics[key] = diagnostic
        result: dict[str, Any] = {'status': 'verification_pending', 'adapter_fingerprint': snapshot.fingerprint, 'address': prepared.assignment.address, 'routes_verified': True, 'diagnostic_port': body.diagnostic_port}
        if self.state().role == 'worker':
            master = next(a for a in body.participants if a.node_id == command.master_node_id)
            proof = await self.diagnostics[key].challenge(master.node_id, master.assignment_revision, body.diagnostic_port)
            result['dedicated_proof'] = proof
            if not options.runtime:
                return result
            child = self.rpc_child()
            if key not in self.bridges:
                if options.health_only:
                    raise PlatformRefusal('STALE_REVISION', 'Worker bridge identity disappeared')
                bridge = await asyncio.to_thread(native.start_network_bridge, {'role': 'worker', 'instance_id': str(uuid4()), 'network_id': key, 'assignment_revision': command.assignment_revision, 'bind_host': prepared.assignment.address, 'bind_port': body.bridge_port, 'target_host': child.bind, 'target_port': child.port, 'source_host': child.bind, 'allowed_peer': master.address, 'child_pid': child.pid, 'child_start_token': child.start_token})
                self.bridges[key] = bridge
            status = native.network_bridge_status(self.bridges[key])
            if status['closed'] or status['last_error']:
                raise PlatformRefusal('STALE_REVISION', 'Worker bridge failed')
            result.update({'child': child.model_dump(mode='json'), 'bridge': status, 'dedicated_proof': proof, 'logical_endpoint': f'{prepared.assignment.address}:{body.bridge_port}'})
        return result

    def _admission(self, command: SignedCommand, deadline: float) -> dict[str, Any]:
        lease = AdmissionLease.model_validate(command.body)
        authority = lease.authority
        same_session = (authority.master_term, authority.master_session_id) == (command.master_term, command.master_session_id)
        release_from_newer_master = command.phase == 'release_admission' and command.master_term > authority.master_term
        if lease.network_id != command.network_id or lease.node_id != self.identity.node_id or lease.assignment_revision != command.assignment_revision or (authority.cluster_binding_id, authority.master_key_id, authority.master_node_id) != (command.cluster_binding_id, command.master_key_id, command.master_node_id) or not (same_session or release_from_newer_master):
            raise PlatformRefusal('STALE_REVISION', 'Admission context mismatch')
        bridge = self.bridges.get(str(command.network_id))
        if bridge is None:
            if command.phase == 'release_admission':
                existing = next((item for item in self.store.leases(command.network_id) if item.lease_id == lease.lease_id), None)
                if existing is None:
                    raise PlatformRefusal('CLEANUP_PENDING', 'Unknown admission requires reconciliation')
                # Restart destroys the in-process relay. Prove its serial child
                # has no surviving owner before acknowledging the old lease.
                if native.process_alive(lease.expected_rpc_child_identity.pid):
                    token, _ = native._managed_native()._process_identity(lease.expected_rpc_child_identity.pid)
                    if token == lease.expected_rpc_child_identity.start_token:
                        self.require_rpc_idle(lease.expected_rpc_child_identity)
                lease.state = 'released'
                lease.release_ack = lease.drain_ack = True
                self.store.lease(lease)
                return {'status': 'configured', 'drained': True, 'lease_id': str(lease.lease_id), 'generation': lease.generation}
            raise PlatformRefusal('RPC_BUSY', 'No current worker bridge is available')
        status = native.network_bridge_status(bridge)
        if status['instance_id'] not in [str(i) for i in lease.relay_instance_ids]:
            raise PlatformRefusal('STALE_REVISION', 'Admission targets a stale bridge')
        if command.phase == 'release_admission':
            existing = next((item for item in self.store.leases(command.network_id) if item.lease_id == lease.lease_id), None)
            if existing is None and status['generation'] < lease.generation and not status['active_streams'] and not status['pending_connects']:
                lease.state = 'released'
                lease.release_ack = lease.drain_ack = True
                self.store.lease(lease)
                return {'status': 'configured', 'drained': True, 'lease_id': str(lease.lease_id), 'generation': lease.generation}
            if existing is not None and existing.state == 'released' and existing.generation == lease.generation:
                return {'status': 'configured', 'drained': True, 'lease_id': str(lease.lease_id), 'generation': lease.generation}
            lease.state = 'draining'
            self.store.lease(lease)
            drained = bool(bridge.release(lease.generation))
            until = min(deadline, time.monotonic() + 2)
            while not drained and time.monotonic() < until:
                time.sleep(0.01)
                drained = bool(bridge.release(lease.generation))
            if drained:
                # Native relay drain precedes upstream CLOSE_WAIT teardown. An
                # ACK proves the serial RPC server can accept the next owner.
                until = min(deadline, time.monotonic() + 2)
                while True:
                    try:
                        child = lease.expected_rpc_child_identity
                        if native.process_alive(child.pid) and native._managed_native()._process_identity(child.pid)[0] == child.start_token:
                            self.require_rpc_idle(child)
                        break
                    except PlatformRefusal:
                        if time.monotonic() >= until:
                            drained = False
                            break
                        time.sleep(0.01)
            lease.release_ack, lease.drain_ack = True, drained
            lease.state = 'released' if drained else 'draining'
            self.store.lease(lease)
            return {'status': 'configured', 'drained': drained, 'lease_id': str(lease.lease_id), 'generation': lease.generation}
        if self.rpc_child() != lease.expected_rpc_child_identity:
            raise PlatformRefusal('RPC_BUSY', 'Admission RPC child differs from verified identity')
        renewal = command.phase == 'renew_admission'
        if not renewal:
            self.require_rpc_idle(self.rpc_child())
            self.platform.check_conflict(self.prepared[str(command.network_id)])
        if command.phase == 'admit_probe' and lease.kind != 'probe' or command.phase == 'admit_run' and lease.kind != 'run':
            raise PlatformRefusal('RPC_BUSY', 'Admission kind differs from its phase')
        self._fence(command, deadline)
        lease.monotonic_deadline = 0.0
        lease.state = 'requested'
        self.store.lease(lease)  # Commit before opening native admission.
        bridge.grant(lease.generation, 15000, renewal)
        lease.state = 'granted'
        lease.grant_ack = True
        lease.renew_ack = renewal
        self.store.lease(lease)
        return {'status': 'configured', 'lease_id': str(lease.lease_id), 'generation': lease.generation, 'bridge_instance_id': status['instance_id'], 'grant_ack': True}

    async def _cleanup(self, command: SignedCommand, deadline: float) -> dict[str, Any]:
        CleanupBody.model_validate(command.body)
        key = str(command.network_id)
        if any(run.state != 'released' for run in self.store.runs(command.network_id)) or any(lease.state != 'released' for lease in self.store.leases(command.network_id)):
            raise PlatformRefusal('INFERENCE_ACTIVE', 'Dependent run/admission must be stopped and drained before cleanup')
        if key in self.bridges:
            bridge = self.bridges[key]
            status = native.network_bridge_status(bridge)
            drained = await asyncio.to_thread(native.stop_network_bridge, bridge, status['instance_id'])
            if not drained:
                raise PlatformRefusal('INFERENCE_ACTIVE', 'Admitted bridge stream has not drained')
            self.bridges.pop(key)
        if key in self.diagnostics:
            await self.diagnostics.pop(key).close()
        journals = [j for j in self.store.journals() if j.network_id == command.network_id and j.node_id == self.identity.node_id and j.phase != 'removed_observed']
        prepared = self.prepared.get(key)
        if prepared or journals:
            await asyncio.to_thread(self.platform.authorize, command)
        for journal in journals:
            if prepared is None:
                assignment = next((a for a in self.store.assignments(command.network_id) if a.node_id == self.identity.node_id), None)
                if assignment is None:
                    raise PlatformRefusal('OWNERSHIP_CONFLICT', 'Journal has no current assignment; local review required')
                snapshot = await asyncio.to_thread(self.platform.inspect, assignment.adapter_id)
                exact = ipaddress.IPv4Interface(journal.exact_address_prefix)
                if exact in snapshot.state.addresses:
                    raise PlatformRefusal('OWNERSHIP_CONFLICT', 'Restart lost continuous ownership; local review required')
                journal.phase = 'removed_observed'
                self.store.journal(journal)
            else:
                journal.phase = 'removal_intent'
                self.store.journal(journal)
                def remove() -> None:
                    with self.store.execution():
                        self._fence(command, deadline)
                        self.platform.remove_owned(prepared, journal, lambda: self._fence(command, deadline))
                        journal.phase = 'removed_observed'
                        journal.event_high_water = prepared.high_water
                        self.store.journal(journal)
                await asyncio.to_thread(remove)
        if prepared or journals:
            await asyncio.to_thread(self.platform.restore_configuration, key)
        if prepared:
            await asyncio.to_thread(self.platform.close_prepared, prepared)
            self.prepared.pop(key, None)
        if self.state().role == 'worker':
            replica = self.store.network(command.network_id)
            if replica is not None and replica.observed_state != 'stopped':
                if replica.observed_state == 'preparing':
                    revision = replica.revision
                    replica.observed_state = 'failed'
                    replica.revision += 1
                    self.store.put_network(replica, expected_revision=revision)
                for state in (('failed',) if command.phase == 'rollback' else ('stopping', 'stopped')):
                    revision = replica.revision
                    replica.desired_state = 'active' if command.phase == 'rollback' else 'stopped'
                    replica.observed_state = cast(NetworkState, state)
                    replica.revision += 1
                    self.store.put_network(replica, expected_revision=revision)
        if command.phase == 'cleanup':
            self.store.set_metadata(f'stopped_network:{command.network_id}', str(command.operation_id))
        return {'status': 'stopped', 'cleanup_pending': False}

    async def _reconcile(self, command: SignedCommand, deadline: float) -> dict[str, Any]:
        self._fence(command, deadline)
        if command.body:
            proposal = PrepareBody.model_validate(command.body)
            current = self.prepared.get(str(command.network_id))
            if current is None or any(getattr(current.assignment, field) != getattr(proposal.assignment, field) for field in ('address', 'prefix_length', 'adapter_id', 'identity_fingerprint', 'assignment_revision')):
                raise PlatformRefusal('CLEANUP_PENDING', 'Reconcile requires an unchanged continuously observed assignment')
            self.store.put_operation(proposal.operation)
            self.preparation[str(command.network_id)] = proposal
            diagnostic = self.diagnostics.get(str(command.network_id))
            if diagnostic:
                diagnostic.peers = {a.node_id: a.address for a in proposal.participants if a.node_id != self.identity.node_id}
        return {'status': 'configured', 'journals': [j.model_dump(mode='json') for j in self.store.journals() if j.network_id == command.network_id], 'local_revision': self.local_revision(command.network_id)}

    async def execute(self, command: SignedCommand, deadline: float) -> dict[str, Any]:
        async with self.lock:
            self._fence(command, deadline)
            if command.phase == 'inspect':
                if command.body:
                    raise PlatformRefusal('STALE_REVISION', 'Inspect does not accept command arguments')
                inventory = await asyncio.to_thread(self.inspect_view)
                setup = getattr(self, 'guided_status', None)
                if setup:
                    inventory['setup'] = await asyncio.to_thread(setup)
                return {'status': 'prepared', 'local_revision': self.local_revision(command.network_id), **inventory}
            key = '/'.join((str(command.cluster_binding_id), command.master_key_id, str(command.network_id), str(command.operation_id), str(command.operation_revision), self.identity.node_id, str(command.phase_sequence)))
            body_digest = digest({'phase': command.phase, 'body': command.body, 'assignment_revision': command.assignment_revision, 'expected_local_revision': command.expected_local_revision})
            fresh, saved = self.store.begin_command(key, body_digest)
            if not fresh:
                if not isinstance(saved, dict):
                    raise StoreConflict('stored command result is corrupt')
                return saved
            result: dict[str, Any]
            try:
                if self.local_revision(command.network_id) != command.expected_local_revision:
                    raise StoreConflict('local revision changed')
                if command.phase in ('prepare', 'apply'):
                    def mutate() -> dict[str, Any]:
                        with self.store.execution():
                            self._fence(command, deadline)
                            return self._prepare(command, deadline) if command.phase == 'prepare' else self._apply(command, deadline)
                    # Shield the OS operation so disconnect/cancellation cannot discard
                    # the observed outcome of a syscall that already started.
                    task = asyncio.create_task(asyncio.to_thread(mutate))
                    self.executing.add(task)
                    try:
                        result = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        result = await task
                    finally:
                        self.executing.discard(task)
                elif command.phase == 'reconcile':
                    result = await self._reconcile(command, deadline)
                elif command.phase == 'verify':
                    result = await self._verify(command, deadline)
                elif command.phase in ('admit_probe', 'admit_run', 'renew_admission', 'release_admission'):
                    result = await asyncio.to_thread(self._admission, command, deadline)
                elif command.phase == 'release_runtime':
                    if command.body or any(lease.state != 'released' for lease in self.store.leases(command.network_id)):
                        raise PlatformRefusal('RPC_BUSY', 'Drain runtime admission before closing its relay')
                    bridge = self.bridges.get(str(command.network_id))
                    if bridge:
                        status = native.network_bridge_status(bridge)
                        if not await asyncio.to_thread(native.stop_network_bridge, bridge, status['instance_id']):
                            raise PlatformRefusal('CLEANUP_PENDING', 'Worker relay has not drained')
                        self.bridges.pop(str(command.network_id), None)
                    result = {'status': 'configured', 'drained': True}
                elif command.phase in ('cleanup', 'rollback'):
                    result = await self._cleanup(command, deadline)
                else:
                    raise PlatformRefusal('UNSUPPORTED_CAPABILITY', 'This phase requires an explicit reconciliation operation')
            except (PlatformRefusal, StoreConflict, ValueError, OSError, RuntimeError) as error:
                code = error.code if isinstance(error, PlatformRefusal) else 'STALE_REVISION' if isinstance(error, StoreConflict) else 'UNSUPPORTED_CAPABILITY'
                result = {'status': 'cleanup_pending' if command.phase in ('cleanup', 'rollback') else 'failed', 'error_code': code, 'message': str(error)[:512] if isinstance(error, (PlatformRefusal, StoreConflict)) else 'Local network phase failed; inspect adapter and runtime prerequisites'}
            revision = self.local_revision(command.network_id) + 1
            self.store.set_metadata(f'local_revision:{command.network_id}', str(revision))
            result['local_revision'] = revision
            self.store.finish_command(key, result)
            return result

    async def close(self) -> None:
        await asyncio.gather(*self.executing, return_exceptions=True)
        for diagnostic in list(self.diagnostics.values()):
            await diagnostic.close()
        self.diagnostics.clear()
        for bridge in list(self.bridges.values()):
            status = native.network_bridge_status(bridge)
            await asyncio.to_thread(native.stop_network_bridge, bridge, status['instance_id'], terminate=True)
        self.bridges.clear()
        for prepared in list(self.prepared.values()):
            await asyncio.to_thread(self.platform.close_prepared, prepared)
        self.prepared.clear()
