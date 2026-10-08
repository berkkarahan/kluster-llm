"""Shared node network directory: durable identity used by both views."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.agent.services.managed_network import ManagedNetworkService
from kllm.managed_network.models import ManagedNetwork, NodeAssignment, NodeNetworkIdentity
from kllm.managed_network.store import NetworkStore


def identity(node_id: str = 'worker', **overrides) -> NodeNetworkIdentity:
    return NodeNetworkIdentity(node_id=node_id, **overrides)


def make_service(tmp_path, role: str = 'master', node_id: str = 'master'):
    state = SimpleNamespace(
        role=role,
        node_id=node_id,
        members={
            'master': {'role': 'master', 'host': '192.168.1.10', 'lan_host': '192.168.1.10', 'rpc_bind': '192.168.1.10'},
            'worker': {'role': 'worker', 'host': '192.168.1.20', 'lan_host': '192.168.1.20',
                       'rpc_bind': '192.168.1.20', 'hostname': 'gpu-box'},
        },
    )
    return ManagedNetworkService(tmp_path / node_id, lambda: state, lambda: 'http://192.168.1.10:18765'), state


def test_directory_rows_round_trip_replace_and_remove(tmp_path):
    store = NetworkStore(tmp_path)
    try:
        store.put_directory(identity(private_address='10.10.10.4', private_prefix_length=24, hostname='gpu-box'))
        row = {item.node_id: item for item in store.directory()}['worker']
        assert (row.hostname, row.private_address, row.private_prefix_length) == ('gpu-box', '10.10.10.4', 24)

        store.put_directory_many([identity('master', hostname='control')], replace=True)
        assert [item.node_id for item in store.directory()] == ['master']

        store.remove_directory('master')
        assert store.directory() == []
    finally:
        store.close()


def test_directory_rejects_unsafe_identity_values(tmp_path):
    store = NetworkStore(tmp_path)
    try:
        with pytest.raises(ValueError):
            identity(hostname='bad name')
        with pytest.raises(ValueError):
            identity(private_address='8.8.8.8', private_prefix_length=24)
        with pytest.raises(ValueError):
            identity(private_address='10.10.10.4', private_prefix_length=31)
    finally:
        store.close()


def test_sync_directory_combines_membership_with_private_assignment(tmp_path):
    service, _ = make_service(tmp_path)
    try:
        network = ManagedNetwork(
            network_id=uuid4(),
            cluster_binding_id=service.store.cluster_binding(),
            master_node_id='master',
            master_key_id=service.identity.key_id,
            subnet='10.10.10.0/24',
        )
        service.store.put_network(network)
        assignment = NodeAssignment(
            network_id=network.network_id, node_id='worker', adapter_id='eth1', os_interface='eth1',
            identity_fingerprint='a' * 64, address='10.10.10.4', prefix_length=24,
            state='ready', link_speed_mbps=1000,
        )
        service.store.put_assignment(assignment)

        service.sync_directory()
        rows = {item.node_id: item for item in service.store.directory()}
        worker = rows['worker']
        assert (worker.hostname, worker.private_address, worker.assignment_state, worker.link_speed_mbps) == (
            'gpu-box', '10.10.10.4', 'ready', 1000)
        assert worker.rpc_bind == '192.168.1.20'
        assert rows['master'].private_address is None

        # A stable identity must not rewrite updated_at on every heartbeat.
        before = {node: item.updated_at for node, item in rows.items()}
        service.sync_directory()
        assert {item.node_id: item.updated_at for item in service.store.directory()} == before

        assignment.state = 'stopped'
        service.store.put_assignment(assignment, expected_revision=1)
        service.sync_directory()
        stopped = {item.node_id: item for item in service.store.directory()}['worker']
        assert stopped.private_address is None
        assert stopped.rpc_bind == '192.168.1.20'
    finally:
        service.store.close()


def test_worker_sync_is_a_noop(tmp_path):
    service, _ = make_service(tmp_path, role='worker', node_id='worker')
    try:
        service.sync_directory()
        assert service.store.directory() == []
    finally:
        service.store.close()


def test_worker_mirrors_directory_snapshot(tmp_path, monkeypatch):
    import kllm.agent as agent_mod

    store = NetworkStore(tmp_path)
    try:
        monkeypatch.setattr(agent_mod.app.state, 'managed_network', SimpleNamespace(store=store), raising=False)
        agent_mod._apply_directory_snapshot([
            identity('worker', hostname='gpu-box', private_address='10.10.10.4', private_prefix_length=24).model_dump(mode='json'),
            {'node_id': '../escaping'},
        ])
        rows = {item.node_id: item for item in store.directory()}
        assert rows['worker'].hostname == 'gpu-box'
        assert '../escaping' not in rows

        agent_mod._apply_directory_snapshot([])
        assert store.directory() == []
    finally:
        store.close()
