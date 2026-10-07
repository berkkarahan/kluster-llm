"""Master target reconciliation over the existing signed node channel."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException

from kllm.runtimes.models import BuildRequest, RuntimeTarget, TargetRequest, body_digest
from kllm.runtimes.registry import RuntimeRegistry
from kllm.runtimes.selection import snapshot, use_runtime


class RuntimeControlService:
    def __init__(self, service, registry: RuntimeRegistry, backend, *, native_supported=lambda: True):
        self.service, self.registry, self.backend = service, registry, backend
        self.native_supported = native_supported
        self.task = None
        self.observed: dict[str, dict] = {}
        self.closed = False
        self.reconcile_error: str | None = None

    def target(self):
        target = self.registry.target()
        if target is None:
            return None
        state = self.service.state()
        if state.role == 'master':
            authority = self.service.channels.master_authority()
            if target['cluster_binding_id'] != str(authority.cluster_binding_id) or target['master_node_id'] != state.node_id:
                return None
        elif state.role != 'worker' or target['master_node_id'] not in state.members:
            return None
        return target

    def change_target(self, request: TargetRequest):
        if self.service.state().role != 'master':
            raise ValueError('Set the cluster target on the master')
        with self.registry.lock:
            requests = self.registry.data.setdefault('target_requests', {})
            key = request.request_id
            authority = self.service.channels.master_authority()
            digest = body_digest({**request.model_dump(), 'cluster_binding_id': str(authority.cluster_binding_id)})
            if key in requests:
                if requests[key]['digest'] != digest:
                    raise ValueError('Request ID reused with a different target')
                return deepcopy(requests[key]['target'])
            prior = self.target()
            if request.expected_revision != (prior or {}).get('revision', 0):
                raise ValueError('Target changed; refresh before applying your selection')
            version = self.registry.data['versions'][request.runtime_id]['version']
            target = RuntimeTarget(version=version, revision=request.expected_revision + 1,
                                   cluster_binding_id=str(authority.cluster_binding_id), master_node_id=self.service.state().node_id)
            self.registry.set_target(target)
            requests[key] = {'digest': digest, 'target': target.model_dump()}
            self.registry._save()
        self.prepare(target.model_dump())
        return target.model_dump()

    def prepare(self, target: dict, *, retry=False):
        value = RuntimeTarget.model_validate(target)
        with self.registry.lock:
            old = self.registry.target()
            if old and old['cluster_binding_id'] == value.cluster_binding_id and old['revision'] > value.revision:
                raise ValueError('Preparation targets an obsolete revision')
            if old and old['cluster_binding_id'] == value.cluster_binding_id and old['revision'] == value.revision and old != value.model_dump():
                raise ValueError('Conflicting target at the same revision')
            if not old or old != value.model_dump():
                for job in self.registry.data['jobs'].values():
                    if job['runtime_id'] != value.version.runtime_id and job['phase'] not in ('prepared', 'failed', 'cancelled', 'interrupted'):
                        self.registry.cancel(job['job_id'])
            self.registry.set_target(value)
        backend = self.backend()
        request = BuildRequest(runtime_id=value.version.runtime_id, backend=backend,
                               request_id=str(uuid4()) if retry else str(uuid5(NAMESPACE_URL, f'{value.cluster_binding_id}:{self.service.state().node_id}:{value.revision}:{backend}')))
        self.registry.build(request)
        return self.local_status()

    def local_status(self, identity: str | None = None):
        state = self.service.state()
        target = self.target()
        identity = identity or (target or {}).get('version', {}).get('runtime_id')
        data = self.registry.snapshot()
        job = next((j for j in reversed(data['jobs']) if j['runtime_id'] == identity), None)
        prepared, error = None, None
        if identity:
            try:
                value = self.registry.capture(identity)
                prepared = {k: value[k] for k in ('runtime_id', 'build_id', 'backend', 'commit')}
            except (OSError, ValueError, KeyError) as failure:
                error = str(failure)
        running = data['running']
        selection_supported = self.native_supported()
        return {'node_id': state.node_id, 'role': state.role, 'capability': 1,
                'selection_supported': selection_supported,
                'target_revision': (target or {}).get('revision'), 'prepared': prepared,
                'running': {k: running[k] for k in ('runtime_id', 'build_id', 'commit')} if running else None,
                'phase': 'upgrade_required' if not selection_supported else 'prepared' if prepared else (job or {}).get('phase', 'not_installed'),
                'job': job, 'error': 'Rebuild the native extension for runtime selection' if not selection_supported else (job or {}).get('error') or error}

    def status(self):
        data = self.registry.snapshot()
        target = self.target()
        data['target'] = target
        nodes = [self.local_status()]
        now = time.monotonic()
        members = self.service.state().members if self.service.state().role == 'master' else {}
        for node, member in list(members.items()):
            if node == self.service.state().node_id:
                continue
            observation = self.observed.get(node)
            connected = node in self.service.channels.sessions and observation and now - observation['time'] < 10
            value = deepcopy(observation['value']) if connected else {'node_id': node, 'role': member.get('role'), 'phase': 'offline', 'capability': member.get('runtime_management_version', 0)}
            if node in self.service.channels.sessions and member.get('runtime_management_version') != 1:
                value.update(phase='upgrade_required', error='Upgrade/rebuild this worker agent for runtime management')
            if connected and target and value.get('target_revision') != target['revision']:
                value['phase'] = 'waiting_for_target'
            value['name'] = member.get('hostname') or node
            nodes.append(value)
        data['nodes'] = nodes
        data['reconcile_error'] = self.reconcile_error
        return data

    @contextmanager
    def operation(self, *, recovery: bool = False):
        if not self.native_supported():
            raise HTTPException(409, 'Rebuild the native extension with python scripts/build.py before Run')
        pinned = snapshot()
        if pinned is None:
            coordinator = self.service.rpc_runs
            pinned = coordinator.runtime_snapshot if recovery and coordinator else None
            try:
                target = self.target()
                pinned = pinned or self.registry.capture((target or {}).get('version', {}).get('runtime_id'))
            except (OSError, ValueError) as error:
                raise HTTPException(409, detail={'code': 'RUNTIME_NOT_READY', 'message': str(error)}) from None
        with use_runtime(pinned):
            yield pinned

    async def inspect_workers(self, workers: list[str], identity: str):
        results = {}
        for node in workers:
            if self.service.state().members.get(node, {}).get('runtime_management_version') != 1:
                raise HTTPException(409, f'{node}: upgrade/rebuild the worker agent for llama.cpp version control')
            session = self.service.channels.sessions.get(node)
            if session is None:
                raise HTTPException(409, f'{node}: signed runtime command channel is unavailable')
            results[node] = await session.request_runtime(uuid4(), 'runtime_status', body={'runtime_id': identity})
        for node, value in results.items():
            if value.get('capability') != 1 or value.get('selection_supported') is False or (value.get('prepared') or {}).get('runtime_id') != identity:
                raise HTTPException(409, detail={'code': 'RUNTIME_NOT_READY', 'node_id': node,
                                               'message': f'{node}: prepare the target in Settings → llama.cpp', 'runtime': value})
        return results

    async def reconcile(self):
        while not self.closed:
            await asyncio.sleep(2)
            if self.service.state().role != 'master':
                continue
            target = self.target()
            if not target:
                continue
            try:
                await asyncio.to_thread(self.prepare, target)
                self.reconcile_error = None
            except (OSError, ValueError, KeyError) as error:
                self.reconcile_error = str(error)[:1000]
            async def update(node, session):
                member = self.service.state().members.get(node, {})
                if member.get('runtime_management_version') != 1:
                    return
                try:
                    value = await session.request_runtime(uuid4(), 'prepare_runtime', body={'target': target})
                    if self.target() == target and node in self.service.state().members:
                        self.observed[node] = {'time': time.monotonic(), 'value': value}
                except (Exception, asyncio.CancelledError) as error:
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    self.observed.pop(node, None)
            semaphore = asyncio.Semaphore(4)
            async def bounded(node, session):
                async with semaphore:
                    await update(node, session)
            await asyncio.gather(*(bounded(n, s) for n, s in list(self.service.channels.sessions.items())))

    async def execute(self, command, authorize):
        authorize()
        state = self.service.state()
        if state.role != 'worker' or command.sender_node_id != command.master_node_id:
            raise ValueError('Runtime preparation requires the enrolled master')
        if command.action == 'prepare_runtime':
            if set(command.body) not in ({'target'}, {'target', 'retry'}) or type(command.body.get('retry', False)) is not bool:
                raise ValueError('Invalid preparation body')
            target = RuntimeTarget.model_validate(command.body['target'])
            if target.cluster_binding_id != str(command.cluster_binding_id) or target.master_node_id != command.master_node_id:
                raise ValueError('Runtime target belongs to another cluster')
            return await asyncio.to_thread(self.prepare, target.model_dump(), retry=command.body.get('retry', False))
        if command.action == 'runtime_status':
            if set(command.body) != {'runtime_id'}:
                raise ValueError('Invalid runtime inspection body')
            return await asyncio.to_thread(self.local_status, command.body['runtime_id'])
        if command.action == 'cancel_runtime_build':
            if set(command.body) != {'job_id'}:
                raise ValueError('Invalid cancellation body')
            return self.registry.cancel(command.body['job_id'])
        if command.action == 'runtime_build_logs':
            if set(command.body) != {'job_id'}:
                raise ValueError('Invalid log request body')
            return {'text': self.registry.logs(command.body['job_id'])[-16000:]}
        raise ValueError('Unsupported runtime action')

    async def close(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await asyncio.to_thread(self.registry.close)
