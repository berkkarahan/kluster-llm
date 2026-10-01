"""Convert placement request mappings into the typed core planner interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from kllm.core.placement import plan_devices
from kllm.core.placement_models import (
    DevicePlacementPlan,
    ModelLayout,
    PlacementNodeInput,
    PlacementSettings,
)


def plan_from_settings(
    nodes: Sequence[Mapping[str, Any] | PlacementNodeInput],
    model_size_bytes: int,
    *,
    launch_settings: Mapping[str, Any],
    model_layout: Mapping[str, Any] | None,
    context: int,
    context_auto: bool,
    headroom: float,
    device: str,
    vram_weight: float,
    latency_weight: float,
    mmproj_size_bytes: int = 0,
    draft_model_size_bytes: int = 0,
    vision: bool = False,
    node_shares: dict[str, float] | None = None,
) -> DevicePlacementPlan:
    """Build a complete structured placement plan from a public request."""
    settings_data = dict(launch_settings)
    settings_data.setdefault(
        "auxiliary_weights_bytes", int(mmproj_size_bytes) + int(draft_model_size_bytes)
    )
    settings_data.setdefault("draft_size_bytes", int(draft_model_size_bytes))
    settings_data.setdefault("vision_runtime", bool(vision or mmproj_size_bytes))
    settings = PlacementSettings.from_mapping(settings_data)
    placement_nodes = tuple(
        node if isinstance(node, PlacementNodeInput) else PlacementNodeInput.from_mapping(node)
        for node in nodes
    )
    layout = ModelLayout.from_mapping(model_layout)

    def compute(context_length: int) -> DevicePlacementPlan:
        result = plan_devices(
            placement_nodes,
            model_size_bytes,
            layout,
            settings,
            context=context_length,
            headroom=headroom,
            device=device,
            vram_weight=vram_weight,
            latency_weight=latency_weight,
            node_shares=node_shares,
        )
        if not isinstance(result, DevicePlacementPlan):
            raise RuntimeError("device planner returned invalid data")
        return result

    if not context_auto:
        return compute(context)

    context_limit = (
        int(layout.metadata.get(f"{layout.architecture}.context_length", 131072))
        if layout
        else 131072
    )
    maximum = min(131072, context_limit)
    low, high = 1, maximum // 512
    result = compute(512)
    while low <= high:
        midpoint = (low + high) // 2
        candidate = compute(midpoint * 512)
        if candidate.fit_status != "overflow":
            result = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return result
