"""Worker-initiated paired control sockets, independent of dashboard telemetry."""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from kllm.managed_network.identity import (
    Challenge,
    ChannelFence,
    ChannelReply,
    IdentityError,
    LocalIdentity,
    SessionAccept,
    SignedCommand,
    SignedRuntimeCommand,
    encode,
    validate_peer,
)
from kllm.managed_network.models import MasterAuthority, PeerIdentity
from kllm.managed_network.store import NetworkStore, StoreConflict


class SocketWire:
    """Normalize ASGI server and websockets client without credentials in URLs."""

    def __init__(self, socket: Any, *, asgi: bool = False):
        self.socket, self.asgi = socket, asgi

    async def send(self, text: str) -> None:
        if self.asgi:
            await asyncio.wait_for(self.socket.send_text(text), 5)
        else:
            await asyncio.wait_for(self.socket.send(text), 5)

    async def receive(self) -> str:
        message = await (self.socket.receive_text() if self.asgi else self.socket.recv())
        if not isinstance(message, str):
            raise IdentityError("text frames required")
        return message

    async def close(self, code: int = 1008) -> None:
        await self.socket.close(code=code)


@dataclass
class Pending:
    command: SignedCommand | SignedRuntimeCommand
    future: asyncio.Future[dict[str, Any]]


class ChannelSession:
    def __init__(
        self,
        wire: SocketWire,
        identity: LocalIdentity,
        fence: ChannelFence,
        *,
        master: bool,
        handler: Callable[[SignedCommand, float], Awaitable[dict[str, Any]]] | None = None,
    ):
        self.wire, self.identity, self.fence = wire, identity, fence
        self.master, self.handler = master, handler
        self.out_sequence = 0
        self.pending: dict[tuple[str, str, int], Pending] = {}
        self.send_lock = asyncio.Lock()
        self.last_received = time.monotonic()
        self.task: asyncio.Task | None = None
        self.closed = False

    async def send(self, command: SignedCommand | SignedRuntimeCommand) -> None:
        async with self.send_lock:
            self.fence.check_live()
            self.out_sequence += 1
            command.sequence = self.out_sequence
            payload = self.identity.sign(command.model_dump(mode="json"))
            try:
                await self.wire.send(payload)
            except TimeoutError:
                expired = True
            else:
                expired = False
        if expired:
            # Drop the socket only after releasing send_lock. Closing while the
            # lock is held deadlocks the pump if it is waiting to send a result.
            await self._drop_after_send_timeout()
            raise TimeoutError("node channel send timed out")

    async def _drop_after_send_timeout(self) -> None:
        """A peer that stops reading must not keep this signed session."""
        self.closed = True
        self.fence.connected = False
        try:
            await asyncio.wait_for(self.wire.close(1001), 2)
        except Exception:
            pass

    async def request(
        self,
        *,
        network_id: UUID,
        operation_id: UUID,
        operation_revision: int,
        phase_sequence: int,
        phase: str,
        assignment_revision: int,
        expected_local_revision: int,
        body: dict[str, Any],
        ttl_ms: int = 5000,
    ) -> dict[str, Any]:
        if self.closed or len(self.pending) >= 16:
            raise IdentityError("node command channel unavailable or full")
        authority = self.fence.authority
        command = SignedCommand.model_validate(
            {
                **authority.model_dump(exclude={"accepted_channel_id"}),
                "channel_id": authority.accepted_channel_id,
                "version": 1,
                "type": "command",
                "sender_node_id": self.identity.node_id,
                "target_node_id": self.fence.peer.node_id,
                "sequence": 1,
                "network_id": network_id,
                "operation_id": operation_id,
                "operation_revision": operation_revision,
                "phase_sequence": phase_sequence,
                "phase": phase,
                "assignment_revision": assignment_revision,
                "expected_local_revision": expected_local_revision,
                "ttl_ms": ttl_ms,
                "body": body,
            }
        )
        key = (str(network_id), str(operation_id), phase_sequence)
        if key in self.pending:
            raise IdentityError("phase already awaiting a result")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[key] = Pending(command, future)
        try:
            await self.send(command)
            return await asyncio.wait_for(future, ttl_ms / 1000)
        finally:
            self.pending.pop(key, None)

    async def request_runtime(
        self, run_id: UUID, action: str, *, ttl_ms: int = 10000, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.closed or len(self.pending) >= 16:
            raise IdentityError("node runtime command channel unavailable or full")
        authority = self.fence.authority
        command = SignedRuntimeCommand.model_validate(
            {
                **authority.model_dump(exclude={"accepted_channel_id"}),
                "channel_id": authority.accepted_channel_id,
                "version": 1,
                "type": "rpc_command",
                "sender_node_id": self.identity.node_id,
                "target_node_id": self.fence.peer.node_id,
                "sequence": 1,
                "request_id": uuid4(),
                "run_id": run_id,
                "action": action,
                "ttl_ms": ttl_ms,
                "body": body or {},
            }
        )
        key = ("runtime", str(command.request_id), 0)
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = Pending(command, future)
        try:
            await self.send(command)
            return await asyncio.wait_for(future, ttl_ms / 1000)
        finally:
            self.pending.pop(key, None)

    async def pump(self) -> None:
        try:
            while not self.closed:
                command = self.fence.receive(await self.wire.receive())
                self.last_received = time.monotonic()
                deadline = ChannelFence.deadline(command)
                if isinstance(command, SignedRuntimeCommand):
                    if self.master:
                        if command.type != "rpc_result":
                            raise IdentityError("worker cannot send runtime commands")
                        pending = self.pending.get(("runtime", str(command.request_id), 0))
                        if pending is None:
                            continue
                        expected = pending.command
                        if not isinstance(expected, SignedRuntimeCommand) or (
                            command.run_id,
                            command.action,
                        ) != (expected.run_id, expected.action):
                            raise IdentityError("runtime result does not match request")
                        if not pending.future.done():
                            pending.future.set_result(command.body)
                    else:
                        if command.type != "rpc_command" or self.handler is None:
                            raise IdentityError("unexpected runtime frame")
                        result = await self.handler(command, deadline)
                        response = command.model_copy(
                            update={
                                "type": "rpc_result",
                                "sender_node_id": self.identity.node_id,
                                "target_node_id": self.fence.peer.node_id,
                                "body": result,
                            }
                        )
                        await self.send(response)
                    continue
                if self.master:
                    if command.type not in ("result", "ack"):
                        raise IdentityError("worker cannot send mutation commands")
                    key = (
                        str(command.network_id),
                        str(command.operation_id),
                        command.phase_sequence,
                    )
                    pending = self.pending.get(key)
                    if pending is None:
                        continue  # Late signed result cannot resurrect a timed-out operation.
                    expected = pending.command
                    if (
                        command.operation_revision,
                        command.phase,
                        command.assignment_revision,
                        command.expected_local_revision,
                    ) != (
                        expected.operation_revision,
                        expected.phase,
                        expected.assignment_revision,
                        expected.expected_local_revision,
                    ):
                        raise IdentityError("result does not match the requested phase")
                    if not pending.future.done():
                        pending.future.set_result(command.body)
                else:
                    if command.type != "command" or self.handler is None:
                        raise IdentityError("unexpected master frame")
                    # Serial worker execution bounds the command queue; a peer cannot
                    # schedule host mutations concurrently by sending more frames.
                    result = await self.handler(command, deadline)
                    response = command.model_copy(
                        update={
                            "type": "result",
                            "sender_node_id": self.identity.node_id,
                            "target_node_id": self.fence.peer.node_id,
                            "body": result,
                        }
                    )
                    await self.send(response)
        finally:
            self.closed = True
            self.fence.connected = False
            for pending in self.pending.values():
                if not pending.future.done():
                    pending.future.set_exception(
                        IdentityError("node channel disconnected; outcome requires reconciliation")
                    )

    async def close(self) -> None:
        self.closed = True
        self.fence.connected = False
        try:
            await asyncio.wait_for(self.wire.close(1000), 2)
        except Exception:
            pass
        if self.task and self.task != asyncio.current_task():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


def _handshake_refused(error: BaseException) -> bool:
    """True when the master rejected pairing, not when the path merely dropped.

    Close code 1008 is the server's refusal. A refused handshake has to re-assert
    the join secret; a timeout or connection error should only reconnect.
    """
    if isinstance(error, IdentityError):
        return True
    received = getattr(error, "rcvd", None)
    code = getattr(received, "code", None)
    if code is None:
        code = getattr(error, "code", None)
    return code == 1008


def reconnect_delay(previous: float, *, established: bool) -> float:
    """Back off after a failed dial; retry quickly after a session that came up.

    Workers that lose the master together would otherwise reconnect in lockstep.
    Callers apply jitter to the returned delay.
    """
    if established:
        return 1.0
    return min(8.0, max(1.0, previous * 2))


class NetworkChannelHub:
    # A pinned master peer can outlive the master's own pairing record (for
    # example after a restored cluster.json). Re-asserting our peer is the only
    # way to reconcile that divergence, so a rejected handshake retries the
    # mutual join-secret bootstrap at this interval instead of looping on a
    # connection the master keeps refusing.
    BOOTSTRAP_RETRY_S = 15.0

    def __init__(
        self,
        store: NetworkStore,
        identity: LocalIdentity,
        state: Callable[[], Any],
        master_base: Callable[[], str | None],
        handler: Callable[[SignedCommand, float], Awaitable[dict[str, Any]]],
    ):
        self.store, self.identity, self.state, self.master_base, self.handler = (
            store,
            identity,
            state,
            master_base,
            handler,
        )
        self.bootstrap: Callable[[], Awaitable[None]] | None = None
        self.authority: MasterAuthority | None = None
        self.sessions: dict[str, ChannelSession] = {}
        self.client_task: asyncio.Task | None = None
        self.stopping = False
        self.handshake_lock = asyncio.Lock()

    def master_authority(self) -> MasterAuthority:
        if self.state().role != "master":
            raise IdentityError("managed network requires current master role")
        if self.authority is None:
            self.authority = self.store.startup_authority(
                self.identity.node_id, self.identity.key_id
            )
        return self.authority

    async def server(self, socket: Any) -> None:
        if socket.headers.get("origin"):
            await socket.close(code=1008)
            return
        try:
            authority = self.master_authority()
            await socket.accept()
            wire = SocketWire(socket, asgi=True)

            async def handshake() -> tuple[PeerIdentity, MasterAuthority]:
                nonce = encode(secrets.token_bytes(32))
                challenge = Challenge(
                    version=1, type="challenge", **authority.model_dump(), nonce=nonce
                )
                await wire.send(self.identity.sign(challenge.model_dump(mode="json"), 8192))
                # Select a pinned key using only a bounded parsed envelope; no data
                # from this selection is trusted until signature verification.
                from kllm.managed_network.identity import strict_json

                raw = await wire.receive()
                envelope = strict_json(raw, 8192)
                untrusted = strict_json(envelope.get("payload", ""), 8192)
                peer = self.store.peer(untrusted.get("sender_node_id", ""))
                if (
                    peer is None
                    or peer.expected_role != "worker"
                    or peer.node_id not in self.state().members
                ):
                    raise IdentityError("worker is not paired and enrolled")
                validate_peer(peer)
                reply = ChannelReply.model_validate(
                    LocalIdentity.verify(raw, peer.public_key, 8192)
                )
                if (
                    peer.cluster_binding_id != authority.cluster_binding_id
                    or reply.sender_node_id != peer.node_id
                    or reply.target_node_id != self.identity.node_id
                    or reply.cluster_binding_id != authority.cluster_binding_id
                    or reply.master_nonce != nonce
                    or reply.master_term != authority.master_term
                    or reply.master_session_id != authority.master_session_id
                ):
                    raise IdentityError("handshake transcript mismatch")
                channel_id = uuid4()
                accepted = authority.model_copy(update={"accepted_channel_id": channel_id})
                response = SessionAccept(
                    version=1,
                    type="session_accept",
                    **authority.model_dump(),
                    worker_node_id=peer.node_id,
                    master_nonce=nonce,
                    worker_nonce=reply.worker_nonce,
                    channel_id=channel_id,
                )
                async with self.handshake_lock:
                    old = self.sessions.pop(peer.node_id, None)
                    if old:
                        await old.close()
                    self.store.accept_authority(accepted, peer.node_id)
                    await wire.send(self.identity.sign(response.model_dump(mode="json"), 8192))
                return peer, accepted

            peer, accepted = await asyncio.wait_for(handshake(), 5)
            session = ChannelSession(
                wire,
                self.identity,
                ChannelFence(self.store, peer, accepted, peer.node_id, self.identity.node_id),
                master=True,
            )
            self.sessions[peer.node_id] = session
            try:
                await session.pump()
            finally:
                if self.sessions.get(peer.node_id) is session:
                    self.sessions.pop(peer.node_id, None)
        except (Exception, asyncio.CancelledError) as error:
            logging.getLogger(__name__).debug("network channel handshake refused: %s", error)
            try:
                await socket.close(code=1008)
            except Exception:
                pass

    async def _connect(self, socket: Any, peer: PeerIdentity, base: str) -> ChannelSession:
        wire = SocketWire(socket)

        async def handshake() -> MasterAuthority:
            challenge = Challenge.model_validate(
                LocalIdentity.verify(await wire.receive(), peer.public_key, 8192)
            )
            if (
                challenge.master_node_id != peer.node_id
                or challenge.master_key_id != peer.key_id
                or challenge.cluster_binding_id != peer.cluster_binding_id
                or challenge.accepted_channel_id is not None
            ):
                raise IdentityError("master identity/binding mismatch")
            nonce = encode(secrets.token_bytes(32))
            reply = ChannelReply(
                version=1,
                type="channel_reply",
                sender_node_id=self.identity.node_id,
                target_node_id=peer.node_id,
                cluster_binding_id=challenge.cluster_binding_id,
                master_term=challenge.master_term,
                master_session_id=challenge.master_session_id,
                master_nonce=challenge.nonce,
                worker_nonce=nonce,
                boot_id=str(uuid4()),
            )
            await wire.send(self.identity.sign(reply.model_dump(mode="json"), 8192))
            accepted = SessionAccept.model_validate(
                LocalIdentity.verify(await wire.receive(), peer.public_key, 8192)
            )
            if (
                accepted.master_node_id,
                accepted.master_key_id,
                accepted.cluster_binding_id,
                accepted.master_term,
                accepted.master_session_id,
                accepted.worker_node_id,
                accepted.master_nonce,
                accepted.worker_nonce,
            ) != (
                challenge.master_node_id,
                challenge.master_key_id,
                challenge.cluster_binding_id,
                challenge.master_term,
                challenge.master_session_id,
                self.identity.node_id,
                challenge.nonce,
                nonce,
            ):
                raise IdentityError("session acceptance transcript mismatch")
            if self.state().role != "worker" or self.master_base() != base:
                raise IdentityError("cluster authority changed while pairing channel")
            authority = MasterAuthority(
                cluster_binding_id=accepted.cluster_binding_id,
                master_node_id=accepted.master_node_id,
                master_key_id=accepted.master_key_id,
                master_term=accepted.master_term,
                master_session_id=accepted.master_session_id,
                accepted_channel_id=accepted.channel_id,
            )
            self.store.accept_authority(authority, peer.node_id)
            return authority

        authority = await asyncio.wait_for(handshake(), 5)

        async def handle(command: SignedCommand, deadline: float) -> dict[str, Any]:
            if self.state().role != "worker" or self.master_base() != base:
                raise IdentityError("worker role or current master changed")
            return await self.handler(command, deadline)

        return ChannelSession(
            wire,
            self.identity,
            ChannelFence(self.store, peer, authority, peer.node_id, self.identity.node_id),
            master=False,
            handler=handle,
        )

    async def client_loop(self) -> None:
        import websockets
        import httpx

        logger = logging.getLogger(__name__)
        next_bootstrap = 0.0
        reassert = False
        # 0.5 grows to 1s on the first failed dial, then 2, 4, and 8.
        delay = 0.5
        while not self.stopping:
            connected = False
            try:
                peers = [p for p in self.store.peers() if p.expected_role == "master"]
                base = self.master_base()
                bootstrap = getattr(self, "bootstrap", None)
                now = time.monotonic()
                # Pair when unpinned, and re-assert a pinned peer whose handshake
                # the master keeps refusing (throttled). The master only pairs an
                # enrolled worker that proves the join secret, and a redundant
                # commit is a safe upsert, so this cannot widen trust.
                if (
                    self.state().role == "worker"
                    and base
                    and bootstrap
                    and (not peers or (reassert and now >= next_bootstrap))
                ):
                    next_bootstrap = now + self.BOOTSTRAP_RETRY_S
                    reassert = False
                    try:
                        await bootstrap()
                    except IdentityError as error:
                        logger.warning("network channel pairing rejected: %s", error)
                    peers = [p for p in self.store.peers() if p.expected_role == "master"]
                if self.state().role != "worker" or len(peers) != 1 or not base:
                    await asyncio.sleep(1)
                    continue
                url = (
                    base.replace("https://", "wss://", 1).replace("http://", "ws://", 1).rstrip("/")
                    + "/v2/cluster/network-control"
                )
                # A 2s pong deadline drops the signed channel when the event loop
                # stalls briefly, and the worker then reconciles every in-flight
                # command. 20s still notices a dead peer inside the RPC lease.
                async with websockets.connect(
                    url,
                    max_size=65536,
                    max_queue=16,
                    open_timeout=5,
                    close_timeout=2,
                    ping_interval=20,
                    ping_timeout=20,
                    proxy=None,
                ) as socket:
                    session = await self._connect(socket, peers[0], base)
                    connected = True
                    self.sessions[peers[0].node_id] = session
                    try:
                        await session.pump()
                    finally:
                        self.sessions.pop(peers[0].node_id, None)
            except (
                OSError,
                IdentityError,
                StoreConflict,
                ValueError,
                TimeoutError,
                websockets.WebSocketException,
                httpx.HTTPError,
            ) as error:
                if _handshake_refused(error):
                    reassert = True
                logger.debug("network channel unavailable: %s", error)
            delay = reconnect_delay(delay, established=connected)
            # Full jitter keeps a cluster from dialing the master in one burst.
            await asyncio.sleep(delay * (0.5 + random.random() * 0.5))

    async def start(self) -> None:
        self.stopping = False
        if self.state().role == "master":
            self.master_authority()
        self.client_task = asyncio.create_task(self.client_loop(), name="kllm-network-control")

    async def close(self) -> None:
        self.stopping = True
        if self.client_task:
            self.client_task.cancel()
        await asyncio.gather(
            *(session.close() for session in list(self.sessions.values())), return_exceptions=True
        )
        if self.client_task:
            await asyncio.gather(self.client_task, return_exceptions=True)
        self.sessions.clear()
