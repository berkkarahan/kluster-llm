"""Isolated MPI provider probing and owned local rank processes.

This module deliberately does not start ordinary llama-server or RPC. Installed
flavors are admitted only when their provider identity and engine capability
record agree. Rank launch accepts a complete server-built job specification and
delivers it over stdin, so credentials never appear in argv or persisted state.
"""

from __future__ import annotations

import collections
import hashlib
import ipaddress
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kllm.state import repo_root

PROTOCOL_VERSION = 1
DATA_PORT_MIN = 20_000
DATA_PORT_MAX = 20_999
MAX_CONTROL_LINE = 64 * 1024
SUPPORTED_BACKENDS = ("vulkan", "metal", "cuda", "rocm")
_CAPABILITY_CACHE_LOCK = threading.Lock()
_CAPABILITY_CACHE_KEY: tuple[Any, ...] | None = None
_CAPABILITY_CACHE_VALUE: dict[str, Any] | None = None


class MpiRuntimeError(RuntimeError):
    """An MPI package, launch, or rank lifecycle check failed."""


def _executable(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MpiRuntimeError(f"invalid MPI install manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MpiRuntimeError(f"MPI install manifest {path} is not an object")
    return value


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def flavor_root(backend: str, root: Path | None = None) -> Path:
    if backend not in SUPPORTED_BACKENDS:
        raise MpiRuntimeError(f"unsupported MPI backend {backend!r}")
    base = root or repo_root()
    if base is None:
        raise MpiRuntimeError("repository root is unavailable")
    return base / "vendor" / "mpi" / backend


def rank_binary(backend: str, root: Path | None = None) -> Path:
    path = flavor_root(backend, root) / "bin" / _executable("kllm-mpi-rank")
    if not path.is_file():
        raise MpiRuntimeError(f"MPI rank binary is missing for {backend}")
    return path


def _provider_probe(flavor: Path) -> dict[str, Any]:
    check = flavor / "bin" / _executable("kllm-mpi-provider-check")
    if not check.is_file():
        raise MpiRuntimeError("MPI provider-check binary is missing")
    try:
        result = subprocess.run(
            [str(check), "--capabilities"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MpiRuntimeError(f"MPI provider probe failed: {exc}") from exc
    if result.returncode != 0:
        raise MpiRuntimeError(f"MPI provider probe exited {result.returncode}")
    try:
        record = json.loads(result.stdout)
    except ValueError as exc:
        raise MpiRuntimeError("MPI provider probe returned invalid JSON") from exc
    if record.get("kind") != "kllm-mpi-provider-capabilities":
        raise MpiRuntimeError("MPI provider probe returned an unexpected record")
    provider = record.get("provider")
    if not isinstance(provider, dict):
        raise MpiRuntimeError("MPI provider identity is missing")
    return provider


def _engine_probe(flavor: Path, engine: dict[str, Any], backend: str) -> dict[str, Any]:
    relative = engine.get("binary")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise MpiRuntimeError("MPI engine binary identity is missing")
    binary = (flavor / relative).resolve()
    try:
        binary.relative_to(flavor.resolve())
    except ValueError as exc:
        raise MpiRuntimeError("MPI engine binary escapes its flavor package") from exc
    if not binary.is_file():
        raise MpiRuntimeError("MPI engine binary is missing")
    try:
        result = subprocess.run(
            [str(binary), "--kllm-capabilities"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MpiRuntimeError(f"MPI engine probe failed: {exc}") from exc
    if result.returncode != 0:
        raise MpiRuntimeError(f"MPI engine probe exited {result.returncode}")
    try:
        record = json.loads(result.stdout)
    except ValueError as exc:
        raise MpiRuntimeError("MPI engine probe returned invalid JSON") from exc
    if (
        not isinstance(record, dict)
        or record.get("kind") != "kllm-mpi-engine-capabilities"
        or record.get("backend") != backend
    ):
        raise MpiRuntimeError("MPI engine probe returned an unexpected identity")
    return record


def _provider_identity(provider: dict[str, Any]) -> tuple[Any, ...]:
    return (
        provider.get("upstream_revision"),
        provider.get("local_patch_digest"),
        provider.get("protocol_version"),
        provider.get("target_triple"),
        tuple(sorted(provider.get("features") or [])),
        provider.get("binary_sha256"),
    )


def probe_local_capabilities(
    physical_devices: list[dict[str, Any]] | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Probe installed flavors without converting provider readiness into GPU proof."""
    base = root or repo_root()
    devices = list(physical_devices or [])
    report: dict[str, Any] = {
        "protocol": PROTOCOL_VERSION,
        "available": False,
        "platform": sys.platform,
        "provider": None,
        "devices": devices,
        "backends": {},
        "blockers": [],
    }
    common_provider: dict[str, Any] | None = None
    common_identity: tuple[Any, ...] | None = None
    if base is None:
        report["blockers"].append("repository root is unavailable")
    else:
        for backend in SUPPORTED_BACKENDS:
            flavor = base / "vendor" / "mpi" / backend
            manifest_path = flavor / "install.json"
            if not manifest_path.is_file():
                continue
            backend_report: dict[str, Any] = {
                "backend": backend,
                "available": False,
                "reason": None,
                "devices": [],
            }
            try:
                manifest = _read_json(manifest_path)
                if manifest.get("schema_version") != 1 or manifest.get("backend") != backend:
                    raise MpiRuntimeError("MPI flavor manifest identity is invalid")
                installed_provider = manifest.get("provider")
                if not isinstance(installed_provider, dict) or installed_provider.get("self_test") != "passed":
                    raise MpiRuntimeError("MPI provider self-test was not recorded as passed")
                live_provider = _provider_probe(flavor)
                fields = (
                    "upstream_revision", "local_patch_digest", "protocol_version",
                    "target_triple", "features", "binary_sha256",
                )
                if any(live_provider.get(key) != installed_provider.get(key) for key in fields):
                    raise MpiRuntimeError("MPI provider binary differs from its install manifest")
                identity = _provider_identity(live_provider)
                if common_identity is None:
                    common_identity = identity
                    common_provider = dict(live_provider, self_test="passed")
                elif common_identity != identity:
                    raise MpiRuntimeError("installed MPI flavors use incompatible providers")
                engine = manifest.get("engine")
                if not isinstance(engine, dict) or engine.get("available") is not True:
                    reason = engine.get("blocker") if isinstance(engine, dict) else None
                    raise MpiRuntimeError(reason or "distributed MPI engine is unavailable")
                required = (
                    "revision", "mpi_compatibility_id", "operation_profile", "fingerprint",
                    "supported_architectures", "devices",
                )
                if any(engine.get(key) is None for key in required):
                    raise MpiRuntimeError("MPI engine manifest is incomplete")
                live_engine = _engine_probe(flavor, engine, backend)
                if any(live_engine.get(key) != engine.get(key) for key in required):
                    raise MpiRuntimeError("MPI engine binary differs from its install manifest")
                backend_report.update({key: live_engine[key] for key in required})
                backend_report["engine_revision"] = backend_report.pop("revision")
                backend_report["available"] = True
            except MpiRuntimeError as exc:
                backend_report["reason"] = str(exc)
            report["backends"][backend] = backend_report
        if not report["backends"]:
            report["blockers"].append("no isolated MPI flavor is installed")
    report["provider"] = common_provider
    usable = [entry for entry in report["backends"].values() if entry.get("available")]
    report["available"] = bool(common_provider and usable and devices)
    if common_provider and not usable:
        report["blockers"].append("no distributed MPI engine flavor is ready")
    if not devices:
        report["blockers"].append("no stable physical GPU inventory is available")
    report["fingerprint"] = _fingerprint(report)
    return report


def cached_local_capabilities(
    physical_devices: list[dict[str, Any]] | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Cache probes until a flavor manifest or provider binary changes."""
    global _CAPABILITY_CACHE_KEY, _CAPABILITY_CACHE_VALUE
    base = root or repo_root()
    identities: list[tuple[str, int, int]] = []
    if base is not None:
        for backend in SUPPORTED_BACKENDS:
            flavor = base / "vendor" / "mpi" / backend
            paths = [flavor / "install.json"]
            if (flavor / "bin").is_dir():
                paths.extend(sorted(path for path in (flavor / "bin").iterdir() if path.is_file()))
            for path in paths:
                try:
                    stat = path.stat()
                    identities.append((str(path), stat.st_mtime_ns, stat.st_size))
                except OSError:
                    identities.append((str(path), 0, 0))
    devices_key = json.dumps(physical_devices or [], sort_keys=True, separators=(",", ":"))
    key = (str(base), devices_key, *identities)
    with _CAPABILITY_CACHE_LOCK:
        if key != _CAPABILITY_CACHE_KEY or _CAPABILITY_CACHE_VALUE is None:
            _CAPABILITY_CACHE_VALUE = probe_local_capabilities(
                physical_devices, root=base
            )
            _CAPABILITY_CACHE_KEY = key
        return json.loads(json.dumps(_CAPABILITY_CACHE_VALUE))


@dataclass
class PreparedPort:
    """Exclusive temporary reservation for one private data-plane listener."""

    address: str
    _socket: socket.socket | None

    def release(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> PreparedPort:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def reserve_private_port(bind_ip: str) -> PreparedPort:
    """Reserve a port from the provider's fixed range on one explicit private IP."""
    try:
        ip = ipaddress.ip_address(bind_ip)
    except ValueError as exc:
        raise MpiRuntimeError("MPI bind address is invalid") from exc
    if ip.is_unspecified or ip.is_multicast or not (
        ip.is_private or ip.is_loopback or ip.is_link_local
    ):
        raise MpiRuntimeError("MPI bind address must be a fixed private address")
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    for port in range(DATA_PORT_MIN, DATA_PORT_MAX + 1):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((bind_ip, port))
            return PreparedPort(f"[{bind_ip}]:{port}" if ip.version == 6 else f"{bind_ip}:{port}", sock)
        except OSError:
            sock.close()
    raise MpiRuntimeError("no MPI data-plane port is available in the allocated range")


class _WindowsJob:
    """Kill-on-close Job Object for the local rank process tree."""

    def __init__(self) -> None:
        self.handle: int | None = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise MpiRuntimeError("create Windows MPI Job Object failed")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            kernel32.CloseHandle(handle)
            raise MpiRuntimeError("configure Windows MPI Job Object failed")
        self.handle = int(handle)

    def assign(self, process: subprocess.Popen[str]) -> None:
        if self.handle is None:
            return
        import ctypes

        process_handle = int(getattr(process, "_handle"))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not kernel32.AssignProcessToJobObject(self.handle, process_handle):
            self.close()
            raise MpiRuntimeError("assign MPI rank to Windows Job Object failed")

    def close(self) -> None:
        if self.handle is not None:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(self.handle)
            self.handle = None


class MpiRankSupervisor:
    """Own one local structured rank and its complete descendant process tree."""

    def __init__(self, binary: Path, *, startup_timeout: float = 30.0) -> None:
        self.binary = binary
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen[str] | None = None
        self._job: _WindowsJob | None = None
        self._lock = threading.RLock()
        self._stderr: collections.deque[str] = collections.deque(maxlen=100)
        self._stopping = False
        self.ready: dict[str, Any] | None = None

    def _readline(self, timeout: float) -> str:
        process = self.process
        if process is None or process.stdout is None:
            raise MpiRuntimeError("MPI rank is not running")
        result: list[str] = []
        done = threading.Event()

        def read() -> None:
            line = process.stdout.readline()
            if line:
                result.append(line)
            done.set()

        threading.Thread(target=read, name="kllm-mpi-rank-read", daemon=True).start()
        if not done.wait(timeout):
            raise MpiRuntimeError("MPI rank response timed out")
        if not result or not result[0]:
            raise MpiRuntimeError("MPI rank control channel closed")
        if len(result[0].encode("utf-8")) > MAX_CONTROL_LINE:
            raise MpiRuntimeError("MPI rank response exceeds 64 KiB")
        return result[0]

    def _drain_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())

    def _monitor(self, process: subprocess.Popen[str]) -> None:
        process.wait()
        with self._lock:
            if self.process is not process or self._stopping:
                return
            # The rank leader disappeared without a clean control-plane stop.
            # Kill every remaining descendant in the owned job/session.
            if os.name == "nt":
                if self._job is not None:
                    self._job.close()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if self._job is not None:
                self._job.close()
            self.process = None
            self._job = None
            self.ready = None

    def start(self, spec: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                raise MpiRuntimeError("an MPI rank is already running on this node")
            encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_CONTROL_LINE:
                raise MpiRuntimeError("MPI job spec exceeds 64 KiB")
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            self._job = _WindowsJob()
            try:
                self.process = subprocess.Popen(
                    [str(self.binary)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    creationflags=flags,
                    start_new_session=os.name != "nt",
                )
                self._job.assign(self.process)
                threading.Thread(
                    target=self._drain_stderr,
                    args=(self.process,),
                    name="kllm-mpi-rank-stderr",
                    daemon=True,
                ).start()
                assert self.process.stdin is not None
                self.process.stdin.write(encoded + "\n")
                self.process.stdin.flush()
                response = json.loads(self._readline(self.startup_timeout))
                if response.get("kind") != "kllm-mpi-rank-ready":
                    raise MpiRuntimeError("MPI rank did not return a readiness record")
                if response.get("rank") != spec.get("rank") or response.get("world_size") != spec.get("world_size"):
                    raise MpiRuntimeError("MPI rank readiness identity differs from the job spec")
                if response.get("manifest_identity") != spec.get("manifest_identity"):
                    raise MpiRuntimeError("MPI rank readiness manifest differs from the job spec")
                self.ready = response
                threading.Thread(
                    target=self._monitor,
                    args=(self.process,),
                    name="kllm-mpi-rank-monitor",
                    daemon=True,
                ).start()
                return dict(response)
            except Exception:
                self._terminate_locked()
                raise

    def command(self, value: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
        with self._lock:
            process = self.process
            if process is None or process.poll() is not None or process.stdin is None:
                raise MpiRuntimeError("MPI rank is not running")
            encoded = json.dumps(value, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_CONTROL_LINE:
                raise MpiRuntimeError("MPI rank command exceeds 64 KiB")
            process.stdin.write(encoded + "\n")
            process.stdin.flush()
            try:
                response = json.loads(self._readline(timeout))
            except ValueError as exc:
                self._terminate_locked()
                raise MpiRuntimeError("MPI rank returned invalid command JSON") from exc
            except (MpiRuntimeError, OSError):
                # A timed-out reader thread remains blocked in readline until
                # the pipe closes. Treat a broken control exchange as fatal so
                # it cannot consume a later command's response.
                self._terminate_locked()
                raise
            if not isinstance(response, dict):
                raise MpiRuntimeError("MPI rank returned an invalid command record")
            return response

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self.process
            return {
                "state": "ready" if process is not None and process.poll() is None and self.ready else "stopped",
                "pid": process.pid if process is not None and process.poll() is None else None,
                "rank": (self.ready or {}).get("rank"),
                "world_size": (self.ready or {}).get("world_size"),
                "manifest_identity": (self.ready or {}).get("manifest_identity"),
                "stderr_tail": list(self._stderr),
            }

    def _terminate_locked(self) -> None:
        process = self.process
        if process is None:
            if self._job is not None:
                self._job.close()
            self._job = None
            self.ready = None
            return
        if process.poll() is None:
            if os.name == "nt":
                if self._job is not None:
                    self._job.close()
                else:
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        if self._job is not None:
            self._job.close()
        self.process = None
        self._job = None
        self.ready = None

    def stop(self) -> None:
        with self._lock:
            process = self.process
            if process is None:
                return
            self._stopping = True
            try:
                if process.poll() is None:
                    try:
                        self.command({"command": "stop"}, timeout=2)
                        process.wait(timeout=2)
                    except (MpiRuntimeError, OSError, subprocess.TimeoutExpired):
                        pass
            finally:
                self._terminate_locked()
                self._stopping = False

    def terminate(self) -> None:
        """Force the owned process tree down for rank-loss validation."""
        with self._lock:
            self._stopping = True
            try:
                self._terminate_locked()
            finally:
                self._stopping = False

    def __enter__(self) -> MpiRankSupervisor:
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
