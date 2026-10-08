"""Worker pairing convergence for the signed node channel.

A pinned master peer can outlive the master's own pairing record (for example a
restored ``cluster.json``). The worker must re-assert its pairing then, otherwise
it retries a handshake the master keeps refusing and the signed runtime channel
stays dead forever.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kllm.agent.services import network_channel
from kllm.agent.services.network_channel import NetworkChannelHub


class _Scheduler:
    """Run the channel loop without its one-second backoff."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def sleep(self, _seconds):
        await self._real.sleep(0)


def _hub(master_base, bootstrap):
    peer = SimpleNamespace(expected_role='master', node_id='master')
    hub = NetworkChannelHub(
        SimpleNamespace(peers=lambda: [peer]),
        SimpleNamespace(),
        lambda: SimpleNamespace(role='worker'),
        master_base,
        lambda *args: None,
    )
    hub.bootstrap = bootstrap
    return hub


def test_worker_reasserts_pairing_after_a_refused_handshake(monkeypatch):
    monkeypatch.setattr(network_channel, 'asyncio', _Scheduler(asyncio))
    calls = []

    async def scenario():
        async def bootstrap():
            calls.append('bootstrap')
            hub.stopping = True
        hub = _hub(lambda: 'http://127.0.0.1:1', bootstrap)
        await hub.client_loop()

    asyncio.run(scenario())
    assert calls == ['bootstrap']


def test_refused_handshakes_do_not_hammer_the_bootstrap(monkeypatch):
    monkeypatch.setattr(network_channel, 'asyncio', _Scheduler(asyncio))
    calls, ticks = [], []

    async def scenario():
        holder = {}

        def master_base():
            ticks.append(1)
            if len(ticks) >= 5:
                holder['hub'].stopping = True
            return 'http://127.0.0.1:1'

        async def bootstrap():
            calls.append('bootstrap')

        holder['hub'] = _hub(master_base, bootstrap)
        await holder['hub'].client_loop()

    asyncio.run(scenario())
    assert len(ticks) >= 5
    assert calls == ['bootstrap']
