"""Same-key master rotation recovery for the mutual join-secret bootstrap.

A rebuilt or restored master store can present the same signing key under a
new node label or cluster binding. The worker's pinned peer then diverges, and
the re-assert bootstrap must adopt the current identity instead of looping on
a refusal. A changed signing key remains an identity swap that requires
explicit local re-pairing.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from kllm.modules.managed_network.router_http import build_router
from kllm.modules.managed_network.interactors.service import ManagedNetworkService
from kllm.modules.managed_network.interactors import bootstrap as bootstrap_module
from kllm.modules.managed_network.interactors.identity import IdentityError
from kllm.modules.managed_network.entities import ManagedNetwork, PeerIdentity


def service(tmp_path, role="master", node_id="master"):
    state = SimpleNamespace(
        role=role,
        node_id=node_id,
        join_key="isolated-join-secret",
        members={"master": {"host": "192.168.68.1"}, "worker": {"host": "192.168.68.2"}},
        master_host=None if role == "master" else "192.168.68.1",
    )
    return ManagedNetworkService(
        tmp_path / node_id, lambda: state, lambda: "http://192.168.68.1:18765"
    )


def master_transport(monkeypatch, master):
    """Route the worker's bootstrap HTTP client into the master ASGI app."""
    app = FastAPI()
    app.include_router(build_router(lambda: master))
    transport = httpx.ASGITransport(app=app)
    monkeypatch.setattr(
        bootstrap_module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        ),
    )


def pinned_master(master, *, node_id="master", key_from=None, binding=None):
    identity = key_from or master.identity
    return PeerIdentity(
        node_id=node_id,
        key_id=identity.key_id,
        public_key=identity.public_key,
        cluster_binding_id=binding or uuid4(),
        expected_role="master",
    )


@pytest.mark.parametrize("stale_node_id", ["master", "master-rebuilt"])
def test_same_key_master_rotation_is_adopted(tmp_path, monkeypatch, stale_node_id):
    master, worker = service(tmp_path), service(tmp_path, "worker", "worker")
    try:
        worker.store.pair(pinned_master(master, node_id=stale_node_id))
        master_transport(monkeypatch, master)

        asyncio.run(worker.bootstrap.connect())

        adopted = worker.store.peer("master")
        assert adopted is not None
        assert adopted.cluster_binding_id == master.store.cluster_binding()
        # A rotated label must not leave the superseded master pin behind.
        assert [peer.node_id for peer in worker.store.peers()] == ["master"]
        assert master.store.peer("worker") is not None
    finally:
        master.store.close()
        worker.store.close()


def test_changed_master_key_still_requires_explicit_pairing(tmp_path, monkeypatch):
    master, worker = service(tmp_path), service(tmp_path, "worker", "worker")
    replacement = service(tmp_path / "replacement", "master", "master")
    try:
        stale = pinned_master(master, key_from=replacement.identity)
        worker.store.pair(stale)
        master_transport(monkeypatch, master)

        with pytest.raises(IdentityError, match="key changed"):
            asyncio.run(worker.bootstrap.connect())

        assert worker.store.peer("master") == stale
        assert master.store.peer("worker") is None
    finally:
        master.store.close()
        worker.store.close()
        replacement.store.close()


def test_same_key_rotation_adopts_with_live_managed_network(tmp_path, monkeypatch):
    master, worker = service(tmp_path), service(tmp_path, "worker", "worker")
    try:
        stale = pinned_master(master)
        worker.store.pair(stale)
        network = ManagedNetwork(
            network_id=uuid4(),
            cluster_binding_id=master.store.cluster_binding(),
            master_node_id="master",
            master_key_id=master.identity.key_id,
            subnet="10.10.10.0/24",
        )
        worker.store.put_network(network)
        master_transport(monkeypatch, master)

        asyncio.run(worker.bootstrap.connect())

        adopted = worker.store.peer("master")
        assert adopted is not None
        assert adopted.cluster_binding_id == master.store.cluster_binding()
        # A same-key rotation must not discard owned network state.
        assert worker.store.network(network.network_id) is not None
        assert master.store.peer("worker") is not None
    finally:
        master.store.close()
        worker.store.close()


def test_changed_master_key_with_live_network_still_refused(tmp_path, monkeypatch):
    master, worker = service(tmp_path), service(tmp_path, "worker", "worker")
    replacement = service(tmp_path / "replacement", "master", "master")
    try:
        stale = pinned_master(master, key_from=replacement.identity)
        worker.store.pair(stale)
        worker.store.put_network(
            ManagedNetwork(
                network_id=uuid4(),
                cluster_binding_id=master.store.cluster_binding(),
                master_node_id="master",
                master_key_id=replacement.identity.key_id,
                subnet="10.10.10.0/24",
            )
        )
        master_transport(monkeypatch, master)

        with pytest.raises(IdentityError, match="key changed"):
            asyncio.run(worker.bootstrap.connect())

        assert worker.store.peer("master") == stale
        assert master.store.peer("worker") is None
    finally:
        master.store.close()
        worker.store.close()
        replacement.store.close()
