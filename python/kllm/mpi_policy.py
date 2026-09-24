"""Cluster-wide MPI GPU admission, independent of RPC backend preferences.

The caller supplies authenticated, fresh capability reports. This module never
promotes an ordinary llama-server or a hardware name to MPI engine evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class MpiAdmissionError(ValueError):
    """A malformed or ambiguous physical GPU selection."""


class MpiSelectionError(MpiAdmissionError):
    """The caller requested an unknown or empty physical GPU selection."""


REQUIRED_OPERATION_PROFILE = "dense_llama_text_f16kv_flash_tp_v1"
_VENDOR_IDS = {0x10DE: "nvidia", 0x1002: "amd", 0x106B: "apple", 0x8086: "intel"}


def _vendor_from_id(device: dict) -> str:
    raw = device.get("vendor_id")
    try:
        identifier = int(raw, 0) if isinstance(raw, str) else int(raw)
    except (TypeError, ValueError):
        return "unknown"
    return _VENDOR_IDS.get(identifier, "unknown")


def _candidates(selected: list[tuple[str, dict, dict]]) -> tuple[str, tuple[str, ...], str]:
    if all(cap["platform"] == "darwin" for _, cap, _ in selected):
        return "all_macs", ("metal", "vulkan"), "All participating hosts are macOS"
    vendors = {_vendor_from_id(device) for _, _, device in selected}
    platforms = {cap["platform"] for _, cap, _ in selected}
    if vendors == {"nvidia"}:
        return "all_nvidia", ("cuda", "vulkan"), "All selected GPUs are NVIDIA"
    if vendors == {"amd"}:
        # Windows AMD prefers Vulkan over ROCm for broader driver support
        if "win32" in platforms:
            return "all_amd", ("vulkan", "rocm"), "All selected GPUs are AMD (Windows prefers Vulkan)"
        return "all_amd", ("rocm", "vulkan"), "All selected GPUs are AMD"
    return ("heterogeneous_or_unknown", ("vulkan",),
            "Mixed or unknown GPU families require Vulkan")


def _backend_ranks(
    selected: list[tuple[str, dict, dict]], backend: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    ranks: list[dict[str, Any]] = []
    errors: list[str] = []
    common_identity: tuple[str, str, str, str, int, tuple[str, ...]] | None = None
    for node_id, cap, physical in selected:
        physical_id = physical["physical_id"]
        label = f"{node_id}/{physical_id}"
        backends = cap.get("backends")
        report = backends.get(backend) if isinstance(backends, dict) else None
        if not isinstance(report, dict):
            report = {}
        if report.get("available") is not True or report.get("backend") != backend:
            errors.append(f"{label}: {backend} unavailable: {report.get('reason', 'no verified flavor')}")
            continue
        architectures = report.get("supported_architectures")
        if (report.get("operation_profile") != REQUIRED_OPERATION_PROFILE
                or not isinstance(architectures, list) or "llama" not in architectures):
            errors.append(f"{label}: {backend} lacks the dense Llama tensor-parallel operation profile")
            continue
        provider = cap["provider"]
        identity = (
            report.get("engine_revision"), report.get("mpi_compatibility_id"),
            provider["upstream_revision"], provider["local_patch_digest"],
            provider["protocol_version"], tuple(sorted(provider["features"])),
        )
        fingerprint = report.get("fingerprint")
        if (not all(isinstance(value, str) and value
                    for value in (*identity[:4], fingerprint))
                or identity[4] < 1):
            errors.append(f"{label}: incomplete {backend} engine/provider identity")
            continue
        if common_identity is None:
            common_identity = identity
        elif identity != common_identity:
            errors.append(f"{label}: incompatible {backend} engine/provider")
            continue
        devices = report.get("devices")
        if not isinstance(devices, list) or any(not isinstance(device, dict) for device in devices):
            errors.append(f"{label}: missing {backend} device inventory")
            continue
        ids = [device.get("physical_id") for device in devices]
        handles = [device.get("device") for device in devices]
        if (not all(isinstance(value, str) and value for value in ids + handles)
                or len(ids) != len(set(ids)) or len(handles) != len(set(handles))):
            errors.append(f"{label}: ambiguous {backend} physical/device inventory")
            continue
        matches = [device for device in devices if device["physical_id"] == physical_id]
        if len(matches) != 1 or matches[0].get("backend") != backend:
            errors.append(f"{label}: no verified {backend} device mapping")
            continue
        ranks.append({
            "rank": len(ranks), "node_id": node_id, "physical_id": physical_id,
            "device": matches[0]["device"], "backend": backend,
            "capability_fingerprint": cap["fingerprint"],
            "engine_fingerprint": fingerprint,
            "engine_revision": identity[0], "mpi_compatibility_id": identity[1],
            "provider": provider,
        })
    return ranks, errors


def select_mpi_ranks(
    members: Sequence[dict[str, Any]],
    selection: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Choose one verified flavor for every selected physical GPU.

    A failed candidate may fall back to common Vulkan during planning. The
    result never contains a partial communicator. Launch still needs model and
    provider checks plus a real distributed GPU warmup.
    """
    requested = selection if selection is not None else {}
    if any(not isinstance(member, dict) for member in members):
        raise MpiAdmissionError("MPI members must be structured capability records")
    ids = [member.get("node_id") for member in members]
    if (not ids or not all(isinstance(value, str) and value for value in ids)
            or len(ids) != len(set(ids))):
        raise MpiAdmissionError("MPI requires unique nonempty node IDs")
    if sum(member.get("role") == "master" for member in members) != 1:
        raise MpiAdmissionError("MPI requires exactly one master")
    unknown = set(requested) - set(ids)
    if unknown:
        raise MpiSelectionError(f"unknown MPI members: {sorted(unknown)}")

    ordered = sorted(members, key=lambda member: (member.get("role") != "master", member["node_id"]))
    selected: list[tuple[str, dict, dict]] = []
    for member in ordered:
        node_id = member["node_id"]
        cap = member.get("mpi_capabilities") or {}
        if (cap.get("protocol") != 1 or cap.get("available") is not True
                or cap.get("platform") not in {"darwin", "win32", "linux"}
                or not isinstance(cap.get("fingerprint"), str) or not cap["fingerprint"]):
            raise MpiAdmissionError(f"{node_id}: MPI capability inventory unavailable or incomplete")
        provider = cap.get("provider")
        if (not isinstance(provider, dict)
                or not all(isinstance(provider.get(key), str) and provider[key]
                           for key in ("upstream_revision", "local_patch_digest",
                                       "target_triple", "binary_sha256"))
                or not isinstance(provider.get("protocol_version"), int)
                or not isinstance(provider.get("features"), list)
                or not all(isinstance(feature, str) for feature in provider["features"])
                or provider.get("self_test") != "passed"):
            raise MpiAdmissionError(f"{node_id}: verified MPI provider identity unavailable")
        devices = cap.get("devices")
        if not isinstance(devices, list) or any(not isinstance(device, dict) for device in devices):
            raise MpiAdmissionError(f"{node_id}: invalid physical inventory")
        physical_ids = [device.get("physical_id") for device in devices]
        if (not physical_ids or not all(isinstance(value, str) and value for value in physical_ids)
                or len(physical_ids) != len(set(physical_ids))):
            raise MpiAdmissionError(f"{node_id}: invalid or duplicate physical inventory")
        by_id = {device["physical_id"]: device for device in devices}
        wanted = requested.get(node_id, list(by_id))
        if (not wanted or len(wanted) != len(set(wanted)) or set(wanted) - set(by_id)):
            raise MpiSelectionError(f"{node_id}: MPI requires nonempty unique known physical IDs")
        selected.extend((node_id, cap, by_id[physical_id]) for physical_id in sorted(wanted))

    cluster_class, candidates, reason = _candidates(selected)
    decision: dict[str, Any] = {
        "cluster_class": cluster_class,
        "preferred_backend": candidates[0],
        "effective_backend": None,
        "backend_reason": reason,
        "backend_diagnostics": {},
        "ranks": [],
        "blockers": [],
        "capability_fingerprints": {member["node_id"]: member["mpi_capabilities"]["fingerprint"]
                                    for member in ordered},
    }
    for backend in candidates:
        ranks, errors = _backend_ranks(selected, backend)
        if errors:
            decision["backend_diagnostics"][backend] = errors
            continue
        decision["effective_backend"] = backend
        decision["ranks"] = ranks
        if backend != candidates[0]:
            decision["backend_reason"] += (
                f"; {candidates[0]} unavailable for this job; using verified common Vulkan"
            )
        return decision
    decision["blockers"] = [error for group in decision["backend_diagnostics"].values()
                            for error in group]
    return decision
