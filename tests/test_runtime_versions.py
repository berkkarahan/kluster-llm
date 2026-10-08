"""Runtime source identity, isolation, HTTP and master-enforcement contracts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import textwrap
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from kllm.agent.handlers.runtimes import build_router
from kllm.agent.services.runtime_control import RuntimeControlService
from kllm.runtimes import source
from kllm.runtimes.models import BuildRequest, RuntimeTarget, RuntimeVersion, SourceRequest, TargetRequest, runtime_id
from kllm.runtimes.registry import RuntimeRegistry, atomic_json, file_digest
from kllm.runtimes.selection import snapshot, use_runtime
from kllm.state import ClusterState

REPO = 'https://github.com/ggml-org/llama.cpp.git'


def version(commit='a' * 40, repository=REPO, ref='v0.5.0'):
    return RuntimeVersion(repository=repository, commit=commit, ref=ref, runtime_id=runtime_id(repository, commit))


def artifact(registry, selected, job_id='controlled', backend='cpu'):
    directory = registry.root / selected.runtime_id / job_id / 'bin'
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('llama-server', 'rpc-server', 'libggml-rpc.so'):
        (directory / name).write_text(selected.commit + name)
    return {'schema': 1, 'runtime_id': selected.runtime_id, 'repository': selected.repository, 'commit': selected.commit,
            'build_id': job_id, 'backend': backend, 'artifact_dir': str(directory),
            'llama_server': str(directory / 'llama-server'), 'rpc_server': str(directory / 'rpc-server'),
            'ggml_rpc': 'ON', 'ggml_rpc_rdma': 'OFF', 'artifacts': {p.name: file_digest(p) for p in directory.iterdir()}}


@pytest.fixture
def registry(tmp_path):
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    yield registry
    registry.close()


def prepared(registry, selected):
    registry.add(selected)
    registry.data['versions'][selected.runtime_id]['build'] = artifact(registry, selected)
    registry._save()
    return registry.capture(selected.runtime_id)


def control(registry, role='master', node_id='master', warm=None):
    state = ClusterState(role=role, node_id=node_id)
    state.members = {
        'master': {'role': 'master'},
        'worker': {'role': 'worker', 'runtime_management_version': 1, 'host': 'worker.example', 'last_seen': time.time()},
        'oldagent': {'role': 'worker', 'runtime_management_version': 0, 'host': 'old.example', 'last_seen': time.time()},
        'offline': {'role': 'worker', 'last_seen': time.time() - 3600},
    }
    authority = SimpleNamespace(cluster_binding_id=uuid4())
    service = SimpleNamespace(state=lambda: state, channels=SimpleNamespace(master_authority=lambda: authority, sessions={}), rpc_runs=None, directory_view=lambda: {})
    return RuntimeControlService(service, registry, lambda: 'cpu', warm_capabilities=warm or (lambda: None))


def test_prepared_runtime_warmup_runs_once_per_identity(registry):
    calls = []
    manager = control(registry, warm=lambda: calls.append('warm'))
    manager._warm_prepared({'phase': 'prepared', 'prepared': {'runtime_id': 'one'}})
    manager._warm_prepared({'phase': 'prepared', 'prepared': {'runtime_id': 'one'}})
    manager._warm_prepared({'phase': 'building', 'prepared': None})
    manager._warm_prepared({'phase': 'prepared', 'prepared': {'runtime_id': 'two'}})
    assert calls == ['warm', 'warm']


@pytest.mark.asyncio
async def test_prepare_command_warms_the_prepared_runtime(tmp_path):
    from kllm.managed_network.identity import SignedRuntimeCommand

    registry = RuntimeRegistry(tmp_path / 'worker', lambda: tmp_path)
    try:
        calls = []
        manager = control(registry, role='worker', node_id='worker', warm=lambda: calls.append('warm'))
        selected = version()
        prepared(registry, selected)
        binding = manager.service.channels.master_authority().cluster_binding_id
        target = RuntimeTarget(version=selected, revision=1, cluster_binding_id=str(binding), master_node_id='master')
        command = SignedRuntimeCommand(
            cluster_binding_id=binding, master_node_id='master', master_key_id='a' * 64,
            master_term=1, master_session_id=uuid4(), version=1, type='rpc_command',
            channel_id=uuid4(), sender_node_id='master', target_node_id='worker',
            sequence=1, request_id=uuid4(), run_id=uuid4(), action='prepare_runtime',
            ttl_ms=10000, body={'target': target.model_dump()},
        )
        result = await manager.execute(command, lambda: None)
        assert result['phase'] == 'prepared'
        assert calls == ['warm']
    finally:
        registry.close()


@pytest.mark.parametrize('url', ['http://github.com/a/b', 'file:///tmp/repo', 'https://user:secret@github.com/a/b', 'https://github.com/a/b?token=secret'])
def test_rejects_nonpublic_or_credentialed_sources(url):
    with pytest.raises(ValueError):
        SourceRequest(repository=url, ref='main')


@pytest.mark.parametrize('ref', ['--upload-pack=command', 'main~1', '../main', 'a b', 'main@{1}'])
def test_ref_is_not_a_shell_or_git_option(ref):
    with pytest.raises(ValueError):
        SourceRequest(ref=ref)


def test_moving_branch_resolves_once_and_fork_identity_is_distinct(monkeypatch, registry):
    current = ['a' * 40]
    monkeypatch.setattr(source, 'refs', lambda _: {'refs/heads/main': current[0]})
    first = source.resolve(SourceRequest(ref='main', ref_type='branch'))
    registry.add(first)
    current[0] = 'b' * 40
    second = source.resolve(SourceRequest(ref='main', ref_type='branch'))
    registry.add(second)
    fork = version(first.commit, 'https://github.com/example/llama.cpp.git')
    assert first.runtime_id != second.runtime_id != fork.runtime_id
    assert registry.snapshot()['versions'][0]['version']['commit'] == 'a' * 40


def test_annotated_tag_pins_peeled_commit(monkeypatch):
    monkeypatch.setattr(source, 'refs', lambda _: {'refs/tags/v1': 'a' * 40, 'refs/tags/v1^{}': 'b' * 40})
    assert source.resolve(SourceRequest(ref='v1')).commit == 'b' * 40


def test_missing_ref_is_actionable(monkeypatch):
    monkeypatch.setattr(source, 'refs', lambda _: {})
    with pytest.raises(ValueError, match='does not exist'):
        source.resolve(SourceRequest(ref='missing'))


def test_artifact_changes_cannot_be_hidden_by_cached_verification(registry):
    selected = version()
    value = prepared(registry, selected)
    registry.capture(selected.runtime_id)
    Path(value['rpc_server']).write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        registry.capture(selected.runtime_id)


def test_manifest_cannot_claim_another_source(registry):
    selected = version()
    prepared(registry, selected)
    registry.data['versions'][selected.runtime_id]['build']['commit'] = 'b' * 40
    with pytest.raises(ValueError, match='provenance'):
        registry.capture(selected.runtime_id)


@pytest.mark.parametrize('protection', ['selected', 'running', 'target'])
def test_protected_versions_cannot_be_removed(registry, protection):
    selected = version()
    value = prepared(registry, selected)
    if protection == 'selected':
        registry.select(selected.runtime_id)
    elif protection == 'running':
        registry.running(value)
    else:
        registry.set_target(RuntimeTarget(version=selected, revision=1, cluster_binding_id=str(uuid4()), master_node_id='master'))
    with pytest.raises(ValueError, match='cannot be removed'):
        registry.remove(selected.runtime_id)


def test_removing_unused_version_does_not_remove_another(registry):
    a, b = version(), version('b' * 40)
    prepared(registry, a)
    prepared(registry, b)
    registry.remove(a.runtime_id)
    assert registry.capture(b.runtime_id)['commit'] == b.commit
    assert not registry._version_dir(a.runtime_id).exists()


def test_staged_job_directories_stay_short_and_resolve_logs(registry):
    selected = version()
    registry.add(selected)
    job_id = 'abc123def456' + '0' * 24
    registry.data['jobs'][job_id] = {'job_id': job_id, 'runtime_id': selected.runtime_id, 'backend': 'cpu',
                                     'phase': 'failed', 'error': None}
    staged = registry._job_dir(selected.runtime_id, job_id)
    assert staged.parent.parent == registry.root
    assert staged.parent.name == selected.runtime_id[:16] and staged.name == job_id[:12]
    assert len(str(staged)) < len(str(registry.root)) + 32
    (staged).mkdir(parents=True, exist_ok=True)
    (staged / 'build.log').write_text('staged build output')
    assert registry.logs(job_id) == 'staged build output'


def test_build_cancellation_preserves_running_artifacts(registry):
    first, pending = version(), version('b' * 40)
    running = prepared(registry, first)
    registry.running(running)
    registry.add(pending)
    started, release = threading.Event(), threading.Event()
    def build(v, backend, job):
        started.set()
        assert release.wait(3)
        return artifact(registry, v, job)
    registry.builder = build
    request = BuildRequest(runtime_id=pending.runtime_id, backend='cpu', request_id=str(uuid4()))
    job = registry.build(request)
    assert started.wait(3)
    assert registry.build(request)['job_id'] == job['job_id']
    assert registry.cancel(job['job_id'])['phase'] == 'cancelled'
    release.set()
    registry.pool.shutdown(wait=True)
    assert registry.capture(first.runtime_id)['build_id'] == running['build_id']
    with pytest.raises(ValueError, match='not prepared'):
        registry.capture(pending.runtime_id)


def test_restart_does_not_publish_incomplete_build(tmp_path):
    selected = version()
    root = tmp_path / 'runtimes'
    job_id = str(uuid4())
    atomic_json(root / 'registry.json', {'schema': 1, 'versions': {selected.runtime_id: {'version': selected.model_dump(), 'build': None}},
                'jobs': {job_id: {'job_id': job_id, 'runtime_id': selected.runtime_id, 'phase': 'building', 'backend': 'cpu'}},
                'selected': None, 'target': None, 'running': None, 'requests': {}})
    restored = RuntimeRegistry(tmp_path, lambda: tmp_path)
    try:
        assert restored.job(job_id)['phase'] == 'interrupted'
        with pytest.raises(ValueError, match='not prepared'):
            restored.capture(selected.runtime_id)
    finally:
        restored.close()


def test_target_is_revisioned_idempotent_and_does_not_switch_running_version(registry):
    a, b = version(), version('b' * 40)
    running = prepared(registry, a)
    prepared(registry, b)
    registry.running(running)
    manager = control(registry)
    request = TargetRequest(runtime_id=b.runtime_id, expected_revision=0, request_id=str(uuid4()))
    target = manager.change_target(request)
    assert manager.change_target(request) == target
    assert registry.snapshot()['running']['runtime_id'] == a.runtime_id
    with manager.operation() as value:
        assert value['runtime_id'] == b.runtime_id
    with pytest.raises(ValueError, match='Target changed'):
        manager.change_target(request.model_copy(update={'request_id': str(uuid4())}))


@pytest.mark.asyncio
async def test_start_gate_checks_only_selected_workers_and_rejects_wrong_fork(registry):
    selected = version()
    prepared(registry, selected)
    manager = control(registry)
    requested = []
    async def inspect(run_id, action, **kwargs):
        requested.append(action)
        return {'capability': 1, 'prepared': {'runtime_id': selected.runtime_id}}
    manager.service.channels.sessions['worker'] = SimpleNamespace(request_runtime=inspect)
    assert await manager.inspect_workers(['worker'], selected.runtime_id)
    assert requested == ['runtime_status']
    with pytest.raises(HTTPException):
        await manager.inspect_workers(['worker'], version(repository='https://github.com/example/fork.git').runtime_id)


def test_recovery_uses_original_snapshot_even_after_target_changes(registry):
    a, b = version(), version('b' * 40)
    original = prepared(registry, a)
    prepared(registry, b)
    manager = control(registry)
    manager.change_target(TargetRequest(runtime_id=b.runtime_id, expected_revision=0, request_id=str(uuid4())))
    manager.service.rpc_runs = SimpleNamespace(runtime_snapshot=original)
    with manager.operation(recovery=True):
        assert snapshot()['runtime_id'] == a.runtime_id
    assert snapshot() is None


def test_unrebuilt_native_is_visible_and_blocks_before_run_admission(registry):
    selected = version()
    prepared(registry, selected)
    manager = control(registry)
    manager.native_supported = lambda: False
    assert manager.local_status(selected.runtime_id)['phase'] == 'upgrade_required'
    with pytest.raises(HTTPException, match='Rebuild'):
        with manager.operation():
            pytest.fail('Run admission must not execute')


def test_worker_view_lists_store_members_without_fabricating_peer_preparation(registry):
    manager = control(registry, role='worker', node_id='worker')
    rows = {node['node_id']: node for node in manager.status()['nodes']}
    # A worker mirrors the main cluster store, so it shows the same cluster as
    # the cluster view instead of only itself.
    assert set(rows) == {'worker', 'master', 'oldagent', 'offline'}
    # The mirrored master is the enrollment authority, never offline or stale.
    assert rows['master']['phase'] == 'waiting_for_target'
    assert rows['master']['capability'] == 1
    # Peer preparation is never observed on a worker, so peers cannot read prepared.
    assert all(rows[node]['phase'] != 'prepared' for node in ('master', 'oldagent', 'offline'))
    # The store's capability and liveness rules drive the remaining labels.
    assert rows['oldagent']['phase'] == 'upgrade_required'
    assert rows['offline']['phase'] == 'offline'
    # Worker command channels are master-only state; a worker must not claim them.
    assert all('Runtime control channel' not in (rows[node].get('error') or '') for node in rows)


def test_master_view_labels_enrolled_live_children_from_the_main_store(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry)
    manager.change_target(TargetRequest(runtime_id=selected.runtime_id, expected_revision=0, request_id=str(uuid4())))
    rows = {node['node_id']: node for node in manager.status()['nodes']}
    # The main store shows these members as connected; a missing signed
    # observation must not relabel a live, supported child as offline.
    assert rows['worker']['phase'] == 'waiting_for_target'
    assert rows['worker']['capability'] == 1
    assert rows['worker']['name'] == 'worker.example'
    assert rows['worker']['error'].startswith('Runtime control channel not connected')
    # The store's own capability field gates old worker agents without a channel.
    assert rows['oldagent']['phase'] == 'upgrade_required'
    # Only the store's liveness rule produces the offline label.
    assert rows['offline']['phase'] == 'offline'


def test_fresh_worker_observation_reports_prepared_target_and_lag_blocks(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry)
    target = manager.change_target(TargetRequest(runtime_id=selected.runtime_id, expected_revision=0, request_id=str(uuid4())))
    manager.observed['worker'] = {'time': time.monotonic(), 'value': {
        'node_id': 'worker', 'role': 'worker', 'capability': 1,
        'target_revision': target['revision'], 'phase': 'prepared',
        'prepared': {'runtime_id': selected.runtime_id, 'build_id': 'job', 'backend': 'cpu', 'commit': selected.commit},
        'running': None, 'job': None, 'error': None}}
    assert {node['node_id']: node for node in manager.status()['nodes']}['worker']['phase'] == 'prepared'
    # A newer master target keeps the observed worker waiting, not prepared.
    replacement = version('b' * 40)
    registry.add(replacement)
    manager.change_target(TargetRequest(runtime_id=replacement.runtime_id, expected_revision=1, request_id=str(uuid4())))
    row = {node['node_id']: node for node in manager.status()['nodes']}['worker']
    assert row['phase'] == 'waiting_for_target'
    assert row['prepared']['runtime_id'] == selected.runtime_id


def test_master_view_reports_per_node_sync_failure_instead_of_silence(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry)
    manager.change_target(TargetRequest(runtime_id=selected.runtime_id, expected_revision=0, request_id=str(uuid4())))
    # A live session whose preparation keeps failing is surfaced by reason, not
    # hidden behind a nameless wait.
    manager.service.channels.sessions['worker'] = SimpleNamespace()
    manager.node_errors['worker'] = 'node runtime command channel unavailable or full'
    row = {node['node_id']: node for node in manager.status()['nodes']}['worker']
    assert row['phase'] == 'waiting_for_target'
    assert row['error'] == 'the master could not sync this worker: node runtime command channel unavailable or full'


def test_retry_without_a_paired_channel_is_actionable(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry)
    manager.change_target(TargetRequest(runtime_id=selected.runtime_id, expected_revision=0, request_id=str(uuid4())))
    app = FastAPI()
    app.include_router(build_router(lambda: registry, lambda: manager, manager.service.state,
                                    token_verifier=lambda value: value == 'test'))
    client = TestClient(app, client=('127.0.0.1', 9999))
    response = client.post('/v2/cluster/runtime-target/nodes/worker/retry', headers={'x-api-key': 'test'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'CHANNEL_UNAVAILABLE'


def test_target_survives_master_label_rotation_and_is_restamped(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry)
    authority = manager.service.channels.master_authority()
    registry.set_target(RuntimeTarget(version=selected, revision=2, cluster_binding_id=str(authority.cluster_binding_id),
                                      master_node_id='old-master'))
    kind, raw, error = manager.target_state()
    assert (kind, error) == ('label_stale', None)
    assert raw['master_node_id'] == 'old-master'
    restamped = manager._restamp_target(raw)
    assert (restamped['master_node_id'], restamped['revision']) == ('master', 3)
    assert registry.target()['master_node_id'] == 'master'
    assert manager.target_state()[0] == 'current'


def test_binding_change_is_surfaced_not_hidden(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry)
    registry.set_target(RuntimeTarget(version=selected, revision=1, cluster_binding_id=str(uuid4()),
                                      master_node_id='master'))
    kind, raw, error = manager.target_state()
    assert kind == 'binding_stale' and error
    data = manager.status()
    assert (data['target_state'], data['target'], data['reconcile_error']) == ('binding_stale', raw, error)
    # The saved version is still selectable for re-application.
    applied = manager.change_target(TargetRequest(runtime_id=selected.runtime_id, expected_revision=1, request_id=str(uuid4())))
    assert applied['version']['runtime_id'] == selected.runtime_id
    assert manager.target_state()[0] == 'current'


def test_worker_target_reports_stale_master_label(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry, role='worker', node_id='worker')
    registry.set_target(RuntimeTarget(version=selected, revision=1, cluster_binding_id=str(uuid4()),
                                      master_node_id='old-master'))
    kind, raw, error = manager.target_state()
    assert kind == 'label_stale' and error
    assert manager.target() == raw  # Displayed while the master re-sends the current target.


def test_runtime_status_labels_nodes_from_the_shared_directory(registry):
    manager = control(registry)
    manager.service.directory_view = lambda: {
        'worker': {'hostname': 'gpu-box', 'private_address': '10.10.10.4', 'rpc_bind': '192.168.1.20'}}
    row = {node['node_id']: node for node in manager.status()['nodes']}['worker']
    assert (row['name'], row['address']) == ('gpu-box', '10.10.10.4')
    manager.service.channels.sessions['worker'] = SimpleNamespace()
    row = {node['node_id']: node for node in manager.status()['nodes']}['worker']
    assert row['channel'] == {'connected': True, 'error': None}


def test_active_preparation_does_not_report_a_missing_artifact(registry):
    selected = version()
    registry.add(selected)
    manager = control(registry)
    registry.data['jobs']['job-1'] = {'job_id': 'job-1', 'runtime_id': selected.runtime_id, 'backend': 'cpu',
                                      'phase': 'building', 'error': None, 'created_at': 0, 'finished_at': None}
    status = manager.local_status(selected.runtime_id)
    assert (status['phase'], status['error']) == ('building', None)
    registry.data['jobs']['job-1'].update(phase='failed', error='toolchain failed')
    status = manager.local_status(selected.runtime_id)
    assert (status['phase'], status['error']) == ('failed', 'toolchain failed')


def test_repeated_failed_observations_do_not_burn_the_retry_budget(registry):
    manager = control(registry)
    failed = {'phase': 'failed', 'job': {'job_id': 'job-1', 'phase': 'failed'}}
    manager._note_retry('worker', failed)
    manager._note_retry('worker', failed)
    assert manager.retry_attempts['worker'] == 1
    assert manager._retry_due('worker') is False
    manager.retry_after['worker'] = 0.0
    assert manager._retry_due('worker') is True
    manager._note_retry('worker', {'phase': 'failed', 'job': {'job_id': 'job-2', 'phase': 'failed'}})
    assert manager.retry_attempts['worker'] == 2
    manager._note_retry('worker', {'phase': 'downloading', 'job': {'job_id': 'job-3', 'phase': 'downloading'}})
    assert manager.retry_attempts == {} and manager._retry_due('worker') is False


def test_local_failure_retry_waits_for_backoff_then_resets(registry):
    selected = version()
    manager = control(registry)
    registry.add(selected)
    registry.data['jobs']['job-1'] = {'job_id': 'job-1', 'runtime_id': selected.runtime_id, 'backend': 'cpu',
                                      'phase': 'failed', 'error': 'boom', 'created_at': 0, 'finished_at': 0}
    assert manager._local_retry(selected.runtime_id) is False  # First failure schedules the backoff.
    assert manager.local_attempts == 1
    assert manager._local_retry(selected.runtime_id) is False
    manager.local_retry_after = 0.0
    assert manager._local_retry(selected.runtime_id) is True
    assert manager.local_attempts == 2
    registry.data['jobs']['job-2'] = {'job_id': 'job-2', 'runtime_id': selected.runtime_id, 'backend': 'cpu',
                                      'phase': 'queued', 'created_at': 0, 'finished_at': None}
    assert manager._local_retry(selected.runtime_id) is False
    assert manager.local_attempts == 0


def test_fast_failed_installer_keeps_logs_and_releases_process_tracking(tmp_path, monkeypatch):
    import psutil
    from kllm.runtimes import registry as module
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'scripts/install.py').write_text('import sys\nprint("KLLM_RUNTIME_PHASE=building", flush=True)\nprint("controlled compile refusal", flush=True)\nsys.exit(2)\n')
    manager = RuntimeRegistry(tmp_path / 'home', lambda: checkout)
    def unavailable(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(module.psutil, 'Process', unavailable)
    try:
        selected = version()
        manager.add(selected)
        job = manager.build(BuildRequest(runtime_id=selected.runtime_id, backend='cpu', request_id=str(uuid4())))
        manager.pool.shutdown(wait=True)
        assert manager.job(job['job_id'])['phase'] == 'failed'
        assert 'controlled compile refusal' in manager.logs(job['job_id'])
        assert manager.processes == {}
    finally:
        manager.close()


def test_api_auth_worker_target_rejection_and_frozen_source_save(registry):
    manager = control(registry, role='worker', node_id='worker')
    app = FastAPI()
    app.include_router(build_router(lambda: registry, lambda: manager, manager.service.state,
                                    token_verifier=lambda value: value == 'test'))
    client = TestClient(app, client=('127.0.0.1', 9999))
    assert client.get('/v2/node/runtimes').status_code == 401
    headers = {'x-api-key': 'test'}
    value = version()
    assert client.post('/v2/node/runtimes', json=value.model_dump(), headers=headers).status_code == 200
    request = TargetRequest(runtime_id=value.runtime_id, expected_revision=0, request_id=str(uuid4())).model_dump()
    assert client.put('/v2/cluster/runtime-target', json=request, headers=headers).status_code == 409
    assert client.put('/v2/node/runtime-selection', json=request, headers=headers).status_code == 409
    assert registry.snapshot()['versions'][0]['version']['commit'] == value.commit


def test_local_library_reads_degrade_explicitly_without_signed_control(registry):
    selected = version()
    registry.add(selected)
    state = ClusterState(role='master', node_id='master')
    reason = 'Signed node runtime control is unavailable (local identity key changed); restart or recover the local agent'

    def unavailable():
        raise HTTPException(503, detail={'code': 'RUNTIME_CONTROL_UNAVAILABLE', 'message': reason})

    app = FastAPI()
    app.include_router(build_router(lambda: registry, unavailable, lambda: state,
                                    token_verifier=lambda value: value == 'test'))
    client = TestClient(app, client=('127.0.0.1', 9999))
    headers = {'x-api-key': 'test'}
    library = client.get('/v2/node/runtimes', headers=headers)
    assert library.status_code == 200
    body = library.json()
    assert body['control'] == {'available': False, 'message': reason}
    assert [entry['version']['runtime_id'] for entry in body['versions']] == [selected.runtime_id]
    assert client.get('/v2/cluster/runtime-target', headers=headers).json()['control']['available'] is False
    # Local library changes and cluster-target writes keep their own contracts.
    assert client.post('/v2/node/runtimes', json=selected.model_dump(), headers=headers).status_code == 200
    request = TargetRequest(runtime_id=selected.runtime_id, expected_revision=0, request_id=str(uuid4())).model_dump()
    blocked = client.put('/v2/cluster/runtime-target', json=request, headers=headers)
    assert blocked.status_code == 503
    assert blocked.json()['detail'] == {'code': 'RUNTIME_CONTROL_UNAVAILABLE', 'message': reason}

    healthy = control(registry)
    live = FastAPI()
    live.include_router(build_router(lambda: registry, lambda: healthy, healthy.service.state,
                                     token_verifier=lambda value: value == 'test'))
    client = TestClient(live, client=('127.0.0.1', 9999))
    assert client.get('/v2/node/runtimes', headers=headers).json()['control'] == {'available': True}


def _source_fixture_repository(tmp_path, monkeypatch, extra_files=None):
    """Create a local C runtime fixture and redirect its public URL to the checkout."""
    import subprocess
    fixture = tmp_path / 'git-source'
    fixture.mkdir()
    (fixture / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.15)
project(runtime_fixture C)
option(GGML_RPC "RPC" ON)
option(GGML_RPC_RDMA "RDMA" OFF)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
add_library(ggml-rpc SHARED rpc.c)
# MSVC produces no import library without exported symbols, so the fixture
# mirrors a real RPC library with the export-all behavior upstream uses.
set_target_properties(ggml-rpc PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
add_executable(llama-server server.c)
add_executable(rpc-server server.c)
target_link_libraries(llama-server ggml-rpc)
target_link_libraries(rpc-server ggml-rpc)
''')
    (fixture / 'rpc.c').write_text('int fixture_rpc(void) {return 0;}')
    (fixture / 'server.c').write_text('#include <stdio.h>\nint main(void){puts("CPU");return 0;}')
    for relative, text in (extra_files or {}).items():
        (fixture / relative).parent.mkdir(parents=True, exist_ok=True)
        (fixture / relative).write_text(text)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(fixture), *args], text=True).strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Runtime fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'controlled runtime')
    commit = git('rev-parse', 'HEAD')
    config = tmp_path / 'gitconfig'
    config.write_text(f'[url "{fixture.as_uri()}"]\n\tinsteadOf = https://github.com/fixture/runtime.git\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    return commit


def test_real_git_cmake_build_is_isolated_from_bootstrap(tmp_path, monkeypatch):
    import shutil
    from kllm.llama_pack import _load_install
    from kllm.runtimes.build import managed_build
    if not shutil.which('git') or not shutil.which('cmake'):
        pytest.skip('Git and CMake required for installer boundary check')
    commit = _source_fixture_repository(tmp_path, monkeypatch)
    install = _load_install(Path(__file__).resolve().parents[1])
    old_bin = install.BIN_DIR
    output = tmp_path / 'managed-artifact'
    manifest = managed_build(install, 'https://github.com/fixture/runtime.git', commit, output, 'cpu')
    assert manifest['commit'] == commit and manifest['ggml_rpc_rdma'] == 'OFF'
    assert Path(manifest['llama_server']).is_relative_to(output)
    assert Path(manifest['rpc_server']).is_relative_to(output)
    assert old_bin != Path(manifest['artifact_dir'])
    assert json.loads((output / 'manifest.json').read_text()) == manifest
    from kllm.managed_network.transport import require_tcp_runtime
    evidence = require_tcp_runtime(install.ROOT, Path(manifest['llama_server']), Path(manifest['rpc_server']))
    assert evidence['runtime_id'] == manifest['runtime_id']


def test_git_environment_disables_prompts_and_enables_long_paths():
    import shutil
    import subprocess
    env = source.git_environment()
    assert env['GIT_TERMINAL_PROMPT'] == '0'
    assert env['GIT_CONFIG_COUNT'] == '2'
    assert (env['GIT_CONFIG_KEY_0'], env['GIT_CONFIG_VALUE_0']) == ('credential.helper', '')
    assert (env['GIT_CONFIG_KEY_1'], env['GIT_CONFIG_VALUE_1']) == ('core.longpaths', 'true')
    if shutil.which('git'):
        read = subprocess.run(['git', 'config', '--get', 'core.longpaths'], env=env, capture_output=True, text=True)
        assert read.stdout.strip() == 'true'


def test_git_failure_detail_prefers_git_errors_and_falls_back():
    from kllm.runtimes.build import _git_failure_detail
    assert _git_failure_detail(
        "Cloning into 'source'...\n"
        'error: unable to create file x.svelte: Filename too long\n'
        "fatal: cannot create directory at 'deep': Filename too long\n"
    ) == "fatal: cannot create directory at 'deep': Filename too long"
    assert _git_failure_detail('remote: Enumerating objects: 10\n') == 'remote: Enumerating objects: 10'
    assert _git_failure_detail('') == 'verify the public repository, network access and resolved commit'


def test_vulkan_build_environment_selects_platform_toolchain():
    from kllm.runtimes.build import _vulkan_build_environment
    calls = []
    macos = SimpleNamespace(_is_macos=lambda: True, _is_windows=lambda: False,
                            ensure_vulkan_macos=lambda: calls.append('macos') or {'CMAKE_PREFIX_PATH': 'brew'})
    windows = SimpleNamespace(_is_macos=lambda: False, _is_windows=lambda: True,
                              ensure_windows_vulkan=lambda: calls.append('windows') or {'VULKAN_SDK': 'sdk'})
    linux = SimpleNamespace(_is_macos=lambda: False, _is_windows=lambda: False)
    assert _vulkan_build_environment(macos, 'cpu') is None
    assert _vulkan_build_environment(macos, 'metal') == {'CMAKE_PREFIX_PATH': 'brew'}
    assert _vulkan_build_environment(windows, 'cuda') is None
    assert _vulkan_build_environment(windows, 'vulkan') == {'VULKAN_SDK': 'sdk'}
    assert _vulkan_build_environment(linux, 'vulkan') is None
    assert calls == ['macos', 'windows']


def test_source_materialization_supports_paths_beyond_windows_max_path(tmp_path, monkeypatch):
    import os
    import shutil
    from kllm.runtimes.build import _materialize_source
    if os.name != 'nt':
        pytest.skip('Windows MAX_PATH regression; other platforms have no fixed limit')
    if not shutil.which('git'):
        pytest.skip('Git required for the source materialization check')
    deep = Path('deep') / Path(*['x' * 24] * 4) / 'long-file.txt'
    if len(str(tmp_path / 'git-source' / deep)) > 259:
        pytest.skip('temporary directory leaves no room below MAX_PATH for the fixture copy')
    commit = _source_fixture_repository(tmp_path, monkeypatch, {deep: 'long path fixture\n'})
    base = tmp_path / 'managed-artifact'
    remaining = 262 - (len(str(base / 'source')) + 1 + len(str(deep)))
    while remaining > 0:
        base = base / ('p' * max(min(remaining - 1, 200), 1))
        remaining -= len(base.name) + 1
    vendor = base / 'source'
    assert len(str(vendor / deep)) > 260
    vendor.parent.mkdir(parents=True, exist_ok=True)
    _materialize_source(vendor, 'https://github.com/fixture/runtime.git', commit)
    assert (vendor / deep).is_file()
    assert (vendor / 'CMakeLists.txt').is_file()


@pytest.mark.asyncio
async def test_signed_channel_build_preparation_does_not_block_runtime_renewals(tmp_path):
    import websockets
    from kllm.agent.services.network_channel import ChannelSession, SocketWire
    from kllm.managed_network.identity import ChannelFence, LocalIdentity
    from kllm.managed_network.models import PeerIdentity
    from kllm.managed_network.store import NetworkStore
    stores = [NetworkStore(tmp_path / n) for n in ('master', 'worker')]
    identities = [LocalIdentity(tmp_path / n, n, store) for n, store in zip(('master', 'worker'), stores)]
    authority = stores[0].startup_authority('master', identities[0].key_id).model_copy(update={'accepted_channel_id': uuid4()})
    peers = [PeerIdentity(**i.public(authority.cluster_binding_id).model_dump(), expected_role=r) for i, r in zip(identities, ('master', 'worker'))]
    for index in (0, 1):
        stores[index].pair(peers[1-index])
        stores[index].accept_authority(authority, peers[1-index].node_id)
    registry = RuntimeRegistry(tmp_path / 'worker', lambda: tmp_path)
    manager = control(registry, role='worker', node_id='worker')
    started, finish = threading.Event(), threading.Event()
    def build(v, backend, identity):
        started.set()
        assert finish.wait(5)
        return artifact(registry, v, identity)
    registry.builder = build
    connection = asyncio.get_running_loop().create_future()
    async def serve(socket):
        value = ChannelSession(SocketWire(socket), identities[0], ChannelFence(stores[0], peers[1], authority, 'worker', 'master'), master=True)
        connection.set_result(value)
        await value.pump()
    async def handler(command, deadline):
        if command.action == 'renew':
            return {'status': 'serving', 'run_id': str(command.run_id)}
        return await manager.execute(command, lambda: stores[1].check_channel('master', authority))
    try:
        async with websockets.serve(serve, '127.0.0.1', 0) as server:
            async with websockets.connect(f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}', proxy=None) as socket:
                worker = ChannelSession(SocketWire(socket), identities[1], ChannelFence(stores[1], peers[0], authority, 'master', 'worker'), master=False, handler=handler)
                pump = asyncio.create_task(worker.pump())
                master = await connection
                target = RuntimeTarget(version=version(), revision=1, cluster_binding_id=str(authority.cluster_binding_id), master_node_id='master')
                result = await master.request_runtime(uuid4(), 'prepare_runtime', body={'target': target.model_dump()})
                assert result['job']['phase'] in ('queued', 'downloading')
                assert await asyncio.to_thread(started.wait, 3)
                assert (await asyncio.wait_for(master.request_runtime(uuid4(), 'renew'), 1))['status'] == 'serving'
                finish.set()
                await worker.close()
                await asyncio.gather(pump, return_exceptions=True)
    finally:
        finish.set()
        registry.close()
        for store in stores:
            store.close()


def test_native_launch_uses_verified_explicit_path_over_environment(tmp_path, monkeypatch):
    import os
    import sys
    from kllm import native
    if os.name == 'nt':
        pytest.skip('Unix executable fixture; Windows executable path is checked by native build tests')
    if not native.native_available() or getattr(native.require_native(), 'RUNTIME_SELECTION_VERSION', 0) != 1:
        pytest.skip('Rebuilt native extension required')
    binary = tmp_path / 'selected-rpc-server'
    binary.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(60)\n')
    binary.chmod(0o755)
    monkeypatch.setenv('KLLM_HOME', str(tmp_path))
    monkeypatch.setenv('KLLM_RPC_SERVER', '/wrong/default/binary')
    with use_runtime({'rpc_server': str(binary), 'llama_server': str(binary)}):
        result = native.start_worker(host='127.0.0.1', port=54321, devices=[])
    try:
        assert result['binary'] == str(binary)
        assert result['argv'][0] == str(binary)
        assert native.process_alive(result['pid'])
    finally:
        native.stop_process(result['pid'], force=True)


def test_old_native_extension_is_rejected_before_spawn(tmp_path, monkeypatch):
    from kllm import native
    binary = tmp_path / 'rpc'
    binary.write_text('controlled')
    calls = []
    monkeypatch.setattr(native, '_NATIVE', SimpleNamespace(start_worker=lambda _: calls.append('spawn')))
    with use_runtime({'rpc_server': str(binary)}), pytest.raises(RuntimeError, match='Rebuild'):
        native.start_worker(devices=[])
    assert calls == []


@pytest.mark.parametrize('matching_identity', [True, False])
def test_restart_reaps_only_the_recorded_build_process(tmp_path, matching_identity):
    import subprocess
    import sys
    import os
    import psutil
    process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=os.name != 'nt')
    try:
        selected = version()
        job_id = str(uuid4())
        created = psutil.Process(process.pid).create_time()
        job = {'job_id': job_id, 'runtime_id': selected.runtime_id, 'phase': 'building', 'backend': 'cpu',
               'pid': process.pid, 'process_created_at': created if matching_identity else created - 1}
        atomic_json(tmp_path / 'runtimes/registry.json', {'schema': 1,
            'versions': {selected.runtime_id: {'version': selected.model_dump(), 'build': None}},
            'jobs': {job_id: job}, 'selected': None, 'target': None, 'running': None, 'requests': {}})
        restored = RuntimeRegistry(tmp_path, lambda: tmp_path)
        try:
            if matching_identity:
                assert restored.job(job_id)['phase'] == 'interrupted'
                process.wait(timeout=3)
            else:
                assert restored.job(job_id)['phase'] == 'failed'
                assert process.poll() is None
        finally:
            restored.close()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)


# ── prebuilt release packs and prebuilt-first preparation ─────────────────────


def test_packs_tag_matching_accepts_peeled_annotated_tags():
    from kllm.runtimes import packs
    values = {
        'refs/tags/v0.5.0': 'c' * 40,
        'refs/tags/v0.5.0^{}': 'a' * 40,
        'refs/tags/v0.6.0': 'b' * 40,
        'refs/heads/main': 'a' * 40,
    }
    assert packs.tags_for_commit(values, 'a' * 40) == {'v0.5.0'}
    assert packs.tags_for_commit(values, 'b' * 40) == {'v0.6.0'}
    assert packs.tags_for_commit(values, 'd' * 40) == set()


def test_release_for_commit_requires_exact_commit_and_github_host():
    from kllm.runtimes import packs
    releases = [{'tag_name': 'v0.5.0', 'assets': [{'name': 'pack.zip'}]}]
    values = {'refs/tags/v0.5.0': 'a' * 40}
    repo = 'https://github.com/ggml-org/llama.cpp.git'
    assert packs.release_for_commit(repo, 'a' * 40, releases=releases, ref_values=values)['tag_name'] == 'v0.5.0'
    # A commit with no resolving release tag, and a non-GitHub host, are ineligible.
    assert packs.release_for_commit(repo, 'b' * 40, releases=releases, ref_values=values) is None
    assert packs.release_for_commit('https://example.com/llama.git', 'a' * 40, releases=releases, ref_values=values) is None


def _load_install_module():
    from kllm.llama_pack import _load_install
    return _load_install(Path(__file__).resolve().parents[1])


def _write_pack_zip(path: Path, install, members=('llama-server', 'rpc-server')):
    suffix = '.exe' if install._is_windows() else ''
    with zipfile.ZipFile(path, 'w') as archive:
        for name in members:
            archive.writestr(name + suffix, f'controlled {name}\n')


def _fake_release(name='pack.zip'):
    return {'tag_name': 'v0.5.0', 'assets': [{'name': name, 'browser_download_url': 'https://example.invalid/' + name, 'size': 123}]}


def test_managed_prebuilt_stages_pack_without_transport_claims(tmp_path, monkeypatch):
    from kllm.runtimes import build
    install = _load_install_module()
    commit = 'a' * 40
    repository = 'https://github.com/example/llama.cpp.git'

    def download(url, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_pack_zip(dest, install)

    monkeypatch.setattr(build, 'release_for_commit', lambda *a, **k: _fake_release())
    monkeypatch.setattr(install, '_download', download)
    monkeypatch.setattr(install, 'pick_release_asset', lambda assets, backend: assets[0])
    monkeypatch.setattr(install, '_probe_llama_backends', lambda binary: ['cpu'])

    output = tmp_path / 'managed'
    manifest = build.managed_prebuilt(install, repository, commit, output, 'cpu')

    assert manifest['prebuilt'] is True and manifest['mode'] == 'prebuilt'
    assert 'ggml_rpc' not in manifest and 'ggml_rpc_rdma' not in manifest
    assert manifest['commit'] == commit and manifest['repository'] == repository
    assert Path(manifest['llama_server']).is_file() and Path(manifest['rpc_server']).is_file()
    assert Path(manifest['artifact_dir']) == output / 'bin'
    assert 'rpc-transport.json' not in manifest['artifacts']
    assert json.loads((output / 'manifest.json').read_text()) == manifest
    assert not (output / 'prebuilt').exists()


def test_managed_prebuilt_refuses_pack_without_rpc_server(tmp_path, monkeypatch):
    from kllm.runtimes import build
    from kllm.runtimes.packs import PrebuiltIneligible
    install = _load_install_module()
    monkeypatch.setattr(build, 'release_for_commit', lambda *a, **k: _fake_release())
    monkeypatch.setattr(install, 'pick_release_asset', lambda assets, backend: assets[0])
    monkeypatch.setattr(install, '_download',
                        lambda url, dest, **kwargs: (dest.parent.mkdir(parents=True, exist_ok=True), _write_pack_zip(dest, install, members=('llama-server',)))[1])

    output = tmp_path / 'managed'
    with pytest.raises(PrebuiltIneligible):
        build.managed_prebuilt(install, 'https://github.com/example/llama.cpp.git', 'a' * 40, output, 'cpu')
    # A rejected pack must not leave a binary a later source build could be mistaken for.
    assert not (output / 'bin').exists() or not any((output / 'bin').iterdir())
    assert not (output / 'prebuilt').exists()


def test_managed_prebuilt_refuses_backend_contract_mismatch(tmp_path, monkeypatch):
    from kllm.runtimes import build
    from kllm.runtimes.packs import PrebuiltIneligible
    install = _load_install_module()
    monkeypatch.setattr(build, 'release_for_commit', lambda *a, **k: _fake_release())
    monkeypatch.setattr(install, 'pick_release_asset', lambda assets, backend: assets[0])
    monkeypatch.setattr(install, '_download',
                        lambda url, dest, **kwargs: (dest.parent.mkdir(parents=True, exist_ok=True), _write_pack_zip(dest, install))[1])
    monkeypatch.setattr(install, '_probe_llama_backends', lambda binary: ['metal'])

    output = tmp_path / 'managed'
    # A Metal-only pack cannot satisfy the macOS Metal+Vulkan contract.
    with pytest.raises(PrebuiltIneligible, match='backends'):
        build.managed_prebuilt(install, 'https://github.com/example/llama.cpp.git', 'a' * 40, output, 'vulkan')
    assert not (output / 'bin').exists() or not any((output / 'bin').iterdir())


def test_managed_prebuilt_ineligible_without_release(tmp_path, monkeypatch):
    from kllm.runtimes import build
    from kllm.runtimes.packs import PrebuiltIneligible
    install = _load_install_module()
    monkeypatch.setattr(build, 'release_for_commit', lambda *a, **k: None)
    output = tmp_path / 'managed'
    with pytest.raises(PrebuiltIneligible, match='no published release'):
        build.managed_prebuilt(install, 'https://github.com/example/llama.cpp.git', 'a' * 40, output, 'cpu')
    assert not (output / 'bin').exists()


_PREBUILT_OR_SOURCE = textwrap.dedent('''
    import hashlib, json, sys
    from pathlib import Path
    argv = sys.argv[1:]
    def value(flag):
        return argv[argv.index(flag) + 1]
    destination = Path(value('--runtime-dir'))
    bins = destination / 'bin'
    bins.mkdir(parents=True, exist_ok=True)
    for name in ('llama-server', 'rpc-server', 'libggml-rpc.so'):
        (bins / name).write_text('controlled ' + name)
    commit, repository = value('--revision'), value('--source-repo')
    identity = hashlib.sha256((repository + chr(10) + commit).encode()).hexdigest()
    manifest = {'schema': 1, 'runtime_id': identity, 'repository': repository, 'commit': commit,
                'build_id': str(destination.name), 'backend': 'cpu',
                'artifact_dir': str(bins.resolve()),
                'llama_server': str((bins / 'llama-server').resolve()),
                'rpc_server': str((bins / 'rpc-server').resolve()),
                'artifacts': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bins.iterdir()}}
    if '--prebuilt' in argv:
        print('KLLM_RUNTIME_PHASE=downloading', flush=True)
        print('KLLM_PREBUILT_INELIGIBLE: controlled no pack', flush=True)
        sys.exit(3)
    print('KLLM_RUNTIME_PHASE=building', flush=True)
    manifest.update(mode='source', ggml_rpc='ON', ggml_rpc_rdma='OFF')
    (destination / 'manifest.json').write_text(json.dumps(manifest))
    print('KLLM_RUNTIME_PHASE=verifying', flush=True)
''').lstrip()


_PREBUILT_SUCCESS = textwrap.dedent('''
    import hashlib, json, sys
    from pathlib import Path
    argv = sys.argv[1:]
    def value(flag):
        return argv[argv.index(flag) + 1]
    destination = Path(value('--runtime-dir'))
    if '--prebuilt' not in argv:
        print('source build must not run when the pack succeeds', flush=True)
        sys.exit(9)
    bins = destination / 'bin'
    bins.mkdir(parents=True, exist_ok=True)
    for name in ('llama-server', 'rpc-server', 'libggml-rpc.so'):
        (bins / name).write_text('controlled ' + name)
    commit, repository = value('--revision'), value('--source-repo')
    identity = hashlib.sha256((repository + chr(10) + commit).encode()).hexdigest()
    manifest = {'schema': 1, 'runtime_id': identity, 'repository': repository, 'commit': commit,
                'build_id': str(destination.name), 'backend': 'cpu', 'mode': 'prebuilt', 'prebuilt': True,
                'artifact_dir': str(bins.resolve()),
                'llama_server': str((bins / 'llama-server').resolve()),
                'rpc_server': str((bins / 'rpc-server').resolve()),
                'artifacts': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bins.iterdir()}}
    (destination / 'manifest.json').write_text(json.dumps(manifest))
    print('KLLM_RUNTIME_PHASE=verifying', flush=True)
''').lstrip()


def _checkout_with_script(tmp_path, script: str) -> Path:
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'scripts/install.py').write_text(script, encoding='utf-8')
    return checkout


def test_preparation_falls_back_to_source_when_pack_is_ineligible(tmp_path):
    checkout = _checkout_with_script(tmp_path, _PREBUILT_OR_SOURCE)
    manager = RuntimeRegistry(tmp_path / 'home', lambda: checkout)
    try:
        selected = version()
        manager.add(selected)
        job = manager.build(BuildRequest(runtime_id=selected.runtime_id, backend='cpu', request_id=str(uuid4())))
        manager.pool.shutdown(wait=True)
        assert manager.job(job['job_id'])['phase'] == 'prepared'
        assert manager.job(job['job_id'])['mode'] == 'source'
        assert 'KLLM_PREBUILT_INELIGIBLE' in manager.logs(job['job_id'])
        assert manager.capture(selected.runtime_id)['mode'] == 'source'
    finally:
        manager.close()


def test_preparation_uses_prebuilt_pack_without_transport_evidence(tmp_path):
    checkout = _checkout_with_script(tmp_path, _PREBUILT_SUCCESS)
    manager = RuntimeRegistry(tmp_path / 'home', lambda: checkout)
    try:
        selected = version()
        manager.add(selected)
        job = manager.build(BuildRequest(runtime_id=selected.runtime_id, backend='cpu', request_id=str(uuid4())))
        manager.pool.shutdown(wait=True)
        assert manager.job(job['job_id'])['phase'] == 'prepared'
        assert manager.job(job['job_id'])['mode'] == 'prebuilt'
        captured = manager.capture(selected.runtime_id)
        assert captured['prebuilt'] is True and captured['mode'] == 'prebuilt'
    finally:
        manager.close()


def test_prebuilt_runtime_is_refused_by_the_managed_network_gate(tmp_path):
    from kllm.managed_network.transport import require_tcp_runtime
    from kllm.managed_network.platform.base import PlatformRefusal
    directory = tmp_path / 'artifact' / 'bin'
    directory.mkdir(parents=True)
    llma, rpc = directory / 'llama-server', directory / 'rpc-server'
    llma.write_text('controlled llama')
    rpc.write_text('controlled rpc')
    # A prebuilt install has no rpc-transport.json, so the TCP-only gate refuses
    # it even though the binaries exist (the source build is required).
    with pytest.raises(PlatformRefusal):
        require_tcp_runtime(tmp_path, llma, rpc)
