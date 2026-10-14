"""Independent SQLite journal. Transactions serialize writers across processes."""

from __future__ import annotations

import hashlib
import json
import os
import time
from sqlalchemy import Connection, and_, delete, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from kllm.shared.persistence import SQLiteOptions, SQLiteStore
from kllm.modules.managed_network.gateways import schema as tables
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, uuid4

from kllm.modules.managed_network.entities import (
    AdmissionLease,
    LocalMutationJournal,
    ManagedNetwork,
    MasterAuthority,
    NetworkOperation,
    NodeAssignment,
    NodeNetworkIdentity,
    ParticipantResult,
    PeerIdentity,
    RunNetworkReservation,
    VerificationEvidence,
    MAX_COUNTER,
    utc_now,
    validate_transition,
)


class StoreConflict(ValueError):
    """A revision, authority, ownership or idempotency precondition changed."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _upsert(table, values, keys, fields):
    statement = insert(table).values(values)
    return statement.on_conflict_do_update(
        index_elements=list(keys), set_={name: statement.excluded[name] for name in fields}
    )


_open_stores: dict[Path, "NetworkStore"] = {}
_open_stores_lock = threading.RLock()


class NetworkStore:
    def __init__(self, home: Path, *, options: SQLiteOptions | None = None):
        self.path = home / "managed-network.sqlite"
        self.lock = threading.RLock()
        self.execution_mutex = threading.RLock()
        self._execution_depth = threading.local()
        self._store = SQLiteStore(self.path, tables.schema, options=options)
        with _open_stores_lock:
            _open_stores[self._store.path] = self

    @classmethod
    def active_for(cls, home: Path) -> "NetworkStore | None":
        target = Path(home).absolute() / "managed-network.sqlite"
        if target.exists() and not target.is_symlink():
            target = target.resolve()
        with _open_stores_lock:
            store = _open_stores.get(target)
            if (
                store is not None
                and store._store.state == "OPEN"
                and store._store.pid == os.getpid()
            ):
                return store
            return None

    def read(self):
        return self._store.connection()

    def maintenance(self, *, write: bool = True):
        return self._store.connection(write=write)

    async def arun(self, callback, *args, cancellable=True, **kwargs):
        return await self._store.arun(callback, *args, cancellable=cancellable, **kwargs)

    async def aclose(self, *, deadline: float | None = None):
        try:
            await self._store.aclose(deadline=deadline)
        finally:
            with _open_stores_lock:
                if _open_stores.get(self._store.path) is self:
                    _open_stores.pop(self._store.path, None)

    @contextmanager
    def execution(self) -> Iterator[None]:
        """Hold across intent, OS mutation and observation; fence changes use it too."""
        with self._store.operation() as operation:
            deadline = time.monotonic() + 5
            if not self.execution_mutex.acquire(timeout=5):
                raise StoreConflict("another agent owns the local mutation boundary")
            try:
                depth = getattr(self._execution_depth, "value", 0)
                if depth:
                    self._execution_depth.value = depth + 1
                    try:
                        yield
                    finally:
                        self._execution_depth.value = depth
                    return
                fd = os.open(
                    self.path.with_suffix(".lock"),
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                locked = False
                try:
                    while not locked:
                        operation.check()
                        try:
                            if os.name == "nt":
                                import msvcrt

                                os.lseek(fd, 0, 0)
                                getattr(msvcrt, "locking")(fd, getattr(msvcrt, "LK_NBLCK"), 1)
                            else:
                                import fcntl

                                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            locked = True
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise StoreConflict(
                                    "another agent owns the local mutation boundary"
                                ) from None
                            time.sleep(0.01)
                    self._execution_depth.value = 1
                    with self._store.protected():
                        yield
                finally:
                    self._execution_depth.value = 0
                    if locked:
                        if os.name == "nt":
                            import msvcrt

                            os.lseek(fd, 0, 0)
                            getattr(msvcrt, "locking")(fd, getattr(msvcrt, "LK_UNLCK"), 1)
                        else:
                            import fcntl

                            fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)

            finally:
                self.execution_mutex.release()

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self._store.connection(write=True) as db:
            yield db

    def set_metadata(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute(_upsert(tables.metadata, {"key": key, "value": value}, ("key",), ("value",)))

    def __enter__(self) -> "NetworkStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self, *, deadline: float | None = None) -> None:
        try:
            self._store.close(deadline=deadline)
        finally:
            with _open_stores_lock:
                if _open_stores.get(self._store.path) is self:
                    _open_stores.pop(self._store.path, None)

    def metadata(self, key: str) -> str | None:
        with self.read() as db:
            row = db.execute(
                select(tables.metadata.c.value)
                .select_from(tables.metadata)
                .where((tables.metadata.c.key == key))
            ).fetchone()
            return row[0] if row else None

    def prima_recipe(self) -> dict[str, Any]:
        from kllm.modules.primacpp.interactors.recipe import DEFAULT_RECIPE, RECIPE_METADATA_KEY

        raw = self.metadata(RECIPE_METADATA_KEY)
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict) and int(loaded.get("build_recipe_version", 0)) >= int(
                    DEFAULT_RECIPE["build_recipe_version"]
                ):
                    return loaded
            except (ValueError, TypeError):
                pass
        return self.set_prima_recipe(DEFAULT_RECIPE)

    def set_prima_recipe(self, value: dict[str, Any]) -> dict[str, Any]:
        from kllm.modules.primacpp.interactors.recipe import RECIPE_METADATA_KEY

        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.set_metadata(RECIPE_METADATA_KEY, serialized)
        return json.loads(serialized)

    def pin_identity(self, node_id: str, key_id: str) -> None:
        """Pin the signing key; adopt a rebuilt local node label.

        The private key is the cryptographic identity. The node label changes
        when the operator rebuilds or restores local cluster state; refusing to
        start then leaves every signed feature (and inference admission) stuck
        with no supported recovery. A changed key is still refused: that is an
        identity swap, not a label rotation.
        """
        with self.execution(), self.transaction() as db:
            row = db.execute(
                select(tables.metadata.c.value)
                .select_from(tables.metadata)
                .where((tables.metadata.c.key == "local_identity"))
            ).fetchone()
            value = f"{node_id}:{key_id}"
            if row and row[0] != value:
                pinned_key = row[0].partition(":")[2]
                if pinned_key != key_id:
                    raise StoreConflict("local identity key changed; explicit re-pairing required")
                # Authority and accepted channels name the previous label.
                # Peers and owned-resource journals are retained for review,
                # rejoin and cleanup.
                db.execute(delete(tables.authority))
                db.execute(delete(tables.channels))
            db.execute(
                _upsert(
                    tables.metadata, {"key": "local_identity", "value": value}, ("key",), ("value",)
                )
            )

    def cluster_binding(self) -> UUID:
        with self.transaction() as db:
            row = db.execute(
                select(tables.metadata.c.value)
                .select_from(tables.metadata)
                .where((tables.metadata.c.key == "cluster_binding"))
            ).fetchone()
            if row:
                return UUID(row[0])
            binding = uuid4()
            db.execute(
                insert(tables.metadata).values({"key": "cluster_binding", "value": str(binding)})
            )
            return binding

    def pair(self, peer: PeerIdentity, *, replace: bool = False, allow_live: bool = False) -> None:
        from kllm.modules.managed_network.interactors.identity import validate_peer

        validate_peer(peer)
        with self.execution(), self.transaction() as db:
            row = db.execute(
                select(tables.peers.c.body)
                .select_from(tables.peers)
                .where((tables.peers.c.node_id == peer.node_id))
            ).fetchone()
            # A worker pins exactly one master, so a rebuilt or restored master
            # presenting a new label or binding must supersede the old pin; two
            # master rows would stall the signed channel loop.
            superseded = peer.expected_role == "master" and bool(
                db.execute(
                    select(literal(1))
                    .select_from(tables.peers)
                    .where(
                        and_(
                            (tables.peers.c.role == peer.expected_role),
                            (tables.peers.c.node_id != peer.node_id),
                        )
                    )
                ).fetchone()
            )
            if (row and row[0] != peer.model_dump_json()) or superseded:
                if not replace:
                    raise StoreConflict("explicit peer replacement required")
                # A same-key rotation is the same cryptographic identity under a
                # new label; owned addresses and journals stay valid, so the
                # settled-networking gate only applies to identity swaps.
                if not allow_live and (
                    db.execute(
                        select(literal(1))
                        .select_from(tables.networks)
                        .where((tables.networks.c.state != "stopped"))
                    ).fetchone()
                    or db.execute(
                        select(literal(1))
                        .select_from(tables.journals)
                        .where((tables.journals.c.phase != "removed_observed"))
                    ).fetchone()
                ):
                    raise StoreConflict("peer replacement requires settled local networking")
                if superseded:
                    db.execute(
                        delete(tables.peers).where(
                            and_(
                                (tables.peers.c.role == peer.expected_role),
                                (tables.peers.c.node_id != peer.node_id),
                            )
                        )
                    )
                db.execute(delete(tables.authority))
                db.execute(delete(tables.channels))
            db.execute(
                _upsert(
                    tables.peers,
                    {
                        "node_id": peer.node_id,
                        "role": peer.expected_role,
                        "body": peer.model_dump_json(),
                    },
                    ("node_id",),
                    ("role", "body"),
                )
            )

    def directory(self) -> list[NodeNetworkIdentity]:
        with self.read() as db:
            return [
                NodeNetworkIdentity.model_validate_json(row[0])
                for row in db.execute(
                    select(tables.node_directory.c.body)
                    .select_from(tables.node_directory)
                    .order_by(tables.node_directory.c.node_id)
                )
            ]

    def put_directory(self, item: NodeNetworkIdentity) -> None:
        with self.transaction() as db:
            self._put_directory(db, item)

    def put_directory_many(
        self, items: list[NodeNetworkIdentity], *, replace: bool = False
    ) -> None:
        with self.transaction() as db:
            if replace:
                db.execute(delete(tables.node_directory))
            for item in items:
                self._put_directory(db, item)

    def remove_directory(self, node_id: str) -> None:
        with self.transaction() as db:
            db.execute(
                delete(tables.node_directory).where((tables.node_directory.c.node_id == node_id))
            )

    @staticmethod
    def _put_directory(db: Connection, item: NodeNetworkIdentity) -> None:
        db.execute(
            _upsert(
                tables.node_directory,
                {"node_id": item.node_id, "body": item.model_dump_json()},
                ("node_id",),
                ("body",),
            )
        )

    def peer(self, node_id: str) -> PeerIdentity | None:
        with self.read() as db:
            row = db.execute(
                select(tables.peers.c.body)
                .select_from(tables.peers)
                .where((tables.peers.c.node_id == node_id))
            ).fetchone()
            return PeerIdentity.model_validate_json(row[0]) if row else None

    def peers(self) -> list[PeerIdentity]:
        with self.read() as db:
            return [
                PeerIdentity.model_validate_json(row[0])
                for row in db.execute(select(tables.peers.c.body).select_from(tables.peers))
            ]

    def startup_authority(self, node_id: str, key_id: str) -> MasterAuthority:
        binding = self.cluster_binding()
        with self.execution(), self.transaction() as db:
            row = db.execute(
                select(tables.metadata.c.value)
                .select_from(tables.metadata)
                .where((tables.metadata.c.key == "master_term"))
            ).fetchone()
            term = int(row[0]) + 1 if row else 1
            if not 1 <= term <= MAX_COUNTER:
                raise StoreConflict("invalid authority high-water mark")
            authority = MasterAuthority(
                cluster_binding_id=binding,
                master_node_id=node_id,
                master_key_id=key_id,
                master_term=term,
                master_session_id=uuid4(),
            )
            db.execute(
                _upsert(
                    tables.metadata,
                    {"key": "master_term", "value": str(term)},
                    ("key",),
                    ("value",),
                )
            )
            self._accept_authority(db, authority)
            return authority

    @staticmethod
    def _accept_authority(db: Connection, authority: MasterAuthority) -> None:
        row = db.execute(
            select(tables.authority.c.body)
            .select_from(tables.authority)
            .where((tables.authority.c.binding == str(authority.cluster_binding_id)))
        ).fetchone()
        if row:
            old = MasterAuthority.model_validate_json(row[0])
            if (
                (old.master_node_id, old.master_key_id)
                != (authority.master_node_id, authority.master_key_id)
                or authority.master_term < old.master_term
                or (
                    authority.master_term == old.master_term
                    and authority.master_session_id != old.master_session_id
                )
            ):
                raise StoreConflict("authority regression or unpaired master")
        db.execute(
            delete(tables.channels).where(
                and_(
                    (tables.channels.c.binding == str(authority.cluster_binding_id)),
                    or_(
                        (tables.channels.c.term != authority.master_term),
                        (tables.channels.c.session != str(authority.master_session_id)),
                    ),
                )
            )
        )
        core = authority.model_copy(update={"accepted_channel_id": None})
        db.execute(
            _upsert(
                tables.authority,
                {"binding": str(authority.cluster_binding_id), "body": core.model_dump_json()},
                ("binding",),
                ("body",),
            )
        )

    def accept_authority(self, authority: MasterAuthority, peer_node_id: str | None = None) -> None:
        with self.execution(), self.transaction() as db:
            self._accept_authority(db, authority)
            if authority.accepted_channel_id is not None:
                db.execute(
                    _upsert(
                        tables.channels,
                        {
                            "peer": peer_node_id or authority.master_node_id,
                            "binding": str(authority.cluster_binding_id),
                            "term": authority.master_term,
                            "session": str(authority.master_session_id),
                            "channel": str(authority.accepted_channel_id),
                        },
                        ("peer",),
                        ("binding", "term", "session", "channel"),
                    )
                )

    def check_channel(self, peer_node_id: str, authority: MasterAuthority) -> None:
        self.check_authority(authority)
        with self.read() as db:
            row = db.execute(
                select(
                    tables.channels.c.binding,
                    tables.channels.c.term,
                    tables.channels.c.session,
                    tables.channels.c.channel,
                )
                .select_from(tables.channels)
                .where((tables.channels.c.peer == peer_node_id))
            ).fetchone()
            expected = (
                str(authority.cluster_binding_id),
                authority.master_term,
                str(authority.master_session_id),
                str(authority.accepted_channel_id),
            )
            if not row or tuple(row) != expected:
                raise StoreConflict("superseded peer channel")

    def check_authority(self, authority: MasterAuthority) -> None:
        with self.read() as db:
            row = db.execute(
                select(tables.authority.c.body)
                .select_from(tables.authority)
                .where((tables.authority.c.binding == str(authority.cluster_binding_id)))
            ).fetchone()
            if not row or MasterAuthority.model_validate_json(row[0]) != authority.model_copy(
                update={"accepted_channel_id": None}
            ):
                raise StoreConflict("superseded authority/channel")

    def put_network(self, network: ManagedNetwork, *, expected_revision: int | None = None) -> None:
        with self.transaction() as db:
            row = db.execute(
                select(tables.networks.c.body)
                .select_from(tables.networks)
                .where((tables.networks.c.id == str(network.network_id)))
            ).fetchone()
            if row:
                old = ManagedNetwork.model_validate_json(row[0])
                if expected_revision != old.revision or network.revision != old.revision + 1:
                    raise StoreConflict("network revision changed")
                if (old.cluster_binding_id, old.master_node_id, old.master_key_id, old.subnet) != (
                    network.cluster_binding_id,
                    network.master_node_id,
                    network.master_key_id,
                    network.subnet,
                ):
                    raise StoreConflict("network authority/subnet is immutable")
                validate_transition(old.observed_state, network.observed_state)
            elif expected_revision is not None or network.revision != 1:
                raise StoreConflict("new network must begin at revision one")
            db.execute(
                _upsert(
                    tables.networks,
                    {
                        "id": str(network.network_id),
                        "binding": str(network.cluster_binding_id),
                        "state": network.observed_state,
                        "revision": network.revision,
                        "body": network.model_dump_json(),
                    },
                    ("id",),
                    ("state", "revision", "body"),
                )
            )

    def network(self, network_id: UUID | str) -> ManagedNetwork | None:
        with self.read() as db:
            row = db.execute(
                select(tables.networks.c.body)
                .select_from(tables.networks)
                .where((tables.networks.c.id == str(network_id)))
            ).fetchone()
            return ManagedNetwork.model_validate_json(row[0]) if row else None

    def networks(self) -> list[ManagedNetwork]:
        with self.read() as db:
            return [
                ManagedNetwork.model_validate_json(row[0])
                for row in db.execute(select(tables.networks.c.body).select_from(tables.networks))
            ]

    def put_assignment(
        self, assignment: NodeAssignment, *, expected_revision: int | None = None
    ) -> None:
        import ipaddress

        with self.transaction() as db:
            row = db.execute(
                select(tables.networks.c.body)
                .select_from(tables.networks)
                .where((tables.networks.c.id == str(assignment.network_id)))
            ).fetchone()
            if not row:
                raise StoreConflict("unknown network")
            subnet = ipaddress.IPv4Network(ManagedNetwork.model_validate_json(row[0]).subnet)
            address = ipaddress.IPv4Address(assignment.address)
            if (
                address not in subnet
                or address in (subnet.network_address, subnet.broadcast_address)
                or assignment.prefix_length != subnet.prefixlen
            ):
                raise StoreConflict("assignment outside usable subnet")
            current = db.execute(
                select(tables.assignments.c.revision, tables.assignments.c.body)
                .select_from(tables.assignments)
                .where(
                    and_(
                        (tables.assignments.c.network == str(assignment.network_id)),
                        (tables.assignments.c.node == assignment.node_id),
                    )
                )
            ).fetchone()
            if current and expected_revision != current[0]:
                raise StoreConflict("assignment revision changed")
            if current:
                old = NodeAssignment.model_validate_json(current[1])
                structural = (
                    "assignment_revision",
                    "adapter_id",
                    "os_interface",
                    "identity_fingerprint",
                    "address",
                    "prefix_length",
                    "logical_rpc_endpoint",
                    "launch_rpc_endpoint",
                    "rpc_child_identity",
                )
                if (
                    any(getattr(old, key) != getattr(assignment, key) for key in structural)
                    and db.execute(
                        select(literal(1))
                        .select_from(tables.reservations)
                        .where(
                            and_(
                                (tables.reservations.c.network == str(assignment.network_id)),
                                (tables.reservations.c.state != "released"),
                            )
                        )
                    ).fetchone()
                ):
                    raise StoreConflict("INFERENCE_ACTIVE")
                if assignment.assignment_revision not in (current[0], current[0] + 1):
                    raise StoreConflict("assignment revision regression")
            elif assignment.assignment_revision != 1:
                raise StoreConflict("new assignment must begin at revision one")
            db.execute(
                _upsert(
                    tables.assignments,
                    {
                        "network": str(assignment.network_id),
                        "node": assignment.node_id,
                        "address": assignment.address,
                        "revision": assignment.assignment_revision,
                        "body": assignment.model_dump_json(),
                    },
                    ("network", "node"),
                    ("address", "revision", "body"),
                )
            )

    def assignments(self, network_id: UUID | str) -> list[NodeAssignment]:
        with self.read() as db:
            return [
                NodeAssignment.model_validate_json(row[0])
                for row in db.execute(
                    select(tables.assignments.c.body)
                    .select_from(tables.assignments)
                    .where((tables.assignments.c.network == str(network_id)))
                    .order_by(tables.assignments.c.node)
                )
            ]

    def operation_for_request(self, binding: UUID, request_id: UUID) -> NetworkOperation | None:
        with self.read() as db:
            row = db.execute(
                select(tables.operations.c.body)
                .select_from(tables.operations)
                .where(
                    and_(
                        (tables.operations.c.binding == str(binding)),
                        (tables.operations.c.request == str(request_id)),
                    )
                )
            ).fetchone()
            return NetworkOperation.model_validate_json(row[0]) if row else None

    def put_operation(self, operation: NetworkOperation) -> NetworkOperation:
        with self.transaction() as db:
            if db.execute(
                select(literal(1))
                .select_from(tables.metadata)
                .where(
                    (
                        tables.metadata.c.key
                        == f"cancel:{operation.cluster_binding_id}:{operation.request_id}"
                    )
                )
            ).fetchone():
                raise StoreConflict("request ID already names a cancellation")
            row = db.execute(
                select(tables.operations.c.digest, tables.operations.c.body)
                .select_from(tables.operations)
                .where(
                    and_(
                        (tables.operations.c.binding == str(operation.cluster_binding_id)),
                        (tables.operations.c.request == str(operation.request_id)),
                    )
                )
            ).fetchone()
            if row:
                if row[0] != operation.proposal_digest:
                    raise StoreConflict("request ID reused with changed body")
                return NetworkOperation.model_validate_json(row[1])
            db.execute(
                insert(tables.operations).values(
                    {
                        "id": str(operation.operation_id),
                        "network": str(operation.network_id),
                        "binding": str(operation.cluster_binding_id),
                        "request": str(operation.request_id),
                        "digest": operation.proposal_digest,
                        "revision": operation.operation_revision,
                        "body": operation.model_dump_json(),
                    }
                )
            )
            return operation

    def operation(self, operation_id: UUID | str) -> NetworkOperation | None:
        with self.read() as db:
            row = db.execute(
                select(tables.operations.c.body)
                .select_from(tables.operations)
                .where((tables.operations.c.id == str(operation_id)))
            ).fetchone()
            return NetworkOperation.model_validate_json(row[0]) if row else None

    def update_operation(self, operation: NetworkOperation, expected_revision: int) -> None:
        with self.execution(), self.transaction() as db:
            row = db.execute(
                select(tables.operations.c.body)
                .select_from(tables.operations)
                .where((tables.operations.c.id == str(operation.operation_id)))
            ).fetchone()
            if not row:
                raise StoreConflict("unknown operation")
            old = NetworkOperation.model_validate_json(row[0])
            if old.operation_revision != expected_revision or operation.operation_revision not in (
                expected_revision,
                expected_revision + 1,
            ):
                raise StoreConflict("operation revision changed")
            if (
                old.request_id,
                old.network_id,
                old.cluster_binding_id,
                old.proposal_digest,
                old.master_term,
                old.master_session_id,
                old.participants,
            ) != (
                operation.request_id,
                operation.network_id,
                operation.cluster_binding_id,
                operation.proposal_digest,
                operation.master_term,
                operation.master_session_id,
                operation.participants,
            ):
                raise StoreConflict("operation binding is immutable")
            if old.terminal_result is not None and operation != old:
                raise StoreConflict("terminal operation cannot be revived")
            db.execute(
                update(tables.operations)
                .values(revision=operation.operation_revision, body=operation.model_dump_json())
                .where((tables.operations.c.id == str(operation.operation_id)))
            )

    def cancel_operation(
        self, operation_id: UUID, request_id: UUID, expected_revision: int
    ) -> NetworkOperation:
        """Commit cancellation fence and request idempotency together."""
        proposal = digest(
            {"operation_id": str(operation_id), "expected_revision": expected_revision}
        )
        with self.execution(), self.transaction() as db:
            row = db.execute(
                select(tables.operations.c.body)
                .select_from(tables.operations)
                .where((tables.operations.c.id == str(operation_id)))
            ).fetchone()
            if row is None:
                raise KeyError("unknown operation")
            operation = NetworkOperation.model_validate_json(row[0])
            key = f"cancel:{operation.cluster_binding_id}:{request_id}"
            previous = db.execute(
                select(tables.metadata.c.value)
                .select_from(tables.metadata)
                .where((tables.metadata.c.key == key))
            ).fetchone()
            if previous:
                if previous[0] != proposal:
                    raise StoreConflict("cancel request ID reused with changed body")
                return operation
            if db.execute(
                select(literal(1))
                .select_from(tables.operations)
                .where(
                    and_(
                        (tables.operations.c.binding == str(operation.cluster_binding_id)),
                        (tables.operations.c.request == str(request_id)),
                    )
                )
            ).fetchone():
                raise StoreConflict("request ID already names another operation")
            if operation.operation_revision != expected_revision:
                raise StoreConflict("operation revision changed")
            if operation.terminal_result is None:
                operation.operation_revision += 1
                db.execute(
                    update(tables.operations)
                    .values(revision=operation.operation_revision, body=operation.model_dump_json())
                    .where((tables.operations.c.id == str(operation_id)))
                )
            db.execute(insert(tables.metadata).values({"key": key, "value": proposal}))
            return operation

    def result(self, operation_id: UUID | str, result: ParticipantResult) -> None:
        with self.transaction() as db:
            row = db.execute(
                select(tables.results.c.body)
                .select_from(tables.results)
                .where(
                    and_(
                        (tables.results.c.operation == str(operation_id)),
                        (tables.results.c.node == result.node_id),
                        (tables.results.c.phase_sequence == result.phase_sequence),
                    )
                )
            ).fetchone()
            if row and row[0] != result.model_dump_json():
                raise StoreConflict("participant result already recorded")
            db.execute(
                insert(tables.results)
                .values(
                    {
                        "operation": str(operation_id),
                        "node": result.node_id,
                        "phase_sequence": result.phase_sequence,
                        "body": result.model_dump_json(),
                    }
                )
                .on_conflict_do_nothing()
            )

    def results(self, operation_id: UUID | str) -> list[ParticipantResult]:
        with self.read() as db:
            return [
                ParticipantResult.model_validate_json(row[0])
                for row in db.execute(
                    select(tables.results.c.body)
                    .select_from(tables.results)
                    .where((tables.results.c.operation == str(operation_id)))
                    .order_by(tables.results.c.phase_sequence, tables.results.c.node)
                )
            ]

    def journal(self, journal: LocalMutationJournal) -> None:
        with self.transaction() as db:
            old = db.execute(
                select(tables.journals.c.body)
                .select_from(tables.journals)
                .where((tables.journals.c.id == str(journal.journal_id)))
            ).fetchone()
            if old:
                previous = LocalMutationJournal.model_validate_json(old[0])
                if previous.phase == "removed_observed" and journal != previous:
                    raise StoreConflict("cleanup tombstone is terminal")
                if (
                    previous.network_id,
                    previous.operation_id,
                    previous.node_id,
                    previous.adapter_fingerprint,
                    previous.exact_address_prefix,
                ) != (
                    journal.network_id,
                    journal.operation_id,
                    journal.node_id,
                    journal.adapter_fingerprint,
                    journal.exact_address_prefix,
                ):
                    raise StoreConflict("journal resource is immutable")
            db.execute(
                _upsert(
                    tables.journals,
                    {
                        "id": str(journal.journal_id),
                        "network": str(journal.network_id),
                        "operation": str(journal.operation_id),
                        "node": journal.node_id,
                        "phase": journal.phase,
                        "body": journal.model_dump_json(),
                    },
                    ("id",),
                    ("phase", "body"),
                )
            )

    def journals(self) -> list[LocalMutationJournal]:
        with self.read() as db:
            return [
                LocalMutationJournal.model_validate_json(row[0])
                for row in db.execute(select(tables.journals.c.body).select_from(tables.journals))
            ]

    def begin_command(self, key: str, body_digest: str) -> tuple[bool, Any]:
        """Intent is committed before execution. Incomplete repeats require reconciliation."""
        with self.transaction() as db:
            row = db.execute(
                select(tables.commands.c.digest, tables.commands.c.state, tables.commands.c.result)
                .select_from(tables.commands)
                .where((tables.commands.c.key == key))
            ).fetchone()
            if row:
                if row[0] != body_digest:
                    raise StoreConflict("command body changed")
                if row[1] != "complete":
                    raise StoreConflict("command outcome uncertain; reconcile before retry")
                return False, json.loads(row[2])
            db.execute(
                insert(tables.commands).values(
                    {"key": key, "digest": body_digest, "state": "intent", "result": None}
                )
            )
            return True, None

    def finish_command(self, key: str, result: Any) -> None:
        with self.transaction() as db:
            encoded = json.dumps(result, allow_nan=False, separators=(",", ":"))
            if (
                db.execute(
                    update(tables.commands)
                    .values(state="complete", result=encoded)
                    .where(
                        and_((tables.commands.c.key == key), (tables.commands.c.state == "intent"))
                    )
                ).rowcount
                != 1
            ):
                raise StoreConflict("command missing or already terminal")

    def reservation(self, run: RunNetworkReservation) -> None:
        # List mutation bypasses pydantic's assignment hooks; validate at storage.
        run = RunNetworkReservation.model_validate(run.model_dump())
        with self.transaction() as db:
            row = db.execute(
                select(tables.reservations.c.body)
                .select_from(tables.reservations)
                .where((tables.reservations.c.id == str(run.run_id)))
            ).fetchone()
            if row:
                old = RunNetworkReservation.model_validate_json(row[0])
                if (
                    old.engine,
                    old.execution_manifest_digest,
                    old.authority,
                    old.network_id,
                    old.assignment_revisions,
                    old.logical_endpoints,
                    old.launch_endpoints,
                    old.relay_instance_ids,
                    old.prima_ports_required,
                ) != (
                    run.engine,
                    run.execution_manifest_digest,
                    run.authority,
                    run.network_id,
                    run.assignment_revisions,
                    run.logical_endpoints,
                    run.launch_endpoints,
                    run.relay_instance_ids,
                    run.prima_ports_required,
                ):
                    raise StoreConflict("reserved endpoints are immutable")
                allowed = {
                    "reserved": {"active", "releasing", "uncertain"},
                    "active": {"releasing", "uncertain"},
                    "releasing": {"released", "uncertain"},
                    "uncertain": {"releasing"},
                    "released": set(),
                }
                if run.state != old.state and run.state not in allowed[old.state]:
                    raise StoreConflict("invalid reservation transition")
                if old.state != "reserved" and (
                    not set(run.prima_port_nodes) <= set(old.prima_port_nodes)
                    or not set(run.prima_admitted_nodes) <= set(old.prima_admitted_nodes)
                ):
                    raise StoreConflict("Prima cleanup cannot acquire new native listeners")
            elif run.state != "reserved":
                raise StoreConflict("reservation must be persisted before launch")
            db.execute(
                _upsert(
                    tables.reservations,
                    {
                        "id": str(run.run_id),
                        "network": str(run.network_id),
                        "state": run.state,
                        "body": run.model_dump_json(),
                    },
                    ("id",),
                    ("state", "body"),
                )
            )

    def runs(self, network_id: UUID | str) -> list[RunNetworkReservation]:
        with self.read() as db:
            return [
                RunNetworkReservation.model_validate_json(row[0])
                for row in db.execute(
                    select(tables.reservations.c.body)
                    .select_from(tables.reservations)
                    .where((tables.reservations.c.network == str(network_id)))
                )
            ]

    def lease(self, lease: AdmissionLease) -> None:
        # Receive-relative monotonic values never belong to durable state.
        durable = lease.model_copy(update={"monotonic_deadline": 0.0})
        with self.transaction() as db:
            row = db.execute(
                select(tables.leases.c.body)
                .select_from(tables.leases)
                .where((tables.leases.c.id == str(lease.lease_id)))
            ).fetchone()
            if row:
                old = AdmissionLease.model_validate_json(row[0])
                if old.state == "released" and durable != old:
                    raise StoreConflict("released admission cannot be revived")
                if lease.generation < old.generation or (
                    old.network_id,
                    old.node_id,
                    old.kind,
                    old.run_id,
                    old.assignment_revision,
                    old.authority,
                    old.relay_instance_ids,
                ) != (
                    lease.network_id,
                    lease.node_id,
                    lease.kind,
                    lease.run_id,
                    lease.assignment_revision,
                    lease.authority,
                    lease.relay_instance_ids,
                ):
                    raise StoreConflict("admission fence changed")
            db.execute(
                _upsert(
                    tables.leases,
                    {
                        "id": str(lease.lease_id),
                        "network": str(lease.network_id),
                        "node": lease.node_id,
                        "generation": lease.generation,
                        "state": lease.state,
                        "body": durable.model_dump_json(),
                    },
                    ("id",),
                    ("generation", "state", "body"),
                )
            )

    def leases(self, network_id: UUID | str) -> list[AdmissionLease]:
        with self.read() as db:
            return [
                AdmissionLease.model_validate_json(row[0])
                for row in db.execute(
                    select(tables.leases.c.body)
                    .select_from(tables.leases)
                    .where((tables.leases.c.network == str(network_id)))
                )
            ]

    def evidence(self, evidence: VerificationEvidence) -> None:
        durable = evidence.model_copy(update={"live_deadline": 0.0})
        with self.transaction() as db:
            db.execute(
                _upsert(
                    tables.evidence,
                    {
                        "network": str(evidence.network_id),
                        "node": evidence.node_id,
                        "body": durable.model_dump_json(),
                    },
                    ("network", "node"),
                    ("body",),
                )
            )

    def recover(self) -> None:
        """A new agent has lost event continuity and all live admission deadlines."""
        with self.transaction() as db:
            for row in db.execute(
                select(tables.journals.c.id, tables.journals.c.body)
                .select_from(tables.journals)
                .where((tables.journals.c.phase != "removed_observed"))
            ).fetchall():
                journal = LocalMutationJournal.model_validate_json(row._mapping["body"])
                journal.observer_health = "gap"
                journal.phase = "ownership_conflict"
                journal.retained_error = (
                    "Agent restart lost ownership observation; inspect locally before removal"
                )
                db.execute(
                    update(tables.journals)
                    .values(phase=journal.phase, body=journal.model_dump_json())
                    .where((tables.journals.c.id == row._mapping["id"]))
                )
            for row in db.execute(
                select(tables.networks.c.id, tables.networks.c.body)
                .select_from(tables.networks)
                .where(tables.networks.c.state.in_(["ready", "applying", "preparing", "verifying"]))
            ).fetchall():
                network = ManagedNetwork.model_validate_json(row._mapping["body"])
                network.observed_state = (
                    "degraded" if network.desired_state == "active" else "cleanup_pending"
                )
                network.revision += 1
                network.updated_at = utc_now()
                db.execute(
                    update(tables.networks)
                    .values(
                        state=network.observed_state,
                        revision=network.revision,
                        body=network.model_dump_json(),
                    )
                    .where((tables.networks.c.id == row._mapping["id"]))
                )
            for row in db.execute(
                select(tables.reservations.c.id, tables.reservations.c.body)
                .select_from(tables.reservations)
                .where((tables.reservations.c.state != "released"))
            ).fetchall():
                run = RunNetworkReservation.model_validate_json(row._mapping["body"])
                run.state = "uncertain"
                db.execute(
                    update(tables.reservations)
                    .values(state="uncertain", body=run.model_dump_json())
                    .where((tables.reservations.c.id == row._mapping["id"]))
                )
            for row in db.execute(
                select(tables.leases.c.id, tables.leases.c.body)
                .select_from(tables.leases)
                .where((tables.leases.c.state != "released"))
            ).fetchall():
                lease = AdmissionLease.model_validate_json(row._mapping["body"])
                lease.state = "uncertain"
                lease.monotonic_deadline = 0.0
                db.execute(
                    update(tables.leases)
                    .values(state="uncertain", body=lease.model_dump_json())
                    .where((tables.leases.c.id == row._mapping["id"]))
                )
            db.execute(delete(tables.evidence))
            db.execute(delete(tables.channels))
