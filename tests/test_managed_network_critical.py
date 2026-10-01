"""Critical refusal/replay and recovery checks; no host configuration is changed."""
from datetime import timedelta
from uuid import uuid4

import pytest

from kllm.managed_network.models import ManagedNetwork, PeerIdentity
from kllm.managed_network.store import NetworkStore, StoreConflict, digest


def test_signed_authority_replay_and_changed_identity(tmp_path):
    from kllm.managed_network.identity import ChannelFence, IdentityError, LocalIdentity, SignedCommand

    master_store = NetworkStore(tmp_path / 'master')
    worker_store = NetworkStore(tmp_path / 'worker')
    master = LocalIdentity(tmp_path / 'master', 'master', master_store)
    worker = LocalIdentity(tmp_path / 'worker', 'worker', worker_store)
    authority = master_store.startup_authority(master.node_id, master.key_id)
    worker_store.pair(PeerIdentity(node_id='master', key_id=master.key_id, public_key=master.public_key, cluster_binding_id=authority.cluster_binding_id, expected_role='master'))
    channel = uuid4()
    authority.accepted_channel_id = channel
    worker_store.accept_authority(authority)
    fence = ChannelFence(worker_store, worker_store.peer('master'), authority, 'master', 'worker')
    command = SignedCommand(version=1, type='command', **authority.model_dump(exclude={'accepted_channel_id'}), channel_id=channel, sender_node_id='master', target_node_id='worker', sequence=1, network_id=uuid4(), operation_id=uuid4(), operation_revision=1, phase_sequence=1, phase='inspect', assignment_revision=1, expected_local_revision=0, ttl_ms=5000, body={})
    wire = master.sign(command.model_dump(mode='json'))
    assert fence.receive(wire).sequence == 1
    with pytest.raises(IdentityError):
        fence.receive(wire)
    with pytest.raises(IdentityError):
        worker.verify(wire, worker.public_key)
    with pytest.raises(IdentityError):
        master.verify('{"payload":"{}","payload":"{}","signature":"a"}', master.public_key)
    with pytest.raises(StoreConflict):
        worker_store.accept_authority(authority.model_copy(update={'master_session_id': uuid4()}))
    newer = authority.model_copy(update={'master_term': authority.master_term + 1, 'master_session_id': uuid4(), 'accepted_channel_id': uuid4()})
    worker_store.accept_authority(newer)
    with pytest.raises(StoreConflict):
        fence.check_live()
    master.path.unlink()
    with pytest.raises(IdentityError):
        LocalIdentity(tmp_path / 'master', 'master', master_store)
    master_store.close()
    worker_store.close()


def test_durable_idempotency_and_restart_no_stale_ready(tmp_path):
    from kllm.managed_network.models import LocalMutationJournal, NetworkOperation, RunNetworkReservation, utc_now

    store = NetworkStore(tmp_path)
    binding = store.cluster_binding()
    authority = store.startup_authority('master', 'a' * 64)
    network = ManagedNetwork(network_id=uuid4(), cluster_binding_id=binding, master_node_id='master', master_key_id='a' * 64, subnet='192.168.234.0/24')
    store.put_network(network)
    operation = NetworkOperation(operation_id=uuid4(), request_id=uuid4(), network_id=network.network_id, cluster_binding_id=binding, action='start', master_term=authority.master_term, master_session_id=authority.master_session_id, expected_network_revision=0, operation_revision=1, participants=['master', 'worker'], proposal_digest=digest({'start': 1}), phase='preparing', deadline=utc_now() + timedelta(seconds=60))
    store.put_operation(operation)
    assert store.begin_command('apply-once', 'b' * 64) == (True, None)
    with pytest.raises(StoreConflict):
        store.begin_command('apply-once', 'b' * 64)
    store.finish_command('apply-once', {'status': 'configured'})
    assert store.begin_command('apply-once', 'b' * 64) == (False, {'status': 'configured'})
    with pytest.raises(StoreConflict):
        store.begin_command('apply-once', 'c' * 64)
    journal = LocalMutationJournal(journal_id=uuid4(), network_id=network.network_id, operation_id=operation.operation_id, node_id='master', adapter_fingerprint='d' * 64, exact_address_prefix='192.168.234.1/24', boot_id='boot', observation_epoch=uuid4(), event_high_water=1, observer_health='continuous', pre_snapshot={}, intended_delta={'add': '192.168.234.1/24'}, phase='applied_observed', authority_fence=authority)
    store.journal(journal)
    for state in ('applying', 'verifying', 'ready'):
        before = network.revision
        network.observed_state = state
        network.revision += 1
        store.put_network(network, expected_revision=before)
    run = RunNetworkReservation(run_id=uuid4(), network_id=network.network_id, assignment_revisions={'worker': 1}, logical_endpoints=['192.168.234.2:50052'], launch_endpoints=['127.0.0.1:34567'], relay_instance_ids=[uuid4()], state='reserved')
    store.reservation(run)
    store.close()
    store = NetworkStore(tmp_path)
    store.recover()
    assert store.network(network.network_id).observed_state == 'degraded'
    assert store.journals()[0].phase == 'ownership_conflict'
    assert store.runs(network.network_id)[0].state == 'uncertain'
    store.close()
