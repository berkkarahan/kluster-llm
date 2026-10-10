"""Worker pairing convergence for the signed node channel.

A pinned master peer can outlive the master's own pairing record (for example a
restored ``cluster.json``). The worker must re-assert its pairing when the master
refuses the handshake, otherwise it retries a socket the master keeps closing and
the signed runtime channel stays dead. A dropped path must reconnect without that
re-pair.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import websockets

from kllm.modules.managed_network.interactors import network_channel
from kllm.modules.managed_network.interactors.network_channel import ChannelSession, NetworkChannelHub, _handshake_refused, reconnect_delay


class _Scheduler:
    """Run the channel loop without its one-second backoff."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def sleep(self, _seconds):
        await self._real.sleep(0)


def _hub(master_base, bootstrap):
    peer = SimpleNamespace(expected_role="master", node_id="master")

    class _Store:
        def peers(self):
            return [peer]

        async def arun(self, callback, *args, **kwargs):
            return callback(*args, **kwargs)

    hub = NetworkChannelHub(
        _Store(),
        SimpleNamespace(),
        lambda: SimpleNamespace(role="worker"),
        master_base,
        lambda *args: None,
    )
    hub.bootstrap = bootstrap
    return hub


class _Refused(websockets.WebSocketException):
    def __init__(self) -> None:
        super().__init__("handshake refused")
        self.rcvd = SimpleNamespace(code=1008)


def _refuse_connect(*_args, **_kwargs):
    raise _Refused()


def test_handshake_refusal_is_not_a_dropped_path():
    assert _handshake_refused(_Refused()) is True
    assert _handshake_refused(OSError("timed out")) is False
    assert _handshake_refused(TimeoutError()) is False


def test_worker_reasserts_pairing_after_a_refused_handshake(monkeypatch):
    monkeypatch.setattr(network_channel, "asyncio", _Scheduler(asyncio))
    monkeypatch.setattr(websockets, "connect", _refuse_connect)
    calls = []

    async def scenario():
        async def bootstrap():
            calls.append("bootstrap")
            hub.stopping = True

        hub = _hub(lambda: "http://127.0.0.1:1", bootstrap)
        await hub.client_loop()

    asyncio.run(scenario())
    assert calls == ["bootstrap"]


def test_refused_handshakes_do_not_hammer_the_bootstrap(monkeypatch):
    monkeypatch.setattr(network_channel, "asyncio", _Scheduler(asyncio))
    monkeypatch.setattr(websockets, "connect", _refuse_connect)
    calls, ticks = [], []

    async def scenario():
        holder = {}

        def master_base():
            ticks.append(1)
            if len(ticks) >= 5:
                holder["hub"].stopping = True
            return "http://127.0.0.1:1"

        async def bootstrap():
            calls.append("bootstrap")

        holder["hub"] = _hub(master_base, bootstrap)
        await holder["hub"].client_loop()

    asyncio.run(scenario())
    assert len(ticks) >= 5
    assert calls == ["bootstrap"]


def test_reconnect_delay_grows_after_failure_and_resets_after_a_session():
    assert reconnect_delay(0.5, established=False) == 1.0
    assert reconnect_delay(1.0, established=False) == 2.0
    assert reconnect_delay(4.0, established=False) == 8.0
    assert reconnect_delay(8.0, established=False) == 8.0
    assert reconnect_delay(8.0, established=True) == 1.0


def test_dropped_path_reconnects_without_repairing(monkeypatch):
    monkeypatch.setattr(network_channel, "asyncio", _Scheduler(asyncio))

    def connect(*_args, **_kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(websockets, "connect", connect)
    calls, ticks = [], []

    async def scenario():
        holder = {}

        def master_base():
            ticks.append(1)
            if len(ticks) >= 4:
                holder["hub"].stopping = True
            return "http://10.10.10.1:18765"

        async def bootstrap():
            calls.append("bootstrap")

        holder["hub"] = _hub(master_base, bootstrap)
        await holder["hub"].client_loop()

    asyncio.run(scenario())
    assert len(ticks) >= 4
    assert calls == []


def test_repeated_path_failures_back_off(monkeypatch):
    # Jitter is 0.5 + 0.5*random(). random() == 1 keeps the recorded sleep equal to the delay.
    monkeypatch.setattr(network_channel.random, "random", lambda: 1.0)
    delays: list[float] = []

    class RecordingScheduler(_Scheduler):
        async def sleep(self, seconds):
            delays.append(seconds)
            await self._real.sleep(0)

    monkeypatch.setattr(network_channel, "asyncio", RecordingScheduler(asyncio))
    monkeypatch.setattr(network_channel.random, "random", lambda: 0.0)
    attempts = 0

    def connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts >= 3:
            holder["hub"].stopping = True
        raise OSError("network unreachable")

    monkeypatch.setattr(websockets, "connect", connect)
    holder: dict = {}

    async def scenario():
        holder["hub"] = _hub(lambda: "http://10.10.10.1:18765", lambda: None)
        await holder["hub"].client_loop()

    asyncio.run(scenario())
    assert delays == [0.5, 1.0, 2.0]


def test_reconnect_delay_backs_off_until_a_session_exists():
    assert network_channel.reconnect_delay(0.5, established=False) == 1.0
    assert network_channel.reconnect_delay(1.0, established=False) == 2.0
    assert network_channel.reconnect_delay(4.0, established=False) == 8.0
    assert network_channel.reconnect_delay(8.0, established=False) == 8.0
    assert network_channel.reconnect_delay(8.0, established=True) == 1.0


@pytest.mark.asyncio
async def test_send_timeout_drops_the_signed_session():
    closed: dict[str, int] = {}

    class Wire:
        async def send(self, _text: str) -> None:
            raise TimeoutError

        async def close(self, code: int = 1000) -> None:
            closed["code"] = code

    class Fence:
        connected = True

        def check_live(self) -> None:
            return None

    session = ChannelSession(
        Wire(),
        SimpleNamespace(sign=lambda _payload: "sig"),
        Fence(),
        master=True,
    )
    command = SimpleNamespace(sequence=0, model_dump=lambda mode="json": {"type": "command"})

    with pytest.raises(TimeoutError, match="send timed out"):
        await session.send(command)

    assert session.closed is True
    assert session.fence.connected is False
    assert closed["code"] == 1001
