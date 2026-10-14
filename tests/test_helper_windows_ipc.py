"""Real isolated Windows pipes; protected installation/policy remain separate."""

import ctypes
import asyncio
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import struct
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.managed_network.interactors import helper
from kllm.shared.platform.base import PlatformRefusal
import test_prima_ports as port_contract_tests

prepared = port_contract_tests.prepared

if os.name == "nt":
    import msvcrt

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe transport")


@contextmanager
def pipe_server(mode):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    account = "ipc-fixture-" + str(uuid4())
    name = r"\\.\pipe\kllm-network-" + account
    handle = kernel.CreateNamedPipeW(name, 3 | 0x00080000, 8, 1, 4096, 4096, 1000, None)
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    file = os.fdopen(fd, "r+b", buffering=0)
    entered, release = threading.Event(), threading.Event()
    received, errors = [], []

    def serve():
        try:
            if not kernel.ConnectNamedPipe(handle, None) and ctypes.get_last_error() != 535:
                raise ctypes.WinError(ctypes.get_last_error())
            entered.set()
            if mode == "blocked-write":
                release.wait(3)
                return
            header = file.read(4)
            if len(header) != 4:
                return
            count = struct.unpack("!I", header)[0]
            data = bytearray()
            while len(data) < count:
                chunk = file.read(count - len(data))
                if not chunk:
                    return
                data.extend(chunk)
            received.append(json.loads(data))
            if mode == "header":
                file.write(b"\0")
            elif mode == "body":
                file.write(struct.pack("!I", 10) + b"{")
            elif mode == "oversized":
                file.write(struct.pack("!I", 65537))
                kernel.FlushFileBuffers(handle)
                return
            elif mode == "invalid":
                file.write(struct.pack("!I", 2) + b"[]")
                kernel.FlushFileBuffers(handle)
                return
            elif mode == "valid":
                raw = json.dumps({"result": {"phase": "released"}}).encode()
                for chunk in (struct.pack("!I", len(raw))[:1], struct.pack("!I", len(raw))[1:], raw[:2], raw[2:]):
                    file.write(chunk)
                kernel.FlushFileBuffers(handle)
                return
            release.wait(3)
        except OSError as error:
            errors.append(error)
        finally:
            kernel.DisconnectNamedPipe(handle)
            file.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield account, received, entered
    finally:
        release.set()
        thread.join(4)
        assert not thread.is_alive(), "Isolated pipe server did not stop"
        assert not errors, errors


@pytest.mark.parametrize("mode", ["header", "body", "blocked-write"])
def test_stalled_named_pipe_request_has_a_total_deadline(monkeypatch, mode):
    # Only installation verification is substituted. Real Win32 pipe I/O must be
    # cancelled and settled before request returns or its serialization lock opens.
    monkeypatch.setattr(helper, "HELPER_QUERY_TIMEOUT", 0.15, raising=False)
    with pipe_server(mode) as (account, _, entered), ThreadPoolExecutor() as executor:
        client = object.__new__(helper.HelperClient)
        client.node_id, client.account, client.lock = "fixture", account, threading.Lock()
        client._verify_windows_server = lambda _: None
        start = time.monotonic()
        future = executor.submit(client.request, "status", **({"padding": "x" * 64000} if mode == "blocked-write" else {}))
        try:
            assert entered.wait(1)
            with pytest.raises(PlatformRefusal) as error:
                future.result(timeout=1)
            assert error.value.code == "TIMEOUT"
            assert time.monotonic() - start < 1
            assert client.lock.acquire(blocking=False)
            client.lock.release()
        finally:
            # The fixture eventually disconnects even for the original unbounded
            # client, so a failing regression cannot strand a test process.
            pass


@pytest.mark.parametrize("mode", ["valid", "oversized", "invalid"])
def test_fragmented_reply_and_invalid_response_boundaries(monkeypatch, mode):
    with pipe_server(mode) as (account, received, _):
        client = object.__new__(helper.HelperClient)
        client.node_id, client.account, client.lock = "fixture", account, threading.Lock()
        client._verify_windows_server = lambda _: None
        if mode == "valid":
            assert client.request("status") == {"phase": "released"}
        else:
            with pytest.raises(PlatformRefusal) as error:
                client.request("status")
            assert error.value.code == "HELPER_REFUSED"
        assert received == [{"action": "status"}]


def test_real_server_image_is_verified_before_any_request_bytes():
    with pipe_server("valid") as (account, received, _):
        client = object.__new__(helper.HelperClient)
        client.node_id, client.account, client.lock = "fixture", account, threading.Lock()
        with pytest.raises(PlatformRefusal) as error:
            client.request("status")
        assert error.value.code == "ADMIN_REQUIRED"
        assert not received, "An unprotected server must receive no payload"


def test_queue_wait_uses_the_same_deadline_and_original_operation_settles(monkeypatch):
    monkeypatch.setattr(helper, "HELPER_QUERY_TIMEOUT", 0.1)
    monkeypatch.setattr(helper, "HELPER_MUTATION_TIMEOUT", 0.3)
    with pipe_server("header") as (account, _, entered), ThreadPoolExecutor() as executor:
        client = object.__new__(helper.HelperClient)
        client.node_id, client.account, client.lock = "fixture", account, threading.Lock()
        client._verify_windows_server = lambda _: None
        first = executor.submit(client.request, "prima_ports_apply")
        assert entered.wait(1)
        with pytest.raises(PlatformRefusal) as error:
            executor.submit(client.request, "status").result(timeout=1)
        assert error.value.code == "TIMEOUT" and not first.done()
        with pytest.raises(PlatformRefusal) as error:
            first.result(timeout=1)
        assert error.value.code == "TIMEOUT"
        assert not client.lock.locked()


@pytest.mark.asyncio
async def test_cancelled_prima_wait_settles_real_pipe_io_before_return(monkeypatch):
    from kllm.modules.managed_network.interactors.prima_admission import _port_call

    monkeypatch.setattr(helper, "HELPER_MUTATION_TIMEOUT", 0.2)
    with pipe_server("body") as (account, _, entered):
        client = object.__new__(helper.HelperClient)
        client.node_id, client.account, client.lock = "fixture", account, threading.Lock()
        client._verify_windows_server = lambda _: None
        task = asyncio.create_task(_port_call(client.request, "prima_ports_apply"))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.03)
        assert not task.done() and client.lock.locked()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not client.lock.locked()
        assert not any(t.name == "kllm-helper-io-deadline" for t in threading.enumerate())


def test_timed_out_admission_retains_durable_owner_until_cleanup_ack(prepared, monkeypatch):
    from kllm.modules.primacpp.interactors.port_runs import PortSupervisor

    manifest, _, path = prepared
    monkeypatch.setattr(helper, "HELPER_MUTATION_TIMEOUT", 0.15)
    client = object.__new__(helper.HelperClient)
    client.node_id, client.lock = "fixture", threading.Lock()
    client._verify_windows_server = lambda _: None
    rank = SimpleNamespace(owner=None)
    platform = SimpleNamespace(release_prima_ports=lambda network, run: client.request(
        "prima_ports_release", network=network, run_id=run))
    ports = PortSupervisor(path.parent / "ports.json", platform=lambda: platform, rank=lambda: rank)
    with pipe_server("header") as (account, received, _):
        client.account = account
        with pytest.raises(PlatformRefusal) as error:
            ports.admit(manifest, {"sender": "master", "authority_digest": "a" * 64},
                lambda: client.request("prima_ports_apply", network=manifest.network_id,
                    run_id=manifest.run_id, wire="complete-original-signed-envelope"), lambda: None)
        assert error.value.code == "TIMEOUT"
        assert received == [{"action": "prima_ports_apply", "network": manifest.network_id,
            "run_id": manifest.run_id, "wire": "complete-original-signed-envelope"}]
        assert ports.owner["phase"] == "cleanup_pending"
        assert json.loads(ports.path.read_text())["manifest_digest"] == manifest.digest
    with pipe_server("valid") as (account, received, _):
        client.account = account
        assert ports.release(manifest.network_id, manifest.run_id) == {"phase": "released"}
        assert ports.owner is None and json.loads(ports.path.read_text()) is None
        assert received == [{"action": "prima_ports_release", "network": manifest.network_id,
            "run_id": manifest.run_id}]
