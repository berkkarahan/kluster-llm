"""Local hardware capacity snapshots for cluster membership."""

from __future__ import annotations

import platform
import time
from collections.abc import Callable, Mapping
from typing import Any

from kllm.shared import netsec
from kllm.modules.settings.interactors.gpus import ResolvedGpuSelection
from kllm.shared.state import ClusterState


class CapacityService:
    """Build and refresh the local member's capacity record."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        list_gpus: Callable[[], dict[str, Any]],
        probe_capacity: Callable[[], dict[str, Any]],
        resolve_gpu_selection: Callable[..., ResolvedGpuSelection],
        pick_rpc_bind_host: Callable[[str | None], str],
        tracked_process_start: Callable[[], str | None],
        configured_gpu_ids: Callable[[], list[int] | None],
    ) -> None:
        self._state = state
        self._list_gpus = list_gpus
        self._probe_capacity = probe_capacity
        self._resolve_gpu_selection = resolve_gpu_selection
        self._pick_rpc_bind_host = pick_rpc_bind_host
        self._tracked_process_start = tracked_process_start
        self._configured_gpu_ids = configured_gpu_ids

    def _selection(
        self, state: ClusterState, listed: Mapping[str, Any]
    ) -> ResolvedGpuSelection:
        """Resolve the local GPU selection, re-applying auto mode.

        A selection recorded by an older build can include an integrated
        adapter that the default rule no longer picks. Auto mode (no saved
        ``gpu_ids`` in config) is therefore re-derived from the inventory; an
        explicit saved selection is always honored.
        """
        selection = self._resolve_gpu_selection(state.gpu_ids, listed=listed)
        if (
            state.gpu_ids
            and self._configured_gpu_ids() is None
            and any(device.integrated for device in selection.devices)
        ):
            default = self._resolve_gpu_selection(None, listed=listed)
            if default.has_devices:
                state.gpu_ids = list(default.gpu_ids)
                selection = default
        return selection

    def master_member_record(self, lan_host: str) -> dict[str, Any]:
        state = self._state()
        gpu_selection = self._selection(state, self._list_gpus())
        capacity = self._probe_capacity()
        vram = gpu_selection.vram_bytes if gpu_selection.has_devices else capacity.get("vram_bytes")
        backend = (
            gpu_selection.backend
            if gpu_selection.has_devices or gpu_selection.backend != "cpu"
            else capacity.get("backend", "cpu")
        )
        return {
            "node_id": state.node_id,
            "host": lan_host,
            "lan_host": lan_host,
            "control_port": state.control_port,
            "rpc_port": state.rpc_port,
            "role": "master",
            "total_ram_bytes": capacity["total_ram_bytes"],
            "available_ram_bytes": capacity["available_ram_bytes"],
            "vram_bytes": vram,
            "backend": backend,
            "actual_backend": backend,
            "requested_backend": capacity.get("requested_backend"),
            "devices": gpu_selection.device_payloads(),
            "unified_memory": capacity.get("unified_memory", False),
            "binary_version": capacity.get("binary_version"),
            "gpu_ids": state.gpu_ids,
            "last_seen": time.time(),
            "latency_ms": 0.0,
            "process_pid": state.process_pid,
            "process_role": state.process_role,
            "process_started_at": self._tracked_process_start(),
            "platform": platform.system() or None,
            "architecture": platform.machine() or None,
        }

    def ensure_master_member(self, *, persist: bool = False) -> bool:
        """Repair a missing master membership row after loading persisted state."""
        state = self._state()
        if state.role != "master":
            return False
        node_id = state.node_id
        previous = state.members.get(node_id)
        if previous is not None and previous.get("role") == "master":
            return False
        try:
            lan_host = (
                (previous or {}).get("lan_host")
                or (previous or {}).get("host")
                or state.rpc_bind
                or self._pick_rpc_bind_host(None)
            )
            record = self.master_member_record(str(lan_host))
            if previous:
                for key in ("wg_address", "wg_public_key"):
                    if previous.get(key) is not None:
                        record[key] = previous[key]
                if state.overlay == netsec.OVERLAY_WIREGUARD and previous.get("host"):
                    record["host"] = previous["host"]
            state.members[node_id] = record
            if persist:
                state.save()
            return True
        except Exception:
            return False

    def refresh_local(self) -> None:
        self.ensure_master_member(persist=False)
        state = self._state()
        member = state.members.get(state.node_id)
        if member is None:
            return
        try:
            capacity = self._probe_capacity()
            previous_ids = state.gpu_ids
            selection = self._selection(state, capacity)
            if state.gpu_ids != previous_ids:
                state.save()
            member.update(
                {
                    key: capacity[key]
                    for key in (
                        "total_ram_bytes",
                        "available_ram_bytes",
                        "requested_backend",
                        "unified_memory",
                        "binary_version",
                    )
                    if key in capacity
                }
            )
            member.update(
                backend=selection.backend,
                actual_backend=selection.backend,
                devices=selection.device_payloads(),
                vram_bytes=selection.vram_bytes,
            )
            member["reclaimable_ram_bytes"] = self._reclaimable_ram(state.process_pid)
            probe_error = capacity.get("error")
            if probe_error:
                member["capacity_probe_error"] = str(probe_error)[:512]
            else:
                member.pop("capacity_probe_error", None)
        except Exception as exc:
            # A failed warm-up used to vanish here, so Run then failed with an
            # empty inventory and no prior signal. Keep the membership tick
            # alive, but leave the message where runtime status can show it.
            detail = getattr(exc, "detail", None)
            message = detail if isinstance(detail, str) and detail else str(exc)
            member["capacity_probe_error"] = (message or "GPU capability probe failed")[:512]

    @staticmethod
    def _reclaimable_ram(pid: int | None) -> int:
        if not pid:
            return 0
        import psutil

        process = psutil.Process(pid)
        if process.name().lower() in (
            "llama-server",
            "llama-server.exe",
            "rpc-server",
            "rpc-server.exe",
        ):
            return int(process.memory_info().rss)
        return 0
