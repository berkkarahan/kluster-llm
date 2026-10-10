"""Signed dedicated diagnostics and volatile freshness, separate from RPC streams."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from typing import Any
from uuid import UUID

from pydantic import Field

from kllm.modules.managed_network.interactors.identity import LocalIdentity, encode
from kllm.modules.managed_network.entities import (
    Digest,
    Identifier,
    MasterAuthority,
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
        if task:
            self.tasks.add(task)
        try:

            async def exchange() -> None:
                raw = await reader.readline()
                if not raw.endswith(b"\n") or len(raw) > 8192:
                    raise ValueError("invalid diagnostic length")
                from kllm.modules.managed_network.interactors.identity import strict_json

                wire = raw[:-1].decode()
                envelope = strict_json(wire, 8192)
                untrusted = strict_json(envelope.get("payload", ""), 8192)
                node_id = untrusted.get("sender_node_id")
                if not isinstance(node_id, str):
                    raise ValueError("invalid dedicated sender")
                peer = await self.store.arun(self.store.peer, node_id)
                if (
                    peer is None
                    or peer.cluster_binding_id != self.authority.cluster_binding_id
                    or writer.get_extra_info("peername")[0] != self.peers.get(node_id)
                ):
                    raise ValueError("dedicated source mismatch")
                frame = EchoFrame.model_validate(LocalIdentity.verify(wire, peer.public_key, 8192))
                self._match(frame)
                if frame.type != "echo" or frame.request_digest is not None:
                    raise ValueError("unexpected diagnostic request")
                now = time.monotonic()
                while self.nonces and next(iter(self.nonces.values())) <= now:
                    self.nonces.popitem(last=False)
                if frame.nonce in self.nonces or len(self.nonces) >= 512:
                    raise ValueError("replayed diagnostic nonce")
                self.nonces[frame.nonce] = now + 2
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

            await asyncio.wait_for(exchange(), 2)
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except Exception:
                pass
            if task:
                self.tasks.discard(task)

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
