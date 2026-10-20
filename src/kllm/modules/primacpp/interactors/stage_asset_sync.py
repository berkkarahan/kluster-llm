"""Stage 2: Resumable chunked model asset staging and worker transfers."""

from __future__ import annotations

from typing import Any

from kllm.modules.primacpp.interactors.preparation_metrics import measure


def execute_asset_sync(
    coordinator: Any,
    run_id: str,
    manifest: Any,
    files: list[tuple[Any, Any]],
    leases: Any,
    check: Any,
    *,
    step_total: int = 1,
    stage_span: float = 0.96,
) -> None:
    """Execute Stage 2: Staging model and companion assets with chunk-level resumption."""
    control = coordinator.control
    store = coordinator.store

    check()
    node_asset_bytes = sum(asset.size for asset, _ in files)
    total_stage_bytes = node_asset_bytes * max(1, len(manifest.ranks))

    store.transition_stage(
        run_id,
        "asset_sync",
        "running",
        {
            "fraction": 0.05,
            "bytes_done": 0,
            "bytes_total": total_stage_bytes,
            "label": f"Staging model assets across {len(manifest.ranks)} node{'s' if len(manifest.ranks) != 1 else ''}…",
        },
    )
    store.append_log(
        run_id,
        "INFO",
        "asset",
        f"Starting asset sync across {len(manifest.ranks)} nodes ({total_stage_bytes} total bytes)",
        stage="asset_sync",
    )

    with control.lock:
        check()
        control.jobs[run_id].update(
            phase="preparing", manifest=manifest.model_dump(), manifest_digest=manifest.digest
        )

    control._update_job_progress(
        run_id,
        phase="staging",
        label=f"Staging model assets across {len(manifest.ranks)} node{'s' if len(manifest.ranks) != 1 else ''}…",
        step_index=1,
        step_total=step_total,
        fraction=0.05,
        bytes_done=0,
        bytes_total=total_stage_bytes,
    )

    # Check for already completed node tasks from previous attempts
    completed_tasks = {
        t["node_id"]: t for t in store.get_node_tasks(run_id, "asset_sync") if t["status"] == "completed"
    }

    completed_nodes_bytes = 0
    members = control.members(getattr(manifest, "settings", {}))

    for rank_idx, rank in enumerate(manifest.ranks, start=1):
        check()
        node_id = rank.node_id

        # If already completed in previous run attempt and verified, skip!
        if node_id in completed_tasks and completed_tasks[node_id].get("bytes_done") == node_asset_bytes:
            store.append_log(
                run_id,
                "INFO",
                "asset",
                f"Reusing already completed asset staging on {node_id} ({node_asset_bytes} bytes)",
                stage="asset_sync",
                node=node_id,
            )
            completed_nodes_bytes += node_asset_bytes
            continue

        store.update_node_task(
            run_id,
            "asset_sync",
            node_id,
            "running",
            {"fraction": 0.0, "bytes_done": 0, "bytes_total": node_asset_bytes},
        )

        if node_id == control.service.state().node_id:
            control.jobs[run_id]["nodes"][node_id] = {
                "phase": "preparing",
                "transferred_bytes": 0,
                "asset_size": node_asset_bytes,
            }
            prior_asset_bytes = 0
            for asset, path in files:
                logical_done = 0
                copy_start = 0

                def _on_local_stage(
                    stage,
                    done: int,
                    size: int,
                    _node=node_id,
                    _prior=prior_asset_bytes,
                    _name=asset.name,
                ):
                    nonlocal logical_done, copy_start
                    if stage == "resume":
                        copy_start = done
                    if stage in ("resume", "local_copy", "cache_hit", "publication"):
                        logical_done = done
                    node_done = _prior + logical_done
                    agg_done = completed_nodes_bytes + node_done
                    frac = 0.05 + (agg_done / max(1, total_stage_bytes)) * stage_span
                    with control.lock:
                        check()
                        control.jobs[run_id]["nodes"][_node].update(
                            phase="preparing",
                            transferred_bytes=node_done,
                            asset_size=node_asset_bytes,
                        )
                    control._update_job_progress(
                        run_id,
                        phase="staging",
                        label=lambda: f"{stage.replace('_', ' ').capitalize()}: {_name} on {_node} ({rank_idx}/{len(manifest.ranks)})…",
                        step_index=1,
                        step_total=step_total,
                        fraction=frac,
                        bytes_done=agg_done,
                        bytes_total=total_stage_bytes,
                        stage=stage,
                        scope=(_node, _name),
                        rate_done=done - copy_start if stage == "local_copy" else done,
                        rate_total=size - copy_start if stage == "local_copy" else size,
                        rate_kind="local_copy"
                        if stage == "local_copy"
                        else "hash"
                        if stage in ("cache_verify", "final_verify")
                        else None,
                        force=stage in ("publication", "cache_hit"),
                    )

                control.receiver.stager.prepare(
                    asset.sha256,
                    asset.size,
                    lambda offset, p=path: control._file_chunks(
                        p, offset, control.jobs[run_id]["local_chunk_bytes"], check
                    ),
                    check=lambda: check(),
                    stage_progress=_on_local_stage,
                )
                prior_asset_bytes += asset.size

            result = {
                "phase": "prepared",
                "transferred_bytes": node_asset_bytes,
                "asset_size": node_asset_bytes,
            }
            store.update_node_task(
                run_id,
                "asset_sync",
                node_id,
                "completed",
                {"fraction": 1.0, "bytes_done": node_asset_bytes, "bytes_total": node_asset_bytes},
            )
            store.update_node(run_id, node_id, status="prepared")
        else:
            if (
                members.get(node_id, {}).get("prima_control_version") != 1
                or type(members.get(node_id, {}).get("prima_asset_lease_version")) is not int
                or members.get(node_id, {})["prima_asset_lease_version"] != 1
            ):
                raise ValueError(f"{node_id}: upgrade the worker agent for Prima preparation")

            result = control._call(
                node_id,
                run_id,
                "prima_prepare",
                {
                    "runtime_id": manifest.runtime_id,
                    "assets": [a.model_dump() for a, _ in files],
                    "operation_id": run_id,
                },
            )
            remote_job = result["job_id"]
            leases.enroll(node_id, remote_job)
            control.jobs[run_id]["nodes"][node_id] = result
            store.update_node(run_id, node_id, status="preparing", remote_job_id=remote_job)

            offsets = dict(result.get("offsets") or {})
            prior_asset_bytes = 0
            for asset, path in files:
                offset = offsets.get(asset.sha256, 0)
                if type(offset) is not int or not 0 <= offset <= asset.size:
                    raise ValueError("Worker prepared offset is outside the declared asset")
                transfer_start = offset
                with measure("remote_resume", node=node_id, asset=asset.sha256) as counts:
                    counts["resume_bytes" if offset < asset.size else "cache_hit_bytes"] = offset
                if offset < asset.size:
                    control._update_job_progress(
                        run_id,
                        phase="staging",
                        stage="grant_hash",
                        scope=(node_id, asset.sha256),
                        label=f"Verifying transfer source: {asset.name}…",
                        bytes_done=completed_nodes_bytes + prior_asset_bytes + offset,
                        bytes_total=total_stage_bytes,
                        step_total=step_total,
                        fraction=0.05
                        + (completed_nodes_bytes + prior_asset_bytes + offset)
                        / max(1, total_stage_bytes)
                        * stage_span,
                    )
                    control.grants.grant(
                        node_id,
                        run_id,
                        asset,
                        path,
                        progress=leases.advance,
                        check=check,
                    )
                control._update_job_progress(
                    run_id,
                    phase="staging",
                    stage="remote_payload" if offset < asset.size else "remote_cache_hit",
                    scope=(node_id, asset.sha256),
                    label=f"Transferring {asset.name} to {node_id}…"
                    if offset < asset.size
                    else f"Reusing verified {asset.name} on {node_id}",
                    bytes_done=completed_nodes_bytes + prior_asset_bytes + offset,
                    bytes_total=total_stage_bytes,
                    fraction=0.05
                    + (completed_nodes_bytes + prior_asset_bytes + offset)
                    / max(1, total_stage_bytes)
                    * stage_span,
                    step_total=step_total,
                    rate_done=0,
                    rate_total=asset.size - transfer_start,
                    rate_kind="acknowledged_payload" if offset < asset.size else None,
                )
                while offset < asset.size:
                    check()
                    chunk = control.grants.chunk(node_id, run_id, asset.sha256, offset)
                    reply = control._call(
                        node_id,
                        run_id,
                        "prima_asset_chunk",
                        {"job_id": remote_job, "chunk": chunk},
                    )
                    acknowledged = reply.get("offset")
                    with measure("remote_acknowledged", node=node_id, asset=asset.sha256, sample=64) as counts:
                        control.grants.acknowledge(node_id, run_id, asset.sha256, acknowledged)
                        counts["acknowledged_bytes"] = acknowledged - offset
                    leases.advance(acknowledged - offset)
                    offset = acknowledged
                    offsets[asset.sha256] = offset
                    node_done = prior_asset_bytes + offset
                    agg_done = completed_nodes_bytes + node_done
                    frac = 0.05 + (agg_done / max(1, total_stage_bytes)) * stage_span
                    check()
                    with control.lock:
                        check()
                        control.jobs[run_id]["nodes"][node_id].update(
                            phase="preparing",
                            transferred_bytes=offset,
                            asset_size=asset.size,
                        )
                    control._update_job_progress(
                        run_id,
                        phase="staging",
                        label=lambda: f"Transferring {asset.name} to {node_id} ({rank_idx}/{len(manifest.ranks)})…",
                        step_index=1,
                        step_total=step_total,
                        fraction=frac,
                        bytes_done=agg_done,
                        bytes_total=total_stage_bytes,
                        stage="remote_payload",
                        scope=(node_id, asset.sha256),
                        rate_done=offset - transfer_start,
                        rate_total=asset.size - transfer_start,
                        rate_kind="acknowledged_payload",
                        force=offset == asset.size,
                    )
                    if offset % (24 * 1024 * 256) == 0 or offset == asset.size:
                        store.update_node_task(
                            run_id,
                            "asset_sync",
                            node_id,
                            "running",
                            {
                                "fraction": node_done / max(1, node_asset_bytes),
                                "bytes_done": node_done,
                                "bytes_total": node_asset_bytes,
                            },
                        )
                prior_asset_bytes += asset.size

            result = control._call(node_id, run_id, "prima_status", {"job_id": remote_job})
            if result.get("phase") != "prepared":
                raise ValueError(result.get("error") or "Worker preparation failed")

            store.update_node_task(
                run_id,
                "asset_sync",
                node_id,
                "completed",
                {"fraction": 1.0, "bytes_done": node_asset_bytes, "bytes_total": node_asset_bytes},
            )
            store.update_node(run_id, node_id, status="prepared")

        completed_nodes_bytes += node_asset_bytes
        control.jobs[run_id]["nodes"][node_id] = result
        leases.advance()

    store.transition_stage(
        run_id,
        "asset_sync",
        "completed",
        {
            "fraction": 1.0,
            "bytes_done": total_stage_bytes,
            "bytes_total": total_stage_bytes,
            "label": "Model assets staged",
        },
    )
    store.append_log(
        run_id,
        "INFO",
        "asset",
        f"Asset sync complete across all {len(manifest.ranks)} nodes ({total_stage_bytes} bytes)",
        stage="asset_sync",
    )
