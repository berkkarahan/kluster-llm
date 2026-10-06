"""Master orchestration of RPC workers for one inference run at a time."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from kllm import native, runtime
from kllm.run_planning import member_rpc_host
from kllm.runtimes.selection import snapshot, use_runtime


def selected_workers(plan: dict[str, Any], members: dict[str, Any]) -> list[str]:
    endpoints = set(plan.get('rpc_endpoints') or [])
    return [
        node for node, member in members.items()
        if member.get('role') == 'worker'
        and f"{member_rpc_host(member)}:{member.get('rpc_port', 50052)}" in endpoints
    ]


def remap_rpc_plan(plan: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Keep placement fixed while replacing logical endpoints with run relays."""
    mapping = {}
    for node, member in before.items():
        if member.get('role') == 'worker' and node in after:
            target = after[node]
            mapping[f"{member_rpc_host(member)}:{member.get('rpc_port', 50052)}"] = f"{member_rpc_host(target)}:{target.get('rpc_port', 50052)}"

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            if value in mapping:
                return mapping[value]
            for source, target in mapping.items():
                value = value.replace(f'RPC[{source}]', f'RPC[{target}]')
            return value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return replace(plan)


class RpcRunCoordinator:
    def __init__(self, service: Any, run: Any, stop_master: Any) -> None:
        self.service, self.run, self.stop_master = service, run, stop_master
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.generation = 0
        self.run_id: UUID | None = None
        self.workers: list[str] = []
        self.body: Any = None
        self.pid: int | None = None
        self.token: str | None = None
        self.phase = 'idle'
        self.healthy_since = time.monotonic()
        self.retries = 0
        self.recovery_task: asyncio.Task | None = None
        self.recovery_workers: set[str] | None = None
        self.runtime_error: str | None = None
        self.runtime_snapshot: dict | None = None
        saved = json.loads(service.store.metadata('rpc_run') or '{}')
        self.saved_master = {'pid': saved.get('pid'), 'start_token': saved.get('start_token')}
        if saved.get('run_id'):
            self.run_id = UUID(saved['run_id'])
            self.workers = saved.get('workers', [])
            self.phase = 'cleanup_pending'

    def reconcile_startup(self) -> None:
        """Release a proven previous child; startup never resumes inference."""
        state = self.service.state()
        if state.role != 'master' or not state.process_pid:
            return
        pid = state.process_pid
        expected = self.saved_master.get('start_token') if self.saved_master.get('pid') == pid else None
        if expected is None:
            child = next((child for child in runtime.load().children if child.pid == pid), None)
            expected = child.start_token if child else None
        if not native.process_alive(pid):
            state.process_pid = state.process_role = None
            state.active_config = None
            state.save()
        elif expected and str(native.require_native()._process_identity(pid)[0]) == expected:
            self.stop_master()
            state.save()
        else:
            self.phase = 'cleanup_pending'
            state.apply_error = 'Previous inference process identity is unavailable; reconcile it locally before Run'
            state.save()

    def _persist(self) -> None:
        self.service.store.set_metadata('rpc_run', json.dumps({
            'run_id': str(self.run_id) if self.run_id else None,
            'workers': self.workers, 'phase': self.phase, 'pid': self.pid, 'start_token': self.token,
            'runtime': self.runtime_snapshot,
        }))

    async def _wait_channels(self, workers: list[str]) -> None:
        until = asyncio.get_running_loop().time() + 5
        while any(node not in self.service.channels.sessions for node in workers):
            if asyncio.get_running_loop().time() >= until:
                raise HTTPException(503, 'Selected workers have no paired command channel; wait for joining or upgrade their agents')
            await asyncio.sleep(0.05)

    async def _commands(self, run_id: UUID, workers: list[str], action: str) -> dict[str, Any]:
        async def request(node: str) -> tuple[str, dict[str, Any]]:
            session = self.service.channels.sessions.get(node)
            if session is None:
                return node, {'status': 'failed', 'message': 'Paired worker channel unavailable'}
            try:
                if action == 'start' and self.runtime_snapshot is not None:
                    return node, await session.request_runtime(run_id, action, body={'runtime_id': self.runtime_snapshot['runtime_id']})
                return node, await session.request_runtime(run_id, action)
            except (Exception, asyncio.CancelledError) as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                return node, {'status': 'failed', 'message': str(error)[:512]}
        return dict(await asyncio.gather(*(request(node) for node in workers)))

    def _release_workers(self) -> None:
        if self.run_id is None:
            return
        results = self.service.admission.call(self._commands(self.run_id, list(self.workers), 'stop'))
        for node, result in results.items():
            member = self.service.state().members.get(node)
            if member is not None and result.get('status') == 'idle':
                member.update(process_pid=None, process_role=None, rpc_devices=[], rpc_status='idle')
        self.workers = [node for node in self.workers if results[node].get('status') != 'idle']
        self.phase = 'cleanup_pending' if self.workers else 'idle'
        if not self.workers:
            self.run_id = None
        self.pid = self.token = None
        self._persist()
        if self.workers:
            raise HTTPException(503, f"RPC cleanup pending on {', '.join(self.workers)}; reconnect workers to reconcile")

    def stop_current(self) -> None:
        pid = self._master_identity()
        self.stop_master()
        if pid and native.process_alive(pid):
            raise HTTPException(503, 'Owned inference process has not stopped; RPC ownership was retained')
        if getattr(self.service, 'runtimes', None) is not None and not (self.phase == 'recovering' and self.body is not None):
            self.service.runtimes.registry.running(None)
        try:
            self.service.admission.call(self.service.admission.release_stopped())
        finally:
            self._release_workers()

    def _master_identity(self) -> int | None:
        pid = self.service.state().process_pid
        if not pid or not native.process_alive(pid):
            return pid
        identity = native.registered_process_identity(pid) or {}
        expected = self.token if pid == self.pid else identity.get('start_token')
        if not expected or str(native.require_native()._process_identity(pid)[0]) != expected:
            raise HTTPException(409, 'Inference process identity changed; reconcile it locally before replacing the run')
        return pid

    def launch(self, body: Any) -> dict[str, Any]:
        self.cancel.set()
        self.generation += 1
        with self.lock:
            self.cancel.clear()
            self.retries = 0
            self.recovery_workers = None
            control = getattr(self.service, 'runtimes', None)
            if control is None:
                return self.run(body)
            with control.operation():
                return self.run(body)

    def stop(self, callback: Any) -> dict[str, Any]:
        self.cancel.set()
        self.generation += 1
        with self.lock:
            self.body = None
            pid = self._master_identity()
            try:
                return callback()
            finally:
                if pid and native.process_alive(pid):
                    raise HTTPException(503, 'Inference process has not stopped; RPC ownership was retained')
                self._release_workers()
                self.runtime_snapshot = None
                if getattr(self.service, 'runtimes', None) is not None:
                    self.service.runtimes.registry.running(None)

    @contextmanager
    def context(self, body: Any, plan: dict[str, Any], members: dict[str, Any]):
        with self.lock:
            workers = selected_workers(plan, members)
            if self.phase == 'cleanup_pending' and self.run_id is None and self.service.state().process_pid:
                raise HTTPException(409, 'Reconcile the previous inference process identity before Run')
            if len(workers) != len(set(plan.get('rpc_endpoints') or [])):
                raise HTTPException(409, 'Planned RPC endpoints no longer match distinct enrolled workers')
            if self.recovery_workers is not None and set(workers) != self.recovery_workers:
                raise HTTPException(409, 'Recovery placement changed; start a new Run to choose different workers')
            for node in workers:
                if members[node].get('rpc_control_version') != 1:
                    raise HTTPException(409, f'{node}: upgrade the worker agent for master-controlled RPC')
            self.service.admission.call(self._wait_channels(workers))
            control = getattr(self.service, 'runtimes', None)
            if control is not None:
                value = snapshot()
                if value is None:
                    raise HTTPException(409, 'Capture the target runtime before planning a Run')
                self.service.admission.call(control.inspect_workers(workers, value['runtime_id']))
                self.runtime_snapshot = value
            self.stop_current()
            self.run_id, self.workers = uuid4(), workers
            self.body = body.model_copy(deep=True)
            self.phase = 'starting'
            self._persist()
            try:
                results = self.service.admission.call(self._commands(self.run_id, workers, 'start'))
                for node, result in results.items():
                    if result.get('status') != 'serving':
                        raise HTTPException(503, f"{node}: {result.get('message') or result.get('error') or 'RPC startup failed'}")
                    if self.runtime_snapshot is not None and result.get('runtime_id') != self.runtime_snapshot['runtime_id']:
                        raise HTTPException(409, f'{node}: RPC started with a different source revision')
                    expected = members[node].get('rpc_capabilities') or members[node].get('rpc_devices') or []
                    if result.get('rpc_devices') != expected:
                        raise HTTPException(409, f'{node}: RPC device inventory changed; refresh the plan')
                    member = self.service.state().members.get(node)
                    if member is None:
                        raise HTTPException(409, f'{node}: worker left during RPC startup')
                    member.update(process_pid=result['pid'], process_role='worker', rpc_devices=result['rpc_devices'], rpc_status='serving')
                if self.cancel.is_set():
                    raise HTTPException(409, 'Run was cancelled')
                yield
                if self.cancel.is_set():
                    raise HTTPException(409, 'Run was cancelled')
                self.pid = self.service.state().process_pid
                if self.pid:
                    token, _ = native.require_native()._process_identity(self.pid)
                    self.token = str(token)
                self.phase = 'serving'
                if control is not None:
                    control.registry.running({**self.runtime_snapshot, 'pid': self.pid})
                self.runtime_error = None
                self.healthy_since = time.monotonic()
                self._persist()
            except BaseException as error:
                self.body = None
                self.stop_current()
                self.phase = 'failed'
                self.service.state().apply_error = str(getattr(error, 'detail', error))[:512]
                self.service.state().save()
                self._persist()
                raise

    async def monitor(self) -> None:
        while not self.service.closed:
            await asyncio.sleep(5)
            # Renewal also runs during startup while the HTTP thread owns the
            # lifecycle lock. It cannot launch or release processes.
            if self.phase not in ('starting', 'serving') or self.run_id is None or self.cancel.is_set():
                continue
            run_id, workers, pid, token = self.run_id, list(self.workers), self.pid, self.token
            phase, generation = self.phase, self.generation
            results = await self._commands(run_id, workers, 'renew')
            if phase == 'starting':
                continue
            healthy = bool(pid and native.process_alive(pid))
            if healthy:
                try:
                    healthy = str(native.require_native()._process_identity(pid)[0]) == token
                except (RuntimeError, ValueError, OSError):
                    healthy = False
            healthy = healthy and all(result.get('status') == 'serving' for result in results.values())
            healthy = healthy and self.runtime_error is None
            if healthy and self.body and self.body.network_id:
                network = self.service.store.network(UUID(self.body.network_id))
                healthy = bool(network and network.observed_state == 'ready')
            if healthy:
                if time.monotonic() - self.healthy_since >= 60:
                    self.retries = 0
                continue
            if generation != self.generation or self.cancel.is_set():
                continue
            if self.recovery_task is None or self.recovery_task.done():
                self.recovery_task = asyncio.create_task(asyncio.to_thread(self._recover, generation))

    def _recover(self, generation: int) -> None:
        with self.lock:
            if generation != self.generation or self.cancel.is_set() or self.body is None:
                return
            body = self.body.model_copy(deep=True)
            workers = set(self.workers)
            self.phase = 'recovering'
            self.recovery_workers = workers
            state = self.service.state()
            state.apply_error = 'RPC worker failed; the master is restarting the model'
            state.save()
            try:
                self.stop_current()
            except HTTPException:
                # Keep ownership until reconnect/lease expiration permits cleanup.
                pass
        while self.retries < 3:
            delay = (1, 2, 4)[self.retries]
            self.retries += 1
            if self.cancel.wait(delay) or generation != self.generation:
                return
            with self.lock:
                if self.cancel.is_set() or generation != self.generation:
                    return
                try:
                    # Retain the original participants; recovery cannot silently
                    # migrate an active workload onto newly joined machines.
                    original = self.service.state().members
                    missing = workers - original.keys()
                    if missing:
                        raise HTTPException(503, f"Recovery workers unavailable: {', '.join(sorted(missing))}")
                    with use_runtime(self.runtime_snapshot):
                        self.run(body)
                    self.recovery_workers = None
                    return
                except Exception as error:
                    self.phase = 'recovering'
                    state.apply_error = f'Automatic restart {self.retries}/3 failed: {error}'[:512]
                    state.save()
        with self.lock:
            if generation == self.generation and not self.cancel.is_set():
                self.body = None
                self.phase = 'failed'
                self.recovery_workers = None
                state.apply_error = 'Automatic model restart failed after 3 attempts; inspect worker RPC logs and run again'
                state.save()

    async def close(self) -> None:
        self.cancel.set()
        self.generation += 1
        if self.recovery_task:
            await self.recovery_task
