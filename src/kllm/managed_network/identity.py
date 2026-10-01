"""Pinned Ed25519 identities and exact-byte, session-bound control messages."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field, field_validator

from .models import Digest, Counter, Identifier, MasterAuthority, NodeIdentity, PeerIdentity, Phase, Record, Revision
from .store import NetworkStore

DOMAIN = b'kllm-managed-network-v1\0'
MAX_FRAME = 64 * 1024


class IdentityError(ValueError):
    """Sanitized identity/protocol refusal."""


def encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode(value: str, length: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise IdentityError('invalid key/signature encoding')
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except ValueError:
        raise IdentityError('invalid key/signature encoding') from None
    if len(raw) != length or encode(raw) != value:
        raise IdentityError('invalid key/signature length')
    return raw


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IdentityError('duplicate JSON key')
        result[key] = value
    return result


def strict_json(raw: str, limit: int = MAX_FRAME) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > limit:
        raise IdentityError('frame exceeds limit')
    try:
        data = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(IdentityError('nonfinite JSON')))
    except (ValueError, RecursionError, UnicodeError):
        raise IdentityError('invalid JSON frame') from None
    def walk(value: Any, depth: int) -> None:
        if depth > 12:
            raise IdentityError('JSON nesting exceeds limit')
        if isinstance(value, dict):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, int) and not isinstance(value, bool) and abs(value) > (1 << 53) - 1:
            raise IdentityError('unsafe JSON integer')
    walk(data, 0)
    if not isinstance(data, dict):
        raise IdentityError('JSON object required')
    return data


def _secure_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise IdentityError('identity directory cannot be a symlink')
    if os.name == 'nt':
        # Resolve the current account SID without accepting account text as a command.
        result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True, timeout=5, check=True)
        sid = re.search(r'S-1-5-(?:[0-9]+-)*[0-9]+', result.stdout)
        if not sid:
            raise IdentityError('cannot establish private identity ACL')
        subprocess.run(['icacls', str(directory), '/inheritance:r', '/grant:r', f'*{sid.group() }:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'], capture_output=True, timeout=5, check=True)
    else:
        os.chmod(directory, 0o700)
        if directory.stat().st_uid != os.getuid():
            raise IdentityError('identity directory has different owner')


class LocalIdentity:
    def __init__(self, home: Path, node_id: str, store: NetworkStore):
        self.node_id = node_id
        directory = home / 'network-identity'
        _secure_directory(directory)
        self.path = directory / 'ed25519.key'
        existed = self.path.exists()
        if not existed and store.metadata('local_identity') is not None:
            raise IdentityError('private identity missing; explicit re-pairing required')
        if not existed:
            raw = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                if os.name != 'nt':
                    dirfd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(dirfd)
                    finally:
                        os.close(dirfd)
        fd = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid())):
                raise IdentityError('private identity permissions are unsafe')
            raw = source.read(33)
        if len(raw) != 32:
            raise IdentityError('private identity is corrupt')
        self._key = Ed25519PrivateKey.from_private_bytes(raw)
        public = self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key = encode(public)
        self.key_id = hashlib.sha256(public).hexdigest()
        store.pin_identity(node_id, self.key_id)

    def public(self, binding: UUID | None = None) -> NodeIdentity:
        return NodeIdentity(node_id=self.node_id, key_id=self.key_id, public_key=self.public_key, cluster_binding_id=binding)

    def sign(self, body: dict[str, Any], limit: int = MAX_FRAME) -> str:
        payload = json.dumps(body, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
        strict_json(payload, limit)
        signature = encode(self._key.sign(DOMAIN + payload.encode('utf-8')))
        wire = json.dumps({'payload': payload, 'signature': signature}, separators=(',', ':'))
        if len(wire.encode('utf-8')) > limit:
            raise IdentityError('signed frame exceeds limit')
        return wire

    @staticmethod
    def verify(wire: str, public_key: str, limit: int = MAX_FRAME) -> dict[str, Any]:
        envelope = strict_json(wire, limit)
        if set(envelope) != {'payload', 'signature'} or not isinstance(envelope['payload'], str):
            raise IdentityError('invalid signed envelope')
        signature = decode(envelope['signature'], 64)
        public = Ed25519PublicKey.from_public_bytes(decode(public_key, 32))
        try:
            public.verify(signature, DOMAIN + envelope['payload'].encode('utf-8'))
        except InvalidSignature:
            raise IdentityError('signature does not match paired identity') from None
        return strict_json(envelope['payload'], limit)


def validate_peer(peer: PeerIdentity) -> None:
    if hashlib.sha256(decode(peer.public_key, 32)).hexdigest() != peer.key_id or peer.cluster_binding_id is None:
        raise IdentityError('paired fingerprint/binding mismatch')


class SignedCommand(Record):
    cluster_binding_id: UUID
    master_node_id: Identifier
    master_key_id: Digest
    master_term: Revision
    master_session_id: UUID
    version: Literal[1]
    type: Literal['command', 'result', 'snapshot', 'ack', 'echo']
    # The public wire uses channel_id; accepted_channel_id is local authority storage.
    channel_id: UUID
    sender_node_id: Identifier
    target_node_id: Identifier
    sequence: Revision
    network_id: UUID
    operation_id: UUID
    operation_revision: Revision
    phase_sequence: Revision
    phase: Phase
    assignment_revision: Revision
    expected_local_revision: Counter
    ttl_ms: int = Field(strict=True, gt=0, le=60000)
    body: dict[str, Any]


class NonceRecord(Record):
    @field_validator('*')
    @classmethod
    def exact_nonce(cls, value: Any, info: Any) -> Any:
        if info.field_name in ('nonce', 'master_nonce', 'worker_nonce'):
            decode(value, 32)
        return value


class Challenge(MasterAuthority, NonceRecord):
    version: Literal[1]
    type: Literal['challenge']
    nonce: str


class ChannelReply(NonceRecord):
    version: Literal[1]
    type: Literal['channel_reply']
    sender_node_id: Identifier
    target_node_id: Identifier
    cluster_binding_id: UUID
    master_term: Revision
    master_session_id: UUID
    master_nonce: str
    worker_nonce: str
    boot_id: Identifier


class SessionAccept(MasterAuthority, NonceRecord):
    version: Literal[1]
    type: Literal['session_accept']
    worker_node_id: Identifier
    master_nonce: str
    worker_nonce: str
    channel_id: UUID


class ChannelFence:
    def __init__(self, store: NetworkStore, peer: PeerIdentity | None, authority: MasterAuthority, sender: str, target: str):
        if peer is None:
            raise IdentityError('peer is not explicitly paired')
        validate_peer(peer)
        if peer.cluster_binding_id != authority.cluster_binding_id:
            raise IdentityError('peer cluster binding mismatch')
        self.store, self.peer, self.authority = store, peer, authority
        self.sender, self.target = sender, target
        self.sequence = 0
        self.lock = threading.RLock()
        self.connected = True

    def check_live(self) -> None:
        if not self.connected:
            raise IdentityError('channel disconnected')
        self.store.check_channel(self.peer.node_id, self.authority)

    def receive(self, wire: str) -> SignedCommand:
        with self.lock:
            self.check_live()
            parsed = LocalIdentity.verify(wire, self.peer.public_key)
            try:
                command = SignedCommand.model_validate(parsed)
            except ValueError:
                raise IdentityError('invalid command schema') from None
            actual = MasterAuthority(cluster_binding_id=command.cluster_binding_id, master_node_id=command.master_node_id, master_key_id=command.master_key_id, master_term=command.master_term, master_session_id=command.master_session_id, accepted_channel_id=command.channel_id)
            if actual != self.authority or command.channel_id != self.authority.accepted_channel_id or command.sender_node_id != self.sender or command.target_node_id != self.target or command.sequence <= self.sequence:
                raise IdentityError('command authority/channel/sequence mismatch')
            self.sequence = command.sequence
            return command

    @staticmethod
    def deadline(command: SignedCommand) -> float:
        return time.monotonic() + command.ttl_ms / 1000
