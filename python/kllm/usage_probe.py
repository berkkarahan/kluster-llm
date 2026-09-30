"""Private, bounded helper ownership for potentially blocking native sources."""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

LIMIT = 65536
_lock = threading.RLock()
_slots: dict[str, "Slot"] = {}
_node: str | None = None
_generation = uuid.uuid4().hex
_sequence = 0


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def decode(frame: bytes, *, framed=True):
    if len(frame) > LIMIT or (framed and not frame.endswith(b"\n")):
        raise ValueError("invalid helper frame size/framing")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("non-finite JSON value")

    return json.loads(frame.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def metric(unit, scope, reason):
    return dict(value=None, unit=unit, scope=scope,
                status="unsupported" if reason in ("unsupported_counter", "native_unavailable") else "error",
                reason=reason, detail=None, observed_at=utc(), interval_start=None,
                interval_end=None, duration_ms=None, source="native.supervisor")


def failure(source, reason, selections=(), node_id=""):
    if source == "gpu":
        return dict(host=None, devices=[], processes=[], device_discovery=dict(
            status="unsupported" if reason in ("native_unavailable", "unsupported_counter") else "error",
            reason=reason, observed_at=utc()))
    return dict(host=dict(logical_cpu_count=None, cpu=metric("percent_machine", "host", reason),
                         memory=dict(pool_id="host:ram", **{k: metric("bytes", "host", reason)
                                     for k in ("total", "used", "free", "available")})),
                devices=[], processes=[dict(**p, node_id=node_id,
                    lifetime_id=f"{node_id}:{p['pid']}:{p['started_at']}", state="unavailable", identity_verified=False,
                    cpu=metric("percent_machine", "process", reason), rss=metric("bytes", "process", reason))
                    for p in selections], device_discovery=None)


def helper_command(source):
    # Windows' venv executable is a redirector process. Own the real interpreter PID.
    executable = (getattr(sys, "_base_executable", None) or sys.executable) if sys.platform == "win32" else sys.executable
    return [executable, "-m", "kllm.usage_probe", "--source", source]


def helper_environment():
    # The helper needs interpreter/loader paths, not the agent's credentials or home state.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
               "APPDATA", "LOCALAPPDATA", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "LANG",
               "LC_ALL", "LC_CTYPE", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    if sys.platform == "win32":
        environment["__PYVENV_LAUNCHER__"] = sys.executable
    return environment


class Job:
    """Windows kill-on-parent-exit ownership; control-pipe EOF covers the startup gap."""
    def __init__(self, process):
        self.handle = None
        if sys.platform != "win32":
            return
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        size = ctypes.c_size_t

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working", size), ("max_working", size),
                        ("active", wintypes.DWORD), ("affinity", size),
                        ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("process_memory", size), ("job_memory", size),
                        ("peak_process", size), ("peak_job", size)]

        dll.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        dll.CreateJobObjectW.restype = wintypes.HANDLE
        dll.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        dll.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        self.dll = dll
        self.handle = dll.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not dll.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not dll.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close()
            raise OSError(ctypes.get_last_error(), "unable to own probe helper")

    def close(self):
        if self.handle:
            self.dll.CloseHandle(self.handle)
            self.handle = None


class Slot:
    def __init__(self, source):
        self.source = source
        self.process = None
        self.job = None
        self.future = None
        self.key = None
        self.stopping = False
        self.cancelled = False
        self.cancel_reason = "timeout"
        self.generation = uuid.uuid4().hex
        self.counter = 0
        self.jobs = queue.Queue(maxsize=1)
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"kllm-probe-{source}")
        self.thread.start()

    def request(self, node, selections):
        key = encode([node, selections])
        with _lock:
            if self.stopping:
                stopped = concurrent.futures.Future()
                stopped.set_result(failure(self.source, "source_quarantined", selections, node))
                return stopped
            if self.future and not self.future.done():
                if key == self.key and not self.cancelled:
                    return self.future
                busy = concurrent.futures.Future()
                busy.set_result(failure(self.source, "source_quarantined" if self.cancelled else "source_busy", selections, node))
                return busy
            self.key, self.cancelled = key, False
            self.cancel_reason = "timeout"
            self.future = concurrent.futures.Future()
            self.counter += 1
            request = dict(version=1, generation=self.generation, request_id=self.counter,
                           node_id=node, managed_processes=selections,
                           reply_budget_bytes=24000)
            self.jobs.put_nowait((request, self.future))
            return self.future

    def kill(self, reason="timeout"):
        with _lock:
            self.cancelled = True
            self.cancel_reason = reason
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.kill()
                except OSError:
                    pass  # Slot remains occupied until the reader confirms exit.

    def run(self):
        while True:
            item = self.jobs.get()
            if item is None:
                return
            request, future = item
            try:
                if self.process is None:
                    process = subprocess.Popen(helper_command(self.source), stdin=subprocess.PIPE,
                                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                               env=helper_environment(), close_fds=True,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    with _lock:
                        self.process = process
                        self.job = Job(process)
                        if self.cancelled or self.stopping:
                            self.kill(self.cancel_reason)
                if self.cancelled or self.stopping:
                    raise RuntimeError("cancelled probe")
                self.process.stdin.write(encode(request))
                self.process.stdin.flush()
                reply = decode(self.process.stdout.readline(LIMIT + 1))
                if not isinstance(reply, dict) or set(reply) != {"version", "generation", "request_id", "result"} or type(reply["version"]) is not int or type(reply["request_id"]) is not int or any(reply[k] != request[k] for k in ("version", "generation", "request_id")):
                    raise ValueError("invalid helper response identity")
                if len(encode(reply)) > request["reply_budget_bytes"]:
                    raise ValueError("helper ignored reply budget")
                result = reply["result"]
                if not isinstance(result, dict) or set(result) != {"host", "devices", "processes", "device_discovery"}:
                    raise ValueError("invalid source shape")
                # Optional extension is checked before this source worker is started.
                from kllm import _native
                _native._validate_source_json(self.source, json.dumps(result, allow_nan=False))
                if self.source == "host":
                    actual = [{k: p[k] for k in ("pid", "role", "start_token", "started_at")} for p in result["processes"]]
                    if actual != request["managed_processes"] or any(p["node_id"] != request["node_id"] for p in result["processes"]):
                        raise ValueError("helper returned another process selection")
            except Exception:
                result = failure(self.source, "source_error", request["managed_processes"], request["node_id"])
                self.kill("source_error")
            finally:
                if self.cancelled or self.stopping:
                    process = self.process
                    if process:
                        # Reaping happens on this fixed worker, never on a request's response path.
                        process.wait()
                        process.stdin.close()
                        process.stdout.close()
                    if self.job:
                        self.job.close()
                    self.process = self.job = None
                    self.generation = uuid.uuid4().hex
            if self.cancelled:
                result = failure(self.source, self.cancel_reason, request["managed_processes"], request["node_id"])
            if not future.done():
                future.set_result(result)
            if self.stopping:
                return


def close():
    global _node, _generation, _sequence
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
            slot.process.stdin.close()
            slot.process.stdout.close()
            if slot.job:
                slot.job.close()
    with _lock:
        for source, slot in list(_slots.items()):
            if not slot.thread.is_alive() and (slot.process is None or slot.process.poll() is not None):
                del _slots[source]
        if not _slots:
            _node, _generation, _sequence = None, uuid.uuid4().hex, 0


def probe(node_id, selections, *, supported=True):
    global _node, _generation, _sequence
    started, deadline = utc(), time.monotonic() + 1.8
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 128:
        raise ValueError("node_id must contain 1–128 characters")
    if not isinstance(selections, list) or len(selections) > 8:
        raise ValueError("managed_processes must contain at most eight selections")
    selections = copy.deepcopy(selections)
    if selections:
        # Deferred import: runtime imports native, which imports this supervisor.
        from kllm import runtime
        registered = {p.pid: p for p in runtime.load().children}
        seen = set()
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {"pid", "role", "start_token", "started_at"}:
                raise ValueError("invalid managed process selection fields")
            pid = selection["pid"]
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid > 0xffffffff or pid in seen:
                raise ValueError("invalid or duplicate managed PID")
            seen.add(pid)
            if selection["role"] not in ("master", "worker") or not isinstance(selection["start_token"], str) or not 1 <= len(selection["start_token"]) <= 128 or not isinstance(selection["started_at"], str) or not selection["started_at"].endswith("Z"):
                raise ValueError("invalid process role/start identity")
            try:
                datetime.fromisoformat(selection["started_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid process start timestamp") from exc
            child = registered.get(pid)
            if not child or not child.start_token or not child.started_at or selection["start_token"] != child.start_token or selection["started_at"] != child.started_at:
                raise ValueError("process selection does not match registered start identity")
            expected_role = {"llama-server": "master", "rpc-server": "worker"}.get(child.kind)
            if selection["role"] != expected_role:
                raise ValueError("process role does not match registration")
    if not _lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise RuntimeError("probe supervisor busy")
    try:
        if _node is not None and _node != node_id:
            raise ValueError("probe supervisor already belongs to another node")
        _node = node_id
        if _sequence >= 9007199254740991:
            _generation, _sequence = uuid.uuid4().hex, 0
        _sequence += 1
        generation, sequence = _generation, _sequence
        pending = {}
        if supported:
            for source in ("host", "gpu"):
                slot = _slots.setdefault(source, None)
                if slot is None:
                    slot = _slots[source] = Slot(source)
                pending[source] = (slot, slot.request(node_id, selections if source == "host" else []))
    finally:
        _lock.release()
    results = {}
    for source in ("host", "gpu"):
        if not supported:
            results[source] = failure(source, "native_unavailable", selections if source == "host" else (), node_id)
            continue
        slot, future = pending[source]
        try:
            results[source] = future.result(timeout=max(0, deadline - time.monotonic()))
        except concurrent.futures.TimeoutError:
            slot.kill()
            results[source] = failure(source, "timeout", selections if source == "host" else (), node_id)
    host = results["host"]["host"]
    result = dict(schema_version=1, node_id=node_id, generation=generation, sequence=sequence,
                  snapshot_id=f"{generation}:{sequence}", requested_at=started, completed_at=utc(),
                  host=host, devices=results["gpu"]["devices"],
                  device_discovery=results["gpu"]["device_discovery"], processes=results["host"]["processes"],
                  totals=memory_totals(host, results["gpu"]),
                  collector=dict(kind="native", platform=sys.platform))
    if len(encode(dict(node_id=node_id, readings=[], resources=result))) > LIMIT:
        # Pool totals can expand GPU identities across several aggregates. Fail that
        # source explicitly instead of dropping the independent host/process readings.
        failed_gpu = failure("gpu", "source_error")
        result.update(devices=[], device_discovery=failed_gpu["device_discovery"],
                      totals=memory_totals(host, failed_gpu))
        if len(encode(dict(node_id=node_id, readings=[], resources=result))) > LIMIT:
            raise RuntimeError("host probe output exceeded its reserved budget")
    return copy.deepcopy(result)


def memory_totals(host, gpu):
    pools = {}
    for device in gpu["devices"]:
        for view in device["memory"]:
            if view["kind"] == "dedicated":
                pools.setdefault(view["pool_id"], view)

    def total(field, physical=False):
        contributors = {key: view[field] for key, view in pools.items()}
        if physical:
            contributors["host:ram"] = host["memory"]["total"]
        included = [key for key, m in contributors.items() if m["status"] == "measured"]
        missing = [key for key in contributors if key not in included]
        complete = gpu["device_discovery"]["status"] == "measured" and not missing
        return dict(value=sum(contributors[k]["value"] for k in included) if included else None,
                    unit="bytes", status=("complete" if complete else "partial") if included else "unavailable",
                    included_pool_ids=included, missing_pool_ids=missing,
                    observed_at=[contributors[k]["observed_at"] for k in included])
    return dict(dedicated_capacity=total("total"), dedicated_used=total("used"), physical_capacity=total("total", True))


def helper(source):
    # Optional extension: an older wheel must fail this private helper explicitly.
    from kllm import _native

    jobs = queue.Queue(maxsize=1)

    def collect():
        collector = _native._UsageCollector(source)
        while True:
            request = jobs.get()
            try:
                result = json.loads(collector.collect_json(json.dumps(request)))
                reply = {k: request[k] for k in ("version", "generation", "request_id")}
                reply["result"] = result
                budget = request["reply_budget_bytes"]
                if len(encode(reply)) > budget:
                    def strip_details(value):
                        if isinstance(value, dict):
                            value.pop("detail", None)
                            for item in value.values():
                                strip_details(item)
                        elif isinstance(value, list):
                            for item in value:
                                strip_details(item)
                    strip_details(reply["result"])
                if len(encode(reply)) > budget:
                    reply["result"] = failure(source, "source_error", request["managed_processes"], request["node_id"])
                    if source == "host":
                        reply["result"]["host"]["cpu"]["detail"] = "native source payload exceeds reply budget"
                if len(encode(reply)) > budget:
                    os._exit(2)
                sys.stdout.buffer.write(encode(reply))
                sys.stdout.buffer.flush()
            except Exception:
                os._exit(2)

    threading.Thread(target=collect, daemon=True).start()
    try:
        while True:
            frame = sys.stdin.buffer.readline(LIMIT + 1)
            if not frame:
                os._exit(0)  # Also terminates a blocked GIL-releasing native call on parent death.
            request = decode(frame)
            jobs.put_nowait(request)
    except Exception:
        os._exit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("host", "gpu"), required=True)
    helper(parser.parse_args().source)
