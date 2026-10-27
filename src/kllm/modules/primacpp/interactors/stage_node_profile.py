"""Stage 3: Per-node hardware, kernel, and generation benchmarking."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from kllm.modules.primacpp.entities import ProfileRequest, TERMINAL
from kllm.modules.primacpp.interactors.execution import authority_owner


def execute_node_profile(
    coordinator: Any,
    run_id: str,
    manifest: Any,
    include_profile: bool,
    leases: Any,
    check: Any,
    *,
    step_total: int = 2,
) -> None:
    """Execute Stage 3: Benchmark hardware/kernel per node, preserving already-measured profiles."""
    control = coordinator.control
    store = coordinator.store

    if not include_profile:
        store.transition_stage(
            run_id,
            "node_profile",
            "skipped",
            {"fraction": 1.0, "label": "Hardware profiling skipped"},
        )
        store.append_log(
            run_id, "INFO", "kernel", "Node profiling skipped because include_profile=False", stage="node_profile"
        )
        return

    check()
    store.transition_stage(
        run_id,
        "node_profile",
        "running",
        {"fraction": 0.50, "label": "Starting node hardware profiling…"},
    )
    store.append_log(
        run_id,
        "INFO",
        "kernel",
        f"Starting hardware profiling for {len(manifest.ranks)} nodes",
        stage="node_profile",
    )

    existing_profiles = store.get_node_profiles(run_id)
    profile_span = 0.35 if len(manifest.ranks) > 1 else 0.49
    rank_count = max(1, len(manifest.ranks))
    runtime = control.registry.capture(manifest.runtime_id)

    for rank_idx, rank in enumerate(manifest.ranks, start=1):
        check()
        node_id = rank.node_id

        # Preserved stage behavior: if already measured in SQLite, skip repeating benchmark!
        if node_id in existing_profiles:
            store.append_log(
                run_id,
                "INFO",
                "kernel",
                f"Reusing already measured profile for node {node_id}",
                stage="node_profile",
                node=node_id,
            )
            continue

        rank_base = 0.50 + ((rank_idx - 1) / rank_count) * profile_span
        rank_width = profile_span / rank_count
        control._update_job_progress(
            run_id,
            phase="profiling",
            label=f"Profiling hardware on {node_id} ({rank_idx}/{rank_count})…",
            step_index=2,
            step_total=step_total,
            fraction=rank_base,
            scope=(node_id,),
        )

        request = ProfileRequest(
            runtime_id=manifest.runtime_id,
            model=next(a.sha256 for a in manifest.assets if a.kind == "model"),
            context=manifest.settings["context"],
            threads=manifest.settings["threads"],
            backend=manifest.backends[node_id],
            devices=rank.devices
            if manifest.backends[node_id] == "vulkan" and (rank.ngl or rank.output_gpu)
            else None,
            mode="generation"
            if len(manifest.ranks) == 1
            and rank.ngl
            and manifest.backends[node_id] == "vulkan"
            and "gpu_execution_evidence_v1" in runtime.get("capabilities", {}).get("features", [])
            else "kernel",
            gpu_layers=rank.ngl
            if len(manifest.ranks) == 1
            and rank.ngl
            and manifest.backends[node_id] == "vulkan"
            and "gpu_execution_evidence_v1" in runtime.get("capabilities", {}).get("features", [])
            else None,
            request_id=str(uuid4()),
            output_gpu=rank.output_gpu,
        )
        if request.mode == "generation":
            request = ProfileRequest.model_validate(
                {
                    **request.model_dump(),
                    **{
                        key: manifest.settings.get(key, default)
                        for key, default in (
                            ("cache_type_k", "f16"),
                            ("cache_type_v", "f16"),
                            ("flash_attention", "off"),
                        )
                    },
                }
            )

        if node_id == control.service.state().node_id:
            scope = {
                **authority_owner(control.service.channels.master_authority()),
                "sender": node_id,
                "operation_id": run_id,
                "preparation_job_id": run_id,
                "manifest_digest": manifest.digest,
                "runtime_id": manifest.runtime_id,
                "model_sha256": request.model,
                "node_id": node_id,
                "backend": request.backend,
                "context": request.context,
                "threads": request.threads,
                **({"total_layers": manifest.total_layers} if request.mode == "generation" else {}),
            }
            profile = control.profiles.start(request, control_owner=scope, check=lambda: check())
            control.jobs[run_id]["nodes"][node_id]["profile"] = profile
            while profile["phase"] not in TERMINAL:
                if profile["phase"] == "cleanup_pending":
                    raise ValueError("Owned Prima profile cleanup is pending")
                check()
                if profile.get("pid") and profile.get("process_created_at"):
                    leases.advance()
                prof_prog = profile.get("progress") or {}
                sub_frac = float(prof_prog.get("fraction") or 0.1)
                sub_label = prof_prog.get("label") or f"Profiling {node_id}…"
                control._update_job_progress(
                    run_id,
                    phase="profiling",
                    label=f"{node_id}: {sub_label}",
                    step_index=2,
                    step_total=step_total,
                    fraction=rank_base + sub_frac * rank_width,
                    bytes_done=int(prof_prog.get("bytes_done") or 0),
                    bytes_total=int(prof_prog.get("bytes_total") or 0),
                    stage=prof_prog.get("stage", "profiling"),
                    scope=(node_id,),
                )
                time.sleep(0.2)
                profile = control.profiles.job(profile["job_id"])
                control.jobs[run_id]["nodes"][node_id]["profile"] = profile
        else:
            remote_preparation = control.jobs[run_id]["nodes"][node_id]["job_id"]
            scope = {
                **control.jobs[run_id].get("command_owner", {}),
                "sender": manifest.ranks[0].node_id,
                "operation_id": run_id,
                "preparation_job_id": remote_preparation,
                "manifest_digest": manifest.digest,
                "runtime_id": manifest.runtime_id,
                "model_sha256": request.model,
                "node_id": node_id,
                "backend": request.backend,
                "context": request.context,
                "threads": request.threads,
            }
            profile = control._call(
                node_id,
                run_id,
                "prima_profile",
                {
                    "preparation_job_id": remote_preparation,
                    "manifest_digest": manifest.digest,
                    "profile": request.model_dump(exclude_none=True),
                },
            )
            control.jobs[run_id]["nodes"][node_id]["profile"] = profile
            while profile["phase"] not in TERMINAL:
                if profile["phase"] == "cleanup_pending":
                    raise ValueError("Owned Prima profile cleanup is pending")
                check()
                if profile.get("pid") and profile.get("process_created_at"):
                    leases.advance()
                prof_prog = profile.get("progress") or {}
                sub_frac = float(prof_prog.get("fraction") or 0.1)
                sub_label = prof_prog.get("label") or f"Profiling {node_id}…"
                control._update_job_progress(
                    run_id,
                    phase="profiling",
                    label=f"{node_id}: {sub_label}",
                    step_index=2,
                    step_total=step_total,
                    fraction=rank_base + sub_frac * rank_width,
                    bytes_done=int(prof_prog.get("bytes_done") or 0),
                    bytes_total=int(prof_prog.get("bytes_total") or 0),
                    stage=prof_prog.get("stage", "profiling"),
                    scope=(node_id,),
                )
                time.sleep(0.2)
                profile = control._call(
                    node_id,
                    run_id,
                    "prima_status",
                    {"profile_job_id": profile["job_id"]},
                )
                control.jobs[run_id]["nodes"][node_id]["profile"] = profile

        control.jobs[run_id]["nodes"][node_id]["profile"] = profile
        if profile["phase"] != "prepared":
            raise ValueError(profile.get("error") or "Rank profiling failed")

        if len(manifest.ranks) > 1:
            control._retain_profile(profile, manifest, rank, request, run_id, scope)

        # Authoritatively persist measured profile to SQLite store
        measured_profile_dict = profile.get("profile") or profile
        store.record_node_profile(run_id, node_id, measured_profile_dict)
        store.update_node_task(
            run_id,
            "node_profile",
            node_id,
            "completed",
            {"fraction": 1.0, "bytes_done": 1, "bytes_total": 1},
        )
        store.append_log(
            run_id,
            "INFO",
            "kernel",
            f"Measured hardware profile on {node_id}: backend={measured_profile_dict.get('backend')}, cpu_layer_ms={measured_profile_dict.get('cpu_layer_ms')}",
            stage="node_profile",
            node=node_id,
        )

    store.transition_stage(
        run_id,
        "node_profile",
        "completed",
        {"fraction": 1.0, "label": "Hardware profiling complete"},
    )
    store.append_log(
        run_id,
        "INFO",
        "kernel",
        f"Node hardware profiling complete across all {len(manifest.ranks)} ranks",
        stage="node_profile",
    )
