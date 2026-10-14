"""Use only cached generation probes that match every feasible local candidate."""

from functools import lru_cache
from pathlib import Path

from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices
from kllm.modules.primacpp.interactors.placement import topology_from_plan
from kllm.modules.primacpp.interactors.profiles import generation_settings, generation_hardware


@lru_cache(maxsize=16)
def _model_digest(path, size, modified, changed):
    model = Path(path)
    digest = file_digest(model)
    current = model.stat()
    if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (size, modified, changed):
        raise ValueError("Model changed while checking its measured profile")
    return digest


def measured_local_candidates(feasible, body, runtime, model, cache):
    from kllm.modules.primacpp.interactors.highs_scheduler import solve_highs_halda_schedule

    fallback = feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
    if getattr(body, "placement_strategy", "auto") in ("capacity_first", "priority"):
        return fallback
    candidates = []
    for plan, members in feasible:
        try:
            topology = topology_from_plan(plan, members, runtime["runtime_id"])
        except ValueError:
            return fallback
        settings = plan.get("resolved_launch_settings", {})
        threads = settings.get("threads", body.threads)
        threads_batch = settings.get("threads_batch", body.threads_batch)
        if (len(topology.ranks) != 1 or settings.get("cache_type_k") != "f16"
                or settings.get("cache_type_v") != "f16" or settings.get("flash_attention") != "off"
                or settings.get("parallel_slots") != 1 or settings.get("batch_size") != 512
                or settings.get("micro_batch") != 128 or settings.get("checkpoints", 0)
                or settings.get("cache_ram", 0) or body.draft_model or body.vision
                or body.extra_args or body.mmap_mlock not in ("auto", "mmap")
                or threads_batch not in (None, threads)):
            return fallback
        rank = topology.ranks[0]
        if not rank.ngl or (members[rank.node_id].get("actual_backend") or members[rank.node_id].get("backend")) != "vulkan":
            return fallback
        candidates.append((plan, members, topology, rank))
    if not cache.root.exists():
        return fallback
    try:
        inventory = discover_vulkan_devices(runtime)
        stat = model.stat()
        digest = _model_digest(str(model.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        scores = []
        candidate_profiles = []
        for plan, members, topology, rank in candidates:
            selected = select_vulkan_devices(inventory, rank.devices)
            if len(selected) != 1:
                return fallback
            context = plan.get("context_effective", body.context)
            threads = plan.get("resolved_launch_settings", {}).get("threads", body.threads)
            settings = generation_settings(context, threads, rank.ngl, selected, rank.output_gpu)
            profile = cache.get({"runtime_id": runtime["runtime_id"], "model_sha256": digest,
                "node_id": rank.node_id, "backend": "vulkan", "hardware_digest": body_digest(generation_hardware(members[rank.node_id], selected)),
                "settings_digest": body_digest(settings), "device_identity": selected[0]["device_identity"],
                "driver_identity": selected[0]["driver_identity"]})
            if (profile is None or profile.measurement_kind != "end_to_end"
                    or profile.total_layers != topology.total_layers or profile.gpu_layers != rank.ngl
                    or profile.output_gpu != rank.output_gpu
                    or profile.decode_ms is None or profile.prefill_ms is None or profile.peak_memory_bytes is None):
                return fallback
            scores.append(profile.decode_ms)
            candidate_profiles.append((rank.node_id, profile))
    except (OSError, ValueError):
        return fallback
    index = min(range(len(scores)), key=lambda i: (scores[i], i))
    chosen_node_id, chosen_profile = candidate_profiles[index]
    scheduler_report = solve_highs_halda_schedule(
        body=body,
        runtime=runtime,
        members=feasible[index][1],
        base_plan=feasible[index][0],
        profiles={chosen_node_id: chosen_profile},
    )
    return feasible[index], {"status": "profiled_model_probe", "predicted_decode_ms": scores[index],
        "model_sha256": digest, "measurement_kind": "end_to_end",
        "scope": "matching local full-model f16/one-slot generation probe",
        "scheduler": scheduler_report.model_dump()}


def measured_distributed_candidates(control, feasible, body, runtime, model):
    from kllm.modules.primacpp.interactors.distributed_planning import measured_candidates

    try:
        stat = model.stat()
        digest = _model_digest(str(model.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        return measured_candidates(control, feasible, body, runtime, model, digest)
    except (OSError, ValueError):
        return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
