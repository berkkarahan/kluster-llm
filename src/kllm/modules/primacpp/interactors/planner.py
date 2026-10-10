"""Capacity candidates and explicit, immutable Prima preparation previews."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from kllm.modules.primacpp.interactors.execution import (
    ExecutionManifest,
    Segment,
    asset_files,
    inventory_identity,
)
from kllm.modules.primacpp.interactors.placement import topology_from_plan, network_edges
from kllm.modules.primacpp.interactors.recipe import qualification_errors
from kllm.modules.runtimes.entities import body_digest


def candidates(body, members, execute_plan, *, limit=32):
    # Explicit priority is a user constraint, never an optimizer suggestion.
    options = [deepcopy(members)]
    if not body.node_priority:
        optional = [
            n
            for n, m in sorted(members.items())
            if m.get("role") == "worker" and n not in body.required_nodes
        ]
        for node in optional[: limit - 1]:
            options.append({n: m for n, m in members.items() if n != node})
    results, seen = [], set()
    for inventory in options:
        plan = execute_plan(body, inventory)
        if plan.get("fit_status") == "overflow":
            continue
        key = body_digest(
            {
                k: plan.get(k)
                for k in (
                    "device_allocations",
                    "cpu_layers",
                    "ngl",
                    "tensor_split_csv",
                    "tensor_overrides",
                )
            }
        )
        if key not in seen:
            results.append((plan, inventory))
            seen.add(key)
    if not results:
        raise ValueError("No feasible Kluster capacity plan")
    return results


def preview(body, runtime: dict, members: dict, model: Path, execute_plan) -> dict:
    feasible = candidates(body, members, execute_plan)
    plan, _ = feasible[0]
    stat = model.stat()
    # This is an observation, not a claim that a large model has been digest-verified.
    identity = {
        "runtime_id": runtime["runtime_id"],
        "settings": body.model_dump(),
        "model": {"name": model.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "inventory_digest": inventory_identity(members, body.network_id),
        "placement": {
            k: plan.get(k)
            for k in (
                "device_allocations",
                "cpu_layers",
                "ngl",
                "tensor_split_csv",
                "tensor_overrides",
            )
        },
    }
    errors = qualification_errors(runtime)
    topology = None
    try:
        topology = topology_from_plan(plan, members, runtime["runtime_id"])
    except ValueError as error:
        errors = [*errors, str(error)]
    return {
        **plan,
        "engine": "primacpp",
        "runtime_id": runtime["runtime_id"],
        "transport": "prima-ring-tcp",
        "plan_digest": body_digest(identity),
        "inventory_digest": identity["inventory_digest"],
        "prima_topology": topology.model_dump() if topology else None,
        "network_edges": network_edges(topology) if topology else [],
        "runtime_required_nodes": [r.node_id for r in topology.ranks] if topology else [],
        "candidate_count": len(feasible),
        "profiling": {"status": "unmeasured", "predicted_decode_ms": None},
        "inference_supported": not errors,
        "qualification_errors": errors,
    }


def materialize(
    body, runtime, members, model, execute_plan, *, expected_digest: str, companions=None
):
    value = preview(body, runtime, members, model, execute_plan)
    if value["plan_digest"] != expected_digest:
        raise ValueError("Prima plan changed; refresh the plan before preparation")
    if value["prima_topology"] is None:
        raise ValueError("Prima cannot represent the selected placement")
    from kllm.modules.primacpp.entities import PrimaTopology

    topology = PrimaTopology.model_validate(value["prima_topology"])
    files = asset_files(model, companions)
    backends = {}
    for rank in topology.ranks:
        member = members[rank.node_id]
        backend = member.get("actual_backend") or member.get("backend")
        if rank.ngl and backend not in ("vulkan", "metal", "cuda", "rocm"):
            raise ValueError(
                f"{rank.node_id}: select an explicit GPU backend before preparing Prima"
            )
        backends[rank.node_id] = backend if rank.ngl else "cpu"
    manifest = ExecutionManifest(
        runtime_id=runtime["runtime_id"],
        run_id=str(uuid4()),
        plan_digest=expected_digest,
        total_layers=topology.total_layers,
        ranks=topology.ranks,
        data_port=topology.data_port,
        signal_port=topology.signal_port,
        split_mode=body.split_mode or "layer",
        assets=[a for a, _ in files],
        settings=body.model_dump(),
        inventory_digest=value["inventory_digest"],
        network_id=body.network_id,
        segments=sorted(
            [
                Segment(start=r.start, end=r.end, node_id=rank.node_id)
                for rank in topology.ranks
                for r in rank.layers
            ],
            key=lambda s: s.start,
        ),
        transfer_edges=value["network_edges"],
        backends=backends,
    )
    manifest = manifest.model_copy(
        update={"segments": sorted(manifest.segments, key=lambda s: s.start)}
    )
    # Validate after sorting; layer ownership remains unchanged.
    return ExecutionManifest.model_validate(manifest.model_dump()), files


def choose_measured_candidate(
    feasible, profiles, links, *, runtime_id, model_sha256, settings_digest, hardware, backends, now
):
    """Only complete end-to-end evidence can replace the capacity-first candidate."""
    from kllm.modules.primacpp.entities import DeviceProfile

    scores = []
    for plan, members in feasible:
        try:
            topology = topology_from_plan(plan, members, runtime_id)
        except ValueError:
            return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
        score = 0.0
        for rank in topology.ranks:
            raw = profiles.get(rank.node_id)
            if raw is None:
                return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
            value = raw if isinstance(raw, DeviceProfile) else DeviceProfile.model_validate(raw)
            if (
                value.measurement_kind != "end_to_end"
                or value.runtime_id != runtime_id
                or value.model_sha256 != model_sha256
                or value.settings_digest != settings_digest
                or value.hardware_digest != hardware.get(rank.node_id)
                or value.backend != backends.get(rank.node_id)
                or not 0 <= now - value.measured_at <= 86400
                or value.prefill_ms is None
                or value.decode_ms is None
                or value.peak_memory_bytes is None
                or (
                    rank.ngl
                    and (
                        not value.device_identity
                        or not value.driver_identity
                        or value.gpu_layer_ms is None
                    )
                )
            ):
                return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
            layers = sum(r.end - r.start for r in rank.layers)
            score += (layers - rank.ngl) * value.cpu_layer_ms + rank.ngl * (value.gpu_layer_ms or 0)
        for edge in network_edges(topology):
            link = links.get((edge["source"], edge["target"]))
            if (
                link is None
                or not 0 <= now - link.get("measured_at", 0) <= 86400
                or link.get("latency_ms", -1) < 0
                or link.get("bandwidth_bytes_s", 0) <= 0
                or link.get("payload_bytes", -1) < 0
            ):
                return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
            score += link["latency_ms"] + link["payload_bytes"] / link["bandwidth_bytes_s"] * 1000
        scores.append(score)
    index = min(range(len(scores)), key=lambda i: (scores[i], i))
    return feasible[index], {"status": "profiled_estimate", "predicted_decode_ms": scores[index]}
