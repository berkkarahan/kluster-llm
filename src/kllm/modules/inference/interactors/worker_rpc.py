"""Execute signed master commands and expire their local RPC ownership."""

from __future__ import annotations

import threading
import time
import ipaddress
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import psutil

from kllm.shared import native, netsec, process_registry as runtime
from kllm.modules.managed_network.interactors.identity import SignedRuntimeCommand
from kllm.shared.platform.base import PlatformRefusal
from kllm.shared.state import ClusterState

from kllm.modules.runtimes.interactors.runtime_setup import RuntimeSetupService
from kllm.modules.runtimes.interactors.selection import use_runtime

# The master renews about every 5s (RpcRunCoordinator.monitor) and this process
# reaps the lease about every 1s (ManagedNetworkService._expire_worker_rpc).
# The signed control channel can stay silent for a ping interval plus timeout
# plus reconnect, which is longer than 15s. Expiring then killed a live
# rpc-server and forced a full model reload. 60s still drops an orphan on the
# private bind if the master is actually gone.
LEASE_SECONDS = 60.0

# A freshly built runtime can need longer than one probe or spawn window on
# macOS (Metal/MoltenVK cold start). Retry the whole start a bounded number of
# times, but never begin an attempt that cannot finish before the signed
# deadline: the master waits for this command, so a late result is dropped.
START_ATTEMPTS = 3
START_BACKOFF_S = (1.0, 2.0)
START_MIN_ATTEMPT_S = 25.0
_LISTEN_TIMEOUT_S = 6.0


def process_token(pid: int) -> str:
    token, _ = native.require_native()._process_identity(pid)
    return str(token)


def listener_ready(pid: int, host: str, port: int) -> bool:
    try:
        connections = psutil.Process(pid).net_connections(kind="tcp")
        return any(
            c.status == psutil.CONN_LISTEN and c.laddr.port == port and c.laddr.ip == host
            for c in connections
        )
    except psutil.AccessDenied:
        # macOS restricts socket inventory even for some owned children. lsof
        # can observe the listener without connecting to the serial RPC socket.
        executable = shutil.which("lsof") if sys.platform == "darwin" else None
        if executable is None:
            raise
        result = subprocess.run(
            [executable, "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return f"n{host}:{port}" in result.stdout.splitlines()


class WorkerRpcService:
    def __init__(
        self,
        state: Callable[[], ClusterState],
        setup: Callable[[], RuntimeSetupService],
        *,
        lock: Any = None,
        alive: Callable[[int], bool] = native.process_alive,
        token: Callable[[int], str] = process_token,
        stop: Callable[..., Any] = native.stop_process,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        before_stop: Callable[[int], None] = lambda _pid: None,
        listener: Callable[[int, str, int], bool] = listener_ready,
        registry: Any = None,
    ) -> None:
        self.state, self.setup = state, setup
        self.lock = lock or threading.RLock()
        self.alive, self.token, self.stop, self.clock = alive, token, stop, clock
        self.sleep = sleep
        self.before_stop = before_stop
        self.listener = listener
        self.deadline = 0.0
        self.registry = registry

    def _member(self) -> dict[str, Any]:
        state = self.state()
        return state.members.setdefault(state.node_id, {})

    def _same_process(self, owner: dict[str, Any]) -> bool:
        pid = owner.get("pid")
        return bool(pid and self.alive(pid) and self.token(pid) == owner.get("start_token"))

    def _release(self, owner: dict[str, Any]) -> None:
        state, member = self.state(), self._member()
        if self._same_process(owner):
            self.before_stop(int(owner["pid"]))
            self.stop(int(owner["pid"]), force=True)
            if self._same_process(owner):
                member["rpc_status"] = "cleanup_pending"
                state.save()
                raise PlatformRefusal("CLEANUP_PENDING", "Owned RPC process has not stopped")
        if state.process_pid == owner.get("pid"):
            state.process_pid = state.process_role = None
            runtime.clear_children()
        member.pop("rpc_owner", None)
        member.pop("rpc_error", None)
        member["rpc_devices"] = []
        member["rpc_status"] = "idle"
        if self.registry is not None:
            self.registry.running(None)
        self.deadline = 0.0
        state.save()

    def reconcile_startup(self) -> None:
        """Persisted ownership permits cleanup, never a new launch or lease."""
        with self.lock:
            if self.state().role != "worker":
                return
            state, member = self.state(), self._member()
            owner = member.get("rpc_owner")
            if owner:
                self._release(owner)
            elif state.role == "worker" and state.process_pid:
                child = next(
                    (c for c in runtime.load().children if c.pid == state.process_pid), None
                )
                if child and child.start_token:
                    self._release({"pid": child.pid, "start_token": child.start_token})
                elif not self.alive(state.process_pid):
                    state.process_pid = state.process_role = None
                    member["rpc_devices"] = []
                    state.save()
                else:
                    member["rpc_status"] = "cleanup_pending"
                    member["rpc_error"] = (
                        "Legacy RPC process identity is unavailable; stop it locally before Run"
                    )
                    state.save()

    def expire(self) -> None:
        with self.lock:
            owner = (self.state().members.get(self.state().node_id) or {}).get("rpc_owner")
            if owner and (self.state().role != "worker" or self.clock() >= self.deadline):
                self._release(owner)

    def _await_listener(self, pid: int) -> None:
        until = self.clock() + _LISTEN_TIMEOUT_S
        state = self.state()
        while self.clock() < until:
            if not self.alive(pid):
                raise PlatformRefusal(
                    "RPC_START_FAILED", "rpc-server exited before listening; inspect RPC logs"
                )
            # Observe the owned listener locally. Connecting without an RPC
            # handshake can consume or tear down the upstream serial listener.
            if self.listener(pid, state.rpc_bind, state.rpc_port):
                return
            self.sleep(0.05)
        raise PlatformRefusal(
            "RPC_START_FAILED", "rpc-server did not bind its configured private endpoint"
        )

    def execute(
        self,
        command: SignedRuntimeCommand,
        deadline: float,
        authorize: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        with self.lock:
            authorize()
            if self.clock() >= deadline:
                raise PlatformRefusal("TIMEOUT", "RPC command expired before runtime verification")
            value = None
            if self.registry is not None and command.action == "start":
                if set(command.body) != {"runtime_id"}:
                    raise PlatformRefusal(
                        "RUNTIME_NOT_READY", "Master must name the exact prepared runtime"
                    )
                value = self.registry.capture(command.body["runtime_id"])
            with use_runtime(value):
                return self._execute(command, deadline, authorize, value)

    def _execute(
        self,
        command: SignedRuntimeCommand,
        deadline: float,
        authorize: Callable[[], None],
        runtime_snapshot: dict | None,
    ) -> dict[str, Any]:
        with self.lock:
            authorize()
            state, member = self.state(), self._member()
            if command.body and runtime_snapshot is None:
                raise PlatformRefusal(
                    "INVALID_INPUT", "RPC commands use the enrolled local configuration"
                )
            if (
                state.role != "worker"
                or not state.join_key
                or command.target_node_id != state.node_id
                or command.sender_node_id != command.master_node_id
            ):
                raise PlatformRefusal(
                    "NOT_MASTER", "RPC command requires the current enrolled master"
                )
            if self.clock() >= deadline:
                raise PlatformRefusal("TIMEOUT", "RPC command expired")
            self.expire()
            owner = member.get("rpc_owner")
            context = {
                "run_id": str(command.run_id),
                "master_node_id": command.master_node_id,
                "master_term": command.master_term,
                "master_session_id": str(command.master_session_id),
                "cluster_binding_id": str(command.cluster_binding_id),
                "master_key_id": command.master_key_id,
            }
            if runtime_snapshot is not None:
                context.update(
                    runtime_id=runtime_snapshot["runtime_id"], build_id=runtime_snapshot["build_id"]
                )
            if owner and any(owner.get(key) != value for key, value in context.items()):
                raise PlatformRefusal("RPC_BUSY", "RPC belongs to another run or master session")
            if command.action == "stop":
                if owner:
                    self._release(owner)
                return {"status": "idle", "run_id": str(command.run_id)}
            if command.action in ("renew", "status") or owner:
                if owner and self._same_process(owner):
                    if command.action in ("renew", "start"):
                        self.deadline = self.clock() + LEASE_SECONDS
                    return self._status(owner)
                member["rpc_status"] = "failed" if owner else "idle"
                member["rpc_devices"] = []
                state.save()
                return {
                    "status": member["rpc_status"],
                    "run_id": str(command.run_id),
                    "error": "RPC is not running",
                }
            if state.process_pid and self.alive(state.process_pid):
                raise PlatformRefusal(
                    "CLEANUP_PENDING", "An existing process must be reconciled before RPC startup"
                )
            host = state.rpc_bind
            if (
                not host
                or not ipaddress.ip_address(host).is_private
                or ipaddress.ip_address(host).is_unspecified
                or ipaddress.ip_address(host).is_multicast
            ):
                raise PlatformRefusal(
                    "INVALID_INPUT", "RPC startup requires one enrolled private bind address"
                )
            firewall = netsec.ensure_rpc_firewall(state.rpc_port, host)
            if firewall.get("warning"):
                state.netsec_warnings = [*state.netsec_warnings, str(firewall["warning"])]
            member["rpc_status"] = "starting"
            state.save()
            return self._start_with_retry(
                context, runtime_snapshot, member, state, deadline, authorize
            )

    def _start_with_retry(
        self,
        context: dict[str, Any],
        runtime_snapshot: dict | None,
        member: dict[str, Any],
        state: ClusterState,
        deadline: float,
        authorize: Callable[[], None],
    ) -> dict[str, Any]:
        attempts = 0
        last: PlatformRefusal | None = None
        while attempts < START_ATTEMPTS:
            if self.clock() >= deadline:
                break
            if attempts:
                if deadline - self.clock() < START_MIN_ATTEMPT_S:
                    break
                self.sleep(START_BACKOFF_S[min(attempts - 1, len(START_BACKOFF_S) - 1)])
                if self.clock() >= deadline:
                    break
            attempts += 1
            try:
                return self._start_once(
                    context, runtime_snapshot, member, state, deadline, authorize
                )
            except PlatformRefusal as error:
                if error.code != "RPC_START_FAILED":
                    raise
                # _start_once always releases its own partial ownership, so the
                # next attempt starts clean; keep the retry visible as starting.
                last = error
                member["rpc_status"] = "starting"
                state.save()
        if last is None:
            raise PlatformRefusal("TIMEOUT", "RPC command expired before startup")
        member["rpc_status"] = "failed"
        member["rpc_error"] = (f"rpc-server startup failed after {attempts} attempt(s): {last}")[
            :512
        ]
        state.save()
        raise PlatformRefusal("RPC_START_FAILED", member["rpc_error"])

    def _start_once(
        self,
        context: dict[str, Any],
        runtime_snapshot: dict | None,
        member: dict[str, Any],
        state: ClusterState,
        deadline: float,
        authorize: Callable[[], None],
    ) -> dict[str, Any]:
        authorize()
        if self.clock() >= deadline:
            raise PlatformRefusal("TIMEOUT", "RPC command expired before startup")
        launched = self.setup().start_commanded_worker(state)
        if launched.get("error") or not launched.get("pid"):
            member["rpc_status"] = "failed"
            member["rpc_error"] = str(launched.get("error") or "RPC did not start")[:512]
            state.save()
            raise PlatformRefusal("RPC_START_FAILED", member["rpc_error"])
        try:
            owner = {**context, "pid": launched["pid"], "start_token": self.token(launched["pid"])}
        except BaseException:
            # This child was just created in this serialized command; do not
            # leave it unowned if process-identity acquisition fails.
            self.stop(launched["pid"], force=True)
            state.process_pid = state.process_role = None
            member["rpc_devices"] = []
            member["rpc_status"] = "failed"
            state.save()
            raise
        member["rpc_owner"] = owner
        if self.registry is not None and runtime_snapshot is not None:
            self.registry.running({**runtime_snapshot, "pid": owner["pid"]})
        self.deadline = self.clock() + LEASE_SECONDS
        state.save()
        try:
            self._await_listener(owner["pid"])
        except BaseException:
            self._release(owner)
            raise
        member["rpc_status"] = "serving"
        member.pop("rpc_error", None)
        state.save()
        return self._status(owner)

    def _status(self, owner: dict[str, Any]) -> dict[str, Any]:
        state = self.state()
        return {
            "status": "serving",
            **owner,
            "rpc_bind": state.rpc_bind,
            "rpc_port": state.rpc_port,
            "rpc_devices": self._member().get("rpc_devices", []),
        }
