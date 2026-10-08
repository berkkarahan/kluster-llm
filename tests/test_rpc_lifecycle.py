"""Observable RPC ownership and signed-channel lifecycle regression checks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from kllm.agent.schemas import JoinBody
from kllm.agent.services.cluster_join import ClusterJoinDependencies, ClusterJoinService
from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.agent.services.rpc_runs import RpcRunCoordinator, remap_rpc_plan
from kllm.agent.services.worker_rpc import WorkerRpcService
from kllm.core.gpu_selection import GpuSelection
from kllm.managed_network.identity import SignedRuntimeCommand
from kllm.managed_network.platform.base import PlatformRefusal
from kllm.run_settings import RunBody
from kllm.state import ClusterState

CPU = [{'index': 0, 'device': 'CPU', 'backend': 'cpu', 'memory_kind': 'ram'}]


def command(run_id=None, action='start', **values):
    return SignedRuntimeCommand(
        cluster_binding_id=uuid4(), master_node_id='master', master_key_id='a' * 64,
        master_term=1, master_session_id=uuid4(), version=1, type='rpc_command',
        channel_id=uuid4(), sender_node_id='master', target_node_id='worker',
        sequence=1, request_id=uuid4(), run_id=run_id or uuid4(), action=action,
        ttl_ms=10000, body={}, **values,
    )


@pytest.fixture
def worker(monkeypatch, tmp_path):
    monkeypatch.setenv('KLLM_HOME', str(tmp_path))
    monkeypatch.setattr('kllm.agent.services.worker_rpc.netsec.ensure_rpc_firewall', lambda *_: {})
    state = ClusterState(role='worker', node_id='worker', master_host='10.0.0.1', join_key='test', rpc_bind='10.0.0.2')
    state.members['worker'] = {'rpc_capabilities': CPU, 'rpc_status': 'idle'}
    state.save = lambda: None
    live, tokens, calls, clock = {}, {}, [], [10.0]

    def start(_state):
        pid = 100 + len(tokens)
        live[pid], tokens[pid] = True, f'token-{pid}'
        state.process_pid, state.process_role = pid, 'worker'
        state.members['worker']['rpc_devices'] = CPU
        calls.append(('start', pid))
        return {'pid': pid}

    def stop(pid, **_):
        live[pid] = False
        calls.append(('stop', pid))

    service = WorkerRpcService(
        lambda: state, lambda: SimpleNamespace(start_commanded_worker=start),
        alive=lambda pid: live.get(pid, False), token=lambda pid: tokens[pid], stop=stop,
        clock=lambda: clock[0], listener=lambda *_: True,
    )
    return SimpleNamespace(service=service, state=state, live=live, tokens=tokens, calls=calls, clock=clock)


def _retry_state(monkeypatch, tmp_path):
    monkeypatch.setenv('KLLM_HOME', str(tmp_path))
    monkeypatch.setattr('kllm.agent.services.worker_rpc.netsec.ensure_rpc_firewall', lambda *_: {})
    state = ClusterState(role='worker', node_id='worker', master_host='10.0.0.1', join_key='test', rpc_bind='10.0.0.2')
    state.members['worker'] = {'rpc_capabilities': CPU, 'rpc_status': 'idle'}
    state.save = lambda: None
    return state


def _retry_service(state, outcomes, *, clock, sleep, listener=None, before_start=None):
    calls, live, tokens = [], {}, {}

    def start(_state):
        if before_start is not None:
            before_start()
        value = outcomes.pop(0)
        calls.append(value.get('pid', 0))
        if value.get('error'):
            return value
        pid = value['pid']
        live[pid], tokens[pid] = True, f'token-{pid}'
        state.process_pid, state.process_role = pid, 'worker'
        state.members['worker']['rpc_devices'] = CPU
        return value

    service = WorkerRpcService(
        lambda: state, lambda: SimpleNamespace(start_commanded_worker=start),
        alive=lambda pid: live.get(pid, False), token=lambda pid: tokens[pid],
        stop=lambda pid, **_: live.__setitem__(pid, False),
        clock=lambda: clock[0], sleep=sleep, listener=listener or (lambda *_: True),
    )
    return SimpleNamespace(service=service, calls=calls, live=live, tokens=tokens)


def test_transient_start_failure_is_retried(monkeypatch, tmp_path):
    state = _retry_state(monkeypatch, tmp_path)
    clock, sleeps = [10.0], []
    retry = _retry_service(state, [{'error': 'probe timed out'}, {'pid': 101}], clock=clock, sleep=sleeps.append)
    result = retry.service.execute(command(), 60)
    assert result['status'] == 'serving' and result['pid'] == 101
    assert retry.calls == [0, 101] and sleeps == [1.0]
    assert state.members['worker']['rpc_status'] == 'serving'


def test_start_retries_are_bounded(monkeypatch, tmp_path):
    state = _retry_state(monkeypatch, tmp_path)
    clock, sleeps = [10.0], []
    retry = _retry_service(state, [{'error': 'probe timed out'}] * 3, clock=clock, sleep=sleeps.append)
    with pytest.raises(PlatformRefusal, match='after 3 attempt'):
        retry.service.execute(command(), 60)
    assert retry.calls == [0, 0, 0] and sleeps == [1.0, 2.0]
    assert state.members['worker']['rpc_status'] == 'failed'
    assert 'probe timed out' in state.members['worker']['rpc_error']


def test_retry_stops_when_deadline_budget_is_small(monkeypatch, tmp_path):
    state = _retry_state(monkeypatch, tmp_path)
    clock, sleeps = [10.0], []
    retry = _retry_service(
        state, [{'error': 'probe timed out'}, {'pid': 101}],
        clock=clock, sleep=sleeps.append, before_start=lambda: clock.__setitem__(0, 40.0),
    )
    with pytest.raises(PlatformRefusal, match='after 1 attempt'):
        retry.service.execute(command(), 50)
    assert retry.calls == [0] and sleeps == []


def test_listener_failure_restarts_rpc_server(monkeypatch, tmp_path):
    state = _retry_state(monkeypatch, tmp_path)
    clock, sleeps = [10.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    retry = _retry_service(
        state, [{'pid': 101}, {'pid': 102}],
        clock=clock, sleep=sleep, listener=lambda pid, host, port: pid == 102,
    )
    result = retry.service.execute(command(), 60)
    assert result['pid'] == 102
    assert retry.calls == [101, 102]
    assert retry.live[101] is False
    assert 1.0 in sleeps


def test_non_transient_refusal_is_not_retried(monkeypatch, tmp_path):
    state = _retry_state(monkeypatch, tmp_path)
    clock, calls = [10.0], []

    def start(_state):
        calls.append('start')
        raise PlatformRefusal('RPC_BUSY', 'RPC belongs to another run')

    service = WorkerRpcService(
        lambda: state, lambda: SimpleNamespace(start_commanded_worker=start),
        alive=lambda *_: True, token=lambda *_: 'token',
        stop=lambda *_, **__: None, clock=lambda: clock[0], sleep=lambda _: None,
    )
    with pytest.raises(PlatformRefusal, match='another run'):
        service.execute(command(), 60)
    assert calls == ['start']


def test_unexpected_start_error_is_not_retried(monkeypatch, tmp_path):
    state = _retry_state(monkeypatch, tmp_path)
    clock, calls = [10.0], []

    def start(_state):
        calls.append('start')
        raise OSError('spawn failed')

    service = WorkerRpcService(
        lambda: state, lambda: SimpleNamespace(start_commanded_worker=start),
        alive=lambda *_: True, token=lambda *_: 'token',
        stop=lambda *_, **__: None, clock=lambda: clock[0], sleep=lambda _: None,
    )
    with pytest.raises(OSError, match='spawn failed'):
        service.execute(command(), 60)
    assert calls == ['start']


def test_join_enrolls_without_runtime_side_effects():
    state = ClusterState(node_id='worker')
    state.save = lambda: None
    calls = []
    service = ClusterJoinService(ClusterJoinDependencies(
        state=lambda: state,
        gpu_selection=lambda _: ResolvedGpuSelection(GpuSelection((), 'cpu', None), ()),
        probe_capacity=lambda: {'backend': 'cpu'}, pick_rpc_bind_host=lambda *_, **__: '10.0.0.2',
        ensure_master_control_route=lambda *_: [], load_or_create_keypair=lambda: ('private', 'public'),
        software_identity=lambda: {}, build_register_payload=lambda **kw: kw,
        post_register=lambda *_: {}, drop_cp_proxy=lambda: None, apply_cluster_snapshot=lambda _: None,
        worker_bring_up_wireguard=lambda *_: '10.66.87.2',
        post_worker_heartbeat=lambda: calls.append('heartbeat'),
    ))
    result = service.join(JoinBody(key='test', master_host='10.0.0.1'))
    assert result['launch'] == {'pid': None, 'deferred': True}
    assert state.role == 'worker' and state.process_pid is None
    assert state.members['worker']['rpc_capabilities'] == CPU
    assert calls == ['heartbeat']


def test_duplicate_start_and_stale_stop_respect_run_ownership(worker):
    start = command()
    first = worker.service.execute(start, 20)
    assert worker.service.execute(start, 20)['pid'] == first['pid']
    stale = start.model_copy(update={'run_id': uuid4(), 'action': 'stop'})
    with pytest.raises(PlatformRefusal, match='another run'):
        worker.service.execute(stale, 20)
    assert worker.live[first['pid']]
    worker.service.execute(start.model_copy(update={'action': 'stop'}), 20)
    assert not worker.live[first['pid']]
    assert worker.state.role == 'worker'
    assert worker.state.members['worker']['rpc_capabilities'] == CPU
    assert worker.calls == [('start', first['pid']), ('stop', first['pid'])]


def test_expired_lease_stops_without_restarting(worker):
    worker.service.execute(command(), 20)
    worker.clock[0] = 26
    worker.service.expire()
    worker.service.expire()
    assert worker.state.process_pid is None
    assert [name for name, _ in worker.calls] == ['start', 'stop']


def test_renewal_requires_a_live_process_and_current_run(worker):
    start = command()
    result = worker.service.execute(start, 20)
    worker.clock[0] = 20
    assert worker.service.execute(start.model_copy(update={'action': 'renew'}), 30)['status'] == 'serving'
    worker.clock[0] = 26
    worker.service.expire()
    assert worker.live[result['pid']]
    worker.live[result['pid']] = False
    assert worker.service.execute(start.model_copy(update={'action': 'renew'}), 30)['status'] == 'failed'
    assert len([c for c in worker.calls if c[0] == 'start']) == 1


def test_reused_pid_is_not_killed_by_old_ownership(worker):
    result = worker.service.execute(command(), 20)
    worker.tokens[result['pid']] = 'a-new-process'
    worker.clock[0] = 26
    worker.service.expire()
    assert worker.live[result['pid']]
    assert [name for name, _ in worker.calls] == ['start']


def test_restart_releases_saved_ownership_without_spawning(worker):
    worker.service.execute(command(), 20)
    worker.service.reconcile_startup()
    assert worker.state.process_pid is None
    assert [name for name, _ in worker.calls] == ['start', 'stop']


def test_runtime_commands_reject_expiry_and_arguments(worker):
    with pytest.raises(PlatformRefusal, match='expired'):
        worker.service.execute(command(), 9)
    with pytest.raises(PlatformRefusal, match='enrolled local configuration'):
        worker.service.execute(command().model_copy(update={'body': {'host': '1.1.1.1'}}), 20)
    assert worker.calls == []


def test_rpc_endpoint_remapping_preserves_allocations():
    before = {'worker': {'role': 'worker', 'rpc_bind': '10.2.0.2', 'rpc_port': 50052}}
    after = {'worker': {'role': 'worker', 'rpc_bind': '127.0.0.1', 'rpc_port': 33001}}
    plan = {'rpc_endpoints': ['10.2.0.2:50052'], 'device_order': ['RPC[10.2.0.2:50052]/0'], 'tensor_split': [3, 1]}
    result = remap_rpc_plan(plan, before, after)
    assert result['rpc_endpoints'] == ['127.0.0.1:33001']
    assert result['device_order'] == ['RPC[127.0.0.1:33001]/0']
    assert result['tensor_split'] == plan['tensor_split']
    assert plan['rpc_endpoints'] == ['10.2.0.2:50052']


@pytest.fixture
def coordinator(monkeypatch):
    from kllm import native
    state = ClusterState(role='master', node_id='master')
    state.save = lambda: None
    state.members = {'master': {'role': 'master', 'host': '10.0.0.1'}, **{
        node: {'role': 'worker', 'rpc_bind': host, 'rpc_port': 50052, 'rpc_control_version': 1, 'rpc_capabilities': CPU}
        for node, host in (('selected', '10.0.0.2'), ('idle', '10.0.0.3'))
    }}
    calls, metadata, ttls = [], {}, []
    failures = {}

    class Session:
        def __init__(self, node):
            self.node = node

        async def request_runtime(self, run_id, action, *, ttl_ms=10000, body=None):
            calls.append((self.node, action, run_id))
            ttls.append((self.node, action, ttl_ms))
            if failures.get((self.node, action)):
                return {'status': 'failed', 'message': 'controlled failure'}
            return {'status': 'idle' if action == 'stop' else 'serving', 'pid': 101, 'rpc_devices': CPU}

    async def released(**_):
        pass

    service = SimpleNamespace(
        state=lambda: state,
        store=SimpleNamespace(metadata=lambda key: metadata.get(key), set_metadata=lambda key, value: metadata.update({key: value})),
        channels=SimpleNamespace(sessions={node: Session(node) for node in ('selected', 'idle')}),
        admission=SimpleNamespace(call=asyncio.run, release_stopped=released),
    )
    monkeypatch.setattr(native, 'require_native', lambda: SimpleNamespace(
        _process_identity=lambda pid: ('master-token', 'now'),
        process_alive=lambda pid: state.process_pid == pid,
    ))

    def stop():
        calls.append(('master', 'stop', state.process_pid))
        state.process_pid = None

    plan = {'rpc_endpoints': ['10.0.0.2:50052']}
    controller = RpcRunCoordinator(service, None, stop)

    def run(body):
        with controller.context(body, plan, state.members):
            state.process_pid = 200
        return {'ok': True}

    controller.run = run
    return SimpleNamespace(controller=controller, state=state, calls=calls, failures=failures, plan=plan, ttls=ttls)


def test_master_start_waits_for_worker_retries(coordinator):
    coordinator.controller.launch(RunBody(model='controlled.gguf'))
    assert ('selected', 'start', 60000) in coordinator.ttls
    assert all(ttl == 10000 for _, action, ttl in coordinator.ttls if action != 'start')


def test_master_starts_only_allocated_workers_and_stops_them_with_model(coordinator):
    c = coordinator
    c.controller.launch(RunBody(model='controlled.gguf'))
    assert [node for node, action, _ in c.calls if action == 'start'] == ['selected']
    assert c.state.members['idle'].get('process_pid') is None
    c.controller.stop(lambda: c.controller.stop_master() or {'ok': True})
    assert c.controller.phase == 'idle'
    assert c.state.members['selected']['rpc_status'] == 'idle'
    assert [node for node, action, _ in c.calls if action == 'stop' and node != 'master'] == ['selected']


def test_stop_drops_rpc_ownership_when_the_worker_has_left(coordinator):
    c = coordinator
    c.controller.run_id = uuid4()
    c.controller.workers = ['departed']
    c.controller.phase = 'cleanup_pending'
    assert 'departed' not in c.state.members
    assert 'departed' not in c.controller.service.channels.sessions

    assert c.controller.stop(lambda: {'ok': True}) == {'ok': True}

    assert c.controller.workers == []
    assert c.controller.phase == 'idle'
    assert c.controller.run_id is None


def test_stop_keeps_cleanup_pending_while_an_enrolled_worker_is_disconnected(coordinator):
    c = coordinator
    c.controller.service.channels.sessions.pop('selected')
    c.controller.run_id = uuid4()
    c.controller.workers = ['selected']
    c.controller.phase = 'cleanup_pending'

    with pytest.raises(HTTPException, match='RPC cleanup pending on selected'):
        c.controller.stop(lambda: {'ok': True})

    assert c.controller.workers == ['selected']
    assert c.controller.phase == 'cleanup_pending'
    assert c.controller.run_id is not None


def test_stop_keeps_cleanup_pending_when_the_worker_process_does_not_stop(coordinator):
    c = coordinator
    c.failures[('selected', 'stop')] = True
    c.controller.launch(RunBody(model='controlled.gguf'))

    with pytest.raises(HTTPException, match='RPC cleanup pending on selected'):
        c.controller.stop(lambda: c.controller.stop_master() or {'ok': True})

    assert c.controller.workers == ['selected']
    assert c.controller.phase == 'cleanup_pending'


def test_failed_rpc_start_releases_all_attempted_owners(coordinator):
    c = coordinator
    c.failures[('selected', 'start')] = True
    with pytest.raises(Exception, match='controlled failure'):
        c.controller.launch(RunBody(model='controlled.gguf'))
    assert c.controller.phase == 'failed'
    assert any(node == 'selected' and action == 'stop' for node, action, _ in c.calls)
    assert c.state.process_pid is None


def test_unsupported_worker_fails_before_stopping_current_model(coordinator):
    c = coordinator
    c.state.members['selected']['rpc_control_version'] = 0
    c.state.process_pid = 77
    with pytest.raises(Exception, match='upgrade'):
        c.controller.launch(RunBody(model='controlled.gguf'))
    assert c.calls == [] and c.state.process_pid == 77


def test_recovery_has_three_bounded_attempts_and_stop_cancels_it(coordinator):
    c = coordinator
    c.controller.launch(RunBody(model='controlled.gguf'))
    delays, attempts = [], []

    class Cancellation:
        cancelled = False
        def wait(self, delay):
            delays.append(delay)
            return self.cancelled
        def is_set(self):
            return self.cancelled
        def set(self):
            self.cancelled = True

    c.controller.cancel = Cancellation()
    c.controller.run = lambda body: attempts.append(body.model) or (_ for _ in ()).throw(RuntimeError('reload failed'))
    c.controller._recover(c.controller.generation)
    assert delays == [1, 2, 4] and len(attempts) == 3
    assert c.controller.phase == 'failed' and '3 attempts' in c.state.apply_error
    c.controller.cancel.set()
    c.controller._recover(c.controller.generation)
    assert len(attempts) == 3


def test_successful_recovery_reloads_saved_settings(coordinator):
    c = coordinator
    body = RunBody(model='controlled.gguf', context=2048)
    c.controller.launch(body)
    c.controller.cancel.wait = lambda _: False
    c.controller._recover(c.controller.generation)
    assert c.controller.phase == 'serving'
    assert c.controller.body.context == 2048
    assert len([x for x in c.calls if x[1] == 'start']) == 2


def test_recovery_keeps_original_runtime_protected_until_explicit_stop(coordinator, tmp_path):
    from kllm.runtimes.registry import RuntimeRegistry
    from test_runtime_versions import version, prepared
    c = coordinator
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    try:
        original = version()
        value = prepared(registry, original)
        registry.running(value)
        c.controller.service.runtimes = SimpleNamespace(registry=registry)
        c.controller.runtime_snapshot = value
        c.controller.body = RunBody(model='controlled.gguf')
        c.controller.phase = 'recovering'
        c.controller.stop_current()
        with pytest.raises(ValueError, match='cannot be removed'):
            registry.remove(original.runtime_id)
        c.controller.stop(lambda: {'ok': True})
        registry.remove(original.runtime_id)
        assert registry.snapshot()['running'] is None
    finally:
        registry.close()


@pytest.mark.asyncio
async def test_real_signed_websocket_commands_control_an_owned_child(tmp_path, monkeypatch):
    """Real sockets, signatures and child lifetime; no host adapter mutation."""
    import socket
    import subprocess
    import sys
    import psutil
    import websockets

    from kllm.agent.services.network_channel import ChannelSession, SocketWire
    from kllm.managed_network.identity import ChannelFence, LocalIdentity
    from kllm.managed_network.models import PeerIdentity
    from kllm.managed_network.store import NetworkStore

    monkeypatch.setenv('KLLM_HOME', str(tmp_path))
    monkeypatch.setattr('kllm.agent.services.worker_rpc.netsec.ensure_rpc_firewall', lambda *_: {})
    stores = [NetworkStore(tmp_path / name) for name in ('master', 'worker')]
    identities = [LocalIdentity(tmp_path / name, name, store) for name, store in zip(('master', 'worker'), stores)]
    master, local = identities
    authority = stores[0].startup_authority('master', master.key_id).model_copy(update={'accepted_channel_id': uuid4()})
    peers = [PeerIdentity(**identity.public(authority.cluster_binding_id).model_dump(), expected_role=role) for identity, role in zip(identities, ('master', 'worker'))]
    for index in (0, 1):
        stores[index].pair(peers[1 - index])
        stores[index].accept_authority(authority, peers[1 - index].node_id)
    state = ClusterState(role='worker', node_id='worker', join_key='isolated', master_host='127.0.0.1', rpc_bind='127.0.0.1')
    state.save = lambda: None
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        state.rpc_port = reserved.getsockname()[1]
    state.members['worker'] = {'rpc_capabilities': CPU}
    children = []

    def start(_):
        # The venv launcher may be a trampoline process; the listening socket
        # belongs to the base interpreter it execs, so spawn that directly.
        interpreter = getattr(sys, '_base_executable', None) or sys.executable
        child = subprocess.Popen([interpreter, '-c', 'import socket,time,sys;s=socket.socket();s.bind(("127.0.0.1",int(sys.argv[1])));s.listen();time.sleep(60)', str(state.rpc_port)])
        children.append(child)
        state.process_pid, state.process_role = child.pid, 'worker'
        state.members['worker']['rpc_devices'] = CPU
        return {'pid': child.pid}

    def alive(pid):
        return any(c.pid == pid and c.poll() is None for c in children)

    def stop(pid, **_):
        child = next(c for c in children if c.pid == pid)
        child.terminate()
        child.wait(timeout=3)

    worker = WorkerRpcService(lambda: state, lambda: SimpleNamespace(start_commanded_worker=start), alive=alive, token=lambda pid: str(psutil.Process(pid).create_time()), stop=stop)
    master_session = asyncio.get_running_loop().create_future()

    async def serve(socket):
        session = ChannelSession(SocketWire(socket), master, ChannelFence(stores[0], peers[1], authority, 'worker', 'master'), master=True)
        master_session.set_result(session)
        await session.pump()

    async def handle(value, deadline):
        try:
            return await asyncio.to_thread(worker.execute, value, deadline)
        except PlatformRefusal as error:
            return {'status': 'failed', 'message': str(error)}

    try:
        async with websockets.serve(serve, '127.0.0.1', 0) as server:
            async with websockets.connect(f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}', proxy=None) as socket:
                session = ChannelSession(SocketWire(socket), local, ChannelFence(stores[1], peers[0], authority, 'master', 'worker'), master=False, handler=handle)
                pump = asyncio.create_task(session.pump())
                controller = await master_session
                run_id = uuid4()
                first = await controller.request_runtime(run_id, 'start')
                assert first['status'] == 'serving', first
                duplicate = await controller.request_runtime(run_id, 'start')
                assert duplicate['pid'] == first['pid'] and len(children) == 1
                stale = await controller.request_runtime(uuid4(), 'stop')
                assert stale['status'] == 'failed' and children[0].poll() is None
                assert (await controller.request_runtime(run_id, 'stop'))['status'] == 'idle'
                assert children[0].poll() is not None and state.role == 'worker'
                await session.close()
                await asyncio.gather(pump, return_exceptions=True)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)
        for store in stores:
            store.close()
