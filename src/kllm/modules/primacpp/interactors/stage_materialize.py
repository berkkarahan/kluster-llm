"""Stage 1: Materialize placement manifest, companions, adapters, and asset batch."""

from __future__ import annotations

import json
from typing import Any

from kllm.modules.primacpp.interactors.participants import ParticipantEvidence, require_shared_identity
from kllm.modules.primacpp.interactors.adapters import adapter_paths
from kllm.modules.primacpp.interactors.planner import candidate_profile_edges, candidates, materialize
from kllm.modules.primacpp.interactors.execution import (
    Asset,
    ExecutionManifest,
    authority_owner,
    require_execution_capabilities,
    validate_asset_batch,
)
from kllm.modules.primacpp.interactors.launch import execution_settings
from kllm.shared.runtime_resources import prima_scope


def execute_materialize(
    coordinator: Any,
    run_id: str,
    runtime_id: str,
    body: Any,
    expected_digest: str,
    include_profile: bool,
    check: Any,
) -> tuple[ExecutionManifest, list[tuple[Asset, Any]]]:
    """Execute Stage 1: Planning, model layout & execution manifest creation."""
    control = coordinator.control
    store = coordinator.store

    check()
    store.transition_stage(
        run_id,
        "materialize",
        "running",
        {"fraction": 0.04, "label": "Materializing placement manifest & verifying assets…"},
    )
    store.append_log(
        run_id, "INFO", "lifecycle", "Materializing placement manifest and verifying assets", stage="materialize"
    )

    runtime = control.registry.capture(runtime_id)
    companions = {
        kind: control.resolve_model(value)
        for kind, value in [
            ("draft", body.draft_model if body.draft_enabled() else None),
            ("mmproj", body.mmproj),
        ]
        if value
    }
    adapters = adapter_paths(body.model_dump())
    members = control.members(body)
    model = control.resolve_model(body.model)
    planned = control.plan(runtime_id, body, members=members, model=model)

    manifest, files = materialize(
        body,
        runtime,
        members,
        model,
        lambda b, m: planned,
        expected_digest=expected_digest,
        companions=companions,
        adapters=adapters,
        score=lambda feasible: (feasible[0], planned["profiling"]),
    )
    manifest = manifest.model_copy(
        update={
            "settings": execution_settings(
                body,
                planned,
                model,
                getattr(control.service.state(), "infer_port", 18080),
            )
        }
    )

    if manifest.settings.get("prima_lora"):
        for rank in manifest.ranks:
            if rank.node_id != control.service.state().node_id and (
                type(members[rank.node_id].get("prima_lora_assets_version")) is not int
                or members[rank.node_id]["prima_lora_assets_version"] != 1
            ):
                raise ValueError(f"{rank.node_id}: upgrade the worker for verified Prima LoRA assets")

    if include_profile and len(manifest.ranks) > 1:
        feasible = candidates(body, members, lambda b, m: control._capacity_plan(runtime, b, m))
        manifest = ExecutionManifest.model_validate(
            manifest.model_copy(
                update={
                    "profile_edges": candidate_profile_edges(
                        manifest,
                        feasible,
                        segmented="segmented_placement" in runtime.get("capabilities", {}).get("features", []),
                    )
                }
            ).model_dump()
        )

    if len(manifest.ranks) > 1:
        for rank in manifest.ranks:
            if rank.node_id != control.service.state().node_id and (
                type(members[rank.node_id].get("prima_resources_version")) is not int
                or members[rank.node_id]["prima_resources_version"] != 1
            ):
                raise ValueError(f"{rank.node_id}: upgrade the worker for Prima resource reservations")

    if include_profile:
        for rank in manifest.ranks:
            if rank.node_id != control.service.state().node_id and members[rank.node_id].get("prima_profile_version") != 2:
                raise ValueError(f"{rank.node_id}: upgrade the worker for preparation-bound Prima profiling")
            if (
                len(manifest.ranks) > 1
                and rank.node_id != control.service.state().node_id
                and (
                    type(members[rank.node_id].get("prima_link_profile_version")) is not int
                    or members[rank.node_id]["prima_link_profile_version"]
                    not in ((2,) if manifest.profile_edges else (1, 2))
                )
            ):
                raise ValueError(f"{rank.node_id}: upgrade the worker for signed candidate private-link profiling")
            if (
                len(manifest.ranks) > 1
                and rank.node_id != control.service.state().node_id
                and members[rank.node_id].get("prima_profile_context_version") != 1
            ):
                raise ValueError(f"{rank.node_id}: upgrade the worker for fresh Prima profile context")

    if len(manifest.ranks) > 1:
        evidence = {}
        for rank in manifest.ranks:
            check()
            if rank.node_id == control.service.state().node_id:
                value = control.observe_participant(runtime_id, rank, manifest.backends[rank.node_id])
            else:
                if members[rank.node_id].get("prima_inventory_version") != 1:
                    raise ValueError(f"{rank.node_id}: upgrade the worker agent for signed Prima hardware inventory")
                raw = control._call(
                    rank.node_id,
                    run_id,
                    "prima_inventory",
                    {
                        "runtime_id": runtime_id,
                        "rank": rank.model_dump(),
                        "backend": manifest.backends[rank.node_id],
                    },
                )
                value = ParticipantEvidence.model_validate(raw)
            require_shared_identity(value, rank.node_id, runtime_id, runtime)
            evidence[rank.node_id] = value
        manifest = ExecutionManifest.model_validate(
            manifest.model_copy(update={"participant_evidence": evidence}).model_dump()
        )

    require_execution_capabilities(manifest, runtime.get("capabilities", {}))

    # Bind reservation
    lease = control.preparation_reservations.get(run_id)
    if lease is not None:
        lease.bind(
            prima_scope(
                run_id,
                authority_owner(control.service.channels.master_authority()),
                manifest.digest,
            )
        )

    manifest_file = control.root / run_id / "execution.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")))
    files.append(
        (
            Asset(
                name="execution.json",
                kind="manifest",
                sha256=manifest.digest,
                size=manifest_file.stat().st_size,
            ),
            manifest_file,
        )
    )
    validate_asset_batch([asset for asset, _ in files], include_manifest=True)

    # Persist materialized manifest & node configuration to SQLite store
    store.update_run(
        run_id,
        manifest=manifest.model_dump(),
        manifest_digest=manifest.digest,
        model_path=str(model),
        command_owner=authority_owner(control.service.channels.master_authority()),
    )
    for rank in manifest.ranks:
        store.update_node(
            run_id,
            rank.node_id,
            role=rank.role,
            rank_index=rank.rank,
            assigned_address=rank.address,
            backend=manifest.backends.get(rank.node_id, "cpu"),
            devices=rank.devices,
        )

    store.transition_stage(
        run_id,
        "materialize",
        "completed",
        {"fraction": 1.0, "label": "Placement manifest materialized"},
    )
    store.append_log(
        run_id,
        "INFO",
        "lifecycle",
        f"Materialized manifest with {len(manifest.ranks)} ranks and {len(files)} assets",
        stage="materialize",
    )
    return manifest, files
