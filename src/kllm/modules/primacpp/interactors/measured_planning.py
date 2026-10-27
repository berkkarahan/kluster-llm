"""Use only cached generation probes that match every feasible local candidate."""

from functools import lru_cache
from pathlib import Path

from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices
from kllm.modules.primacpp.interactors.placement import topology_from_plan
from kllm.modules.primacpp.interactors.platforms import prima_member_backend
from kllm.modules.primacpp.interactors.profiles import generation_settings, generation_hardware


@lru_cache(maxsize=16)
def _model_digest(path, size, modified, changed):
    model = Path(path)
    digest = file_digest(model)
    current = model.stat()
    if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (size, modified, changed):
        raise ValueError("Model changed while checking its measured profile")
    return digest


def _unmeasured(feasible, reason):
    return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None, "reason": reason}


def _settings_decline(settings, body, threads, threads_batch):
    """Effective settings typed end-to-end costs do not cover, or None."""
    unsupported = []
    if settings.get("cache_type_k") not in ("f16", "q8_0") or settings.get("cache_type_v") not in ("f16", "q8_0"):
        unsupported.append("f16 or q8_0 K/V cache")
    if settings.get("flash_attention") not in ("on", "off"):
        unsupported.append("explicit flash attention on/off")
    if settings.get("parallel_slots") != 1:
        unsupported.append("one parallel slot")
    if settings.get("batch_size") != 512 or settings.get("micro_batch") != 128:
        unsupported.append("batch 512 / micro-batch 128")
    if settings.get("checkpoints", 0) or settings.get("cache_ram", 0):
        unsupported.append("no context checkpoints or prompt cache")
    if body.draft_model or body.vision or body.extra_args:
        unsupported.append("no draft model, vision or extra arguments")
    if body.mmap_mlock not in ("auto", "mmap"):
        unsupported.append("mmap load mode")
    if threads_batch not in (None, threads):
        unsupported.append("matching batch threads")
    if not unsupported:
        return None
    return "Profiled costs do not cover these effective execution settings: " + ", ".join(unsupported)


def measured_local_candidates(feasible, body, runtime, model, cache):
    from kllm.modules.primacpp.interactors.highs_scheduler import solve_highs_halda_schedule

    strategy = getattr(body, "placement_strategy", "auto")
    if strategy in ("capacity_first", "priority"):
        return _unmeasured(feasible, f"Operator selected {strategy} placement strategy")
    candidates = []
    for plan, members in feasible:
        try:
            topology = topology_from_plan(plan, members, runtime["runtime_id"])
        except ValueError:
            return _unmeasured(feasible, "Candidate placement cannot be represented for profiling")
        settings = plan.get("resolved_launch_settings", {})
        threads = settings.get("threads", body.threads)
        threads_batch = settings.get("threads_batch", body.threads_batch)
        if len(topology.ranks) != 1:
            return _unmeasured(feasible, "Profiled planning covers only single-node placement")
        decline = _settings_decline(settings, body, threads, threads_batch)
        if decline:
            return _unmeasured(feasible, decline)
        rank = topology.ranks[0]
        if not rank.ngl or prima_member_backend(members[rank.node_id], runtime=runtime) != "vulkan":
            return _unmeasured(feasible, "Profiled planning requires a Vulkan GPU rank")
        candidates.append((plan, members, topology, rank))
    if not cache.root.exists():
        return _unmeasured(feasible, "No profile cache exists; prepare and profile this model in Prima Preparation")
    try:
        inventory = discover_vulkan_devices(runtime)
        stat = model.stat()
        digest = _model_digest(str(model.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        scores = []
        candidate_profiles = []
        for plan, members, topology, rank in candidates:
            selected = select_vulkan_devices(inventory, rank.devices)
            if len(selected) != 1:
                return _unmeasured(feasible, "Profiled planning requires exactly one selected Vulkan device")
            context = plan.get("context_effective", body.context)
            threads = plan.get("resolved_launch_settings", {}).get("threads", body.threads)
            launch_settings = plan.get("resolved_launch_settings", {})
            settings = generation_settings(context, threads, rank.ngl, selected, rank.output_gpu,
                **{key: launch_settings.get(key) for key in ("cache_type_k", "cache_type_v", "flash_attention")})
            profile = cache.get({"runtime_id": runtime["runtime_id"], "model_sha256": digest,
                "node_id": rank.node_id, "backend": "vulkan", "hardware_digest": body_digest(generation_hardware(members[rank.node_id], selected)),
                "settings_digest": body_digest(settings), "device_identity": selected[0]["device_identity"],
                "driver_identity": selected[0]["driver_identity"]})
            if (profile is None or profile.measurement_kind != "end_to_end"
                    or profile.total_layers != topology.total_layers or profile.gpu_layers != rank.ngl
                    or profile.output_gpu != rank.output_gpu
                    or profile.decode_ms is None or profile.prefill_ms is None or profile.peak_memory_bytes is None):
                return _unmeasured(feasible, "No cached end-to-end profile matches this model, node and settings; profile this configuration")
            scores.append(profile.decode_ms)
            candidate_profiles.append((rank.node_id, profile))
    except (OSError, ValueError):
        return _unmeasured(feasible, "Profile cache or model identity changed while checking measured costs")
    index = min(range(len(scores)), key=lambda i: (scores[i], i))
    chosen_node_id, chosen_profile = candidate_profiles[index]
    scheduler_report = solve_highs_halda_schedule(
        body=body,
        runtime=runtime,
        members={chosen_node_id: feasible[index][1][chosen_node_id]},
        base_plan=feasible[index][0],
        profiles={chosen_node_id: chosen_profile},
    )
    return feasible[index], {"status": "profiled_model_probe", "predicted_decode_ms": scores[index],
        "model_sha256": digest, "measurement_kind": "end_to_end",
        "scope": "matching local full-model cache/attention/one-slot generation probe",
        "scheduler": scheduler_report.model_dump()}


def measured_distributed_candidates(control, feasible, body, runtime, model):
    from kllm.modules.primacpp.interactors.distributed_planning import measured_candidates

    try:
        stat = model.stat()
        digest = _model_digest(str(model.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        return measured_candidates(control, feasible, body, runtime, model, digest)
    except (OSError, ValueError):
        return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None}
