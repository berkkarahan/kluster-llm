"""Signed dedicated diagnostics and volatile freshness, separate from RPC streams."""

from __future__ import annotations

import asyncio
import hashlib
import statistics
import secrets
import time
from collections import OrderedDict
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from kllm.modules.managed_network.interactors.identity import LocalIdentity, encode
from kllm.modules.managed_network.entities import (
    Digest,
    Identifier,
    MasterAuthority,
    NodeIdentity,
    Record,
    Revision,
    utc_now,
)
from kllm.shared.platform.base import PlatformRefusal
from kllm.modules.managed_network.gateways.store import NetworkStore, digest


class EchoFrame(Record):
    version: int = Field(strict=True, ge=1, le=1)
    type: str = Field(pattern=r"^(echo|echo_reply)$")
    network_id: UUID
    assignment_revision: Revision
    cluster_binding_id: UUID
    master_term: Revision
    master_session_id: UUID
    sender_node_id: Identifier
    target_node_id: Identifier
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    ttl_ms: int = Field(strict=True, gt=0, le=2000)
    request_digest: Digest | None = None


class StreamFrame(EchoFrame):
    version: Literal[2] = 2
    type: Literal["stream", "stream_ready", "stream_reply"]
    ttl_ms: Literal[10000] = 10000
    payload_bytes: Literal[1048576] = 1048576
    samples: Literal[3] = 3
    sample: Literal[0, 1, 2] | None = None
    grant_id: Digest | None = None

    @field_validator("version", "ttl_ms", "payload_bytes", "samples", "sample", mode="before")
    @classmethod
    def integer_geometry(cls, value, info):
        if info.field_name == "sample" and value is None:
            return value
        if type(value) is not int:
            raise ValueError("Stream geometry requires exact integers")
        return value


class StreamScope(Record):
    """Ephemeral peer keys approved over the pinned master's signed channel."""

    probe_id: UUID
    operation_id: UUID
    contract_digest: Digest
    network_id: UUID
    authority: MasterAuthority
    source: NodeIdentity
    target: NodeIdentity
    source_revision: Revision
    target_revision: Revision

    @property
    def digest(self):
        return digest(self.model_dump(mode="json"))


def stream_block(nonce, sample):
    seed = hashlib.sha256(f"{nonce}:{sample}".encode()).digest()
    return seed * (65536 // len(seed))


class DedicatedDiagnostics:
    def __init__(
        self,
        identity: LocalIdentity,
        store: NetworkStore,
        network_id: UUID,
        revision: int,
        authority: MasterAuthority,
        address: str,
        port: int,
        peers: dict[str, str],
    ):
        self.identity, self.store, self.network_id, self.revision = (
            identity,
            store,
            network_id,
            revision,
        )
        self.authority, self.address, self.port, self.peers = authority, address, port, peers
        self.server: asyncio.Server | None = None
        self.nonces: OrderedDict[str, float] = OrderedDict()
        self.tasks: set[asyncio.Task] = set()
        self.streaming = 0
        self.stream_scopes = {}
        self.scope_timers = {}
        self.scope_cleanup = {}
        self.scope_readers = {}
        self.scope_expiry_tasks = set()
        self.scope_lifetime = 12

    async def grant_stream(self, scope: StreamScope, check, *, release=None):
        await asyncio.to_thread(check)
        self.store.check_authority(self.authority)
        local = scope.source if scope.source.node_id == self.identity.node_id else scope.target
        other = scope.target if local is scope.source else scope.source
        revision = scope.source_revision if local is scope.source else scope.target_revision
        if (
            scope.network_id != self.network_id
            or scope.authority != self.authority
            or local != self.identity.public(self.authority.cluster_binding_id)
            or other.node_id not in self.peers
            or revision != self.revision
            or other.cluster_binding_id != self.authority.cluster_binding_id
        ):
            raise ValueError("Stream grant differs from the selected private assignment")
        now = time.monotonic()
        for key, entry in list(self.stream_scopes.items()):
            if entry[1] <= now:
                await self.revoke_stream(key)
        if scope.digest in self.stream_scopes or len(self.stream_scopes) >= 128:
            raise ValueError("Stream grant already exists or scope limit reached")
        self.stream_scopes[scope.digest] = (scope, now + self.scope_lifetime, check)
        if release is not None:
            self.scope_cleanup[scope.digest] = release
        def expire():
            task = asyncio.create_task(self.revoke_stream(scope.digest))
            self.scope_expiry_tasks.add(task)
            task.add_done_callback(self.scope_expiry_tasks.discard)
        self.scope_timers[scope.digest] = asyncio.get_running_loop().call_later(self.scope_lifetime, expire)
        return scope.digest

    async def revoke_stream(self, grant_id):
        self.stream_scopes.pop(grant_id, None)
        timer = self.scope_timers.pop(grant_id, None)
        if timer is not None:
            timer.cancel()
        release = self.scope_cleanup.pop(grant_id, None)
        readers = self.scope_readers.pop(grant_id, set())
        current = asyncio.current_task()
        pending = [task for task in readers if task is not current]
        for task in pending:
            task.cancel()
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            if release is not None:
                release()

    async def _scoped_peer(self, grant_id, *, receiving):
        entry = self.stream_scopes.get(grant_id)
        if entry is None or entry[1] <= time.monotonic():
            raise ValueError("Stream grant expired or disappeared")
        scope, _, check = entry
        await asyncio.to_thread(check)
        if self.stream_scopes.get(grant_id) is not entry or entry[1] <= time.monotonic():
            raise ValueError("Stream grant expired or was closed during authorization")
        self.store.check_authority(self.authority)
        local, peer = (scope.target, scope.source) if receiving else (scope.source, scope.target)
        if local != self.identity.public(self.authority.cluster_binding_id):
            raise ValueError("Stream grant targets another local owner")
        pinned = await self.store.arun(self.store.peer, peer.node_id)
        if pinned is not None and (
            pinned.public_key != peer.public_key
            or pinned.cluster_binding_id != peer.cluster_binding_id
        ):
            raise ValueError("Stream grant conflicts with the pinned peer")
        return peer

    def _match(self, frame: EchoFrame) -> None:
        self.store.check_authority(self.authority)
        if (
            frame.network_id,
            frame.cluster_binding_id,
            frame.master_term,
            frame.master_session_id,
            frame.target_node_id,
            frame.assignment_revision,
        ) != (
            self.network_id,
            self.authority.cluster_binding_id,
            self.authority.master_term,
            self.authority.master_session_id,
            self.identity.node_id,
            self.revision,
        ):
            raise PlatformRefusal("STALE_REVISION", "Dedicated diagnostic context changed")
        if frame.sender_node_id not in self.peers:
            raise PlatformRefusal("UNPAIRED_IDENTITY", "Dedicated sender is not selected")

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle, self.address, self.port, limit=8192, reuse_address=False
        )

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        active_scope = None
        if task:
            self.tasks.add(task)
        try:

            async def exchange() -> None:
                nonlocal active_scope
                raw = await asyncio.wait_for(reader.readline(), 2)
                if not raw.endswith(b"\n") or len(raw) > 8192:
                    raise ValueError("invalid diagnostic length")
                from kllm.modules.managed_network.interactors.identity import strict_json

                wire = raw[:-1].decode()
                envelope = strict_json(wire, 8192)
                untrusted = strict_json(envelope.get("payload", ""), 8192)
                node_id = untrusted.get("sender_node_id")
                if not isinstance(node_id, str):
                    raise ValueError("invalid dedicated sender")
                grant_id = untrusted.get("grant_id") if untrusted.get("version") == 2 else None
                peer = (
                    await self._scoped_peer(grant_id, receiving=True)
                    if grant_id
                    else await self.store.arun(self.store.peer, node_id)
                )
                if (
                    peer is None
                    or peer.node_id != node_id
                    or peer.cluster_binding_id != self.authority.cluster_binding_id
                    or writer.get_extra_info("peername")[0] != self.peers.get(node_id)
                ):
                    raise ValueError("dedicated source mismatch")
                verified = LocalIdentity.verify(wire, peer.public_key, 8192)
                frame = (StreamFrame if verified.get("version") == 2 else EchoFrame).model_validate(
                    verified
                )
                self._match(frame)
                if frame.type not in ("echo", "stream") or frame.request_digest is not None:
                    raise ValueError("unexpected diagnostic request")
                if isinstance(frame, StreamFrame) and (
                    frame.sample is not None or self.streaming >= 2
                ):
                    raise ValueError("invalid or busy stream diagnostic")
                now = time.monotonic()
                while self.nonces and next(iter(self.nonces.values())) <= now:
                    self.nonces.popitem(last=False)
                if frame.nonce in self.nonces or len(self.nonces) >= 512:
                    raise ValueError("replayed diagnostic nonce")
                self.nonces[frame.nonce] = now + (12 if isinstance(frame, StreamFrame) else 2)
                if isinstance(frame, StreamFrame):
                    if frame.grant_id:
                        if self.scope_readers.get(frame.grant_id):
                            raise ValueError("Stream grant already has an active reader")
                        active_scope = frame.grant_id
                        self.scope_readers[active_scope] = {task}
                    self.streaming += 1
                    try:
                        await self._stream_receive(frame, reader, writer, peer)
                    finally:
                        self.streaming -= 1
                    return
                response = frame.model_copy(
                    update={
                        "type": "echo_reply",
                        "sender_node_id": self.identity.node_id,
                        "target_node_id": frame.sender_node_id,
                        "request_digest": digest(frame.model_dump(mode="json")),
                    }
                )
                writer.write(
                    self.identity.sign(response.model_dump(mode="json"), 8192).encode() + b"\n"
                )
                await writer.drain()

            await asyncio.wait_for(exchange(), 12)
        except Exception:
            pass
        finally:
            if active_scope is not None:
                await self.revoke_stream(active_scope)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except Exception:
                pass
            if task:
                self.tasks.discard(task)

    async def _stream_receive(self, frame, reader, writer, approved_peer):
        response = frame.model_copy(
            update={
                "type": "stream_ready",
                "sender_node_id": self.identity.node_id,
                "target_node_id": frame.sender_node_id,
                "request_digest": digest(frame.model_dump(mode="json")),
            }
        )
        writer.write(self.identity.sign(response.model_dump(mode="json"), 8192).encode() + b"\n")
        await writer.drain()
        for sample in range(frame.samples):
            block = stream_block(frame.nonce, sample)
            for _ in range(frame.payload_bytes // len(block)):
                if await reader.readexactly(len(block)) != block:
                    raise ValueError("stream diagnostic bytes differ")
            self._match(frame)
            peer = (
                await self._scoped_peer(frame.grant_id, receiving=True)
                if frame.grant_id
                else await self.store.arun(self.store.peer, frame.sender_node_id)
            )
            if (
                peer is None
                or peer.public_key != approved_peer.public_key
                or peer.cluster_binding_id != approved_peer.cluster_binding_id
            ):
                raise ValueError("stream peer identity changed")
            reply = response.model_copy(update={"type": "stream_reply", "sample": sample})
            writer.write(self.identity.sign(reply.model_dump(mode="json"), 8192).encode() + b"\n")
            await writer.drain()

    async def measure(
        self, peer_node_id: str, peer_revision: int, peer_port: int, *, grant_id=None
    ) -> dict[str, Any]:
        peer = (
            await self._scoped_peer(grant_id, receiving=False)
            if grant_id
            else await self.store.arun(self.store.peer, peer_node_id)
        )
        if (
            peer is None
            or peer.node_id != peer_node_id
            or peer_node_id not in self.peers
            or peer.cluster_binding_id != self.authority.cluster_binding_id
        ):
            raise PlatformRefusal("UNPAIRED_IDENTITY", "Stream peer is not paired and selected")
        await self.store.arun(self.store.check_authority, self.authority)
        frame = StreamFrame(
            type="stream",
            network_id=self.network_id,
            assignment_revision=peer_revision,
            cluster_binding_id=self.authority.cluster_binding_id,
            master_term=self.authority.master_term,
            master_session_id=self.authority.master_session_id,
            sender_node_id=self.identity.node_id,
            target_node_id=peer_node_id,
            nonce=encode(secrets.token_bytes(32)),
            ttl_ms=10000,
            grant_id=grant_id,
        )
        wire = self.identity.sign(frame.model_dump(mode="json"), 8192).encode() + b"\n"

        async def exchange():
            if grant_id:
                if grant_id not in self.stream_scopes:
                    raise ValueError("Stream grant disappeared before transfer")
                self.scope_readers.setdefault(grant_id, set()).add(asyncio.current_task())
            reader, writer = await asyncio.open_connection(
                self.peers[peer_node_id], peer_port, local_addr=(self.address, 0), limit=8192
            )
            try:
                if writer.get_extra_info("sockname")[0] != self.address:
                    raise ValueError("stream source binding changed")
                expected = frame.model_copy(
                    update={
                        "type": "stream_ready",
                        "sender_node_id": peer_node_id,
                        "target_node_id": self.identity.node_id,
                        "request_digest": digest(frame.model_dump(mode="json")),
                    }
                )

                async def receive(expected):
                    raw = await reader.readline()
                    if not raw.endswith(b"\n") or len(raw) > 8192:
                        raise ValueError("invalid stream receipt length")
                    elapsed = time.perf_counter()
                    reply = StreamFrame.model_validate(
                        LocalIdentity.verify(raw[:-1].decode(), peer.public_key, 8192)
                    )
                    if reply != expected:
                        raise ValueError("stream receipt context mismatch")
                    return elapsed

                started = time.perf_counter()
                writer.write(wire)
                await writer.drain()
                ready_at = await receive(expected)
                durations = []
                for sample in range(frame.samples):
                    block = stream_block(frame.nonce, sample)
                    started_sample = time.perf_counter()
                    for _ in range(frame.payload_bytes // len(block)):
                        writer.write(block)
                        await writer.drain()
                    ended = await receive(
                        expected.model_copy(update={"type": "stream_reply", "sample": sample})
                    )
                    durations.append(ended - started_sample)
                current = (
                    await self._scoped_peer(grant_id, receiving=False)
                    if grant_id
                    else await self.store.arun(self.store.peer, peer_node_id)
                )
                if current is None or current.public_key != peer.public_key:
                    raise ValueError("stream peer identity changed")
                await self.store.arun(self.store.check_authority, self.authority)
                return {
                    "method": "signed_private_tcp_application_v1",
                    "source_node_id": self.identity.node_id,
                    "target_node_id": peer_node_id,
                    "source": self.address,
                    "peer": self.peers[peer_node_id],
                    "network_id": str(self.network_id),
                    "source_revision": self.revision,
                    "target_revision": peer_revision,
                    "diagnostic_port": peer_port,
                    "request_digest": expected.request_digest,
                    "payload_bytes": frame.payload_bytes,
                    "grant_id": grant_id,
                    "samples": frame.samples,
                    "latency_ms": (ready_at - started) * 1000,
                    "bandwidth_bytes_s": frame.payload_bytes / statistics.median(durations),
                    "sample_seconds": durations,
                    "measured_at": time.time(),
                }
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 0.5)
                except Exception:
                    pass

        try:
            return await asyncio.wait_for(exchange(), 10)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PlatformRefusal("TIMEOUT", "Signed private stream measurement failed") from None

    async def challenge(
        self, peer_node_id: str, peer_revision: int, peer_port: int
    ) -> dict[str, Any]:
        peer = await self.store.arun(self.store.peer, peer_node_id)
        if peer is None or peer_node_id not in self.peers:
            raise PlatformRefusal("UNPAIRED_IDENTITY", "Dedicated peer is not paired and selected")
        frame = EchoFrame(
            version=1,
            type="echo",
            network_id=self.network_id,
            assignment_revision=peer_revision,
            cluster_binding_id=self.authority.cluster_binding_id,
            master_term=self.authority.master_term,
            master_session_id=self.authority.master_session_id,
            sender_node_id=self.identity.node_id,
            target_node_id=peer_node_id,
            nonce=encode(secrets.token_bytes(32)),
            ttl_ms=2000,
        )

        async def exchange() -> dict[str, Any]:
            reader, writer = await asyncio.open_connection(
                self.peers[peer_node_id], peer_port, local_addr=(self.address, 0), limit=8192
            )
            try:
                if writer.get_extra_info("sockname")[0] != self.address:
                    raise ValueError("dedicated source binding changed")
                writer.write(
                    self.identity.sign(frame.model_dump(mode="json"), 8192).encode() + b"\n"
                )
                await writer.drain()
                raw = await reader.readline()
                if not raw.endswith(b"\n") or len(raw) > 8192:
                    raise ValueError("invalid diagnostic response length")
                response = EchoFrame.model_validate(
                    LocalIdentity.verify(raw[:-1].decode(), peer.public_key, 8192)
                )
                expected = frame.model_copy(
                    update={
                        "type": "echo_reply",
                        "sender_node_id": peer_node_id,
                        "target_node_id": self.identity.node_id,
                        "request_digest": digest(frame.model_dump(mode="json")),
                    }
                )
                if response != expected:
                    raise ValueError("diagnostic response context mismatch")
                current_peer = await self.store.arun(self.store.peer, peer_node_id)
                if current_peer is None or current_peer.public_key != peer.public_key:
                    raise ValueError("dedicated peer identity changed during challenge")
                await self.store.arun(self.store.check_authority, self.authority)
                return {
                    "request_digest": expected.request_digest,
                    "response_digest": digest(response.model_dump(mode="json")),
                    "source": self.address,
                    "peer": self.peers[peer_node_id],
                    "verified_at_monotonic": time.monotonic(),
                }
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 0.5)
                except Exception:
                    pass

        try:
            return await asyncio.wait_for(exchange(), 2)
        except Exception:
            raise PlatformRefusal("TIMEOUT", "Signed dedicated path challenge failed") from None

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        for key in list(self.stream_scopes):
            await self.revoke_stream(key)
        await asyncio.gather(*self.scope_expiry_tasks, return_exceptions=True)
        self.scope_expiry_tasks.clear()


class Freshness:
    def __init__(self):
        self.live: dict[tuple[str, str], dict[str, Any]] = {}

    def publish(self, network_id: UUID, node_id: str, evidence: dict[str, Any]) -> None:
        self.live[(str(network_id), node_id)] = {
            **evidence,
            "health_observed_at": utc_now().isoformat(),
            "health_deadline": time.monotonic() + 7,
            "invalidated": None,
        }

    def invalidate(self, network_id: UUID, reason: str) -> None:
        for (network, _), evidence in self.live.items():
            if network == str(network_id):
                evidence["health_deadline"] = 0
                evidence["invalidated"] = reason

    def require(self, network_id: UUID, node_id: str, *, idle: bool = True) -> dict[str, Any]:
        evidence = self.live.get((str(network_id), node_id))
        now = time.monotonic()
        if not evidence or evidence["health_deadline"] <= now or evidence.get("invalidated"):
            raise PlatformRefusal("STALE_REVISION", "Dedicated network evidence expired or changed")
        if idle and (
            not evidence.get("rpc_released") or now - evidence.get("idle_probe_at", 0) > 10
        ):
            raise PlatformRefusal("RPC_BUSY", "A fresh released idle RPC proof is required")
        return evidence
