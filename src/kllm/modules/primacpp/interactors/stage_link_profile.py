"""Stage 4: Pairwise Ring-TCP private link profiling across ranks."""

from __future__ import annotations

from typing import Any

from kllm.modules.primacpp.interactors.execution import profile_pairs
from kllm.modules.primacpp.interactors.link_profile import collect_links
from kllm.modules.primacpp.interactors.preparation_metrics import measure


def execute_link_profile(
    coordinator: Any,
    run_id: str,
    manifest: Any,
    include_profile: bool,
    leases: Any,
    check: Any,
    *,
    step_total: int = 3,
) -> None:
    """Execute Stage 4: Measure pairwise Ring-TCP link latency and bandwidth."""
    control = coordinator.control
    store = coordinator.store

    if not include_profile or len(manifest.ranks) <= 1:
        store.transition_stage(
            run_id,
            "link_profile",
            "skipped",
            {"fraction": 1.0, "label": "Link profiling skipped (single node or profiling disabled)"},
        )
        store.append_log(
            run_id,
            "INFO",
            "network",
            "Link profiling skipped (single node or include_profile=False)",
            stage="link_profile",
        )
        return

    check()
    store.transition_stage(
        run_id,
        "link_profile",
        "running",
        {"fraction": 0.85, "label": "Measuring Ring-TCP private links…"},
    )
    store.append_log(
        run_id,
        "INFO",
        "network",
        f"Starting link profiling for {len(manifest.ranks)} ranks",
        stage="link_profile",
    )

    control._update_job_progress(
        run_id,
        phase="links",
        label="Measuring Ring-TCP private links…",
        step_index=3,
        step_total=step_total,
        fraction=0.85,
    )

    # Check already measured links in SQLite
    existing_links = {
        (link_rec["source_node_id"], link_rec["target_node_id"]): link_rec
        for link_rec in store.get_link_profiles(run_id)
    }
    required_pairs = set(profile_pairs(manifest))

    if required_pairs and required_pairs.issubset(existing_links.keys()):
        store.append_log(
            run_id,
            "INFO",
            "network",
            f"All {len(required_pairs)} required links already measured in database; preserving existing measurements",
            stage="link_profile",
        )
        control.jobs[run_id]["links"] = [existing_links[p] for p in sorted(required_pairs)]
    else:
        with measure("private_link_profile"):
            measured = collect_links(control, manifest, run_id, check, leases.advance)
            control.jobs[run_id]["links"] = measured

            # Persist each measured edge into SQLite store
            for m in measured:
                source = m.get("source_node_id") or m.get("source")
                target = m.get("target_node_id") or m.get("peer")
                store.record_link_profile(run_id, source, target, m)
                store.append_log(
                    run_id,
                    "INFO",
                    "network",
                    f"Measured link {source} -> {target}: latency={m.get('latency_ms')}ms, bandwidth={m.get('bandwidth_bytes_s')} B/s",
                    stage="link_profile",
                )

    store.transition_stage(
        run_id,
        "link_profile",
        "completed",
        {"fraction": 1.0, "label": "Link profiling complete"},
    )
    store.append_log(
        run_id,
        "INFO",
        "network",
        f"Pairwise link profiling complete for {len(required_pairs)} directed edges",
        stage="link_profile",
    )
