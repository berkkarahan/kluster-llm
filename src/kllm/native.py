"""Thin wrapper around the Rust extension with a pure-Python fallback for tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from typing import Any

from kllm.accel import (
    binary_capabilities,
    devices_for_backend,
    is_unified_memory,
    requested_backend,
    resolve_effective_backend,
    selectable_accel_backends,
)
from kllm.core.placement_api import plan_from_settings
from kllm.runtimes.selection import selected_binary
from pathlib import Path
import time
import sys
from kllm import usage_probe, log_sink

if TYPE_CHECKING:
    from kllm.core.placement_models import PlacementNodeInput

_NATIVE = None
_IMPORT_ERROR: Exception | None = None
_launched_identities: dict[int, dict[str, Any]] = {}

try:
    from kllm import _native as _NATIVE  # type: ignore
except Exception as exc:  # pragma: no cover - depends on maturin build
    _IMPORT_ERROR = exc


def native_available() -> bool:
    return _NATIVE is not None


def resource_probe_available() -> bool:
    return (
        _NATIVE is not None
        and hasattr(_NATIVE, "_UsageCollector")
        and hasattr(_NATIVE, "_validate_source_json")
    )


def resource_schema_versions() -> list[int]:
    return [1, 2] if getattr(_NATIVE, "RESOURCE_SCHEMA_VERSION", 1) >= 2 else [1]


def probe_resources(*, node_id: str, managed_processes: list[dict] | None = None) -> dict:
    result = usage_probe.probe(
        node_id,
        [] if managed_processes is None else managed_processes,
        supported=resource_probe_available(),
    )
    if not isinstance(result, dict):
        raise RuntimeError("native resource probe returned invalid data")
    return result


def close_resource_probes() -> None:
    usage_probe.close()


def registered_process_identity(pid: int) -> dict[str, Any] | None:
    record = _launched_identities.get(pid)
    return dict(record) if record else None


def _launch_record(result: Any) -> dict[str, Any]:
    token = getattr(result, "process_start_token", None)
    started = getattr(result, "process_started_at", None)
    _launched_identities.clear()  # The existing runtime owns one managed child per node.
    if token and started:
        _launched_identities[result.pid] = dict(
            pid=result.pid, role=result.role, start_token=token, started_at=started,
            log_sink_pid=getattr(result, "log_sink_pid", None),
        )
    record = dict(pid=result.pid, binary=result.binary, argv=list(result.argv), role=result.role)
    if token and started:
        record.update(process_start_token=token, process_started_at=started)
    record["log_sink_pid"] = getattr(result, "log_sink_pid", None)
    return record


def install_recovery_command() -> str:
    """Installer invocation for this OS. Metal exists only on Apple Silicon."""
    cmd = "python scripts/install.py"
    if sys.platform == "darwin":
        cmd += " --backend metal"
    return f"{cmd} && python scripts/install.py --check"


def _missing_native_message() -> str:
    return (
        "kllm._native extension is not built. "
        "Install with: python scripts/install.py\n"
        f"Import error: {_IMPORT_ERROR}"
    )


def require_native() -> Any:
    if _NATIVE is None:
        raise RuntimeError(_missing_native_message())
    return _NATIVE


def _inventory_vram_bytes(devices: dict[str, Any], fallback: int | None) -> int | None:
    """Canonical VRAM is the llama.cpp device total, not the OS adapter sum.

    A failed inventory keeps the Rust registry/SMI sum. A successful inventory with
    no GPU for the selected backend is unknown (``None``), so an integrated adapter
    the engine did not expose cannot inflate the node total.
    """
    if devices.get("error"):
        return fallback
    total: int | None = None
    for gpu in devices.get("gpus") or []:
        value = gpu.get("vram_bytes") if isinstance(gpu, dict) else None
        if type(value) is int and value >= 0:
            total = (total or 0) + value
    return total


def probe_capacity() -> dict[str, Any]:
    n = require_native()
    hardware_devices: Any = []
    if hasattr(n, "_probe_capacity_and_gpus"):
        snap, hardware_devices, probed = n._probe_capacity_and_gpus()
        devices = _list_gpus_from_probe(hardware_devices, probed)
    else:
        # Compatibility with an older installed extension during source upgrades.
        snap = n.probe_capacity()
        devices = list_gpus()
    listed = devices.get("gpus") or hardware_devices
    return {
        **devices,
        "unified_memory": is_unified_memory(snap.backend, listed),
        "total_ram_bytes": snap.total_ram_bytes,
        "available_ram_bytes": snap.available_ram_bytes,
        "vram_bytes": _inventory_vram_bytes(devices, snap.vram_bytes),
        "backend": devices["backend"],
        "hostname": snap.hostname,
    }


def _list_gpus_from_probe(devices: Any, backend: Any) -> dict[str, Any]:
    """Combine one native topology observation with llama.cpp capabilities."""
    devices = list(devices)
    probed = str(backend or "cpu").lower()
    requested = requested_backend()
    try:
        # Deferred import: state walks the kllm package, which must not import native at load time.
        from kllm.state import repo_root

        root = repo_root()
        binary = resolve_llama_server(str(root) if root else None)
        if not binary:
            raise RuntimeError("llama-server is not installed")
        capabilities = binary_capabilities(
            binary, Path(binary).stat().st_mtime_ns, int(time.monotonic() // 30)
        )
        installed = capabilities["devices"]
        supported = list(dict.fromkeys(d["backend"] for d in installed))
        actual, backend_warning = resolve_effective_backend(
            probed=probed,
            supported=supported,
            requested=requested,
        )
        selected = devices_for_backend(installed, actual)
        selected = [dict(d, index=i) for i, d in enumerate(selected)]
        probe_error: str | None = None
        wants_metal = probed == "metal" or requested == "metal"
        if capabilities.get("metal_init_failed"):
            probe_error = (
                "llama-server linked Metal but could not create a Metal device "
                "(empty device id in --list-devices)"
            )
        elif wants_metal and not selected:
            probe_error = (
                "llama-server did not expose any Metal devices in --list-devices "
                f"(hardware probe reported {len(devices)} device(s))"
            )
        out: dict[str, Any] = {
            "backend": actual,
            "actual_backend": actual,
            "requested_backend": requested,
            "probed_backend": probed,
            "gpus": selected,
            "devices": selected,
            "unified_memory": is_unified_memory(probed, selected or devices),
            "binary_version": capabilities["binary_version"],
            "allowed_backends": selectable_accel_backends(),
            "installed_backends": supported,
            "backend_warning": backend_warning,
            "probed_device_count": len(devices),
        }
        if probe_error:
            out["error"] = probe_error
        return out
    except Exception as exc:
        # Hardware probes alone do not establish installed llama.cpp capabilities.
        return {
            "backend": "cpu",
            "actual_backend": "unknown",
            "requested_backend": requested,
            "probed_backend": probed,
            "gpus": [],
            "devices": [],
            "unified_memory": is_unified_memory(probed, devices),
            "binary_version": None,
            "allowed_backends": selectable_accel_backends(),
            "installed_backends": [],
            "error": str(exc),
            "probed_device_count": len(devices),
        }


def list_gpus() -> dict[str, Any]:
    """Return ``{backend, probed_backend, gpus: [{index, name, vram_bytes}, ...]}``."""
    if _NATIVE is None or not hasattr(_NATIVE, "list_gpus"):
        error = _missing_native_message() if _NATIVE is None else "kllm._native has no list_gpus"
        return {"backend": "cpu", "probed_backend": "cpu", "gpus": [], "error": error}
    return _list_gpus_from_probe(*_NATIVE.list_gpus())


def _fit_status_from_bytes(needed: int, budget: int) -> tuple[str, str]:
    """Match the native planner when an older PlanResult lacks fit_status."""
    if budget <= 0:
        return "overflow", "cluster budget is zero; add workers or reduce context"
    if needed > budget:
        return (
            "overflow",
            f"need ~{needed // (1024 * 1024)} MB but cluster budget is "
            f"~{budget // (1024 * 1024)} MB; reduce context or add capacity",
        )
    if needed > int(budget * 0.90):
        return (
            "tight",
            f"usage ~{needed // (1024 * 1024)} MB is above 90% of "
            f"~{budget // (1024 * 1024)} MB budget; run may be tight",
        )
    return "fits", ""


def plan_split(
    nodes: Sequence[Mapping[str, Any] | PlacementNodeInput],
    model_size_bytes: int,
    context: int = 4096,
    headroom: float = 0.80,
    tensor_split: list[float] | None = None,
    device: str = "gpu",
    context_auto: bool = False,
    vram_weight: float = 0.6,
    latency_weight: float = 0.4,
    cache_type_k: str = "q8_0",
    cache_type_v: str = "q8_0",
    parallel_slots: int = 1,
    mmproj_size_bytes: int = 0,
    draft_model_size_bytes: int = 0,
    vision: bool = False,
    model_layout: dict | None = None,
    launch_settings: dict | None = None,
    node_shares: dict[str, float] | None = None,
    required_nodes: Sequence[str] = (),
    node_priority: Sequence[str] = (),
) -> dict[str, Any]:
    if launch_settings is not None:
        return plan_from_settings(
            nodes,
            model_size_bytes,
            launch_settings=launch_settings,
            model_layout=model_layout,
            context=context,
            context_auto=context_auto,
            headroom=headroom,
            device=device,
            vram_weight=vram_weight,
            latency_weight=latency_weight,
            mmproj_size_bytes=mmproj_size_bytes,
            draft_model_size_bytes=draft_model_size_bytes,
            vision=vision,
            node_shares=node_shares,
            required_nodes=required_nodes,
            node_priority=node_priority,
        ).to_payload()
    if required_nodes or node_priority or node_shares:
        raise ValueError("node placement constraints require resolved launch settings")
    n = require_native()
    req: dict[str, Any] = {
        "nodes": nodes,
        "model_size_bytes": model_size_bytes,
        "context": context,
        "context_auto": context_auto,
        "headroom": headroom,
        "device": device,
        "vram_weight": vram_weight,
        "latency_weight": latency_weight,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
        "parallel_slots": max(1, int(parallel_slots)),
        "mmproj_size_bytes": int(mmproj_size_bytes),
        "draft_model_size_bytes": int(draft_model_size_bytes),
        "vision": bool(vision),
    }
    if tensor_split is not None:
        req["tensor_split"] = tensor_split
    plan = n.plan_split(req)
    node_ids = list(plan.node_ids)
    effective = list(plan.node_effective_devices)
    fallbacks = list(plan.node_fallbacks)
    node_devices = [
        {
            "node_id": nid,
            "effective": eff,
            "fallback": fb,
        }
        for nid, eff, fb in zip(node_ids, effective, fallbacks)
    ]
    model_size = int(plan.model_size_bytes)
    kv_estimate = int(plan.kv_estimate_bytes)
    total_budget = int(plan.total_budget_bytes)
    estimated = int(getattr(plan, "estimated_bytes", model_size + kv_estimate))
    budget = int(getattr(plan, "budget_bytes", total_budget))
    fit_status = getattr(plan, "fit_status", None)
    hint = getattr(plan, "hint", None)
    if not fit_status:
        fit_status, hint = _fit_status_from_bytes(estimated, budget)
    return {
        "tensor_split": list(plan.tensor_split),
        "tensor_split_csv": plan.tensor_split_csv(),
        "budgets_bytes": list(plan.budgets_bytes),
        "node_ids": node_ids,
        "total_budget_bytes": total_budget,
        "model_size_bytes": model_size,
        "kv_estimate_bytes": kv_estimate,
        "estimated_layers": plan.estimated_layers,
        "ngl": plan.ngl,
        "split_mode": plan.split_mode,
        "device_requested": plan.device_requested,
        "device_effective": plan.device_effective,
        "node_devices": node_devices,
        "fit_status": str(fit_status),
        "estimated_bytes": estimated,
        "budget_bytes": budget,
        "hint": hint or "",
        "context_effective": int(getattr(plan, "context_effective", context)),
        "node_alloc_bytes": list(getattr(plan, "node_alloc_bytes", [])),
        "node_scores": list(getattr(plan, "node_scores", [])),
        "node_latency_ms": list(getattr(plan, "node_latency_ms", [])),
        "vram_alloc_bytes": int(getattr(plan, "vram_alloc_bytes", 0)),
        "ram_alloc_bytes": int(getattr(plan, "ram_alloc_bytes", 0)),
        "estimate_confidence": "approximate; legacy native estimator",
        "estimate_complete": False,
        "estimate_warnings": [
            "Legacy native estimate uses file-size heuristics; use PLAN & RUN for the component estimate"
        ],
    }


def start_master(**kwargs: Any) -> dict[str, Any]:
    n = require_native()
    selected = selected_binary('llama-server')
    if selected:
        if getattr(n, 'RUNTIME_SELECTION_VERSION', 0) != 1:
            raise RuntimeError('Rebuild the native extension with python scripts/build.py for managed runtimes')
        kwargs['binary_path'] = selected
    if kwargs.get("log_path"):
        kwargs["log_helper_argv"] = log_sink.command(kwargs["log_path"])
    if kwargs.get("env") is None:
        kwargs.pop("env", None)
    # Installed pyd extracts micro_batch as i32; Python used to pass str "2".
    mb = kwargs.get("micro_batch")
    if isinstance(mb, str) and mb not in ("auto", ""):
        try:
            kwargs["micro_batch"] = int(mb)
        except ValueError:
            pass
    result = n.start_master(kwargs)
    return _launch_record(result)


def start_worker(**kwargs: Any) -> dict[str, Any]:
    n = require_native()
    selected = selected_binary('rpc-server')
    if selected:
        if getattr(n, 'RUNTIME_SELECTION_VERSION', 0) != 1:
            raise RuntimeError('Rebuild the native extension with python scripts/build.py for managed runtimes')
        kwargs['binary_path'] = selected
    if kwargs.get("log_path"):
        kwargs["log_helper_argv"] = log_sink.command(kwargs["log_path"])
    if "devices" not in kwargs:
        # Deferred import: config reads ~/.kllm only when the caller omitted devices.
        from kllm import config

        selected = config.get_gpu_ids()
        inventory = list_gpus().get("devices", [])
        kwargs["devices"] = [
            d["device"] for d in inventory if selected is None or d["index"] in selected
        ]
    # Explicit device names preserve the probed order; visibility masks renumber them.
    if kwargs.get("devices"):
        kwargs.pop("env", None)
    if kwargs.get("env") is None:
        kwargs.pop("env", None)
    result = n.start_worker(kwargs)
    return _launch_record(result)


def stop_process(pid: int, force: bool = False) -> bool:
    stopped = bool(require_native().stop_process(pid, force))
    if stopped:
        _launched_identities.pop(pid, None)
    return stopped


def process_alive(pid: int) -> bool:
    return bool(require_native().process_alive(pid))


def resolve_llama_server(repo_root: str | None = None) -> str | None:
    selected = selected_binary('llama-server')
    if selected:
        return selected
    path = require_native().resolve_llama_server(repo_root)
    return path if isinstance(path, str) else None


def resolve_rpc_server(repo_root: str | None = None) -> str | None:
    selected = selected_binary('rpc-server')
    if selected:
        return selected
    path = require_native().resolve_rpc_server(repo_root)
    return path if isinstance(path, str) else None


# These operations deliberately have no Python networking fallback: host mutation
# and run readiness must fail closed when the native boundary is unavailable.
def managed_network_available() -> bool:
    return _NATIVE is not None and all(hasattr(_NATIVE, name) for name in (
        "start_network_bridge", "probe_network_rpc", "start_address_observer", "probe_ipv4_conflict",
    ))


def _managed_native():
    if not managed_network_available():
        raise RuntimeError("managed-network native support is unavailable; rebuild the agent")
    return _NATIVE


def start_network_bridge(config: dict):
    import json
    return _managed_native().start_network_bridge(json.dumps(config, allow_nan=False))


def network_bridge_status(handle) -> dict:
    import json
    result: dict = json.loads(handle.status())
    return result


def stop_network_bridge(handle, expected_instance_id: str, *, terminate: bool = False) -> bool:
    return bool(handle.stop(expected_instance_id, terminate))


def probe_network_rpc(endpoint: str, source: str, installed_tag: str, timeout_ms: int = 2000) -> dict:
    import json
    result: dict = json.loads(_managed_native().probe_network_rpc(endpoint, source, installed_tag, timeout_ms))
    return result


def start_address_observer(interface_index: int, epoch: str):
    return _managed_native().start_address_observer(interface_index, epoch)


def address_observer_events(handle) -> dict:
    import json
    result: dict = json.loads(handle.events())
    return result


def stop_address_observer(handle) -> None:
    handle.close()


def probe_ipv4_conflict(interface: str, mac: str, address: str) -> bool:
    return bool(_managed_native().probe_ipv4_conflict(interface, mac, address))


def start_ipv4_conflict_observer(interface: str, mac: str, address: str):
    backend = _managed_native()
    if not hasattr(backend, 'start_ipv4_conflict_observer'):
        raise RuntimeError('native Ethernet conflict observation is unavailable')
    return backend.start_ipv4_conflict_observer(interface, mac, address)
