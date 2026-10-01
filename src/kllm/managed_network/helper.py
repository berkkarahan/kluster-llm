"""Account-restricted native helper IPC and explicit local OS authorization."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shlex
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from typing import Any, cast

from .platform.base import PlatformRefusal


def account_id() -> str:
    if os.name != "nt":
        return str(os.getuid())
    output = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout
    match = re.search(r"S-1-5-(?:[0-9]+-)*[0-9]+", output)
    if not match:
        raise PlatformRefusal("UNSUPPORTED_CAPABILITY", "Windows account identity is unavailable")
    return match.group()


class HelperClient:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.account = account_id()
        self.lock = threading.Lock()

    def request(self, action: str, **values: Any) -> Any:
        with self.lock:
            return self._request(action, **values)

    def _request(self, action: str, **values: Any) -> Any:
        raw = json.dumps({"action": action, **values}, allow_nan=False).encode()
        if len(raw) > 65536:
            raise PlatformRefusal("INVALID_INPUT", "Helper request exceeds 64 KiB")

        def receive(source: Any, count: int) -> bytes:
            chunks = bytearray()
            while len(chunks) < count:
                chunk = (
                    source.recv(count - len(chunks))
                    if isinstance(source, socket.socket)
                    else source.read(count - len(chunks))
                )
                if not chunk:
                    raise OSError("Helper disconnected")
                chunks.extend(chunk)
            return bytes(chunks)

        try:
            if os.name == "nt":
                with open(r"\\.\pipe\kllm-network-" + self.account, "r+b", buffering=0) as pipe:
                    self._verify_windows_server(pipe)
                    pipe.write(struct.pack("!I", len(raw)) + raw)
                    length = struct.unpack("!I", receive(pipe, 4))[0]
                    if length > 65536:
                        raise ValueError("Oversized helper response")
                    response = json.loads(receive(pipe, length))
            else:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(
                        45
                        if action in {"authorize", "configure", "add", "restore", "resolve"}
                        else 8
                    )
                    client.connect(f"/var/run/kllm-network-{self.account}.sock")
                    self._verify_unix_server(client)
                    client.sendall(struct.pack("!I", len(raw)) + raw)
                    length = struct.unpack("!I", receive(client, 4))[0]
                    if length > 65536:
                        raise ValueError("Oversized helper response")
                    response = json.loads(receive(client, length))
        except (OSError, ValueError) as error:
            raise PlatformRefusal(
                "ADMIN_REQUIRED", "Enable administrator access on this computer before setup"
            ) from error
        if "error" in response:
            raise PlatformRefusal("HELPER_REFUSED", str(response["error"])[:512])
        return response["result"]

    @staticmethod
    def _verify_unix_server(client: socket.socket) -> None:
        if sys.platform == "darwin":
            import ctypes

            library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            uid, gid = ctypes.c_uint(), ctypes.c_uint()
            if (
                library.getpeereid(client.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0
                or uid.value != 0
            ):
                raise OSError("Network helper must run as root")
        else:
            _, uid, _ = struct.unpack(
                "3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid != 0:
                raise OSError("Network helper must run as root")

    @staticmethod
    def _verify_windows_server(pipe: Any) -> None:
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        kernel.GetNamedPipeServerProcessId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.ULONG),
        ]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        pid = wintypes.ULONG()
        if not kernel.GetNamedPipeServerProcessId(
            getattr(msvcrt, "get_osfhandle")(pipe.fileno()), ctypes.byref(pid)
        ):
            raise OSError("Helper identity is unavailable")
        process = kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            raise OSError("Helper identity is unavailable")
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if (
                not kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length))
                or path.value.casefold()
                != r"C:\Program Files\KlusterLLM\NetworkHelper\kllm-network-helper.exe".casefold()
            ):
                raise OSError("Helper process is not the protected installation")
        finally:
            kernel.CloseHandle(process)

    def status(self, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = self.request("status")
            grant = result.get("grant", {})
            if grant.get("node_id") != self.node_id or (expected is not None and grant != expected):
                return {
                    "ready": False,
                    "reason": "This cluster needs administrator authorization again",
                }
            return cast(dict[str, Any], result)
        except PlatformRefusal as error:
            return {"ready": False, "reason": str(error)}

    async def enable(self, repository: Path, grant: dict[str, Any]) -> dict[str, Any]:
        binary = (
            repository
            / "vendor"
            / "bin"
            / ("kllm-network-helper.exe" if os.name == "nt" else "kllm-network-helper")
        )
        if not binary.is_file():
            raise PlatformRefusal(
                "UPGRADE_REQUIRED",
                "Update Kluster on this computer to install administrator support",
            )
        with tempfile.TemporaryDirectory(prefix="kllm-admin-") as directory:
            config = Path(directory) / "authorization.json"
            config.write_text(json.dumps(grant))
            if os.name != "nt":
                config.chmod(0o600)
            arguments = [str(binary), "--install", str(config)]
            if sys.platform == "darwin":
                shell = shlex.join(arguments)
                # JSON escaping produces an AppleScript string literal; shlex
                # independently quotes the fixed executable and file arguments.
                args = [
                    "osascript",
                    "-e",
                    f"do shell script {json.dumps(shell)} with administrator privileges",
                ]
            elif os.name == "nt":

                def quote(text: str) -> str:
                    return "'" + text.replace("'", "''") + "'"

                argv = subprocess.list2cmdline(arguments[1:])
                script = f"$p = Start-Process -FilePath {quote(str(binary))} -ArgumentList {quote(argv)} -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
                args = ["powershell.exe", "-NoProfile", "-Command", script]
            else:
                args = ["pkexec", *arguments]

            def run() -> None:
                try:
                    result = subprocess.run(args, capture_output=True, timeout=120, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    raise PlatformRefusal(
                        "ADMIN_REQUIRED",
                        "Administrator authorization did not complete; retry on this computer",
                    ) from None
                if result.returncode:
                    raise PlatformRefusal(
                        "ADMIN_REQUIRED",
                        "Administrator access was not enabled. Approve the operating system prompt to retry.",
                    )

            await asyncio.to_thread(run)
        for _ in range(30):
            status = await asyncio.to_thread(self.status, grant)
            if status.get("ready"):
                return status
            await asyncio.sleep(0.2)
        raise PlatformRefusal(
            "ADMIN_REQUIRED",
            "Administrator support was installed but has not started; retry shortly",
        )
