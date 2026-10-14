"""Compare feasible placements using complete typed and signed preparation evidence."""

from copy import deepcopy
import math
import time
from pathlib import Path
from statistics import median

from kllm.modules.models.interactors.gguf_meta import read_model_layout
from kllm.modules.primacpp.entities import DeviceProfile, StorageEvidence
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, authority_owner, inventory_identity, profile_pairs
from kllm.modules.primacpp.interactors.kernel_profile import kernel_hardware, kernel_settings
from kllm.modules.primacpp.interactors.launch import execution_settings
from kllm.modules.primacpp.interactors.participants import ParticipantEvidence, require_participant
from kllm.modules.primacpp.interactors.placement import topology_from_plan, layer_windows, require_layer_execution, network_edges
from kllm.modules.primacpp.interactors.segment_costs import segments, ring_messages
from kllm.modules.managed_network.interactors.verification import StreamScope
from kllm.modules.runtimes.entities import body_digest


def fallback(feasible, reason):
    return feasible[0], {"status": "unmeasured", "predicted_decode_ms": None, "reason": reason}


def score_candidates(feasible, body, runtime, model_sha256, layout, profiles, contexts, links, *, now):
    metadata = layout.get("metadata", {})
    embedding = metadata.get(str(layout.get("architecture")) + ".embedding_length")
    vocab = None
    for tensor in layout.get("tensors", []):
        if tensor.get("name") == "token_embd.weight":
            shape = tensor.get("dimensions", [])
            if len(shape) == 2 and shape[0] == embedding and type(shape[1]) is int and 0 < shape[1] <= 1048576:
                vocab = shape[1]
    if type(embedding) is not int or not 0 < embedding <= 1048576 or layout.get("confidence") != "metadata":
        return fallback(feasible, "Complete model embedding/tensor metadata is required")
    scores = []
    for plan, members in feasible:
        topology = topology_from_plan(plan, members, runtime["runtime_id"])
        if (any(rank.output_gpu and not rank.ngl for rank in topology.ranks)
                and "output_only_gpu_v1" not in runtime.get("capabilities", {}).get("features", [])):
            return fallback(feasible, "This runtime cannot execute output-only GPU placement")
        try:
            layer_windows(topology)
        except ValueError:
            segmented = True
        else:
            segmented = False
        settings = execution_settings(body, plan, Path(body.model), 0)
        try:
            require_layer_execution(topology, set(runtime.get("capabilities", {}).get("features", [])), settings)
        except ValueError as error:
            return fallback(feasible, str(error))
        visits = segments(topology) if segmented else []
        if (settings.get("cache_type_k") != "f16" or settings.get("cache_type_v") != "f16"
                or settings.get("flash_attention") != "off" or (settings.get("parallel_slots") or 1) != 1
                or settings.get("threads_batch") not in (None, settings["threads"])
                or settings.get("draft_model") or settings.get("vision") or settings.get("mmproj")
                or settings.get("extra_args") or settings.get("checkpoints", 0) or settings.get("cache_ram", 0)):
            return fallback(feasible, "Typed costs do not cover these effective execution settings")
        if topology.total_layers != len(layout.get("layers", [])):
            return fallback(feasible, "Candidate layer count differs from the measured model")
        compute = handoff = weight_read = 0.0
        for rank in topology.ranks:
            value = profiles.get(rank.node_id)
            context = contexts.get(rank.node_id)
            if value is None or context is None:
                return fallback(feasible, "Every candidate needs a complete current participant profile")
            value = DeviceProfile.model_validate(value)
            evidence = ParticipantEvidence.model_validate(context["participant_evidence"])
            devices = [d.model_dump() for d in evidence.devices]
            if value.backend != evidence.backend:
                return fallback(feasible, "Profile backend differs from the selected participant")
            if value.backend == "vulkan":
                if (len(devices) != 1 or value.device_identity != devices[0]["device_identity"]
                        or value.driver_identity != devices[0]["driver_identity"]):
                    return fallback(feasible, "Profile Vulkan identity differs from the selected physical device")
            elif value.device_identity or value.driver_identity:
                return fallback(feasible, "CPU profile claims a physical GPU")
            if (value.kernel_profile_version != 2 or value.runtime_id != runtime["runtime_id"]
                    or value.node_id != rank.node_id or value.model_sha256 != model_sha256
                    or value.total_layers != topology.total_layers or value.kernel_context != settings["context"]
                    or value.kernel_threads != settings["threads"] or not 0 <= now - value.measured_at <= 86400
                    or value.storage_identity != StorageEvidence.model_validate(context["storage_identity"])
                    or value.hardware_digest != body_digest(kernel_hardware(evidence.host.model_dump(), devices, value.backend))
                    or value.settings_digest != body_digest(kernel_settings(settings["context"], settings["threads"], devices))):
                return fallback(feasible, "Profile hardware, storage, source, model or effective settings changed")
            layers = [i for window in rank.layers for i in range(window.start, window.end)]
            cpu_count = len(layers) - rank.ngl
            compute += sum(value.cpu_layer_ms_by_index[i] for i in layers[:cpu_count])
            if rank.ngl or rank.output_gpu:
                backend = members[rank.node_id].get("actual_backend") or members[rank.node_id].get("backend")
                if (backend != "vulkan" or value.backend != "vulkan" or len(rank.devices) != 1
                        or len(devices) != 1 or rank.devices[0] not in (devices[0]["name"], devices[0]["device_identity"])
                        or value.gpu_transfer_bytes != 4 * embedding or value.gpu_upload_ms is None
                        or value.gpu_download_ms is None):
                    return fallback(feasible, "Exact selected Vulkan embedding handoff measurements are required")
                compute += sum(value.gpu_layer_ms_by_index[i] for i in layers[cpu_count:])
                gpu_indices = set(layers[cpu_count:])
                gpu_visits = (sum(owner == rank.node_id and any(start <= i < end for i in gpu_indices)
                                  for start, end, owner in visits) if segmented else int(rank.ngl > 0))
                handoff += value.gpu_upload_ms * gpu_visits
                if len(topology.ranks) > 1 or not rank.output_gpu:
                    handoff += value.gpu_download_ms * gpu_visits
            if rank.rank == 0:
                if rank.output_gpu:
                    if (vocab is None or value.gpu_logits_bytes != 4 * vocab
                            or value.gpu_logits_download_ms is None):
                        return fallback(feasible, "Exact model-width GPU logits downloads are required")
                    compute += value.input_ms + value.output_gpu_ms
                    handoff += value.gpu_logits_download_ms
                    if len(topology.ranks) > 1 or not rank.ngl:
                        # Distributed or CPU-transformer rank zero's output graph uploads the host
                        # embedding again after its separate transformer graph.
                        handoff += value.gpu_upload_ms
                else:
                    compute += value.input_ms + value.output_cpu_ms
            weights = sum(value.weights_bytes_by_index[i] for i in layers)
            if rank.rank == 0:
                weights += layout["input_bytes"] + layout["output_bytes"]
            weight_read += weights / value.disk_read_bytes_s * 1000
        network = 0.0
        pairs = {(e["source"], e["target"]) for e in network_edges(topology)}
        for source, target in pairs:
            receipt = links.get((source, target))
            durations = receipt.get("sample_seconds", []) if receipt else []
            if (receipt is None or not 0 <= now - receipt["received_at"] <= 300
                    or receipt.get("method") != "signed_private_tcp_application_v1"
                    or receipt.get("payload_bytes") != 1048576 or receipt.get("samples") != 3
                    or len(durations) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 10 for v in durations)
                    or type(receipt.get("latency_ms")) not in (int, float) or not math.isfinite(receipt["latency_ms"]) or receipt["latency_ms"] <= 0
                    or type(receipt.get("bandwidth_bytes_s")) not in (int, float)
                    or not math.isfinite(receipt["bandwidth_bytes_s"])
                    or not math.isclose(receipt["bandwidth_bytes_s"], 1048576 / median(durations), rel_tol=1e-9)
                    or receipt["source"] != members[source]["rpc_bind"]
                    or receipt["peer"] != members[target]["rpc_bind"]
                    or receipt["source_revision"] != members[source]["network_revision"]
                    or receipt["target_revision"] != members[target]["network_revision"]):
                return fallback(feasible, "Every candidate needs fresh exact directed private links")
        for source, target, size in ring_messages(topology, embedding) if segmented else []:
            receipt = links[(source, target)]
            network += receipt["latency_ms"] + size / receipt["bandwidth_bytes_s"] * 1000
        for rank in topology.ranks if len(topology.ranks) > 1 and not segmented else []:
            receipt = links[(rank.node_id, rank.next_node_id)]
            # Published native llama_send_tensors sends F32 embedding plus shape/
            # position fields (61 payload bytes). Metadata travels down the ring
            # until its last worker, with at most74 single-token payload bytes.
            messages = [4 * embedding + 61]
            if rank.rank != len(topology.ranks) - 1:
                messages.append(74)
            network += sum(receipt["latency_ms"] + size / receipt["bandwidth_bytes_s"] * 1000 for size in messages)
        decode = compute + handoff + network
        if not all(math.isfinite(v) and v >= 0 for v in (decode, weight_read)):
            return fallback(feasible, "Candidate estimate is unavailable")
        scores.append({"predicted_decode_ms": decode, "predicted_weight_read_ms": weight_read,
            "compute_ms": compute, "host_handoff_ms": handoff, "application_link_ms": network,
            "wire_cost_model": "segmented_ring_header_bound_v1" if segmented else "legacy_ring_v1"})
    chosen = min(range(len(scores)), key=lambda i: (scores[i]["predicted_decode_ms"], scores[i]["predicted_weight_read_ms"], i))
    return feasible[chosen], {"status": "profiled_estimate", "measurement_kind": "typed_roofline_estimate",
        "scope": "one-token f16/off resident layer execution, per-visit handoffs and signed application link proxy",
        "model_sha256": model_sha256, "candidate_estimates": scores, **scores[chosen]}


def measured_candidates(control, feasible, body, runtime, model, model_digest):
    if control.service.state().role != "master":
        return fallback(feasible, "Measured distributed planning requires current master authority")
    owner = authority_owner(control.service.channels.master_authority())
    now = time.time()
    with control.lock:
        jobs = [deepcopy(j) for j in control.jobs.values() if j["phase"] == "prepared"
            and j.get("expires_at", 0) > now and j.get("command_owner") == owner
            and j.get("request_settings_digest") == body_digest(body.model_dump()) and j.get("links")]
    try:
        layout = read_model_layout(model)
        if layout is None:
            return fallback(feasible, "Complete model layout is unavailable")
        for job in reversed(jobs):
            manifest = ExecutionManifest.model_validate(job["manifest"])
            if (manifest.runtime_id != runtime["runtime_id"] or manifest.network_id != body.network_id
                    or manifest.inventory_digest != inventory_identity(feasible[0][1], body.network_id)
                    or next(a.sha256 for a in manifest.assets if a.kind == "model") != model_digest):
                continue
            profiles, contexts = {}, {}
            for rank in manifest.ranks:
                result = job["nodes"][rank.node_id]["profile"]
                profiles[rank.node_id] = result["profile"]
                if rank.node_id == control.service.state().node_id:
                    context = control.profile_context(manifest, result["profile"], job["job_id"])
                else:
                    context = control._call(rank.node_id, job["job_id"], "prima_profile_context", {"profile_job_id": result["job_id"]})
                if context["manifest_digest"] != manifest.digest:
                    raise ValueError("Signed profile context changed its manifest")
                require_participant(manifest, rank.node_id, ParticipantEvidence.model_validate(context["participant_evidence"]))
                contexts[rank.node_id] = context
            links = {}
            for receipt in job["links"]:
                scope = StreamScope.model_validate(receipt["scope"])
                if (scope.contract_digest != manifest.digest or str(scope.operation_id) != job["job_id"]
                        or scope.digest != receipt["grant_id"] or authority_owner(scope.authority) != owner
                        or str(scope.network_id) != manifest.network_id
                        or (scope.source.node_id, scope.target.node_id) not in profile_pairs(manifest)
                        or scope.source.node_id != receipt["source_node_id"] or scope.target.node_id != receipt["target_node_id"]
                        or scope.source_revision != receipt["source_revision"] or scope.target_revision != receipt["target_revision"]):
                    raise ValueError("Private link scope changed")
                pair = (scope.source.node_id, scope.target.node_id)
                if pair in links:
                    raise ValueError("Private link receipt duplicated its directed edge")
                links[pair] = receipt
            if (control.jobs[job["job_id"]]["phase"] != "prepared"
                    or control.jobs[job["job_id"]].get("expires_at", 0) <= time.time()
                    or authority_owner(control.service.channels.master_authority()) != owner):
                raise ValueError("Preparation authority or approval changed during preview")
            return score_candidates(feasible, body, runtime, model_digest, layout, profiles, contexts, links, now=time.time())
    except (ValueError, KeyError, OSError, TypeError, RuntimeError):
        return fallback(feasible, "Current complete signed measurement context is unavailable")
    return fallback(feasible, "Prepare matching typed profiles and private links before measured planning")
