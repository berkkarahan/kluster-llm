"""Build the worker and master membership heartbeat contract."""

from __future__ import annotations

import re
import socket
from collections.abc import Callable
from typing import Any

from kllm.shared.state import ClusterState
from kllm.modules.logs.interactors.logging_control import local_status

_HOSTNAME_INVALID = re.compile(r"[^A-Za-z0-9._-]+")


def local_hostname() -> str | None:
    """Return a bounded, safe hostname for the shared node directory."""
    value = _HOSTNAME_INVALID.sub("-", (socket.gethostname() or "").strip()).strip(".-").strip()
    return value[:64] or None


class HeartbeatPayloadBuilder:
    """Create a control-plane heartbeat payload from current state providers."""

    _OPTIONAL_MEMBER_FIELDS = (
        "actual_backend",
        "requested_backend",
        "devices",
        "unified_memory",
        "binary_version",
        "reclaimable_ram_bytes",
    )

    def __init__(
        self,
        state: Callable[[], ClusterState],
        *,
        software_identity: Callable[[], dict[str, Any]],
        process_started_at: Callable[[], str | None],
        process_start_token: Callable[[], str | None],
        runtime_status: Callable[[], dict[str, Any]] = lambda: {},
    ) -> None:
        self._state = state
        self._software_identity = software_identity
        self._process_started_at = process_started_at
        self._process_start_token = process_start_token
        self._runtime_status = runtime_status

    def build(self) -> dict[str, Any]:
        """Return the serialized heartbeat fields expected by the control API."""
        state = self._state()
        member = state.members.get(state.node_id) or {}
        identity = self._software_identity()
        from kllm.shared.config import load_config

        config = load_config()
        return {
            "node_id": state.node_id,
            "hostname": local_hostname(),
            "rpc_devices": member.get("rpc_devices", [])
            if state.process_pid and state.process_role == "worker"
            else [],
            "rpc_capabilities": member.get("rpc_capabilities", []),
            "rpc_control_version": 1,
            "prima_control_version": 1,
            "runtime_management_version": 1,
            "runtime": self._runtime_status(),
            "rpc_status": member.get("rpc_status", "idle"),
            "rpc_error": member.get("rpc_error"),
            "host": state.rpc_bind or member.get("host") or member.get("lan_host") or "",
            "rpc_bind": state.rpc_bind,
            "control_port": state.control_port,
            "rpc_port": state.rpc_port,
            "total_ram_bytes": member.get("total_ram_bytes") or 0,
            "available_ram_bytes": member.get("available_ram_bytes") or 0,
            "vram_bytes": member.get("vram_bytes"),
            "backend": member.get("backend") or "unknown",
            "vram_percent": config["vram_percent"],
            "ram_percent": config["ram_percent"],
            **{
                key: member[key]
                for key in self._OPTIONAL_MEMBER_FIELDS
                if member.get(key) is not None
            },
            "lan_host": state.rpc_bind or member.get("lan_host"),
            "gpu_ids": state.gpu_ids,
            "git_head": identity.get("git_head"),
            "llama_release": identity.get("llama_release"),
            "process_pid": state.process_pid,
            "process_role": state.process_role,
            "process_started_at": self._process_started_at(),
            "process_start_token": self._process_start_token(),
            "logging_ack": local_status(state),
        }
