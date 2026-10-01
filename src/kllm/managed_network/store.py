"""Independent SQLite journal. Transactions serialize writers across processes."""
from __future__ import annotations

import hashlib
import json
import os
import time
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, uuid4

from .models import (
    AdmissionLease, LocalMutationJournal, ManagedNetwork, MasterAuthority,
    NetworkOperation, NodeAssignment, ParticipantResult, PeerIdentity, RunNetworkReservation,
    VerificationEvidence, MAX_COUNTER, utc_now, validate_transition,
)


class StoreConflict(ValueError):
    """A revision, authority, ownership or idempotency precondition changed."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class NetworkStore:
    def __init__(self, home: Path):
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / 'managed-network.sqlite'
        self.lock = threading.RLock()
        self.execution_mutex = threading.RLock()
        self._execution_depth = threading.local()
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        if os.name != 'nt':
            os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, timeout=5, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS peers (node_id TEXT PRIMARY KEY, role TEXT NOT NULL, body TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_master ON peers(role) WHERE role='master';
        CREATE TABLE IF NOT EXISTS authority (binding TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS channels (peer TEXT PRIMARY KEY, binding TEXT NOT NULL, term INTEGER NOT NULL, session TEXT NOT NULL, channel TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS networks (id TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_live_network ON networks(binding) WHERE state!='stopped';
        CREATE TABLE IF NOT EXISTS assignments (network TEXT REFERENCES networks(id), node TEXT, address TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(network,node), UNIQUE(network,address));
        CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), binding TEXT NOT NULL, request TEXT NOT NULL, digest TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(binding,request));
        CREATE TABLE IF NOT EXISTS results (operation TEXT REFERENCES operations(id), node TEXT, phase_sequence INTEGER, body TEXT NOT NULL, PRIMARY KEY(operation,node,phase_sequence));
        CREATE TABLE IF NOT EXISTS journals (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), operation TEXT REFERENCES operations(id), node TEXT NOT NULL, phase TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS commands (key TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, result TEXT);
        CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), state TEXT NOT NULL, body TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_live_run ON reservations(network) WHERE state!='released';
        CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), node TEXT NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_live_lease ON leases(network,node) WHERE state!='released';
        CREATE TABLE IF NOT EXISTS evidence (network TEXT REFERENCES networks(id), node TEXT, body TEXT NOT NULL, PRIMARY KEY(network,node));
        ''')

    @contextmanager
    def execution(self) -> Iterator[None]:
        """Hold across intent, OS mutation and observation; fence changes use it too."""
        with self.execution_mutex:
            depth = getattr(self._execution_depth, 'value', 0)
            if depth:
                self._execution_depth.value = depth + 1
                try:
                    yield
                finally:
                    self._execution_depth.value = depth
                return
            fd = os.open(self.path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            deadline = time.monotonic() + 5
            locked = False
            try:
                while not locked:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            os.lseek(fd, 0, 0)
                            getattr(msvcrt, 'locking')(fd, getattr(msvcrt, 'LK_NBLCK'), 1)
                        else:
                            import fcntl
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise StoreConflict('another agent owns the local mutation boundary') from None
                        time.sleep(0.01)
                self._execution_depth.value = 1
                yield
            finally:
                self._execution_depth.value = 0
                if locked:
                    if os.name == 'nt':
                        import msvcrt
                        os.lseek(fd, 0, 0)
                        getattr(msvcrt, 'locking')(fd, getattr(msvcrt, 'LK_UNLCK'), 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def metadata(self, key: str) -> str | None:
        with self.lock:
            row = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            return row[0] if row else None

    def pin_identity(self, node_id: str, key_id: str) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='local_identity'").fetchone()
            value = f'{node_id}:{key_id}'
            if row and row[0] != value:
                raise StoreConflict('local identity changed; explicit recovery required')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('local_identity',?)", (value,))

    def cluster_binding(self) -> UUID:
        with self.transaction() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='cluster_binding'").fetchone()
            if row:
                return UUID(row[0])
            binding = uuid4()
            db.execute("INSERT INTO metadata VALUES ('cluster_binding',?)", (str(binding),))
            return binding

    def pair(self, peer: PeerIdentity, *, replace: bool = False) -> None:
        from .identity import validate_peer
        validate_peer(peer)
        with self.execution(), self.transaction() as db:
            row = db.execute('SELECT body FROM peers WHERE node_id=?', (peer.node_id,)).fetchone()
            if row and row[0] != peer.model_dump_json():
                if not replace:
                    raise StoreConflict('explicit peer replacement required')
                if db.execute("SELECT 1 FROM networks WHERE state!='stopped'").fetchone() or db.execute("SELECT 1 FROM journals WHERE phase!='removed_observed'").fetchone():
                    raise StoreConflict('peer replacement requires settled local networking')
                db.execute('DELETE FROM authority')
                db.execute('DELETE FROM channels')
            db.execute('INSERT INTO peers VALUES (?,?,?) ON CONFLICT(node_id) DO UPDATE SET role=excluded.role,body=excluded.body', (peer.node_id, peer.expected_role, peer.model_dump_json()))

    def peer(self, node_id: str) -> PeerIdentity | None:
        with self.lock:
            row = self.db.execute('SELECT body FROM peers WHERE node_id=?', (node_id,)).fetchone()
            return PeerIdentity.model_validate_json(row[0]) if row else None

    def peers(self) -> list[PeerIdentity]:
        with self.lock:
            return [PeerIdentity.model_validate_json(row[0]) for row in self.db.execute('SELECT body FROM peers')]

    def startup_authority(self, node_id: str, key_id: str) -> MasterAuthority:
        binding = self.cluster_binding()
        with self.execution(), self.transaction() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='master_term'").fetchone()
            term = int(row[0]) + 1 if row else 1
            if not 1 <= term <= MAX_COUNTER:
                raise StoreConflict('invalid authority high-water mark')
            authority = MasterAuthority(cluster_binding_id=binding, master_node_id=node_id, master_key_id=key_id, master_term=term, master_session_id=uuid4())
            db.execute("INSERT INTO metadata VALUES ('master_term',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(term),))
            self._accept_authority(db, authority)
            return authority

    @staticmethod
    def _accept_authority(db: sqlite3.Connection, authority: MasterAuthority) -> None:
        row = db.execute('SELECT body FROM authority WHERE binding=?', (str(authority.cluster_binding_id),)).fetchone()
        if row:
            old = MasterAuthority.model_validate_json(row[0])
            if (old.master_node_id, old.master_key_id) != (authority.master_node_id, authority.master_key_id) or authority.master_term < old.master_term or (authority.master_term == old.master_term and authority.master_session_id != old.master_session_id):
                raise StoreConflict('authority regression or unpaired master')
        db.execute('DELETE FROM channels WHERE binding=? AND (term!=? OR session!=?)', (str(authority.cluster_binding_id), authority.master_term, str(authority.master_session_id)))
        core = authority.model_copy(update={'accepted_channel_id': None})
        db.execute('INSERT INTO authority VALUES (?,?) ON CONFLICT(binding) DO UPDATE SET body=excluded.body', (str(authority.cluster_binding_id), core.model_dump_json()))

    def accept_authority(self, authority: MasterAuthority, peer_node_id: str | None = None) -> None:
        with self.execution(), self.transaction() as db:
            self._accept_authority(db, authority)
            if authority.accepted_channel_id is not None:
                db.execute('INSERT INTO channels VALUES (?,?,?,?,?) ON CONFLICT(peer) DO UPDATE SET binding=excluded.binding,term=excluded.term,session=excluded.session,channel=excluded.channel', (peer_node_id or authority.master_node_id, str(authority.cluster_binding_id), authority.master_term, str(authority.master_session_id), str(authority.accepted_channel_id)))

    def check_channel(self, peer_node_id: str, authority: MasterAuthority) -> None:
        self.check_authority(authority)
        with self.lock:
            row = self.db.execute('SELECT binding,term,session,channel FROM channels WHERE peer=?', (peer_node_id,)).fetchone()
            expected = (str(authority.cluster_binding_id), authority.master_term, str(authority.master_session_id), str(authority.accepted_channel_id))
            if not row or tuple(row) != expected:
                raise StoreConflict('superseded peer channel')

    def check_authority(self, authority: MasterAuthority) -> None:
        with self.lock:
            row = self.db.execute('SELECT body FROM authority WHERE binding=?', (str(authority.cluster_binding_id),)).fetchone()
            if not row or MasterAuthority.model_validate_json(row[0]) != authority.model_copy(update={'accepted_channel_id': None}):
                raise StoreConflict('superseded authority/channel')

    def put_network(self, network: ManagedNetwork, *, expected_revision: int | None = None) -> None:
        with self.transaction() as db:
            row = db.execute('SELECT body FROM networks WHERE id=?', (str(network.network_id),)).fetchone()
            if row:
                old = ManagedNetwork.model_validate_json(row[0])
                if expected_revision != old.revision or network.revision != old.revision + 1:
                    raise StoreConflict('network revision changed')
                if (old.cluster_binding_id, old.master_node_id, old.master_key_id, old.subnet) != (network.cluster_binding_id, network.master_node_id, network.master_key_id, network.subnet):
                    raise StoreConflict('network authority/subnet is immutable')
                validate_transition(old.observed_state, network.observed_state)
            elif expected_revision is not None or network.revision != 1:
                raise StoreConflict('new network must begin at revision one')
            db.execute('INSERT INTO networks VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,revision=excluded.revision,body=excluded.body', (str(network.network_id), str(network.cluster_binding_id), network.observed_state, network.revision, network.model_dump_json()))

    def network(self, network_id: UUID | str) -> ManagedNetwork | None:
        with self.lock:
            row = self.db.execute('SELECT body FROM networks WHERE id=?', (str(network_id),)).fetchone()
            return ManagedNetwork.model_validate_json(row[0]) if row else None

    def networks(self) -> list[ManagedNetwork]:
        with self.lock:
            return [ManagedNetwork.model_validate_json(row[0]) for row in self.db.execute('SELECT body FROM networks')]

    def put_assignment(self, assignment: NodeAssignment, *, expected_revision: int | None = None) -> None:
        import ipaddress
        with self.transaction() as db:
            row = db.execute('SELECT body FROM networks WHERE id=?', (str(assignment.network_id),)).fetchone()
            if not row:
                raise StoreConflict('unknown network')
            subnet = ipaddress.IPv4Network(ManagedNetwork.model_validate_json(row[0]).subnet)
            address = ipaddress.IPv4Address(assignment.address)
            if address not in subnet or address in (subnet.network_address, subnet.broadcast_address) or assignment.prefix_length != subnet.prefixlen:
                raise StoreConflict('assignment outside usable subnet')
            current = db.execute('SELECT revision,body FROM assignments WHERE network=? AND node=?', (str(assignment.network_id), assignment.node_id)).fetchone()
            if current and expected_revision != current[0]:
                raise StoreConflict('assignment revision changed')
            if current:
                old = NodeAssignment.model_validate_json(current[1])
                structural = ('assignment_revision', 'adapter_id', 'os_interface', 'identity_fingerprint', 'address', 'prefix_length', 'logical_rpc_endpoint', 'launch_rpc_endpoint', 'rpc_child_identity')
                if any(getattr(old, key) != getattr(assignment, key) for key in structural) and db.execute("SELECT 1 FROM reservations WHERE network=? AND state!='released'", (str(assignment.network_id),)).fetchone():
                    raise StoreConflict('INFERENCE_ACTIVE')
                if assignment.assignment_revision not in (current[0], current[0] + 1):
                    raise StoreConflict('assignment revision regression')
            elif assignment.assignment_revision != 1:
                raise StoreConflict('new assignment must begin at revision one')
            db.execute('INSERT INTO assignments VALUES (?,?,?,?,?) ON CONFLICT(network,node) DO UPDATE SET address=excluded.address,revision=excluded.revision,body=excluded.body', (str(assignment.network_id), assignment.node_id, assignment.address, assignment.assignment_revision, assignment.model_dump_json()))

    def assignments(self, network_id: UUID | str) -> list[NodeAssignment]:
        with self.lock:
            return [NodeAssignment.model_validate_json(row[0]) for row in self.db.execute('SELECT body FROM assignments WHERE network=? ORDER BY node', (str(network_id),))]

    def put_operation(self, operation: NetworkOperation) -> NetworkOperation:
        with self.transaction() as db:
            row = db.execute('SELECT digest,body FROM operations WHERE binding=? AND request=?', (str(operation.cluster_binding_id), str(operation.request_id))).fetchone()
            if row:
                if row[0] != operation.proposal_digest:
                    raise StoreConflict('request ID reused with changed body')
                return NetworkOperation.model_validate_json(row[1])
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)', (str(operation.operation_id), str(operation.network_id), str(operation.cluster_binding_id), str(operation.request_id), operation.proposal_digest, operation.operation_revision, operation.model_dump_json()))
            return operation

    def operation(self, operation_id: UUID | str) -> NetworkOperation | None:
        with self.lock:
            row = self.db.execute('SELECT body FROM operations WHERE id=?', (str(operation_id),)).fetchone()
            return NetworkOperation.model_validate_json(row[0]) if row else None

    def update_operation(self, operation: NetworkOperation, expected_revision: int) -> None:
        with self.execution(), self.transaction() as db:
            row = db.execute('SELECT body FROM operations WHERE id=?', (str(operation.operation_id),)).fetchone()
            if not row:
                raise StoreConflict('unknown operation')
            old = NetworkOperation.model_validate_json(row[0])
            if old.operation_revision != expected_revision or operation.operation_revision not in (expected_revision, expected_revision + 1):
                raise StoreConflict('operation revision changed')
            if (old.request_id, old.network_id, old.cluster_binding_id, old.proposal_digest, old.master_term, old.master_session_id, old.participants) != (operation.request_id, operation.network_id, operation.cluster_binding_id, operation.proposal_digest, operation.master_term, operation.master_session_id, operation.participants):
                raise StoreConflict('operation binding is immutable')
            if old.terminal_result is not None and operation != old:
                raise StoreConflict('terminal operation cannot be revived')
            db.execute('UPDATE operations SET revision=?,body=? WHERE id=?', (operation.operation_revision, operation.model_dump_json(), str(operation.operation_id)))

    def result(self, operation_id: UUID | str, result: ParticipantResult) -> None:
        with self.transaction() as db:
            row = db.execute('SELECT body FROM results WHERE operation=? AND node=? AND phase_sequence=?', (str(operation_id), result.node_id, result.phase_sequence)).fetchone()
            if row and row[0] != result.model_dump_json():
                raise StoreConflict('participant result already recorded')
            db.execute('INSERT OR IGNORE INTO results VALUES (?,?,?,?)', (str(operation_id), result.node_id, result.phase_sequence, result.model_dump_json()))

    def results(self, operation_id: UUID | str) -> list[ParticipantResult]:
        with self.lock:
            return [ParticipantResult.model_validate_json(row[0]) for row in self.db.execute('SELECT body FROM results WHERE operation=? ORDER BY phase_sequence,node', (str(operation_id),))]

    def journal(self, journal: LocalMutationJournal) -> None:
        with self.transaction() as db:
            old = db.execute('SELECT body FROM journals WHERE id=?', (str(journal.journal_id),)).fetchone()
            if old:
                previous = LocalMutationJournal.model_validate_json(old[0])
                if previous.phase == 'removed_observed' and journal != previous:
                    raise StoreConflict('cleanup tombstone is terminal')
                if (previous.network_id, previous.operation_id, previous.node_id, previous.adapter_fingerprint, previous.exact_address_prefix) != (journal.network_id, journal.operation_id, journal.node_id, journal.adapter_fingerprint, journal.exact_address_prefix):
                    raise StoreConflict('journal resource is immutable')
            db.execute('INSERT INTO journals VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET phase=excluded.phase,body=excluded.body', (str(journal.journal_id), str(journal.network_id), str(journal.operation_id), journal.node_id, journal.phase, journal.model_dump_json()))

    def journals(self) -> list[LocalMutationJournal]:
        with self.lock:
            return [LocalMutationJournal.model_validate_json(row[0]) for row in self.db.execute('SELECT body FROM journals')]

    def begin_command(self, key: str, body_digest: str) -> tuple[bool, Any]:
        """Intent is committed before execution. Incomplete repeats require reconciliation."""
        with self.transaction() as db:
            row = db.execute('SELECT digest,state,result FROM commands WHERE key=?', (key,)).fetchone()
            if row:
                if row[0] != body_digest:
                    raise StoreConflict('command body changed')
                if row[1] != 'complete':
                    raise StoreConflict('command outcome uncertain; reconcile before retry')
                return False, json.loads(row[2])
            db.execute("INSERT INTO commands VALUES (?,?,'intent',NULL)", (key, body_digest))
            return True, None

    def finish_command(self, key: str, result: Any) -> None:
        with self.transaction() as db:
            encoded = json.dumps(result, allow_nan=False, separators=(',', ':'))
            if db.execute("UPDATE commands SET state='complete',result=? WHERE key=? AND state='intent'", (encoded, key)).rowcount != 1:
                raise StoreConflict('command missing or already terminal')

    def reservation(self, run: RunNetworkReservation) -> None:
        with self.transaction() as db:
            row = db.execute('SELECT body FROM reservations WHERE id=?', (str(run.run_id),)).fetchone()
            if row:
                old = RunNetworkReservation.model_validate_json(row[0])
                if (old.network_id, old.assignment_revisions, old.logical_endpoints, old.launch_endpoints, old.relay_instance_ids) != (run.network_id, run.assignment_revisions, run.logical_endpoints, run.launch_endpoints, run.relay_instance_ids):
                    raise StoreConflict('reserved endpoints are immutable')
                allowed = {'reserved': {'active', 'releasing', 'uncertain'}, 'active': {'releasing', 'uncertain'}, 'releasing': {'released', 'uncertain'}, 'uncertain': {'releasing'}, 'released': set()}
                if run.state != old.state and run.state not in allowed[old.state]:
                    raise StoreConflict('invalid reservation transition')
            elif run.state != 'reserved':
                raise StoreConflict('reservation must be persisted before launch')
            db.execute('INSERT INTO reservations VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,body=excluded.body', (str(run.run_id), str(run.network_id), run.state, run.model_dump_json()))

    def runs(self, network_id: UUID | str) -> list[RunNetworkReservation]:
        with self.lock:
            return [RunNetworkReservation.model_validate_json(row[0]) for row in self.db.execute('SELECT body FROM reservations WHERE network=?', (str(network_id),))]

    def lease(self, lease: AdmissionLease) -> None:
        # Receive-relative monotonic values never belong to durable state.
        durable = lease.model_copy(update={'monotonic_deadline': 0.0})
        with self.transaction() as db:
            row = db.execute('SELECT body FROM leases WHERE id=?', (str(lease.lease_id),)).fetchone()
            if row:
                old = AdmissionLease.model_validate_json(row[0])
                if old.state == 'released' and durable != old:
                    raise StoreConflict('released admission cannot be revived')
                if lease.generation < old.generation or (old.network_id, old.node_id, old.kind, old.run_id, old.assignment_revision, old.authority, old.relay_instance_ids) != (lease.network_id, lease.node_id, lease.kind, lease.run_id, lease.assignment_revision, lease.authority, lease.relay_instance_ids):
                    raise StoreConflict('admission fence changed')
            db.execute('INSERT INTO leases VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,state=excluded.state,body=excluded.body', (str(lease.lease_id), str(lease.network_id), lease.node_id, lease.generation, lease.state, durable.model_dump_json()))

    def evidence(self, evidence: VerificationEvidence) -> None:
        durable = evidence.model_copy(update={'live_deadline': 0.0})
        with self.transaction() as db:
            db.execute('INSERT INTO evidence VALUES (?,?,?) ON CONFLICT(network,node) DO UPDATE SET body=excluded.body', (str(evidence.network_id), evidence.node_id, durable.model_dump_json()))

    def recover(self) -> None:
        """A new agent has lost event continuity and all live admission deadlines."""
        with self.transaction() as db:
            for row in db.execute("SELECT id,body FROM journals WHERE phase!='removed_observed'").fetchall():
                journal = LocalMutationJournal.model_validate_json(row['body'])
                journal.observer_health = 'gap'
                journal.phase = 'ownership_conflict'
                journal.retained_error = 'Agent restart lost ownership observation; inspect locally before removal'
                db.execute('UPDATE journals SET phase=?,body=? WHERE id=?', (journal.phase, journal.model_dump_json(), row['id']))
            for row in db.execute("SELECT id,body FROM networks WHERE state IN ('ready','applying','preparing','verifying')").fetchall():
                network = ManagedNetwork.model_validate_json(row['body'])
                network.observed_state = 'degraded' if network.desired_state == 'active' else 'cleanup_pending'
                network.revision += 1
                network.updated_at = utc_now()
                db.execute('UPDATE networks SET state=?,revision=?,body=? WHERE id=?', (network.observed_state, network.revision, network.model_dump_json(), row['id']))
            for row in db.execute("SELECT id,body FROM reservations WHERE state!='released'").fetchall():
                run = RunNetworkReservation.model_validate_json(row['body'])
                run.state = 'uncertain'
                db.execute("UPDATE reservations SET state='uncertain',body=? WHERE id=?", (run.model_dump_json(), row['id']))
            for row in db.execute("SELECT id,body FROM leases WHERE state!='released'").fetchall():
                lease = AdmissionLease.model_validate_json(row['body'])
                lease.state = 'uncertain'
                lease.monotonic_deadline = 0.0
                db.execute("UPDATE leases SET state='uncertain',body=? WHERE id=?", (lease.model_dump_json(), row['id']))
            db.execute('DELETE FROM evidence')
            db.execute('DELETE FROM channels')
