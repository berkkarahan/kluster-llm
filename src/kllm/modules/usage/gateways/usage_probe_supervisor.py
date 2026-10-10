"""Bounded process supervision for potentially blocking native probes."""

from __future__ import annotations

import concurrent.futures
import ctypes
from ctypes import wintypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from importlib import import_module
from typing import Any

from kllm.modules.usage.gateways.usage_probe_protocol import LIMIT, decode, encode, failure

_lock = threading.RLock()
_slots: dict[str, Slot] = {}


def helper_command(source: str) -> list[str]:
    # Windows' venv executable is a redirector process. Own the real interpreter PID.
    executable = (
        (getattr(sys, "_base_executable", None) or sys.executable)
        if sys.platform == "win32"
        else sys.executable
    )
    return [executable, "-m", "kllm.modules.usage.gateways.usage_probe", "--source", source]


def helper_environment() -> dict[str, str]:
    # The helper needs interpreter/loader paths, not the agent's credentials or home state.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    if sys.platform == "win32":
        environment["__PYVENV_LAUNCHER__"] = sys.executable
    return environment


class Job:
    """Windows kill-on-parent-exit ownership; control-pipe EOF covers the startup gap."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.handle = None
        self.dll: Any = None
        if sys.platform != "win32":
            return
        dll: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        size = ctypes.c_size_t

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_working", size),
                ("max_working", size),
                ("active", wintypes.DWORD),
                ("affinity", size),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_uint64 * 6),
                ("process_memory", size),
                ("job_memory", size),
                ("peak_process", size),
                ("peak_job", size),
            ]

        dll.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        dll.CreateJobObjectW.restype = wintypes.HANDLE
        dll.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dll.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        self.dll = dll
        self.handle = dll.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (
            not self.handle
            or not dll.SetInformationJobObject(
                self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            )
            or not dll.AssignProcessToJobObject(self.handle, int(process._handle))
        ):
            self.close()
            raise OSError(ctypes.get_last_error(), "unable to own probe helper")

    def close(self) -> None:
        if self.handle:
            self.dll.CloseHandle(self.handle)
            self.handle = None


class Slot:
    def __init__(
        self,
        source: str,
        command_builder: Callable[[str], list[str]],
        environment_builder: Callable[[], dict[str, str]],
    ) -> None:
        self.source = source
        self.command_builder = command_builder
        self.environment_builder = environment_builder
        self.process: subprocess.Popen[bytes] | None = None
        self.job: Job | None = None
        self.future: Future[dict[str, Any]] | None = None
        self.key: bytes | None = None
        self.stopping = False
        self.cancelled = False
        self.cancel_reason = "timeout"
        self.generation = uuid.uuid4().hex
        self.counter = 0
        self.jobs: queue.Queue[tuple[dict[str, Any], Future[dict[str, Any]]] | None] = queue.Queue(
            maxsize=1
        )
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"kllm-probe-{source}")
        self.thread.start()

    def request(self, node: str, selections: list[dict[str, Any]]) -> Future[dict[str, Any]]:
        key = encode([node, selections])
        with _lock:
            if self.stopping:
                stopped: Future[dict[str, Any]] = Future()
                stopped.set_result(failure(self.source, "source_quarantined", selections, node))
                return stopped
            if self.future and not self.future.done():
                if key == self.key and not self.cancelled:
                    return self.future
                busy: Future[dict[str, Any]] = Future()
                busy.set_result(
                    failure(
                        self.source,
                        "source_quarantined" if self.cancelled else "source_busy",
                        selections,
                        node,
                    )
                )
                return busy
            self.key, self.cancelled = key, False
            self.cancel_reason = "timeout"
            self.future = concurrent.futures.Future()
            self.counter += 1
            request = dict(
                version=1,
                generation=self.generation,
                request_id=self.counter,
                node_id=node,
                managed_processes=selections,
                reply_budget_bytes=24000,
            )
            self.jobs.put_nowait((request, self.future))
            return self.future

    def kill(self, reason: str = "timeout") -> None:
        with _lock:
            self.cancelled = True
            self.cancel_reason = reason
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.kill()
                except OSError:
                    pass  # Slot remains occupied until the reader confirms exit.

    def run(self) -> None:
        while True:
            item = self.jobs.get()
            if item is None:
                return
            request, future = item
            try:
                if self.process is None:
                    process = subprocess.Popen(
                        self.command_builder(self.source),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        env=self.environment_builder(),
                        close_fds=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    with _lock:
                        self.process = process
                        self.job = Job(process)
                        if self.cancelled or self.stopping:
                            self.kill(self.cancel_reason)
                if self.cancelled or self.stopping:
                    raise RuntimeError("cancelled probe")
                process = self.process
                if process is None or process.stdin is None or process.stdout is None:
                    raise RuntimeError("probe helper pipes are unavailable")
                process.stdin.write(encode(request))
                process.stdin.flush()
                reply = decode(process.stdout.readline(LIMIT + 1))
                if (
                    not isinstance(reply, dict)
                    or set(reply) != {"version", "generation", "request_id", "result"}
                    or type(reply["version"]) is not int
                    or type(reply["request_id"]) is not int
                    or any(reply[k] != request[k] for k in ("version", "generation", "request_id"))
                ):
                    raise ValueError("invalid helper response identity")
                if len(encode(reply)) > request["reply_budget_bytes"]:
                    raise ValueError("helper ignored reply budget")
                result = reply["result"]
                if not isinstance(result, dict) or set(result) != {
                    "host",
                    "devices",
                    "processes",
                    "device_discovery",
                }:
                    raise ValueError("invalid source shape")
                # Optional extension is checked before this source worker is started.
                native_module = import_module("kllm._native")
                native_module._validate_source_json(
                    self.source, json.dumps(result, allow_nan=False)
                )
                if self.source == "host":
                    actual = [
                        {k: p[k] for k in ("pid", "role", "start_token", "started_at")}
                        for p in result["processes"]
                    ]
                    if actual != request["managed_processes"] or any(
                        p["node_id"] != request["node_id"] for p in result["processes"]
                    ):
                        raise ValueError("helper returned another process selection")
            except Exception:
                result = failure(
                    self.source, "source_error", request["managed_processes"], request["node_id"]
                )
                self.kill("source_error")
            finally:
                if self.cancelled or self.stopping:
                    cleanup_process = self.process
                    if cleanup_process:
                        # Reaping happens on this fixed worker, never on a request's response path.
                        cleanup_process.wait()
                        if cleanup_process.stdin is not None:
                            cleanup_process.stdin.close()
                        if cleanup_process.stdout is not None:
                            cleanup_process.stdout.close()
                    if self.job:
                        self.job.close()
                    self.process = self.job = None
                    self.generation = uuid.uuid4().hex
            if self.cancelled:
                result = failure(
                    self.source,
                    self.cancel_reason,
                    request["managed_processes"],
                    request["node_id"],
                )
            if not future.done():
                future.set_result(result)
            if self.stopping:
                return


def close_slots() -> None:
    with _lock:
        slots = list(_slots.values())
        for slot in slots:
            slot.stopping = True
            slot.kill("source_quarantined")
            try:
                slot.jobs.put_nowait(None)
            except queue.Full:
                pass
    deadline = time.monotonic() + 1.8
    for slot in slots:
        slot.thread.join(max(0, deadline - time.monotonic()))
        if slot.process:
            try:
                slot.process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                continue  # Keep the slot quarantined; never replace an unreaped helper.
        if slot.process and slot.process.poll() is not None:
            if slot.process.stdin is not None:
                slot.process.stdin.close()
            if slot.process.stdout is not None:
                slot.process.stdout.close()
            if slot.job:
                slot.job.close()
    with _lock:
        for source, slot in list(_slots.items()):
            if not slot.thread.is_alive() and (
                slot.process is None or slot.process.poll() is not None
            ):
                del _slots[source]
