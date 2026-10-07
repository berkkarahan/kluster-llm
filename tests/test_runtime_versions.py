"""Runtime source identity, isolation, HTTP and master-enforcement contracts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

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


def control(registry, role='master', node_id='master'):
    state = ClusterState(role=role, node_id=node_id)
    state.members = {'master': {'role': 'master'}, 'worker': {'role': 'worker', 'runtime_management_version': 1}, 'offline': {'role': 'worker'}}
    authority = SimpleNamespace(cluster_binding_id=uuid4())
    service = SimpleNamespace(state=lambda: state, channels=SimpleNamespace(master_authority=lambda: authority, sessions={}), rpc_runs=None)
    return RuntimeControlService(service, registry, lambda: 'cpu')


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
    assert not (registry.root / a.runtime_id).exists()


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


def test_worker_view_reports_local_preparation_without_fabricating_peer_liveness(registry):
    manager = control(registry, role='worker', node_id='worker')
    assert [node['node_id'] for node in manager.status()['nodes']] == ['worker']


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


def test_real_git_cmake_build_is_isolated_from_bootstrap(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from kllm.llama_pack import _load_install
    from kllm.runtimes.build import managed_build
    if not shutil.which('git') or not shutil.which('cmake'):
        pytest.skip('Git and CMake required for installer boundary check')
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
    def git(*args):
        return subprocess.check_output(['git', '-C', str(fixture), *args], text=True).strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Runtime fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'controlled runtime')
    commit = git('rev-parse', 'HEAD')
    config = tmp_path / 'gitconfig'
    config.write_text(f'[url "{fixture.as_uri()}"]\n\tinsteadOf = https://github.com/fixture/runtime.git\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
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
