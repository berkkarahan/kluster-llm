"""Real signed TCP diagnostics on loopback; no physical network qualification."""

import asyncio
import math
import secrets
from uuid import uuid4

import pytest
import pytest_asyncio

from kllm.modules.managed_network.entities import PeerIdentity
from kllm.modules.managed_network.gateways.store import NetworkStore
from kllm.modules.managed_network.interactors.identity import LocalIdentity, encode
from kllm.modules.managed_network.interactors.verification import (
    DedicatedDiagnostics,
    StreamFrame,
    StreamScope,
    stream_block,
)
from kllm.shared.platform.base import PlatformRefusal


def _foreign_address_bindable() -> bool:
    """The foreign-source case needs a second loopback address (Linux has it by default)."""
    import socket

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.2", 0))
        return True
    except OSError:
        return False
    finally:
        probe.close()


@pytest_asyncio.fixture
async def link(tmp_path):
    stores = [NetworkStore(tmp_path / name) for name in ("master", "worker")]
    identities = [
        LocalIdentity(tmp_path / name, name, store)
        for name, store in zip(("master", "worker"), stores)
    ]
    authority = stores[0].startup_authority("master", identities[0].key_id)
    stores[1].accept_authority(authority)
    for index, store in enumerate(stores):
        peer = identities[1 - index]
        store.pair(
            PeerIdentity(
                node_id=peer.node_id,
                key_id=peer.key_id,
                public_key=peer.public_key,
                cluster_binding_id=authority.cluster_binding_id,
                expected_role="worker" if index == 0 else "master",
            )
        )
    network = uuid4()
    diagnostics = [
        DedicatedDiagnostics(
            identity,
            store,
            network,
            1,
            authority,
            "127.0.0.1",
            0,
            {identities[1 - index].node_id: "127.0.0.1"},
        )
        for index, (identity, store) in enumerate(zip(identities, stores))
    ]
    try:
        for diagnostic in diagnostics:
            await diagnostic.start()
        yield diagnostics, identities, stores
    finally:
        for diagnostic in diagnostics:
            await diagnostic.close()
        for store in stores:
            await store.aclose()


def port(diagnostic):
    return diagnostic.server.sockets[0].getsockname()[1]


def request(source, target, **changes):
    return StreamFrame(
        type="stream",
        network_id=source.network_id,
        assignment_revision=target.revision,
        cluster_binding_id=source.authority.cluster_binding_id,
        master_term=source.authority.master_term,
        master_session_id=source.authority.master_session_id,
        sender_node_id=source.identity.node_id,
        target_node_id=target.identity.node_id,
        nonce=encode(secrets.token_bytes(32)),
        **changes,
    )


async def send(source, target, frame, *, source_address="127.0.0.1", signer=None):
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port(target), local_addr=(source_address, 0)
    )
    writer.write(
        (signer or source.identity).sign(frame.model_dump(mode="json"), 8192).encode() + b"\n"
    )
    await writer.drain()
    return reader, writer


async def close_writer(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 1)
    except ConnectionError:
        pass


async def refused(reader):
    try:
        assert await asyncio.wait_for(reader.read(), 2) == b""
    except ConnectionError:
        pass


async def settle(diagnostic):
    async def wait():
        while diagnostic.tasks:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), 2)
    assert diagnostic.streaming == 0


@pytest.mark.asyncio
async def test_signed_stream_measures_complete_payload_and_preserves_echo(link):
    diagnostics, _, _ = link
    source, target = diagnostics
    echo = await source.challenge("worker", 1, port(target))
    measured = await source.measure("worker", 1, port(target))
    assert echo["source"] == measured["source"] == "127.0.0.1"
    assert measured["source_node_id"] == "master" and measured["target_node_id"] == "worker"
    assert measured["method"] == "signed_private_tcp_application_v1"
    assert measured["network_id"] == str(source.network_id)
    assert measured["source_revision"] == measured["target_revision"] == 1
    assert measured["payload_bytes"] == 1048576 and measured["samples"] == 3
    assert len(measured["request_digest"]) == 64
    assert math.isfinite(measured["bandwidth_bytes_s"]) and measured["bandwidth_bytes_s"] > 0
    assert 0 < measured["latency_ms"] < 10000
    assert len(measured["sample_seconds"]) == 3 and all(
        0 < value < 10 for value in measured["sample_seconds"]
    )
    reverse = await target.measure("master", 1, port(source))
    assert reverse["source_node_id"] == "worker"
    await settle(target)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["signature", "source", "revision", "network"])
async def test_stream_refuses_foreign_context_before_bulk(link, failure):
    if failure == "source" and not _foreign_address_bindable():
        pytest.skip("A second loopback source address is unavailable on this host")
    diagnostics, identities, _ = link
    source, target = diagnostics
    frame = request(source, target)
    if failure == "revision":
        frame = frame.model_copy(update={"assignment_revision": 2})
    if failure == "network":
        frame = frame.model_copy(update={"network_id": uuid4()})
    reader, writer = await send(
        source,
        target,
        frame,
        source_address="127.0.0.2" if failure == "source" else "127.0.0.1",
        signer=identities[1] if failure == "signature" else None,
    )
    try:
        await refused(reader)
        await settle(target)
    finally:
        writer.close()
        await close_writer(writer)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["payload", "authority", "key"])
async def test_stream_does_not_acknowledge_corrupt_or_revoked_sample(link, failure):
    diagnostics, _, stores = link
    source, target = diagnostics
    frame = request(source, target)
    reader, writer = await send(source, target, frame)
    try:
        ready = StreamFrame.model_validate(
            LocalIdentity.verify(
                (await reader.readline()).decode().strip(), target.identity.public_key
            )
        )
        assert ready.type == "stream_ready"
        if failure == "authority":
            stores[1].accept_authority(
                target.authority.model_copy(update={"master_term": 2, "master_session_id": uuid4()})
            )
        if failure == "key":
            original = stores[1].peer("master")
            stores[1].pair(
                original.model_copy(
                    update={
                        "public_key": target.identity.public_key,
                        "key_id": target.identity.key_id,
                    }
                ),
                replace=True,
            )
            stores[1].accept_authority(target.authority)
        block = stream_block(frame.nonce, 0) if failure != "payload" else bytes(65536)
        try:
            writer.write(block * 16)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            # The target refused the bulk before this host's socket observed it.
            pass
        await refused(reader)
        await settle(target)
    finally:
        writer.close()
        await close_writer(writer)


@pytest.mark.asyncio
async def test_stream_replay_busy_and_owned_close(link):
    diagnostics, _, _ = link
    source, target = diagnostics
    streams = []
    try:
        for _ in range(2):
            frame = request(source, target)
            reader, writer = await send(source, target, frame)
            assert (await reader.readline()).endswith(b"\n")
            streams.append((reader, writer))
        assert target.streaming == 2
        reader, writer = await send(source, target, request(source, target))
        await refused(reader)
        writer.close()
        await close_writer(writer)
        await target.close()
        assert target.streaming == 0 and not target.tasks
        for reader, _ in streams:
            await refused(reader)
        await target.start()
        assert (await source.challenge("worker", 1, port(target)))["peer"] == "127.0.0.1"
    finally:
        for _, writer in streams:
            writer.close()
            await close_writer(writer)


@pytest.mark.asyncio
async def test_stream_client_cancellation_drains_owned_connection(link, monkeypatch):
    diagnostics, _, _ = link
    source, target = diagnostics
    started = asyncio.Event()

    async def stalled(*args):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(target, "_stream_receive", stalled)
    task = asyncio.create_task(source.measure("worker", 1, port(target)))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await target.close()
    assert not target.tasks and target.streaming == 0


@pytest.mark.asyncio
async def test_stream_old_or_unselected_peer_is_not_measured(link):
    diagnostics, _, _ = link
    source, target = diagnostics
    with pytest.raises(PlatformRefusal):
        await source.measure("other", 1, port(target))
    with pytest.raises(PlatformRefusal):
        await source.measure("worker", 2, port(target))


@pytest.mark.asyncio
async def test_stream_nonce_cannot_be_replayed_after_disconnect(link):
    diagnostics, _, _ = link
    source, target = diagnostics
    frame = request(source, target)
    reader, writer = await send(source, target, frame)
    assert (await asyncio.wait_for(reader.readline(), 2)).endswith(b"\n")
    await close_writer(writer)
    await settle(target)
    reader, writer = await send(source, target, frame)
    try:
        await refused(reader)
    finally:
        await close_writer(writer)


@pytest.mark.asyncio
async def test_stream_scopes_recheck_owner_and_expiry(link):
    diagnostics, identities, _ = link
    source, target = diagnostics
    scope = StreamScope(
        probe_id=uuid4(),
        operation_id=uuid4(),
        contract_digest="a" * 64,
        network_id=source.network_id,
        authority=source.authority,
        source=identities[0].public(source.authority.cluster_binding_id),
        target=identities[1].public(source.authority.cluster_binding_id),
        source_revision=1,
        target_revision=1,
    )
    approved = [True]

    def guard():
        if not approved[0]:
            raise ValueError("preparation cancelled")

    grant = await target.grant_stream(scope, guard)
    assert (await target._scoped_peer(grant, receiving=True)).node_id == "master"
    approved[0] = False
    with pytest.raises(ValueError, match="cancelled"):
        await target._scoped_peer(grant, receiving=True)
    approved[0] = True
    entry = target.stream_scopes[grant]
    target.stream_scopes[grant] = (scope, 0, entry[2])
    with pytest.raises(ValueError, match="expired"):
        await target._scoped_peer(grant, receiving=True)
    with pytest.raises(ValueError, match="another local owner"):
        source.stream_scopes[grant] = entry
        await source._scoped_peer(grant, receiving=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2.0),
        ("ttl_ms", 10000.0),
        ("payload_bytes", 1048576.0),
        ("samples", 3.0),
        ("sample", False),
        ("sample", 1.0),
    ],
)
def test_stream_geometry_refuses_noninteger_values(field, value):
    with pytest.raises(ValueError, match="exact integers"):
        StreamFrame.model_validate({field: value})


@pytest.mark.asyncio
async def test_stream_grant_expiry_retires_only_its_resource_token(link):
    from kllm.shared.runtime_resources import NodeResources
    diagnostics, identities, _ = link
    source, target = diagnostics
    resources = NodeResources()
    lease = resources.acquire(("primacpp", "owned-probe"), "link")
    scope = StreamScope(probe_id=uuid4(), operation_id=uuid4(), contract_digest="a" * 64,
        network_id=source.network_id, authority=source.authority,
        source=identities[0].public(source.authority.cluster_binding_id),
        target=identities[1].public(source.authority.cluster_binding_id), source_revision=1, target_revision=1)
    target.scope_lifetime = 0.05
    grant = await target.grant_stream(scope, lambda: None, release=lease.release)
    with pytest.raises(ValueError, match="resources"):
        resources.acquire(("llamacpp", "other-run"), "rpc_start")
    async def expired():
        while resources.busy:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(expired(), 2)
    assert grant not in target.stream_scopes and grant not in target.scope_cleanup
    later = resources.acquire(("primacpp", "later-probe"), "link")
    await target.revoke_stream(grant)
    assert resources.busy
    later.release()


@pytest.mark.asyncio
async def test_stream_grant_close_cancels_reader_before_resource_release(link):
    from kllm.shared.runtime_resources import NodeResources
    diagnostics, identities, _ = link
    source, target = diagnostics
    resources = NodeResources()
    lease = resources.acquire(("primacpp", "owned-probe"), "link")
    scope = StreamScope(probe_id=uuid4(), operation_id=uuid4(), contract_digest="a" * 64,
        network_id=source.network_id, authority=source.authority,
        source=identities[0].public(source.authority.cluster_binding_id),
        target=identities[1].public(source.authority.cluster_binding_id), source_revision=1, target_revision=1)
    grant = await target.grant_stream(scope, lambda: None, release=lease.release)
    frame = request(source, target).model_copy(update={"grant_id": grant})
    reader, writer = await send(source, target, frame)
    try:
        assert (await asyncio.wait_for(reader.readline(), 2)).endswith(b"\n")
        assert target.streaming == 1 and resources.busy
        await target.close()
        assert target.streaming == 0 and not target.tasks and not resources.busy
        await refused(reader)
    finally:
        await close_writer(writer)
