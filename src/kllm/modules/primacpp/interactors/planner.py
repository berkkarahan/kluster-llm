"""Capacity candidates and explicit, immutable Prima preparation previews."""

from __future__ import annotations

from copy import deepcopy
from itertools import combinations
from pathlib import Path
from uuid import uuid4

from starlette.exceptions import HTTPException

from kllm.modules.primacpp.interactors.execution import (
    ExecutionManifest,
    ProfileEdge,
    Segment,
    asset_files,
    inventory_identity,
    vulkan_kv_cache_error,
)
from kllm.modules.primacpp.interactors.placement import (
    topology_from_plan, network_edges, require_layer_execution,
)
from kllm.modules.primacpp.interactors.model_support import architecture_support, model_architecture
from kllm.modules.primacpp.interactors.platforms import normalize_platform, prima_member_backend
from kllm.modules.primacpp.interactors.recipe import qualification_errors
from kllm.modules.runtimes.entities import body_digest


def participant_platforms(topology, members: dict, runtime: dict | None = None) -> dict:
    """Reported OS, architecture and backend per planned rank; unreported values stay None."""
    result = {}
    for rank in topology.ranks:
        member = members[rank.node_id]
        result[rank.node_id] = {
            "rank": rank.rank,
            "platform": normalize_platform(member.get("platform")),
            "architecture": member.get("architecture"),
            "backend": prima_member_backend(member, runtime=runtime),
        }
    return result


def _member_matches_required(member: dict, member_key: str, req_set_lower: set[str]) -> bool:
    nid = str(member.get("node_id") or member_key or "").strip().lower()
    hname = str(member.get("hostname") or "").strip().lower()
    host = str(member.get("host") or "").strip().lower()
    lan = str(member.get("lan_host") or "").strip().lower()
    is_m = member.get("role") == "master" or member.get("is_master")
    k_lower = str(member_key or "").strip().lower()

    if (
        (nid and nid in req_set_lower)
        or (k_lower and k_lower in req_set_lower)
        or (hname and hname in req_set_lower)
        or (host and host in req_set_lower)
        or (lan and lan in req_set_lower)
        or (is_m and "master" in req_set_lower)
    ):
        return True
    for r in req_set_lower:
        if r != "master" and len(r) >= 6:
            if nid and (nid.startswith(r) or r.startswith(nid)):
                return True
            if k_lower and (k_lower.startswith(r) or r.startswith(k_lower)):
                return True
    return False


def candidates(body, members, execute_plan, *, limit=32):
    # Explicit priority is a user constraint, never an optimizer suggestion.
    if type(limit) is not int or limit < 1:
        raise ValueError("Prima candidate limit must be positive")
    options = [deepcopy(members)]
    if not body.node_priority:
        req_set = {str(n).strip() for n in body.required_nodes if str(n).strip()}
        req_set_lower = {n.lower() for n in req_set}
        for k, m in members.items():
            if _member_matches_required(m, k, req_set_lower):
                nid = m.get("node_id")
                if nid:
                    req_set.add(str(nid))
                    req_set_lower.add(str(nid).lower())
                if m.get("hostname"):
                    req_set.add(str(m["hostname"]))
                    req_set_lower.add(str(m["hostname"]).lower())
                if m.get("host"):
                    req_set.add(str(m["host"]))
                    req_set_lower.add(str(m["host"]).lower())
                if m.get("lan_host"):
                    req_set.add(str(m["lan_host"]))
                    req_set_lower.add(str(m["lan_host"]).lower())
                if k:
                    req_set.add(str(k))
                    req_set_lower.add(str(k).lower())
        optional = [
            n
            for n, m in sorted(members.items())
            if m.get("role") == "worker" and not _member_matches_required(m, n, req_set_lower)
        ]
        workers = sum(m.get("role") == "worker" for m in members.values())
        for removed_count in range(1, len(optional) + 1):
            if body.network_id and workers - removed_count < 1:
                break  # A network-bound candidate still needs a native worker.
            for removed in combinations(optional, removed_count):
                if len(options) >= limit:
                    break
                omitted = set(removed)
                options.append({n: m for n, m in members.items() if n not in omitted})
            if len(options) >= limit:
                break
    results, seen = [], set()
    capacity_error = None
    for index, inventory in enumerate(options):
        candidate_body = body
        if index > 0:
            def _node_in_inventory(node_ref: str, inv: dict) -> bool:
                ref = str(node_ref).strip()
                ref_lower = ref.lower()
                for k, m in inv.items():
                    if ref == k or ref_lower == str(k).lower():
                        return True
                    nid = str(m.get("node_id") or "")
                    if ref == nid or ref_lower == nid.lower():
                        return True
                    hname = str(m.get("hostname") or "")
                    if ref == hname or ref_lower == hname.lower():
                        return True
                    host = str(m.get("host") or "")
                    if ref == host or ref_lower == host.lower():
                        return True
                    lan = str(m.get("lan_host") or "")
                    if ref == lan or ref_lower == lan.lower():
                        return True
                    if (m.get("role") == "master" or m.get("is_master")) and ref_lower == "master":
                        return True
                    if ref_lower != "master" and len(ref_lower) >= 6 and (nid.lower().startswith(ref_lower) or ref_lower.startswith(nid.lower())):
                        return True
                return False

            pruned_req = [n for n in body.required_nodes if _node_in_inventory(n, inventory)]
            pruned_pri = [n for n in body.node_priority if _node_in_inventory(n, inventory)]
            if hasattr(body, "model_copy"):
                candidate_body = body.model_copy(
                    update={"required_nodes": pruned_req, "node_priority": pruned_pri}
                )
            elif hasattr(body, "__dataclass_fields__"):
                from dataclasses import replace

                candidate_body = replace(
                    body, required_nodes=pruned_req, node_priority=pruned_pri
                )
        try:
            plan = execute_plan(candidate_body, inventory)
        except (ValueError, HTTPException):
            if index == 0:
                raise
            continue  # An optional subset may be invalid without invalidating the full plan.
        if plan.get("fit_status") == "overflow":
            if index == 0:
                capacity_error = plan.get("hint")
            continue
        key = body_digest(
            {
                "participants": sorted(inventory),
                "placement": {k: plan.get(k) for k in (
                    "device_allocations",
                    "cpu_layers",
                    "ngl",
                    "tensor_split_csv",
                    "tensor_overrides",
                )},
            }
        )
        if key not in seen:
            results.append((plan, inventory))
            seen.add(key)
    if not results:
        detail = f": {capacity_error}" if capacity_error else ""
        raise ValueError(
            "No feasible Kluster capacity plan" + detail
            + ". Reduce context or increase the selected nodes' usable memory for whole-layer placement."
        )
    return results


def _stable_placement(plan: dict) -> dict:
    """Digest identity for the selected placement, without live measurements.

    Device allocations carry ``budget_bytes`` (derived from live available
    memory), ``latency_ms`` (probe EMA) and ``score`` (a function of both):
    all three change between consecutive plans on a live cluster, so binding
    them into the plan digest makes preparation and Run unable to match their
    own re-planned digest.
    """
    volatile = ("budget_bytes", "latency_ms", "score")
    placement = {}
    for key in ("device_allocations", "cpu_layers", "ngl", "tensor_split_csv", "tensor_overrides"):
        value = plan.get(key)
        if key == "device_allocations" and isinstance(value, list):
            value = [
                {name: item for name, item in entry.items() if name not in volatile}
                for entry in value
            ]
        placement[key] = value
    return placement


def preview(body, runtime: dict, members: dict, model: Path, execute_plan, *, score=None) -> dict:
    from kllm.modules.primacpp.entities import HighsScheduleReport
    from kllm.modules.primacpp.interactors.highs_scheduler import solve_highs_halda_schedule

    feasible = candidates(body, members, execute_plan)
    plan, selected_members = feasible[0]
    profiling = {"status": "unmeasured", "predicted_decode_ms": None}
    if score is not None:
        (plan, selected_members), profiling = score(feasible)
    if isinstance(profiling, dict) and isinstance(profiling.get("scheduler"), dict):
        scheduler_report = HighsScheduleReport.model_validate(profiling["scheduler"])
    else:
        scheduler_report = solve_highs_halda_schedule(
            body=body,
            runtime=runtime,
            members=selected_members,
            base_plan=plan,
        )
        measured_reason = profiling.get("reason") if isinstance(profiling, dict) else None
        if (scheduler_report.fallback_reason == "missing_device_profiles"
                and scheduler_report.requested_strategy == "auto"
                and isinstance(measured_reason, str) and measured_reason):
            # The measured scorer declined for a concrete reason; never claim
            # profiles are missing when they merely do not cover this candidate.
            scheduler_report = scheduler_report.model_copy(update={
                "fallback_reason": measured_reason,
                "missing_profile_nodes": [],
            })
        profiling = {
            **(profiling if isinstance(profiling, dict) else {}),
            "scheduler": scheduler_report.model_dump(),
        }
    stat = model.stat()
    # This is an observation, not a claim that a large model has been digest-verified.
    identity = {
        "runtime_id": runtime["runtime_id"],
        "settings": body.model_dump(),
        "model": {"name": model.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "inventory_digest": inventory_identity(members, body.network_id),
        "placement": _stable_placement(plan),
    }
    errors = qualification_errors(runtime)
    placement_errors = []
    topology = None
    try:
        topology = topology_from_plan(plan, members, runtime["runtime_id"])
        topology = topology.model_copy(update={"scheduler_report": scheduler_report})
    except ValueError as error:
        placement_errors.append(str(error))
        errors = [*errors, str(error)]
    if topology is not None and (plan.get("split_mode") or "layer") == "layer":
        resolved = plan.get("resolved_launch_settings") or {}
        if not isinstance(resolved, dict):
            resolved = {}
        try:
            require_layer_execution(topology, set((runtime.get("capabilities") or {}).get("features", [])), {
                "prima_graphs": resolved.get("prima_graphs"),
                "prima_graph_counts": plan.get("prima_graph_counts"),
            })
        except ValueError as error:
            placement_errors.append(str(error))
            errors = [*errors, str(error)]
    if topology is not None:
        resolved = plan.get("resolved_launch_settings") or {}
        capabilities = runtime.get("capabilities") or {}
        kv_error = vulkan_kv_cache_error(
            topology.ranks,
            {node: info["backend"] for node, info in participant_platforms(topology, members, runtime=runtime).items()},
            resolved if isinstance(resolved, dict) else {},
            capabilities.get("features", []),
            capabilities=capabilities)
        if kv_error:
            errors = [*errors, kv_error]
    support = architecture_support(model_architecture(model), runtime.get("capabilities") or {})
    if support["error"]:
        errors = [*errors, support["error"]]
    warnings = [support["warning"]] if support["warning"] else []
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
        "participant_platforms": participant_platforms(topology, members, runtime=runtime) if topology else {},
        "candidate_count": len(feasible),
        "profiling": profiling,
        "scheduler": scheduler_report.model_dump(),
        "placement_mode": scheduler_report.scheduler_mode,
        "inference_supported": not errors,
        "placement_errors": placement_errors,
        "qualification_errors": errors,
        "qualification_warnings": warnings,
        "model_support": support,
    }


def candidate_profile_edges(manifest, feasible, *, segmented=False):
    """Approve additional candidate links only among this preparation's ranks."""
    prepared = {rank.node_id: rank.address for rank in manifest.ranks}
    execution = {(edge["source"], edge["target"]) for edge in manifest.transfer_edges}
    pairs = set()
    for plan, members in feasible:
        try:
            topology = topology_from_plan(plan, members, manifest.runtime_id)
            resolved = plan.get("resolved_launch_settings") or {}
            if not isinstance(resolved, dict):
                resolved = {}
            require_layer_execution(topology, {"segmented_placement"} if segmented else set(), {
                "prima_graphs": resolved.get("prima_graphs"),
                "prima_graph_counts": plan.get("prima_graph_counts"),
            })
        except ValueError:
            continue  # Unrepresentable candidates cannot supply measurement edges.
        if (topology.total_layers != manifest.total_layers
                or any(prepared.get(rank.node_id) != rank.address for rank in topology.ranks)):
            continue  # No asset or measurement authorization for an unprepared node.
        pairs.update((edge["source"], edge["target"]) for edge in network_edges(topology))
    return [ProfileEdge(source=source, target=target) for source, target in sorted(pairs - execution)]


def materialize(
    body, runtime, members, model, execute_plan, *, expected_digest: str, companions=None, adapters=None, score=None
):
    value = preview(body, runtime, members, model, execute_plan, score=score)
    if value["plan_digest"] != expected_digest:
        raise ValueError("Prima plan changed; refresh the plan before preparation")
    if value["placement_errors"]:
        raise ValueError("Prima placement is unsupported: " + "; ".join(value["placement_errors"]))
    from kllm.modules.primacpp.entities import PrimaTopology

    topology = PrimaTopology.model_validate(value["prima_topology"])
    files = asset_files(model, companions, adapters=adapters)
    backends = {}
    for rank in topology.ranks:
        member = members[rank.node_id]
        backend = prima_member_backend(member, runtime=runtime)
        if (rank.ngl or rank.output_gpu) and backend not in ("vulkan", "metal", "cuda", "rocm"):
            raise ValueError(
                f"{rank.node_id}: select an explicit GPU backend before preparing Prima"
            )
        backends[rank.node_id] = backend if rank.ngl or rank.output_gpu else "cpu"
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
                    )
                )
            ):
                return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
            layers = sum(r.end - r.start for r in rank.layers)
            if (len(topology.ranks) != 1 or value.total_layers != layers or value.gpu_layers != rank.ngl
                    or value.output_gpu != rank.output_gpu):
                return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
            score += value.decode_ms
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
