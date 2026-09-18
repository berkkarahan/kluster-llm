"""Thin wrapper around the Rust extension with a pure-Python fallback for tests."""

from __future__ import annotations

from typing import Any

_NATIVE = None
_IMPORT_ERROR: Exception | None = None

try:
    from kllm import _native as _NATIVE  # type: ignore
except Exception as exc:  # pragma: no cover - depends on maturin build
    _IMPORT_ERROR = exc


def native_available() -> bool:
    return _NATIVE is not None


def require_native():
    if _NATIVE is None:
        raise RuntimeError(
            "kllm._native extension is not built. "
            "Install with: python scripts/install.py\n"
            f"Import error: {_IMPORT_ERROR}"
        )
    return _NATIVE


def probe_capacity() -> dict[str, Any]:
    n = require_native()
    snap = n.probe_capacity()
    return {
        "total_ram_bytes": snap.total_ram_bytes,
        "available_ram_bytes": snap.available_ram_bytes,
        "vram_bytes": snap.vram_bytes,
        "backend": snap.backend,
        "hostname": snap.hostname,
    }


def list_gpus() -> dict[str, Any]:
    """Return ``{backend, gpus: [{index, name, vram_bytes}, ...]}``."""
    if _NATIVE is None or not hasattr(_NATIVE, "list_gpus"):
        return {"backend": "cpu", "gpus": []}
    devices, backend = _NATIVE.list_gpus()
    return {
        "backend": backend,
        "gpus": [
            {
                "index": int(d.index),
                "name": str(d.name),
                "vram_bytes": d.vram_bytes,
            }
            for d in devices
        ],
    }


def plan_split(
    nodes: list[dict[str, Any]],
    model_size_bytes: int,
    context: int = 4096,
    headroom: float = 0.80,
    tensor_split: list[float] | None = None,
    device: str = "gpu",
    context_auto: bool = False,
    vram_weight: float = 0.6,
    latency_weight: float = 0.4,
) -> dict[str, Any]:
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
    return {
        "tensor_split": list(plan.tensor_split),
        "tensor_split_csv": plan.tensor_split_csv(),
        "budgets_bytes": list(plan.budgets_bytes),
        "node_ids": node_ids,
        "total_budget_bytes": plan.total_budget_bytes,
        "model_size_bytes": plan.model_size_bytes,
        "kv_estimate_bytes": plan.kv_estimate_bytes,
        "estimated_layers": plan.estimated_layers,
        "ngl": plan.ngl,
        "split_mode": plan.split_mode,
        "device_requested": plan.device_requested,
        "device_effective": plan.device_effective,
        "node_devices": node_devices,
        "fit_status": plan.fit_status,
        "estimated_bytes": plan.estimated_bytes,
        "budget_bytes": plan.budget_bytes,
        "hint": plan.hint,
        "context_effective": plan.context_effective,
        "node_alloc_bytes": list(plan.node_alloc_bytes),
        "node_scores": list(plan.node_scores),
        "node_latency_ms": list(plan.node_latency_ms),
        "vram_alloc_bytes": plan.vram_alloc_bytes,
        "ram_alloc_bytes": plan.ram_alloc_bytes,
    }


def start_master(**kwargs: Any) -> dict[str, Any]:
    n = require_native()
    if kwargs.get("env") is None:
        kwargs.pop("env", None)
    result = n.start_master(kwargs)
    return {
        "pid": result.pid,
        "binary": result.binary,
        "argv": list(result.argv),
        "role": result.role,
    }


def start_worker(**kwargs: Any) -> dict[str, Any]:
    n = require_native()
    if kwargs.get("env") is None:
        kwargs.pop("env", None)
    result = n.start_worker(kwargs)
    return {
        "pid": result.pid,
        "binary": result.binary,
        "argv": list(result.argv),
        "role": result.role,
    }


def stop_process(pid: int, force: bool = False) -> bool:
    return bool(require_native().stop_process(pid, force))


def process_alive(pid: int) -> bool:
    return bool(require_native().process_alive(pid))


def resolve_llama_server(repo_root: str | None = None) -> str | None:
    return require_native().resolve_llama_server(repo_root)


def resolve_rpc_server(repo_root: str | None = None) -> str | None:
    return require_native().resolve_rpc_server(repo_root)
