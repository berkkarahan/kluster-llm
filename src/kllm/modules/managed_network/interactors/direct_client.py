"""Administrator-owned native Prima policy process, over private correlated stdio."""

from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import threading
import time
from uuid import uuid4

from kllm.modules.managed_network.interactors.helper import HELPER_QUERY_TIMEOUT, HELPER_MUTATION_TIMEOUT, MUTATING_ACTIONS
from kllm.modules.runtimes.interactors.registry import atomic_json
from kllm.shared.platform.base import PlatformRefusal, is_admin
from kllm.shared.platform.windows_io import synchronous_io_deadline


class DirectClient:
    def __init__(self, home: Path, repository, grant):
        self.home, self.repository, self.grant = home / "prima-direct", repository, grant
        self.config = self.home / "authorization.json"
        self.journal = self.home / "authorization.journal.json"
        self.lock = threading.RLock()
        self.process = None
        self.bound = None
        self.closing = False
        self.probed = False
        self.input = bytearray()
        self.reply_length = None

    def _start(self):
        if os.name != "nt" or not is_admin():
            raise PlatformRefusal("ADMIN_REQUIRED", "Direct Prima admission requires an already elevated Windows agent")
        if self.closing:
            raise PlatformRefusal("CLEANUP_PENDING", "Previous direct native process is still closing")
        expected = self.grant()
        self.home.mkdir(parents=True, exist_ok=True)
        if self.config.exists():
            bound = json.loads(self.config.read_text())
        elif expected:
            bound = expected
            atomic_json(self.config, bound)
        else:
            raise PlatformRefusal("UNPAIRED_IDENTITY", "Join the intended cluster before direct listener admission")
        repo = self.repository()
        binary = Path(repo) / "vendor" / "bin" / "kllm-network-helper.exe" if repo else None
        if binary is None or not binary.is_file():
            raise PlatformRefusal("UPGRADE_REQUIRED", "Build the native helper for direct Prima admission")
        self.home.mkdir(parents=True, exist_ok=True)
        with (self.home / "native.log").open("ab") as log:
            self.process = subprocess.Popen([str(binary.resolve()), "--stdio-prima", str(self.config.resolve())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.bound = bound
        self.probed = False
        self.input.clear()
        self.reply_length = None

    def _exchange(self, action, values, deadline):
        process = self.process
        if process is None or process.poll() is not None:
            raise PlatformRefusal("UPGRADE_REQUIRED", "Direct native helper exited; update the helper and review pending cleanup")
        request_id = str(uuid4())
        raw = json.dumps({"action": action, **values, "request_id": request_id}, allow_nan=False).encode()
        if len(raw) > 65536:
            raise PlatformRefusal("INVALID_INPUT", "Direct helper request exceeds 64 KiB")
        writing = True
        try:
            with synchronous_io_deadline(deadline):
                frame = struct.pack("!I", len(raw)) + raw
                while frame:
                    written = process.stdin.write(frame)
                    if not written:
                        raise OSError("Direct helper disconnected")
                    frame = frame[written:]
                writing = False

                def receive(count):
                    while len(self.input) < count:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Direct helper reply deadline expired")
                        part = process.stdout.read(count - len(self.input))
                        if not part:
                            raise OSError("Direct helper disconnected")
                        self.input.extend(part)
                    data = self.input[:count]
                    del self.input[:count]
                    return data

                while True:
                    if self.reply_length is None:
                        self.reply_length = struct.unpack("!I", receive(4))[0]
                    if self.reply_length > 65536:
                        raise ValueError("Oversized direct reply")
                    payload = receive(self.reply_length)
                    self.reply_length = None
                    response = json.loads(payload)
                    if not isinstance(response, dict) or set(response) not in (
                        {"request_id", "result"}, {"request_id", "error"}
                    ):
                        raise ValueError("Invalid direct reply envelope")
                    if response["request_id"] != request_id:
                        continue  # A late admission reply cannot acknowledge newer cleanup.
                    if "error" in response:
                        raise PlatformRefusal("HELPER_REFUSED", str(response["error"])[:512])
                    return response["result"]
        except TimeoutError as error:
            if writing:
                self._abandon_transport()
            raise PlatformRefusal("TIMEOUT", "Direct native helper timed out; retry owned cleanup") from error
        except (ValueError, OSError) as error:
            self._abandon_transport()
            raise PlatformRefusal("HELPER_REFUSED", "Direct native helper connection failed; retain and review cleanup") from error

    def _abandon_transport(self):
        # A partial request or malformed framing cannot be repaired by appending
        # another command. EOF makes the owned native process journal/drain rules.
        self.closing = True
        for stream in (self.process.stdin, self.process.stdout):
            if stream and not stream.closed:
                stream.close()

    def _check_status(self, status):
        if (not isinstance(status, dict)
                or type(status.get("prima_direct_version")) is not int
                or status["prima_direct_version"] != 1 or status.get("grant") != self.bound
                or any(type(status.get(key)) is not list or any(not isinstance(item, str) for item in status[key])
                       for key in ("prima_ports_owned_runs", "prima_direct_active_scopes"))):
            raise PlatformRefusal("UPGRADE_REQUIRED", "Update the native helper for verified direct Prima ownership")
        return status

    def request(self, action, **values):
        timeout = HELPER_MUTATION_TIMEOUT if action in MUTATING_ACTIONS or action.startswith("direct_") else HELPER_QUERY_TIMEOUT
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=timeout):
            raise PlatformRefusal("TIMEOUT", "Direct native helper is busy")
        try:
            if self.closing:
                try:
                    self._close(deadline)
                except PlatformRefusal:
                    if self.process is not None:
                        raise  # Old mutation is not settled; never spawn another owner.
            if self.process is None:
                self._start()
            if not self.probed:
                self._check_status(self._exchange("status", {}, deadline))
                self.probed = True
            if action == "authorize" and self.bound != self.grant():
                expected = self.grant()
                if not expected:
                    raise PlatformRefusal("UNPAIRED_IDENTITY", "Current master authorization is unavailable")
                status = self._check_status(self._exchange("status", {}, deadline))
                if status["prima_ports_owned_runs"] or status["prima_direct_active_scopes"]:
                    raise PlatformRefusal("CLEANUP_PENDING", "Drain the old direct private network before changing master authorization")
                self._close(deadline)
                if self.journal.exists():
                    self.journal.replace(self.journal.with_name(f"authorization-{uuid4()}.journal.json"))
                atomic_json(self.config, expected)
                self._start()
                self._check_status(self._exchange("status", {}, deadline))
                self.probed = True
            return self._exchange(action, values, deadline)
        finally:
            self.lock.release()

    def status(self, expected=None):
        try:
            result = self._check_status(self.request("status"))
            if result.get("grant") != (expected or self.bound):
                return {**result, "ready": False, "reason": "Drain the previous direct network before using a new master"}
            return result
        except PlatformRefusal as error:
            return {"ready": False, "reason": str(error), "error_code": error.code}

    def _close(self, deadline):
        process = self.process
        if process is None:
            if self.journal.exists() and json.loads(self.journal.read_text()).get("prima_ports"):
                raise PlatformRefusal("CLEANUP_PENDING", "Direct native rules still need local recovery")
            return
        self.closing = True
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed:
                stream.close()
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise PlatformRefusal("CLEANUP_PENDING", "Direct native process has not finished its owned cleanup") from None
        self.process = None
        self.bound = None
        self.closing = False
        self.probed = False
        if self.journal.exists() and json.loads(self.journal.read_text()).get("prima_ports"):
            raise PlatformRefusal("CLEANUP_PENDING", "Direct native process retained rules requiring local recovery")

    def close(self):
        deadline = time.monotonic() + HELPER_MUTATION_TIMEOUT
        if not self.lock.acquire(timeout=HELPER_MUTATION_TIMEOUT):
            raise PlatformRefusal("CLEANUP_PENDING", "Direct native process is still handling an owned operation")
        try:
            self._close(deadline)
        finally:
            self.lock.release()
