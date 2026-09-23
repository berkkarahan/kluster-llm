"""Truthful MPI plan preview while the distributed GPU engine is unavailable."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kllm.gguf_meta import read_inference_metadata, read_model_layout
from kllm.mpi_policy import MpiAdmissionError, MpiSelectionError, select_mpi_ranks
from kllm.mpi_manifest import (
    MANIFEST_VERSION,
    MpiManifestError,
    build_shard_manifest,
    model_sha256,
)
from kllm.run_settings import RunSettingsFields

MPI_PLAN_CONTRACT = "kllm-mpi-plan-v1"


def _fingerprint(
    body: RunSettingsFields,
    members: dict[str, Any],
    model_path: Path,
    decision: dict[str, Any],
    manifest_identity: str | None,
    blockers: list[str],
) -> str:
    stat = model_path.stat()
    try:
        runtime_version = importlib.metadata.version("kllm")
    except importlib.metadata.PackageNotFoundError:
        runtime_version = "source-checkout"
    request = body.normalized_auto_fields()
    request.pop("remember", None)
    request.pop("advanced_open", None)
    evidence = {
        "runtime": {"package": runtime_version, "plan_contract": MPI_PLAN_CONTRACT},
        "request": request,
        "model": {"name": model_path.name, "size": stat.st_size,
                  "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
                  "sha256": model_sha256(model_path)},
        "members": [
            {key: member.get(key) for key in
             ("node_id", "role", "host", "lan_host", "wg_address", "control_port",
              "mpi_capabilities")}
            for member in sorted(members.values(), key=lambda item: str(item.get("node_id", "")))
        ],
        "admission": decision,
        "manifest_identity": manifest_identity,
        "blockers": blockers,
    }
    raw = json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _surface_blockers(body: RunSettingsFields, meta: dict | None, layout: dict | None) -> list[str]:
    blockers = []
    if not meta or meta.get("architecture") != "llama":
        blockers.append("MPI first release requires a dense Llama GGUF model")
    if not layout or layout.get("architecture") != "llama" or layout.get("confidence") != "metadata":
        blockers.append("MPI requires a complete GGUF tensor layout")
    if body.context_auto:
        blockers.append("MPI requires an explicit context")
    if body.cache_type_k != "f16" or body.cache_type_v != "f16":
        blockers.append("MPI requires f16 key and value caches")
    if body.parallel_slots not in ("auto", 1):
        blockers.append("MPI supports one concurrent sequence")
    if body.vision or body.mmproj:
        blockers.append("MPI first release does not support vision")
    if body.draft_model or body.speculative != "auto":
        blockers.append("MPI first release does not support speculative decoding")
    if body.extra_args:
        blockers.append("MPI first release does not accept extra llama-server arguments")
    if body.gpu_ids is not None:
        blockers.append("Use mpi_device_ids to select physical GPUs for MPI")
    return blockers


def execute_mpi_plan(
    body: RunSettingsFields, members: dict[str, Any], model_path: Path,
) -> dict[str, Any]:
    """Describe admission without claiming that a provider smoke is an engine."""
    unknown_members = set(body.mpi_device_ids or {}) - {
        member.get("node_id") for member in members.values()
    }
    if unknown_members:
        raise HTTPException(400, f"unknown MPI members: {sorted(unknown_members)}")
    meta = read_inference_metadata(model_path)
    layout = read_model_layout(model_path)
    blockers = _surface_blockers(body, meta, layout)
    try:
        decision = select_mpi_ranks(list(members.values()), body.mpi_device_ids)
    except MpiSelectionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except MpiAdmissionError as exc:
        decision = {
            "cluster_class": None, "preferred_backend": None, "effective_backend": None,
            "backend_reason": "No verified cluster-wide MPI capability inventory",
            "backend_diagnostics": {}, "ranks": [], "blockers": [str(exc)],
            "capability_fingerprints": {},
        }
    blockers.extend(decision["blockers"])
    manifest = None
    if decision["ranks"] and layout and layout.get("architecture") == "llama":
        try:
            manifest = build_shard_manifest(model_path, len(decision["ranks"]), body.context)
        except (MpiManifestError, OSError) as exc:
            blockers.append(f"MPI shard manifest unavailable: {exc}")
    # Engine capability reports and a manifest are necessary, but the current
    # checkout still lacks the reviewed b11026 distributed graph patch. Keep
    # admission closed until that executable produces its own warmup proof.
    blockers.append("MPI tensor-parallel llama.cpp graph engine is unavailable")
    fingerprint = _fingerprint(
        body,
        members,
        model_path,
        decision,
        manifest["identity"] if manifest else None,
        blockers,
    )
    return {
        "transport": "mpi", "runtime_transport": "mpi",
        **decision,
        "node_ids": sorted(str(member.get("node_id", "")) for member in members.values()),
        "launchable": False, "blockers": blockers,
        "plan_fingerprint": fingerprint,
        "engine_revision": (decision["ranks"][0]["engine_revision"]
                            if decision["ranks"] else None),
        "manifest_version": manifest["version"] if manifest else MANIFEST_VERSION,
        "manifest_identity": manifest["identity"] if manifest else None,
        "rank_weight_bytes": manifest["rank_weight_bytes"] if manifest else None,
        "rank_kv_bytes": manifest["rank_kv_bytes"] if manifest else None,
        "rank_model_bytes": manifest["rank_model_bytes"] if manifest else None,
        "resolved_launch_settings": {
            "context": body.context,
            "parallel_slots": 1,
            "cache_type_k": body.cache_type_k,
            "cache_type_v": body.cache_type_v,
            "flash_attention": "on",
        },
        "mpi_moves_activations": False,
        "mpi": {"state": "blocked", "ranks": decision["ranks"],
                "manifest_identity": manifest["identity"] if manifest else None,
                "warmup_complete": False},
    }
