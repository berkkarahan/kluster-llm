"""Thin wrapper around the Rust extension with a pure-Python fallback for tests."""

from __future__ import annotations

from typing import Any

from kllm.accel import effective_accel_backend

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
        "backend": effective_accel_backend(snap.backend),
        "hostname": snap.hostname,
    }


def list_gpus() -> dict[str, Any]:
    """Return ``{backend, probed_backend, gpus: [{index, name, vram_bytes}, ...]}``."""
    if _NATIVE is None or not hasattr(_NATIVE, "list_gpus"):
        return {"backend": "cpu", "probed_backend": "cpu", "gpus": []}
    devices, backend = _NATIVE.list_gpus()
    probed = str(backend).strip().lower() if backend else "cpu"
    return {
        "probed_backend": probed,
        "backend": effective_accel_backend(probed),
        "gpus": [
            {
                "index": int(d.index),
                "name": str(d.name),
                "vram_bytes": d.vram_bytes,
            }
            for d in devices
        ],
    }


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
    nodes: list[dict[str, Any]],
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
    }


def start_master(**kwargs: Any) -> dict[str, Any]:
    n = require_native()
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
