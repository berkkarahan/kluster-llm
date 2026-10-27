"""Capacity candidates and explicit, immutable Prima preparation previews."""

from __future__ import annotations

from copy import deepcopy
from itertools import combinations
from pathlib import Path
from typing import Any
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
    auto_deploy = getattr(body, "prima_auto_deploy", None)
    if auto_deploy is None:
        from kllm.shared import config as kllm_config
        auto_deploy = kllm_config.get_feature_flag("prima-auto-deploy")
    if auto_deploy:
        value = auto_plan(body, members, model=model, runtime=runtime)
    else:
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


def is_prima_auto_deploy_active(body: Any, runtime: dict[str, Any] | None = None) -> bool:
    from kllm.shared import config as kllm_config
    from kllm.modules.runtimes.interactors.selection import selected_runtime, snapshot

    explicit = getattr(body, "prima_auto_deploy", None)
    if explicit is False:
        return False
    if explicit is True:
        return True
    if not kllm_config.get_feature_flag("prima-auto-deploy"):
        return False
    if runtime is None:
        runtime = snapshot()
        if runtime is None:
            try:
                runtime = selected_runtime()
            except Exception:
                runtime = None
    return bool(runtime and runtime.get("engine") == "primacpp")


def auto_plan(
    body: Any,
    members: dict[str, Any],
    *,
    model: Path | str | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from kllm.modules.models.interactors.gguf_meta import read_model_layout
    from kllm.modules.primacpp.entities import DeviceProfile
    from kllm.modules.primacpp.interactors.highs_scheduler import solve_highs_halda_schedule
    from kllm.modules.primacpp.interactors.platforms import _vulkan_device_name, prima_members_view, prima_member_backend
    from kllm.modules.runtimes.interactors.selection import selected_runtime, snapshot
    from kllm.modules import models as model_ops
    import time

    # 0. Model path resolution
    if model is None:
        model = model_ops.resolve_local_model(body.model)
    elif not isinstance(model, Path):
        model = Path(model)

    # 1. Fast Refusal on corrupt / 0-byte models (FR-016)
    if not model.exists():
        raise HTTPException(
            status_code=400,
            detail={"code": "PRIMA_MODEL_CORRUPT", "message": f"Model file {model.name} does not exist"},
        )
    try:
        stat = model.stat()
    except OSError as err:
        raise HTTPException(
            status_code=400,
            detail={"code": "PRIMA_MODEL_CORRUPT", "message": str(err)},
        ) from None

    if stat.st_size <= 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "PRIMA_MODEL_CORRUPT", "message": f"Model file {model.name} is 0-byte or empty"},
        )

    try:
        layout = read_model_layout(model)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "PRIMA_MODEL_CORRUPT", "message": f"Corrupt GGUF layout in {model.name}: {exc}"},
        ) from None

    if layout is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "PRIMA_MODEL_CORRUPT", "message": f"Corrupt or non-GGUF model file {model.name}"},
        )

    # 2. Runtime & Architecture Support (FR-013)
    if runtime is None:
        runtime = snapshot()
        if runtime is None:
            try:
                runtime = selected_runtime()
            except Exception:
                runtime = {"runtime_id": "0" * 64, "engine": "primacpp", "capabilities": {}}

    capabilities = (runtime or {}).get("capabilities") or {}
    arch = (layout or {}).get("architecture") or model_architecture(model)
    support = architecture_support(arch, capabilities)
    if support.get("error"):
        raise HTTPException(
            status_code=400,
            detail={"code": "PRIMA_MODEL_UNSUPPORTED", "message": support["error"]},
        )

    # 3. Discover online cluster participants without manual layer/fraction limits (FR-006, FR-007)
    plan_members = (
        prima_members_view(members, runtime=runtime)
        if (runtime or {}).get("engine") == "primacpp"
        else members
    )
    if not plan_members:
        raise HTTPException(status_code=400, detail={"code": "NO_CLUSTER_MEMBERS", "message": "no cluster members"})

    total_layers = int(
        (layout or {}).get("n_layers")
        or len((layout or {}).get("layers") or [])
        or 32
    )
    total_layers = max(1, total_layers)

    model_size_bytes = stat.st_size
    layer_weights = list((layout or {}).get("layer_bytes") or [])
    if not layer_weights and (layout or {}).get("layers"):
        layer_weights = [
            int(item.get("bytes", 0))
            for item in ((layout or {}).get("layers") or [])
            if isinstance(item, dict) and item.get("bytes")
        ]
    if layer_weights:
        bytes_per_layer_weight = max(1, int(sum(layer_weights) // len(layer_weights)))
    else:
        bytes_per_layer_weight = max(1, model_size_bytes // total_layers)

    context = getattr(body, "context", 4096) or 4096
    bytes_per_layer_kv = max(1, int(context * 4 * 1024))
    io_bytes = int((layout or {}).get("input_bytes", 0) or 0) + int((layout or {}).get("output_bytes", 0) or 0)

    gpu_budgets: dict[str, int] = {}
    ram_budgets: dict[str, int] = {}
    profiles: dict[str, DeviceProfile] = {}

    now = time.time()
    for nid, m in plan_members.items():
        mb = prima_member_backend(m, runtime=runtime)
        vram = int(m.get("vram_bytes") or 0)
        has_gpu = mb not in ("cpu", "unknown") and (vram > 0 or bool(m.get("devices")))
        ram = int(m.get("available_ram_bytes") or m.get("total_ram_bytes") or 8 * 1024**3)
        gpu_budgets[nid] = vram if has_gpu else 0
        ram_budgets[nid] = ram
        profiles[nid] = DeviceProfile(
            runtime_id=(runtime or {}).get("runtime_id", "0" * 64),
            model_sha256="0" * 64,
            node_id=nid,
            hardware_digest="0" * 64,
            settings_digest="0" * 64,
            backend=mb if has_gpu else "cpu",
            measured_at=now,
            cpu_layer_ms=10.0,
            gpu_layer_ms=2.0 if has_gpu else None,
            disk_read_bytes_s=1_000_000_000.0,
            memory_available_bytes=vram if has_gpu else ram,
        )

    base_plan = {
        "estimated_layers": total_layers,
        "model_size_bytes": model_size_bytes,
        "weights_estimate_bytes": model_size_bytes,
        "kv_estimate_bytes": total_layers * bytes_per_layer_kv,
        "node_gpu_budget_bytes": gpu_budgets,
        "node_ram_budget_bytes": ram_budgets,
        "split_mode": "layer",
    }

    scheduler_report = solve_highs_halda_schedule(
        body=body,
        runtime=runtime or {},
        members=plan_members,
        base_plan=base_plan,
        layout=layout,
        profiles=profiles,
    )

    # 4. Handle Infeasible Capacity (FR-005)
    feasible = (
        scheduler_report.fallback_reason != "milp_infeasible_for_cluster_budget"
        and bool(scheduler_report.node_allocations)
        and sum(a.w_m for a in scheduler_report.node_allocations) == total_layers
    )
    if not feasible:
        total_required = model_size_bytes + total_layers * bytes_per_layer_kv + io_bytes
        total_available = sum(gpu_budgets.values()) + sum(ram_budgets.values())
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PRIMA_INSUFFICIENT_CAPACITY",
                "message": (
                    f"Cluster capacity is insufficient to host {model.name}. "
                    f"Required memory: {total_required} bytes, Available memory: {total_available} bytes."
                ),
                "required_bytes": total_required,
                "available_bytes": total_available,
                "required_breakdown": {
                    "weights_bytes": model_size_bytes,
                    "kv_bytes": total_layers * bytes_per_layer_kv,
                    "io_bytes": io_bytes,
                },
                "available_breakdown": {
                    "vram_bytes": sum(gpu_budgets.values()),
                    "ram_bytes": sum(ram_budgets.values()),
                },
            },
        )

    # 5. Build allocations and topology
    master_id = next(
        (str(nid) for nid, m in plan_members.items() if (m or {}).get("role") == "master" or (m or {}).get("is_master")),
        next(iter(plan_members.keys())),
    )

    allocs = scheduler_report.node_allocations
    master_alloc = next((a for a in allocs if a.node_id == master_id), None)
    worker_allocs = [a for a in allocs if a.node_id != master_id and a.w_m > 0]

    device_allocations = []
    plan_cpu_layers = 0

    if not worker_allocs:
        # Single-node deployment
        w = master_alloc.w_m if master_alloc else total_layers
        ngl = min(master_alloc.n_m if master_alloc else 0, w)
        cpu_l = w - ngl
        plan_cpu_layers = cpu_l
        m_devs = plan_members[master_id].get("devices") or []
        m_dev = str(m_devs[0].get("name") or m_devs[0].get("device") or "0") if m_devs else "0"
        if prima_member_backend(plan_members[master_id], runtime=runtime) == "vulkan":
            m_dev = _vulkan_device_name(m_dev, 0)
        if ngl > 0:
            device_allocations.append({
                "node_id": master_id,
                "device": m_dev,
                "layer_start": cpu_l,
                "layer_end": total_layers,
                "layers": ngl,
                "memory_kind": "gpu",
                "local": True,
                "backend": prima_member_backend(plan_members[master_id], runtime=runtime),
                "budget_bytes": gpu_budgets.get(master_id, 0),
                "estimated_bytes": ngl * bytes_per_layer_weight,
            })
    else:
        # Multi-node deployment
        cur_layer = 0
        for w_alloc in worker_allocs:
            w = w_alloc.w_m
            start = cur_layer
            end = cur_layer + w
            cur_layer = end
            ngl = min(w_alloc.n_m, w)
            cpu_l = w - ngl
            w_devs = plan_members[w_alloc.node_id].get("devices") or []
            w_dev = str(w_devs[0].get("name") or w_devs[0].get("device") or "0") if w_devs else "0"
            if prima_member_backend(plan_members[w_alloc.node_id], runtime=runtime) == "vulkan":
                w_dev = _vulkan_device_name(w_dev, 0)
            if cpu_l > 0:
                device_allocations.append({
                    "node_id": w_alloc.node_id,
                    "device": "cpu",
                    "layer_start": start,
                    "layer_end": end - ngl,
                    "layers": cpu_l,
                    "memory_kind": "ram",
                    "local": False,
                    "backend": "cpu",
                    "budget_bytes": ram_budgets.get(w_alloc.node_id, 0),
                    "estimated_bytes": cpu_l * bytes_per_layer_weight,
                })
            if ngl > 0:
                device_allocations.append({
                    "node_id": w_alloc.node_id,
                    "device": w_dev,
                    "layer_start": end - ngl,
                    "layer_end": end,
                    "layers": ngl,
                    "memory_kind": "gpu",
                    "local": False,
                    "backend": prima_member_backend(plan_members[w_alloc.node_id], runtime=runtime),
                    "budget_bytes": gpu_budgets.get(w_alloc.node_id, 0),
                    "estimated_bytes": ngl * bytes_per_layer_weight,
                })
        remaining_w = total_layers - cur_layer
        if remaining_w > 0 and master_alloc:
            ngl_m = min(master_alloc.n_m, remaining_w)
            cpu_m = remaining_w - ngl_m
            m_devs = plan_members[master_id].get("devices") or []
            m_dev = str(m_devs[0].get("name") or m_devs[0].get("device") or "0") if m_devs else "0"
            if prima_member_backend(plan_members[master_id], runtime=runtime) == "vulkan":
                m_dev = _vulkan_device_name(m_dev, 0)
            if cpu_m > 0:
                device_allocations.append({
                    "node_id": master_id,
                    "device": "cpu",
                    "layer_start": cur_layer,
                    "layer_end": total_layers - ngl_m,
                    "layers": cpu_m,
                    "memory_kind": "ram",
                    "local": True,
                    "backend": "cpu",
                    "budget_bytes": ram_budgets.get(master_id, 0),
                    "estimated_bytes": cpu_m * bytes_per_layer_weight,
                })
            if ngl_m > 0:
                device_allocations.append({
                    "node_id": master_id,
                    "device": m_dev,
                    "layer_start": total_layers - ngl_m,
                    "layer_end": total_layers,
                    "layers": ngl_m,
                    "memory_kind": "gpu",
                    "local": True,
                    "backend": prima_member_backend(plan_members[master_id], runtime=runtime),
                    "budget_bytes": gpu_budgets.get(master_id, 0),
                    "estimated_bytes": ngl_m * bytes_per_layer_weight,
                })

    plan = {
        "device_allocations": device_allocations,
        "cpu_layers": plan_cpu_layers,
        "ngl": sum(a["layers"] for a in device_allocations if a.get("memory_kind") == "gpu"),
        "estimated_layers": total_layers,
        "model_size_bytes": model_size_bytes,
        "split_mode": "layer",
        "fit_status": "fits",
        "node_gpu_budget_bytes": gpu_budgets,
        "node_ram_budget_bytes": ram_budgets,
        "resolved_launch_settings": {
            "context": context,
            "parallel_slots": getattr(body, "parallel_slots", 1) if getattr(body, "parallel_slots", 1) != "auto" else 1,
            "split_mode": "layer",
            "prima_graphs": True,
        },
    }

    topology = topology_from_plan(plan, plan_members, runtime["runtime_id"])
    topology = topology.model_copy(update={"scheduler_report": scheduler_report})

    identity = {
        "runtime_id": runtime["runtime_id"],
        "settings": body.model_dump(),
        "model": {"name": model.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        "inventory_digest": inventory_identity(members, getattr(body, "network_id", None)),
        "placement": _stable_placement(plan),
    }

    return {
        **plan,
        "engine": "primacpp",
        "runtime_id": runtime["runtime_id"],
        "transport": "prima-ring-tcp",
        "plan_digest": body_digest(identity),
        "inventory_digest": identity["inventory_digest"],
        "prima_topology": topology.model_dump(),
        "network_edges": network_edges(topology),
        "runtime_required_nodes": [r.node_id for r in topology.ranks],
        "participant_platforms": participant_platforms(topology, members, runtime=runtime),
        "candidate_count": 1,
        "profiling": {
            "status": "auto_planned",
            "predicted_decode_ms": scheduler_report.objective_step_ms,
            "scheduler": scheduler_report.model_dump(),
        },
        "scheduler": scheduler_report.model_dump(),
        "placement_mode": scheduler_report.scheduler_mode,
        "inference_supported": True,
        "placement_errors": [],
        "qualification_errors": [],
        "qualification_warnings": [],
        "model_support": support,
        "fit_status": "fits",
    }

